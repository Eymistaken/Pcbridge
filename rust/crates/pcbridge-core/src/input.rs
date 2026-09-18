//! Pure pointer motion and coordinate conversion.
//!
//! No shot or monitor identity belongs here. Callers resolve those coordinate
//! spaces before crossing the native boundary; this module accepts global
//! desktop points only.

const MOVE_MIN_MS: f64 = 60.0;

/// Convert a resolved global desktop point to the absolute device range.
///
/// A zero-sized canvas has no valid device coordinate and is rejected. The
/// caller must not apply monitor offsets before or after this function.
#[must_use]
pub fn global_to_device(x: i32, y: i32, width: u32, height: u32) -> Option<(i32, i32)> {
    if width == 0 || height == 0 {
        return None;
    }
    let max_x = i32::try_from(width - 1).ok()?;
    let max_y = i32::try_from(height - 1).ok()?;
    Some((x.clamp(0, max_x), y.clamp(0, max_y)))
}

/// Return the smoothstep path from `(x1, y1)` to `(x2, y2)`, excluding the
/// starting point and always including the target.
#[must_use]
#[allow(clippy::too_many_arguments)]
pub fn move_path(
    x1: i32,
    y1: i32,
    x2: i32,
    y2: i32,
    speed: f64,
    max_ms: f64,
    step_seconds: f64,
    min_steps: usize,
) -> Vec<(i32, i32)> {
    let target = (x2, y2);
    let dx = f64::from(x2) - f64::from(x1);
    let dy = f64::from(y2) - f64::from(y1);
    let distance = dx.hypot(dy);
    if distance < 1.0 {
        return vec![target];
    }

    let steps = if speed > 0.0 {
        let duration_ms = (distance / speed * 1000.0)
            .max(MOVE_MIN_MS)
            .min(max_ms.max(1.0));
        let timed_steps = (duration_ms / (step_seconds * 1000.0)).round_ties_even() as usize;
        min_steps.max(timed_steps).max(1)
    } else {
        min_steps.max(1)
    };

    let mut points = Vec::with_capacity(steps);
    for index in 1..=steps {
        let time = index as f64 / steps as f64;
        let smooth = time * time * (3.0 - 2.0 * time);
        let point = (
            (f64::from(x1) + dx * smooth).round_ties_even() as i32,
            (f64::from(y1) + dy * smooth).round_ties_even() as i32,
        );
        if points.last().copied() != Some(point) {
            points.push(point);
        }
    }
    if points.last().copied() != Some(target) {
        points.push(target);
    }
    points
}
