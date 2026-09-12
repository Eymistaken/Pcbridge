//! Raw capture buffers to RGBA8, and RGBA8 to PNG.
//!
//! A PipeWire buffer is not an image. It is a pointer, a stride that is usually
//! larger than the row, an offset into a shared memory block, a byte count the
//! producer claims is valid, and a channel order that is not the one PNG wants.
//! Every one of those five can be wrong or hostile, and the cost of getting one
//! wrong is not a crash -- it is a screenshot that looks plausible and is not.
//!
//! So the rules live here, in the crate with no I/O and no platform, and the
//! transport hands them a description plus bytes. Nothing in this module
//! guesses: an unreadable buffer is an error, never a best effort.
//!
//! Three traps worth naming, because each one produces a *quiet* wrong answer:
//!
//! * **The `x` channel is not alpha.** `BGRx` and `RGBx` leave the fourth byte
//!   undefined. Copying it into the alpha channel produces a fully transparent
//!   PNG whenever the producer happens to write zeros -- an image that is
//!   technically valid and completely blank.
//! * **Stride is not width times four.** Rows are padded. Reading the buffer as
//!   one contiguous block gives an image that shears further right with every
//!   row.
//! * **Size limits belong before the allocation, not after.** A frame header
//!   claiming 60000x60000 must be refused while it is still a number, not after
//!   14 GB has been asked for.

use crate::protocol::MAX_BINARY_BYTES;

/// Bytes per pixel in every format we accept.
const BYTES_PER_PIXEL: u64 = 4;

/// Largest frame we will convert, in pixels.
///
/// 32 million covers an 8K display several times over; beyond that a frame
/// description is far more likely to be wrong than real.
pub const MAX_PIXELS: u64 = 32_000_000;

/// The pixel limit has to stay under the payload limit. If it ever does not, a
/// frame could pass the size check and then build a buffer the IPC layer cannot
/// carry -- a failure that would appear only on the largest screens anyone
/// happens to own. Checked when this crate compiles, not when it runs.
const _: () = assert!(MAX_PIXELS * BYTES_PER_PIXEL <= MAX_BINARY_BYTES);

/// The four packed layouts we accept.
///
/// Names describe **memory order**, the way SPA and GStreamer name them:
/// `Bgrx` means byte 0 is blue, byte 1 green, byte 2 red, byte 3 undefined.
/// Which SPA enum value maps to which variant is the transport's problem, and
/// is decided in `platform/linux/capture.rs` against the real header rather
/// than against a constant copied into this file.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PixelFormat {
    Rgba,
    Rgbx,
    Bgra,
    Bgrx,
}

impl PixelFormat {
    /// Whether the fourth byte carries alpha or is padding.
    #[must_use]
    pub const fn has_alpha(self) -> bool {
        matches!(self, Self::Rgba | Self::Bgra)
    }
}

/// What the producer says about one buffer.
///
/// `offset` and `size` are the chunk fields: where the frame starts inside the
/// mapped memory and how many bytes of it the producer marked valid. They are
/// separate on purpose -- a producer can hand over a large mapping with a small
/// valid region, and trusting the mapping instead of the chunk reads whatever
/// was in that memory before.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FrameSpec {
    pub format: PixelFormat,
    pub width: u32,
    pub height: u32,
    pub stride: u32,
    pub offset: u32,
    pub size: u32,
}

/// Producer-side identity of a frame, carried through untouched.
///
/// `captured_at_ns` is the producer's clock, not ours. It is reported, never
/// compared against a local timestamp: deciding whether a frame is new enough
/// is done with our own monotonic receipt time, in the capture worker.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct FrameId {
    pub sequence: u64,
    pub captured_at_ns: i64,
}

/// A frame we own outright: tightly packed RGBA8, no padding, no borrowed
/// producer memory.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RgbaFrame {
    pub width: u32,
    pub height: u32,
    pub id: FrameId,
    pub pixels: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum FrameError {
    #[error("frame has no pixels: {width}x{height}")]
    EmptyFrame { width: u32, height: u32 },
    #[error("frame is {pixels} pixels; the limit is {limit}")]
    TooManyPixels { pixels: u64, limit: u64 },
    #[error("stride {stride} is smaller than one row of {needed} bytes")]
    StrideTooSmall { stride: u32, needed: u64 },
    #[error("the producer marked {available} valid bytes; the frame needs {needed}")]
    ChunkTooSmall { needed: u64, available: u64 },
    #[error("the buffer holds {available} bytes; the frame needs {needed}")]
    BufferTooSmall { needed: u64, available: u64 },
    #[error("{bytes} bytes exceeds the {limit} byte payload limit")]
    PayloadTooLarge { bytes: u64, limit: u64 },
    #[error("PNG encoding failed: {0}")]
    Encoding(String),
}

