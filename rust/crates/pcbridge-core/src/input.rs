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

/// Largest delta a single relative nudge may carry, per axis.
///
/// Wider than the canvas in either direction, so it never blocks real work;
/// an unbounded delta would be a denial of service against the user's own
/// desktop. Mirrors `input.MOVE_BY_MAX` on the Python side.
pub const MOVE_BY_MAX: i32 = 4000;

/// Ceiling on how many chunks one relative nudge is split into.
///
/// Worst case 64 x 8 ms ~ 512 ms, the same order as the measured 498 ms
/// diagonal ceiling of an absolute move.
pub const MOVE_BY_MAX_CHUNKS: usize = 64;

/// Target size of one chunk, in device units.
pub const MOVE_BY_CHUNK_UNITS: i32 = 16;

/// Split a relative delta into the chunks that are sent one after another.
///
/// This is not `move_path`. There are no two points to interpolate between,
/// only a total, so the pattern is the one `scroll` uses: a fixed step, N
/// times, with a hard cap.
///
/// MEASURED 2026-09-20 on the real desktop: splitting does not change the
/// total. 200 units travelled 92 px whether sent as one event or as 200
/// separate 1-unit events, because libinput accumulates the remainder. The
/// split exists so a client reading relative motion sees a turn rather than
/// a jump.
///
/// The sum of the returned chunks is always exactly the clamped delta.
#[must_use]
pub fn relative_chunks(dx: i32, dy: i32) -> Vec<(i32, i32)> {
    let dx = dx.clamp(-MOVE_BY_MAX, MOVE_BY_MAX);
    let dy = dy.clamp(-MOVE_BY_MAX, MOVE_BY_MAX);
    let span = dx.abs().max(dy.abs());
    if span == 0 {
        return Vec::new();
    }
    let wanted = ((span + MOVE_BY_CHUNK_UNITS - 1) / MOVE_BY_CHUNK_UNITS).max(1) as usize;
    let steps = wanted.min(MOVE_BY_MAX_CHUNKS);
    let xs = share(dx, steps);
    let ys = share(dy, steps);
    xs.into_iter().zip(ys).collect()
}

/// Split `total` into `steps` parts, remainder first. The sum is preserved.
fn share(total: i32, steps: usize) -> Vec<i32> {
    let sign = if total < 0 { -1 } else { 1 };
    let magnitude = total.unsigned_abs();
    let count = steps as u32;
    let base = magnitude / count;
    let extra = magnitude % count;
    (0..count)
        .map(|index| sign * (base + u32::from(index < extra)) as i32)
        .collect()
}
