#!/usr/bin/env python3
"""Display snapshot parity: Python must resolve the same table as Rust.

Both sides read `tests/fixtures/native/display_state_cases.json` and feed it to
their own production resolver. Only the transport adapters differ -- busctl JSON
here, zbus in `rust/crates/pcbridge-native` -- so a divergence in the rules that
actually matter (current mode, rotation, fractional scale, ordering, topology)
shows up as a failure on one side only.

No D-Bus call, no compositor, no display window. Safe with every live flag unset.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.desktop.backends.python import (  # noqa: E402
    PythonCaptureProvider,
    _display_mapping_error,
)
from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "native" / "display_state_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))


class DisplayResolveTests(unittest.TestCase):
    """The rules Rust has to reproduce, stated once, over shared fixtures."""

    def test_every_accepted_case_resolves_to_the_expected_table(self) -> None:
        for case in CASES["cases"]:
            with self.subTest(case=case["name"]):
                got = monitorslib.resolve_state(case["state"])
                want = case["expect"]["monitors"]
                self.assertEqual(len(got), len(want), case["note"])
                for monitor, expected in zip(got, want):
                    self.assertEqual(monitor.index, expected["index"])
                    self.assertEqual(monitor.connector, expected["connector"])
                    self.assertEqual(monitor.x, expected["x"])
                    self.assertEqual(monitor.y, expected["y"])
                    self.assertEqual(monitor.width, expected["width"])
                    self.assertEqual(monitor.height, expected["height"])
                    self.assertAlmostEqual(monitor.scale, expected["scale"])
                    self.assertEqual(monitor.transform, expected["transform"])
                    self.assertEqual(monitor.primary, expected["primary"])
                    self.assertEqual(monitor.serial, expected["serial"])

    def test_canvas_size_matches(self) -> None:
        for case in CASES["cases"]:
            with self.subTest(case=case["name"]):
                got = monitorslib.resolve_state(case["state"])
                self.assertEqual(
                    list(monitorslib.canvas_size(got)), case["expect"]["canvas"]
                )

    def test_topology_id_is_the_shared_canonical_string(self) -> None:
        for case in CASES["cases"]:
            with self.subTest(case=case["name"]):
                got = monitorslib.resolve_state(case["state"])
                self.assertEqual(
                    monitorslib.topology_id(got), case["expect"]["topology"]
                )

    def test_topology_id_ignores_connector_names(self) -> None:
        # OLCULDU 2026-09-12: bu makinede connector adlari DP-1/DP-2 iken
        # DP-3/DP-4 oldu, geometri hic degismeden. Ada bagli bir kimlik
        # her yeniden adlandirmada "duzen degisti" derdi.
        case = CASES["cases"][0]
        renamed = json.loads(json.dumps(case["state"]))
        mapping = {"DP-3": "DP-77", "DP-4": "DP-88"}
        for physical in renamed["physical"]:
            physical["connector"] = mapping[physical["connector"]]
        for logical in renamed["logical"]:
            logical["connectors"] = [mapping[c] for c in logical["connectors"]]
        self.assertEqual(
            monitorslib.topology_id(monitorslib.resolve_state(renamed)),
            case["expect"]["topology"],
        )

    def test_topology_id_changes_when_geometry_changes(self) -> None:
        case = CASES["cases"][0]
        moved = json.loads(json.dumps(case["state"]))
        moved["logical"][1]["x"] = 1921
        self.assertNotEqual(
            monitorslib.topology_id(monitorslib.resolve_state(moved)),
            case["expect"]["topology"],
        )

    def test_topology_id_changes_when_rotation_changes(self) -> None:
        # 180 derece donuste genislik/yukseklik AYNI kalir; kimlik yine de
        # degismeli, cunku goruntu bas asagi ve koordinat esleme farkli.
        case = CASES["cases"][0]
        turned = json.loads(json.dumps(case["state"]))
        turned["logical"][0]["transform"] = 2
        resolved = monitorslib.resolve_state(turned)
        self.assertEqual((resolved[0].width, resolved[0].height), (1920, 1080))
        self.assertNotEqual(
            monitorslib.topology_id(resolved), case["expect"]["topology"]
        )

    def test_rejected_cases_raise_instead_of_guessing(self) -> None:
        for case in CASES["rejected"]:
            with self.subTest(case=case["name"]):
                with self.assertRaises(monitorslib.MonitorError, msg=case["note"]):
                    monitorslib.resolve_state(case["state"])

    def test_provider_maps_the_refusal_to_the_stable_code(self) -> None:
        # Fixture'daki `error` alani saglayici sinirindaki koda isaret ediyor.
        for case in CASES["rejected"]:
            with self.subTest(case=case["name"]):
                with self.assertRaises(monitorslib.MonitorError) as raw:
                    monitorslib.resolve_state(case["state"])
                translated = _display_mapping_error(raw.exception)
                self.assertIsInstance(translated, DesktopError)
                self.assertEqual(translated.code, ErrorCode[case["error"]])


class ProviderSeamTests(unittest.TestCase):
    """`topology_id` is on the provider contract, so 3.2/3.4 have one seam."""

    def test_provider_returns_the_same_string_as_the_module(self) -> None:
        provider = PythonCaptureProvider.__new__(PythonCaptureProvider)
        resolved = monitorslib.resolve_state(CASES["cases"][0]["state"])
        with mock.patch.object(monitorslib, "list_monitors", return_value=resolved):
            self.assertEqual(
                provider.topology_id(), CASES["cases"][0]["expect"]["topology"]
            )

    def test_provider_reports_an_unreadable_layout_as_the_stable_code(self) -> None:
        provider = PythonCaptureProvider.__new__(PythonCaptureProvider)
        with mock.patch.object(
            monitorslib,
            "list_monitors",
            side_effect=monitorslib.MonitorError("okunamadi"),
        ):
            with self.assertRaises(DesktopError) as caught:
                provider.topology_id()
        self.assertEqual(caught.exception.code, ErrorCode.DISPLAY_MAPPING_UNKNOWN)



KSCREEN = json.loads(
    (ROOT / "tests" / "fixtures" / "native" / "kscreen_cases.json").read_text(encoding="utf-8")
)


class KScreenAdapterTests(unittest.TestCase):
    """KDE Plasma: `kscreen-doctor -j` becomes the same neutral state."""

    def test_every_case_maps_to_the_expected_state(self) -> None:
        for case in KSCREEN["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(monitorslib._kscreen_state(case["kscreen"]), case["state"])

    def test_the_vm_layout_resolves_like_kwin_places_the_pointer(self) -> None:
        # Measured: two 1280x800 outputs side by side, a 2560x800 canvas.
        mons = monitorslib.resolve_state(KSCREEN["cases"][0]["state"])
        self.assertEqual(monitorslib.canvas_size(mons), (2560, 800))
        self.assertEqual([m.connector for m in mons], ["Virtual-1", "Virtual-2"])
        self.assertTrue(mons[0].primary)
        # Scale 1.5 turned right: 800x1280 pixels become 533x853 canvas units.
        rotated = monitorslib.resolve_state(KSCREEN["cases"][1]["state"])
        self.assertEqual((rotated[1].width, rotated[1].height), (533, 853))

    def test_an_unknown_rotation_is_refused_not_guessed(self) -> None:
        bad = json.loads(json.dumps(KSCREEN["cases"][0]["kscreen"]))
        bad["outputs"][0]["rotation"] = 3
        with self.assertRaises(monitorslib.MonitorError):
            monitorslib._kscreen_state(bad)

    def test_plasma_reads_kscreen_and_gnome_reads_mutter(self) -> None:
        from pcbridge.desktop import compositor

        table = monitorslib.resolve_state(KSCREEN["cases"][0]["state"])
        for kde, used in ((True, "_from_kscreen"), (False, "_from_mutter")):
            with self.subTest(kde=kde), \
                    mock.patch.object(compositor, "is_kde", return_value=kde), \
                    mock.patch.object(monitorslib, "_from_kscreen", return_value=table) as ks, \
                    mock.patch.object(monitorslib, "_from_mutter", return_value=table) as mu:
                monitorslib.list_monitors(use_cache=False)
            self.assertEqual((ks.called, mu.called), (used == "_from_kscreen", used == "_from_mutter"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
