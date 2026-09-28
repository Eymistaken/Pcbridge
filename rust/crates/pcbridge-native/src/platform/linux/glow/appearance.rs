//! GNOME frame constants, easing, and premultiplied strip pixels.

pub const EDGE_ALPHA: f64 = 0.42;
pub const FADE_IN_MS: f64 = 700.0;
pub const FADE_OUT_MS: f64 = 500.0;
pub const BREATH_HALF_MS: f64 = 5500.0;
pub const FALLOFF: [(f64, f64); 7] = [
    (0.00, 1.000),
    (0.06, 0.700),
    (0.18, 0.380),
    (0.35, 0.160),
    (0.55, 0.055),
    (0.78, 0.012),
    (1.00, 0.000),
];
#[must_use]
pub fn depth(width: u32, height: u32) -> u32 {
    (f64::from(width.min(height)) * 0.085)
        .clamp(48.0, 150.0)
        .round() as u32
}

#[must_use]
pub fn breath(elapsed_ms: f64) -> f64 {
    1.0 - 0.12 * (1.0 - (std::f64::consts::PI * elapsed_ms / BREATH_HALF_MS).cos()) / 2.0
}

#[must_use]
pub fn fade_in(elapsed_ms: f64) -> f64 {
    1.0 - (1.0 - (elapsed_ms / FADE_IN_MS).clamp(0.0, 1.0)).powi(2)
}

#[must_use]
pub fn fade_out(elapsed_ms: f64) -> f64 {
    (1.0 - (elapsed_ms / FADE_OUT_MS).clamp(0.0, 1.0)).powi(2)
}

#[must_use]
pub fn falloff(position: f64) -> f64 {
    if position <= 0.0 {
        return 1.0;
    }
    for pair in FALLOFF.windows(2) {
        let [(x0, y0), (x1, y1)] = [pair[0], pair[1]];
        if position <= x1 {
            return y0 + (y1 - y0) * (position - x0) / (x1 - x0);
        }
    }
    0.0
}

#[derive(Clone, Copy, Debug)]
pub enum Edge {
    Top,
    Bottom,
    Left,
    Right,
}

impl Edge {
    pub(super) fn horizontal(self) -> bool {
        matches!(self, Self::Top | Self::Bottom)
    }
    pub(super) fn reversed(self) -> bool {
        matches!(self, Self::Bottom | Self::Right)
    }
}

pub(super) fn pixels(edge: Edge, width: u32, height: u32, opacity: f64, breathing: f64) -> Vec<u8> {
    let mut bytes = vec![0; width as usize * height as usize * 4];
    let axis = if edge.horizontal() { height } else { width };
    let alphas: Vec<_> = (0..axis)
        .map(|coordinate| {
            let distance = if edge.reversed() {
                axis - 1 - coordinate
            } else {
                coordinate
            };
            (255.0
                * EDGE_ALPHA
                * opacity
                * falloff(f64::from(distance) / f64::from(axis) / breathing))
            .round() as u8
        })
        .collect();
    // wl_shm ARGB8888 is native endian and premultiplied. White's R/G/B
    // channels equal alpha, so all four bytes have the same value.
    if edge.horizontal() {
        for (row, alpha) in bytes.chunks_exact_mut(width as usize * 4).zip(alphas) {
            row.fill(alpha);
        }
    } else {
        let row: Vec<_> = alphas.into_iter().flat_map(|alpha| [alpha; 4]).collect();
        for destination in bytes.chunks_exact_mut(row.len()) {
            destination.copy_from_slice(&row);
        }
    }
    bytes
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reference_constants_and_falloff_are_preserved() {
        assert_eq!(depth(1920, 1080), 92);
        assert_eq!(depth(320, 240), 48);
        assert_eq!(depth(7680, 4320), 150);
        assert_eq!(depth(1280, 800), 68);
        for (position, factor) in FALLOFF {
            assert!((falloff(position) - factor).abs() < 1e-10);
        }
        assert_eq!(falloff(1.2), 0.0);
        assert!((breath(0.0) - 1.0).abs() < 1e-10);
        assert!((breath(5500.0) - 0.88).abs() < 1e-10);
        assert!((breath(11000.0) - 1.0).abs() < 1e-10);
        assert_eq!(fade_in(0.0), 0.0);
        assert_eq!(fade_in(700.0), 1.0);
        assert_eq!(fade_out(0.0), 1.0);
        assert_eq!(fade_out(500.0), 0.0);
    }

    #[test]
    fn outside_edge_stays_fixed_and_argb_white_is_premultiplied() {
        for edge in [Edge::Top, Edge::Bottom, Edge::Left, Edge::Right] {
            let (w, h) = if edge.horizontal() { (2, 68) } else { (68, 2) };
            let thick = pixels(edge, w, h, 1.0, 1.0);
            let thin = pixels(edge, w, h, 1.0, 0.88);
            let coordinates = if edge.horizontal() {
                (0..h).map(|y| (0, y)).collect::<Vec<_>>()
            } else {
                (0..w).map(|x| (x, 0)).collect()
            };
            let mut alphas = Vec::new();
            for (x, y) in coordinates {
                let offset = (y * w + x) as usize * 4;
                assert_eq!(thick[offset..offset + 4], [thick[offset]; 4]);
                assert!(thin[offset] <= thick[offset]);
                alphas.push(thick[offset]);
            }
            if edge.reversed() {
                alphas.reverse();
            }
            assert_eq!(alphas[0], 107);
            assert!(alphas.windows(2).all(|p| p[0] >= p[1]));
            assert_eq!(*alphas.last().unwrap(), 0);
        }
    }
}
