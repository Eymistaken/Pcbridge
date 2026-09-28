//! Native renderer observation only: grants and protected control stay closed.

use std::time::{Duration, Instant};

#[test]
fn transparent_edge_strips_receive_real_presentation_feedback() {
    if std::env::var("PCBRIDGE_TEST_HYPRLAND_GLOW").as_deref() != Ok("1") {
        return;
    }
    assert_eq!(
        std::fs::read_to_string("/etc/hostname").unwrap().trim(),
        "pcbridge-hyprland"
    );
    use pcbridge_native::platform::linux::desktop_state::{
        DesktopStateProvider, ScreenLockState, SessionDesktopState,
    };
    use pcbridge_native::platform::linux::display::DisplayReader;
    use pcbridge_native::platform::linux::glow::{Overlay, breath, fade_in, fade_out};

    let state = SessionDesktopState::connect().unwrap();
    assert_eq!(state.screen_lock().state, ScreenLockState::KnownUnlocked);
    let reader = DisplayReader::connect().unwrap();
    let snapshot = reader.snapshot().unwrap();
    let mut overlay =
        Overlay::connect(&snapshot.monitors).expect("layer-shell/presentation globals");
    assert_eq!(overlay.strip_count(), snapshot.monitors.len() * 4);
    assert!(
        !overlay.presented(),
        "No grant health before actual presentation"
    );
    let start = Instant::now();
    let mut announced = false;
    let mut next_draw = Instant::now();
    while start.elapsed() < Duration::from_secs(13) {
        if Instant::now() >= next_draw {
            let elapsed = start.elapsed().as_secs_f64() * 1000.0;
            overlay.draw(fade_in(elapsed), breath(elapsed)).unwrap();
            next_draw = Instant::now()
                + if elapsed < 700.0 {
                    Duration::from_millis(50)
                } else {
                    Duration::from_millis(250)
                };
        }
        overlay.poll(Duration::from_millis(25)).unwrap();
        if !announced && overlay.presented() {
            println!(
                "GLOW_PRESENTED strips={} elapsed_ms={}",
                overlay.strip_count(),
                start.elapsed().as_millis()
            );
            announced = true;
        }
        if start.elapsed() > Duration::from_secs(4) {
            assert!(announced, "No displayed edge strips");
        }
    }
    assert!(overlay.presented());
    let exit = Instant::now();
    while exit.elapsed() < Duration::from_millis(550) {
        overlay
            .draw(fade_out(exit.elapsed().as_secs_f64() * 1000.0), 1.0)
            .unwrap();
        overlay.poll(Duration::from_millis(25)).unwrap();
    }
    drop(overlay);
    println!("GLOW_CLOSED after one full breathing cycle and fade-out");
}
