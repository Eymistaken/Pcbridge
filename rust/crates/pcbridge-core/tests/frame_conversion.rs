//! Buffer-to-RGBA conversion, against buffers built by hand.
//!
//! Every case here is a shape a real PipeWire producer can hand over: rows
//! padded to an alignment, a frame that starts part way into a shared mapping,
//! a chunk that claims fewer valid bytes than a full frame, and the two channel
//! orders GNOME actually uses. The expected bytes are written out literally --
//! none of them come from running the converter and recording what it did.

use pcbridge_core::frame::{FrameError, FrameId, FrameSpec, MAX_PIXELS, PixelFormat, RgbaFrame};

fn spec(format: PixelFormat, width: u32, height: u32, stride: u32) -> FrameSpec {
    FrameSpec {
        format,
        width,
        height,
        stride,
        offset: 0,
        size: (height - 1) * stride + width * 4,
    }
}

fn id() -> FrameId {
    FrameId {
        sequence: 7,
        captured_at_ns: 1_234_567_890,
    }
}

fn decode(png: &[u8]) -> (u32, u32, Vec<u8>) {
    let decoder = png::Decoder::new(std::io::Cursor::new(png));
    let mut reader = decoder.read_info().expect("PNG header");
    let mut pixels = vec![0; reader.output_buffer_size().expect("output size")];
    let info = reader.next_frame(&mut pixels).expect("PNG frame");
    assert_eq!(info.color_type, png::ColorType::Rgba);
    assert_eq!(info.bit_depth, png::BitDepth::Eight);
    pixels.truncate((info.width * info.height * 4) as usize);
    (info.width, info.height, pixels)
}

// ------------------------------------------------------------ channel order

#[test]
fn rgba_rows_are_copied_untouched() {
    // Two pixels: opaque red, half-transparent green.
    let data = vec![0xFF, 0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0x80];

    let frame =
        RgbaFrame::from_buffer(&spec(PixelFormat::Rgba, 2, 1, 8), &data, id()).expect("convert");

    assert_eq!(frame.pixels, data);
}

#[test]
fn bgra_swaps_blue_and_red_and_keeps_alpha() {
    // In memory: B, G, R, A.
    let data = vec![0x11, 0x22, 0x33, 0x44];

    let frame =
        RgbaFrame::from_buffer(&spec(PixelFormat::Bgra, 1, 1, 4), &data, id()).expect("convert");

    assert_eq!(frame.pixels, vec![0x33, 0x22, 0x11, 0x44]);
}

#[test]
fn bgrx_swaps_blue_and_red() {
    let data = vec![0x11, 0x22, 0x33, 0x99];

    let frame =
        RgbaFrame::from_buffer(&spec(PixelFormat::Bgrx, 1, 1, 4), &data, id()).expect("convert");

    assert_eq!(frame.pixels, vec![0x33, 0x22, 0x11, 0xFF]);
}

#[test]
fn the_x_channel_never_becomes_alpha() {
    // A producer that writes zeros into the padding byte. Copying it would
    // make the whole screenshot transparent -- valid PNG, blank image.
    let data = vec![0x11, 0x22, 0x33, 0x00, 0x44, 0x55, 0x66, 0x00];

    for format in [PixelFormat::Bgrx, PixelFormat::Rgbx] {
        let frame = RgbaFrame::from_buffer(&spec(format, 2, 1, 8), &data, id()).expect("convert");
        assert_eq!(frame.pixels[3], 0xFF, "{format:?} pixel 0 alpha");
        assert_eq!(frame.pixels[7], 0xFF, "{format:?} pixel 1 alpha");
    }
}

#[test]
fn only_bgra_and_rgba_carry_alpha() {
    assert!(PixelFormat::Rgba.has_alpha());
    assert!(PixelFormat::Bgra.has_alpha());
    assert!(!PixelFormat::Rgbx.has_alpha());
    assert!(!PixelFormat::Bgrx.has_alpha());
}

// ------------------------------------------------------------------ layout

