//! A real KWin `ScreenShot2` frame for every monitor, through the helper's code.
//!
//! Skipped unless `PCBRIDGE_TEST_KWIN_CAPTURE=1` in a KDE Plasma session:
//! it reads pixels and writes no image, sends no input. The test binary must
//! be authorized the way `pcbridge setup` authorizes the helper (a
//! `pcbridge*.desktop` file with its path as `Exec`); the test says so when
//! it is not.

use std::time::Instant;

use pcbridge_native::platform::linux::display::DisplayReader;
use pcbridge_native::platform::linux::kwin_screenshot::{KWinScreenShot, authorizing_desktop_file};

fn enabled() -> bool {
    std::env::var("PCBRIDGE_TEST_KWIN_CAPTURE").as_deref() == Ok("1")
}

#[test]
fn every_monitor_comes_back_at_its_pixel_size() {
    if !enabled() {
        eprintln!("skipped: set PCBRIDGE_TEST_KWIN_CAPTURE=1 in a Plasma session");
        return;
    }
    let exe = std::env::current_exe().expect("test binary path");
    assert!(
        authorizing_desktop_file(&exe).is_some(),
        "no pcbridge*.desktop authorizes {}",
        exe.display()
    );
    let snapshot = DisplayReader::connect()
        .expect("display reader")
        .snapshot()
        .expect("KScreen table");
    let kwin = KWinScreenShot::connect().expect("session bus");
    for monitor in &snapshot.monitors {
        let started = Instant::now();
        let frame = kwin
            .capture_screen(&monitor.connector, false)
            .unwrap_or_else(|error| panic!("{}: {error}", monitor.connector));
        let elapsed = started.elapsed();
        let (width, height) = monitor.source_pixel_size();
        assert_eq!(
            (frame.frame.width, frame.frame.height),
            (width, height),
            "{}",
            monitor.connector
        );
        let png = frame.frame.to_png().expect("PNG");
        if let Ok(dir) = std::env::var("PCBRIDGE_TEST_KWIN_SAVE") {
            std::fs::write(format!("{dir}/{}.png", monitor.connector), &png).expect("save");
        }
        let translucent = frame
            .frame
            .pixels
            .chunks(4)
            .filter(|pixel| pixel[3] != 0xff)
            .count();
        let opaque = translucent == 0;
        eprintln!(
            "{}: {translucent} pixels with alpha below 255",
            monitor.connector
        );
        eprintln!(
            "{}: {}x{} in {:?}, PNG {} bytes, opaque {opaque}",
            monitor.connector,
            frame.frame.width,
            frame.frame.height,
            elapsed,
            png.len()
        );
        assert!(opaque, "a screenshot has no transparent pixels");
    }
}
