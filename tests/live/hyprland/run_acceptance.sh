#!/usr/bin/env bash
# Run a scoped Hyprland acceptance cohort only in the disposable VM.
set -euo pipefail

if [[ "${PCBRIDGE_TEST_HYPRLAND_MATRIX:-}" != 1 || "$(< /proc/sys/kernel/hostname)" != pcbridge-hyprland ]]; then
    echo 'Set PCBRIDGE_TEST_HYPRLAND_MATRIX=1 in the disposable pcbridge-hyprland VM' >&2
    exit 2
fi
if [[ -v PCBRIDGE_NATIVE_BIN || -n "${PYTHONOPTIMIZE:-}" ]]; then
    echo 'Native helper overrides and optimized Python are forbidden' >&2
    exit 2
fi

cd "$(dirname "$0")/../../.."
out_dir="$(mktemp -d /tmp/pcbridge-hyprland-acceptance-XXXXXX)"
python=.venv/bin/python
echo "Acceptance logs: $out_dir"

verify_clean() {
    "$python" - <<'PY'
from pcbridge.desktop import hyprland, idlewatch
from tests.live.hyprland.check_glow_owner import layers

rows = hyprland.monitors()
expected = {"Virtual-1": (0, 0, "none", False),
            "Virtual-2": (1280, 0, "none", False)}
observed = {row["name"]: (row["x"], row["y"], row["mirrorOf"], row["disabled"])
            for row in rows}
if observed != expected or hyprland.screen_locked() is not False:
    raise RuntimeError(f"VM layout or lock state changed: {observed}")
if layers() or idlewatch.read_idle_ms() is not None:
    raise RuntimeError("A frame or idle watcher remains after a case")
PY
    if pgrep -x pcbridge-native >/dev/null || pgrep -f '[p]attern_window.py|[i]nput_window.py|[a]11y_window.py' >/dev/null; then
        echo 'A native helper or test window remains after a case' >&2
        return 1
    fi
}

run_case() {
    local name="$1" flag="$2" script="$3"
    shift 3
    echo "RUN $name"
    if ! env "$flag=1" "$python" "tests/live/hyprland/$script" "$@" >"$out_dir/$name.log" 2>&1; then
        tail -n 35 "$out_dir/$name.log" >&2
        echo "FAIL $name; logs: $out_dir" >&2
        exit 1
    fi
    if ! verify_clean >"$out_dir/$name-cleanup.log" 2>&1; then
        cat "$out_dir/$name-cleanup.log" >&2
        echo "FAIL cleanup after $name; logs: $out_dir" >&2
        exit 1
    fi
    echo "PASS $name"
}

verify_clean
run_case pointer_lock PCBRIDGE_TEST_HYPRLAND_POINTER_LOCK check_pointer_lock.py --out-dir "$out_dir/pointer-lock"
run_case touch PCBRIDGE_TEST_HYPRLAND_TOUCH check_touch_transparency.py --out-dir "$out_dir/touch"
run_case accessibility_policy PCBRIDGE_TEST_HYPRLAND_A11Y_POLICY check_accessibility_policy.py --out-dir "$out_dir/accessibility-policy"
run_case safety_lock PCBRIDGE_TEST_HYPRLAND_SAFETY check_safety_transitions.py --case lock
run_case safety_activity PCBRIDGE_TEST_HYPRLAND_SAFETY check_safety_transitions.py --case activity
for case in cancellation expiry revoke replacement failure; do
    run_case "sequence_$case" PCBRIDGE_TEST_HYPRLAND_SEQUENCE check_sequence_lifecycle.py --case "$case"
done
for layout in rotated negative swapped vertical hotplug mirror; do
    run_case "topology_$layout" PCBRIDGE_TEST_HYPRLAND_TOPOLOGY check_topology_capture.py --layout "$layout" --out-dir "$out_dir/topology-$layout"
done
run_case capture_performance PCBRIDGE_TEST_HYPRLAND_PERFORMANCE check_capture_performance.py --out-dir "$out_dir/capture-performance"
echo "PASS scoped Hyprland acceptance cohort; logs: $out_dir"
