//! Opt-in compositor observation; run only inside the disposable Hyprland VM.

#[test]
fn selected_hyprland_session_has_a_real_display_snapshot() {
    if std::env::var_os("PCBRIDGE_TEST_LIVE_HYPRLAND").is_none() {
        return;
    }
    use pcbridge_core::display::canvas_size;
    use pcbridge_native::platform::linux::display::DisplayReader;

    let reader = DisplayReader::connect().expect("selected Hyprland display socket");
    let snapshot = reader.snapshot().expect("live Hyprland monitor reply");
    assert!(!snapshot.monitors.is_empty());
    assert!(
        snapshot
            .monitors
            .iter()
            .all(|monitor| !monitor.connector.is_empty())
    );
    let canvas = canvas_size(&snapshot.monitors);
    assert!(canvas.0 > 0 && canvas.1 > 0);
    println!(
        "Hyprland native display: {} monitor(s), canvas {}x{}, topology {}",
        snapshot.monitors.len(),
        canvas.0,
        canvas.1,
        snapshot.topology_id,
    );
}