impl FrameSpec {
    /// Everything that can be decided from the numbers alone.
    ///
    /// Returns the byte span the frame occupies, measured from `offset`. Called
    /// before a single byte is allocated, which is the whole point: a hostile or
    /// broken header must not be able to ask for memory.
    fn span(&self) -> Result<u64, FrameError> {
        if self.width == 0 || self.height == 0 {
            return Err(FrameError::EmptyFrame {
                width: self.width,
                height: self.height,
            });
        }

        let width = u64::from(self.width);
        let height = u64::from(self.height);
        let pixels = width * height;
        if pixels > MAX_PIXELS {
            return Err(FrameError::TooManyPixels {
                pixels,
                limit: MAX_PIXELS,
            });
        }

        let row_bytes = width * BYTES_PER_PIXEL;
        let stride = u64::from(self.stride);
        if stride < row_bytes {
            return Err(FrameError::StrideTooSmall {
                stride: self.stride,
                needed: row_bytes,
            });
        }

        let output_bytes = pixels * BYTES_PER_PIXEL;
        if output_bytes > MAX_BINARY_BYTES {
            return Err(FrameError::PayloadTooLarge {
                bytes: output_bytes,
                limit: MAX_BINARY_BYTES,
            });
        }

        // The last row needs a row, not a stride: padding after it may not
        // exist, and demanding it would reject frames that are entirely valid.
        Ok((height - 1) * stride + row_bytes)
    }
}

impl RgbaFrame {
    /// Convert one producer buffer into an owned RGBA8 frame.
    ///
    /// `data` is the whole mapped region; the frame starts at `spec.offset`.
    pub fn from_buffer(spec: &FrameSpec, data: &[u8], id: FrameId) -> Result<Self, FrameError> {
        let span = spec.span()?;
        let offset = u64::from(spec.offset);
        let available = data.len() as u64;

        if u64::from(spec.size) < span {
            return Err(FrameError::ChunkTooSmall {
                needed: span,
                available: u64::from(spec.size),
            });
        }
        let end = offset.checked_add(span).ok_or(FrameError::BufferTooSmall {
            needed: u64::MAX,
            available,
        })?;
        if end > available {
            return Err(FrameError::BufferTooSmall {
                needed: end,
                available,
            });
        }

        let width = spec.width as usize;
        let height = spec.height as usize;
        let stride = spec.stride as usize;
        let offset = spec.offset as usize;
        let row_bytes = width * 4;
        let mut pixels = Vec::with_capacity(row_bytes * height);

        for row in 0..height {
            let start = offset + row * stride;
            let source = &data[start..start + row_bytes];
            match spec.format {
                // Already what we want, padding and all: copy the row whole.
                PixelFormat::Rgba => pixels.extend_from_slice(source),
                PixelFormat::Rgbx => {
                    for pixel in source.chunks_exact(4) {
                        pixels.extend_from_slice(&[pixel[0], pixel[1], pixel[2], 0xFF]);
                    }
                }
                PixelFormat::Bgra => {
                    for pixel in source.chunks_exact(4) {
                        pixels.extend_from_slice(&[pixel[2], pixel[1], pixel[0], pixel[3]]);
                    }
                }
                PixelFormat::Bgrx => {
                    for pixel in source.chunks_exact(4) {
                        pixels.extend_from_slice(&[pixel[2], pixel[1], pixel[0], 0xFF]);
                    }
                }
            }
        }

        Ok(Self {
            width: spec.width,
            height: spec.height,
            id,
            pixels,
        })
    }

    #[must_use]
    pub fn byte_len(&self) -> usize {
        self.pixels.len()
    }

    /// Encode as PNG.
    ///
    /// Kept separate from the conversion because of where each one runs: the
    /// conversion has to happen while the producer's memory is still mapped,
    /// and encoding must not. Splitting them is what lets the capture worker
    /// release the buffer first and encode afterwards.
    pub fn to_png(&self) -> Result<Vec<u8>, FrameError> {
        let mut buffer = Vec::new();
        let mut encoder = png::Encoder::new(&mut buffer, self.width, self.height);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder
            .write_header()
            .map_err(|error| FrameError::Encoding(error.to_string()))?;
        writer
            .write_image_data(&self.pixels)
            .map_err(|error| FrameError::Encoding(error.to_string()))?;
        writer
            .finish()
            .map_err(|error| FrameError::Encoding(error.to_string()))?;
        let bytes = buffer.len() as u64;
        if bytes > MAX_BINARY_BYTES {
            return Err(FrameError::PayloadTooLarge {
                bytes,
                limit: MAX_BINARY_BYTES,
            });
        }
        Ok(buffer)
    }
}