#[test]
fn a_padded_stride_does_not_shear_the_image() {
    // 2x2 RGBA with a 4 byte pad after each row. Read as one block, row 1 would
    // start four bytes late and every row would slide further right.
    let mut data = Vec::new();
    data.extend_from_slice(&[1, 1, 1, 255, 2, 2, 2, 255]); // row 0
    data.extend_from_slice(&[0xDE, 0xAD, 0xBE, 0xEF]); // padding
    data.extend_from_slice(&[3, 3, 3, 255, 4, 4, 4, 255]); // row 1
    data.extend_from_slice(&[0xDE, 0xAD, 0xBE, 0xEF]); // padding

    let frame =
        RgbaFrame::from_buffer(&spec(PixelFormat::Rgba, 2, 2, 12), &data, id()).expect("convert");

    assert_eq!(
        frame.pixels,
        vec![1, 1, 1, 255, 2, 2, 2, 255, 3, 3, 3, 255, 4, 4, 4, 255]
    );
    assert_eq!(frame.byte_len(), 16);
}

#[test]
fn a_nonzero_chunk_offset_is_honored() {
    let mut data = vec![0xAA; 7]; // whatever was in the mapping before us
    data.extend_from_slice(&[9, 8, 7, 255]);

    let mut layout = spec(PixelFormat::Rgba, 1, 1, 4);
    layout.offset = 7;

    let frame = RgbaFrame::from_buffer(&layout, &data, id()).expect("convert");

    assert_eq!(frame.pixels, vec![9, 8, 7, 255]);
}

#[test]
fn the_last_row_does_not_need_its_padding() {
    // A producer may stop the buffer right after the final row of pixels.
    // Demanding a full stride for it would reject a perfectly good frame.
    let mut data = Vec::new();
    data.extend_from_slice(&[1, 1, 1, 255]);
    data.extend_from_slice(&[0, 0, 0, 0]); // padding after row 0 only
    data.extend_from_slice(&[2, 2, 2, 255]);

    let mut layout = spec(PixelFormat::Rgba, 1, 2, 8);
    layout.size = 12;

    let frame = RgbaFrame::from_buffer(&layout, &data, id()).expect("convert");

    assert_eq!(frame.pixels, vec![1, 1, 1, 255, 2, 2, 2, 255]);
}

// ------------------------------------------------------------- refusals

#[test]
fn a_stride_narrower_than_a_row_is_refused() {
    let data = vec![0; 64];
    let mut layout = spec(PixelFormat::Rgba, 4, 2, 16);
    layout.stride = 12;

    let error = RgbaFrame::from_buffer(&layout, &data, id()).expect_err("refuse");

    assert_eq!(
        error,
        FrameError::StrideTooSmall {
            stride: 12,
            needed: 16
        }
    );
}

#[test]
fn a_chunk_that_claims_fewer_bytes_than_the_frame_is_refused() {
    let data = vec![0; 64];
    let mut layout = spec(PixelFormat::Rgba, 4, 4, 16);
    layout.size = 48; // producer marked three rows valid, not four

    let error = RgbaFrame::from_buffer(&layout, &data, id()).expect_err("refuse");

    assert_eq!(
        error,
        FrameError::ChunkTooSmall {
            needed: 64,
            available: 48
        }
    );
}

#[test]
fn a_buffer_shorter_than_the_frame_is_refused() {
    let data = vec![0; 40];

    let error = RgbaFrame::from_buffer(&spec(PixelFormat::Rgba, 4, 4, 16), &data, id())
        .expect_err("refuse");

    assert_eq!(
        error,
        FrameError::BufferTooSmall {
            needed: 64,
            available: 40
        }
    );
}

#[test]
fn an_offset_past_the_end_of_the_buffer_is_refused() {
    let data = vec![0; 16];
    let mut layout = spec(PixelFormat::Rgba, 1, 1, 4);
    layout.offset = 4000;

    let error = RgbaFrame::from_buffer(&layout, &data, id()).expect_err("refuse");

    assert_eq!(
        error,
        FrameError::BufferTooSmall {
            needed: 4004,
            available: 16
        }
    );
}

#[test]
fn a_frame_with_no_pixels_is_refused() {
    let data = vec![0; 16];

    for (width, height) in [(0, 4), (4, 0), (0, 0)] {
        let layout = FrameSpec {
            format: PixelFormat::Rgba,
            width,
            height,
            stride: 16,
            offset: 0,
            size: 16,
        };
        assert_eq!(
            RgbaFrame::from_buffer(&layout, &data, id()).expect_err("refuse"),
            FrameError::EmptyFrame { width, height }
        );
    }
}

