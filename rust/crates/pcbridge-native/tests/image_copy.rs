//! Packed image-copy constraints and orientation must not silently guess.

use pcbridge_core::frame::{FrameId, MAX_PIXELS, RgbaFrame};
use pcbridge_native::platform::linux::image_copy::{buffer_spec, normalize};
use wayland_client::protocol::{wl_output::Transform as T, wl_shm::Format as F};

#[test]
fn unsupported_formats_and_unbounded_dimensions_are_refused_before_allocation() {
    for (width, height) in [
        (0, 1),
        (1, 0),
        (u32::MAX, 1),
        (8000, 8000),
        (MAX_PIXELS as u32 + 1, 1),
    ] {
        assert!(buffer_spec(width, height, &[F::Xrgb8888]).is_err());
    }
    assert!(buffer_spec(1280, 800, &[]).is_err());
    assert!(buffer_spec(1280, 800, &[F::Rgb565]).is_err());
    let (spec, format) = buffer_spec(1280, 800, &[F::Argb8888, F::Xrgb8888]).unwrap();
    assert_eq!(format, F::Xrgb8888);
    assert_eq!((spec.stride, spec.size), (5120, 4_096_000));
}

fn numbered() -> RgbaFrame {
    RgbaFrame {
        width: 3,
        height: 2,
        id: FrameId::default(),
        pixels: (1..=6).flat_map(|value| [value, 0, 0, 255]).collect(),
    }
}

#[test]
fn all_eight_wayland_transforms_preserve_asymmetric_pixel_positions() {
    let cases = [
        (T::Normal, (3, 2), vec![1, 2, 3, 4, 5, 6]),
        (T::_90, (2, 3), vec![4, 1, 5, 2, 6, 3]),
        (T::_180, (3, 2), vec![6, 5, 4, 3, 2, 1]),
        (T::_270, (2, 3), vec![3, 6, 2, 5, 1, 4]),
        (T::Flipped, (3, 2), vec![3, 2, 1, 6, 5, 4]),
        (T::Flipped90, (2, 3), vec![1, 4, 2, 5, 3, 6]),
        (T::Flipped180, (3, 2), vec![4, 5, 6, 1, 2, 3]),
        (T::Flipped270, (2, 3), vec![6, 3, 5, 2, 4, 1]),
    ];
    for (transform, size, expected) in cases {
        let frame = normalize(numbered(), transform).unwrap();
        assert_eq!((frame.width, frame.height), size, "{transform:?}");
        assert_eq!(
            frame
                .pixels
                .chunks_exact(4)
                .map(|pixel| pixel[0])
                .collect::<Vec<_>>(),
            expected,
            "{transform:?}"
        );
        assert!(frame.pixels.chunks_exact(4).all(|pixel| pixel[3] == 255));
    }
}

#[test]
fn malformed_owned_frame_cannot_panic_during_orientation_conversion() {
    let mut frame = numbered();
    frame.pixels.pop();
    assert!(normalize(frame, T::_90).is_err());
    let mut frame = numbered();
    frame.width = u32::MAX;
    assert!(normalize(frame, T::Normal).is_err());
}
