//! Native frame ownership for one exact lease, independent of input devices.

use std::path::Path;
use std::time::{Duration, Instant, SystemTime};

use pcbridge_core::{DesktopLease, LEASE_STATE_FILE, LeaseToken};

use super::desktop_state::{DesktopStateProvider, ScreenLockState, SessionDesktopState};
use super::display::{DisplayReader, DisplaySnapshot};
use super::glow::{Overlay, breath, fade_in, fade_out};
use super::glow_state::{FrameRecord, parent_pid};
use super::idle::{unix_ms, writer_start_ticks};

fn lease_valid(directory: &Path, token: &LeaseToken) -> bool {
    DesktopLease::read(&directory.join(LEASE_STATE_FILE))
        .is_ok_and(|lease| lease.validates_at(token, unix_ms(SystemTime::now()) as f64 / 1000.0))
}

fn close_frame(overlay: &mut Overlay) {
    let started = Instant::now();
    let mut next_draw = Instant::now();
    while started.elapsed() < Duration::from_millis(500) {
        if Instant::now() >= next_draw {
            if overlay
                .draw(fade_out(started.elapsed().as_secs_f64() * 1000.0), 1.0)
                .is_err()
            {
                break;
            }
            next_draw = Instant::now() + Duration::from_millis(50);
        }
        if overlay.poll(Duration::from_millis(25)).is_err() {
            break;
        }
    }
}

fn rebuild(
    overlay: &mut Overlay,
    snapshot: &DisplaySnapshot,
    record: &mut FrameRecord,
    directory: &Path,
) -> Result<(), String> {
    record.ready = false;
    record.presented_unix_ms = 0;
    record.write(directory).map_err(|e| e.to_string())?;
    *overlay = Overlay::connect(&snapshot.monitors)?;
    record.topology_id = snapshot.topology_id.clone();
    record.outputs = snapshot
        .monitors
        .iter()
        .map(|monitor| monitor.connector.clone())
        .collect();
    record.strip_count = overlay.strip_count() as u32;
    Ok(())
}

/// Own the visible frame until expiry/revoke/replacement, parent death, or a
/// failed compositor connection. This entry point never opens input/capture.
pub fn watch(directory: &Path, token: LeaseToken) -> Result<(), String> {
    let canonical = directory.canonicalize().map_err(|e| e.to_string())?;
    if directory != canonical {
        return Err("Frame state directory must be its canonical absolute path".into());
    }
    let directory = canonical;
    if !lease_valid(&directory, &token) {
        return Err("No matching active frame lease".into());
    }
    let provider = SessionDesktopState::connect().map_err(|e| e.to_string())?;
    if provider.screen_lock().state != ScreenLockState::KnownUnlocked {
        return Err("The selected session is not known unlocked".into());
    }
    let reader = DisplayReader::connect().map_err(|e| e.to_string())?;
    let snapshot = reader.snapshot().map_err(|e| e.to_string())?;
    let mut overlay = Overlay::connect(&snapshot.monitors)?;
    let pid = std::process::id();
    let owner_pid = parent_pid(pid).ok_or("No frame owner process")?;
    let mut record = FrameRecord {
        version: 2,
        ready: false,
        pid,
        writer_start_ticks: writer_start_ticks(pid).ok_or("No frame process identity")?,
        owner_pid,
        owner_start_ticks: writer_start_ticks(owner_pid).ok_or("No owner process identity")?,
        grant_id: token.grant_id().into(),
        revoke_epoch: token.revoke_epoch(),
        wayland_display: std::env::var("WAYLAND_DISPLAY").map_err(|_| "No Wayland display")?,
        hyprland_instance: std::env::var("HYPRLAND_INSTANCE_SIGNATURE")
            .map_err(|_| "No Hyprland instance")?,
        topology_id: snapshot.topology_id.clone(),
        outputs: snapshot
            .monitors
            .iter()
            .map(|monitor| monitor.connector.clone())
            .collect(),
        strip_count: overlay.strip_count() as u32,
        presented_unix_ms: 0,
    };
    record.write(&directory).map_err(|e| e.to_string())?;
    let mut appeared = Instant::now();
    let mut next_draw = Instant::now();
    let mut next_topology = Instant::now() + Duration::from_millis(500);
    let mut previously_unlocked = true;
    let mut last_publication = (false, 0);
    let outcome = loop {
        if !lease_valid(&directory, &token) {
            break Ok(());
        }
        if parent_pid(pid) != Some(owner_pid)
            || writer_start_ticks(owner_pid) != Some(record.owner_start_ticks)
        {
            break Err("Frame owner process ended".into());
        }
        let unlocked = provider.screen_lock().state == ScreenLockState::KnownUnlocked;
        if unlocked && !previously_unlocked {
            appeared = Instant::now();
        }
        previously_unlocked = unlocked;
        if Instant::now() >= next_topology {
            reader.invalidate();
            let current = match reader.snapshot() {
                Ok(current) => current,
                Err(error) => break Err(error.to_string()),
            };
            if !record.covers_outputs(&current.monitors) {
                if let Err(error) = rebuild(&mut overlay, &current, &mut record, &directory) {
                    break Err(error);
                }
                appeared = Instant::now();
                last_publication = (false, 0);
            }
            next_topology = Instant::now() + Duration::from_millis(500);
        }
        if Instant::now() >= next_draw {
            let elapsed = appeared.elapsed().as_secs_f64() * 1000.0;
            if let Err(error) = overlay.draw(
                if unlocked { fade_in(elapsed) } else { 0.0 },
                breath(elapsed),
            ) {
                break Err(error);
            }
            next_draw = Instant::now()
                + if elapsed < 700.0 {
                    Duration::from_millis(50)
                } else {
                    Duration::from_millis(250)
                };
        }
        if let Err(error) = overlay.poll(Duration::from_millis(25)) {
            record.ready = false;
            record.presented_unix_ms = 0;
            if let Err(error) = record.write(&directory) {
                break Err(error.to_string());
            }
            reader.invalidate();
            let current = match reader.snapshot() {
                Ok(current) => current,
                Err(error) => break Err(error.to_string()),
            };
            if !record.covers_outputs(&current.monitors) {
                if let Err(error) = rebuild(&mut overlay, &current, &mut record, &directory) {
                    break Err(error);
                }
                appeared = Instant::now();
                next_draw = Instant::now();
                next_topology = Instant::now() + Duration::from_millis(500);
                last_publication = (false, 0);
                continue;
            }
            break Err(error);
        }
        record.ready = unlocked && overlay.presented();
        record.presented_unix_ms = if record.ready {
            unix_ms(SystemTime::now()).saturating_sub(
                overlay.presentation_age().map_or(u64::MAX, |age| {
                    u64::try_from(age.as_millis()).unwrap_or(u64::MAX)
                }),
            )
        } else {
            0
        };
        let publication = (record.ready, overlay.presentation_serial());
        if publication != last_publication {
            if let Err(error) = record.write(&directory) {
                break Err(error.to_string());
            }
            if record.ready && super::glow_state::read(&directory, &token).is_none() {
                break Err("Native frame presentation record could not be validated".into());
            }
            last_publication = publication;
        }
    };
    // Never leave valid health while fading an expired or revoked frame.
    record.ready = false;
    record.presented_unix_ms = 0;
    let _ = record.write(&directory);
    close_frame(&mut overlay);
    outcome
}