#[test]
fn an_oversized_frame_is_refused_before_anything_is_allocated() {
    // Four bytes of data against a frame claiming 3.6 billion pixels. The
    // answer has to be the size limit, not "the buffer is too small": that
    // ordering is the proof that the number was rejected while it was still a
    // number, before a 14 GB allocation was attempted.
    let data = vec![0; 4];
    let layout = FrameSpec {
        format: PixelFormat::Bgrx,
        width: 60_000,
        height: 60_000,
        stride: 240_000,
        offset: 0,
        size: u32::MAX,
    };

    let error = RgbaFrame::from_buffer(&layout, &data, id()).expect_err("refuse");

    assert_eq!(
        error,
        FrameError::TooManyPixels {
            pixels: 3_600_000_000,
            limit: MAX_PIXELS
        }
    );
}

#[test]
fn the_largest_dimensions_do_not_overflow_the_arithmetic() {
    let data = vec![0; 4];
    let layout = FrameSpec {
        format: PixelFormat::Bgrx,
        width: u32::MAX,
        height: u32::MAX,
        stride: u32::MAX,
        offset: u32::MAX,
        size: u32::MAX,
    };

    // The point is that this returns rather than panicking on a multiplication.
    assert!(matches!(
        RgbaFrame::from_buffer(&layout, &data, id()),
        Err(FrameError::TooManyPixels { .. })
    ));
}

// ---------------------------------------------------------------- identity

#[test]
fn the_producer_frame_id_is_carried_through_untouched() {
    let data = vec![0; 4];
    let marker = FrameId {
        sequence: 99,
        captured_at_ns: -5,
    };

    let frame =
        RgbaFrame::from_buffer(&spec(PixelFormat::Rgba, 1, 1, 4), &data, marker).expect("convert");

    assert_eq!(frame.id, marker);
}

// --------------------------------------------------------------------- PNG

#[test]
fn a_png_round_trips_to_the_same_pixels() {
    let mut data = Vec::new();
    for row in 0..3u8 {
        for column in 0..5u8 {
            data.extend_from_slice(&[row * 40, column * 50, 0x10, 0xFF]);
        }
        data.extend_from_slice(&[0xFF; 8]); // padding this conversion must drop
    }
    let mut layout = spec(PixelFormat::Rgba, 5, 3, 28);
    layout.size = 2 * 28 + 20;

    let frame = RgbaFrame::from_buffer(&layout, &data, id()).expect("convert");
    let png = frame.to_png().expect("encode");
    let (width, height, decoded) = decode(&png);

    assert_eq!((width, height), (5, 3));
    assert_eq!(decoded, frame.pixels);
}

#[test]
fn an_encoded_png_is_not_a_blank_placeholder() {
    // The acceptance rule: a successful capture must never come back as an
    // empty or all-black image that merely decodes.
    let mut data = Vec::new();
    for index in 0..16u8 {
        data.extend_from_slice(&[index * 16, 0x20, 0x30, 0x00]);
    }

    let frame =
        RgbaFrame::from_buffer(&spec(PixelFormat::Bgrx, 4, 4, 16), &data, id()).expect("convert");
    let png = frame.to_png().expect("encode");
    let (_, _, decoded) = decode(&png);

    assert!(!png.is_empty());
    assert!(
        decoded.chunks_exact(4).any(|pixel| pixel[..3] != [0, 0, 0]),
        "every pixel decoded to black"
    );
    assert!(
        decoded.chunks_exact(4).all(|pixel| pixel[3] == 0xFF),
        "an x-format frame decoded with transparent pixels"
    );
    // Red and blue really were exchanged on the way through.
    assert_eq!(&decoded[..4], &[0x30, 0x20, 0x00, 0xFF]);
}

#[test]
fn a_single_pixel_png_still_decodes() {
    let frame = RgbaFrame::from_buffer(
        &spec(PixelFormat::Rgba, 1, 1, 4),
        &[0x01, 0x02, 0x03, 0x04],
        id(),
    )
    .expect("convert");

    let png = frame.to_png().expect("encode");
    let (width, height, decoded) = decode(&png);

    assert_eq!((width, height), (1, 1));
    assert_eq!(decoded, vec![0x01, 0x02, 0x03, 0x04]);
}
