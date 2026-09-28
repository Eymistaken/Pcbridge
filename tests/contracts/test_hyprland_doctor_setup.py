"""Desktop diagnostics never open grants or install another compositor's integration."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from pcbridge.cli.doctor import Doctor
from pcbridge.cli.ops import setup_desktop
from pcbridge.cli import hyprland


class DesktopDispatchTests(unittest.TestCase):
    def test_setup_dispatch_and_skip(self):
        for kind in ('gnome', 'kde', 'hyprland', 'unknown'):
            with self.subTest(kind=kind), ExitStack() as stack:
                stack.enter_context(patch('pcbridge.desktop.session.desktop_kind', return_value=kind))
                ext = stack.enter_context(patch('pcbridge.cli.install.install_extension', return_value='installed'))
                kde = stack.enter_context(patch('pcbridge.cli.install.install_kde_entries', return_value=[]))
                notes = stack.enter_context(patch('pcbridge.cli.hyprland.setup_notes'))
                for name in ('say', 'ok', 'warn'):
                    stack.enter_context(patch('pcbridge.cli.install.' + name))
                setup_desktop(None, None)
                self.assertEqual(ext.call_count, int(kind == 'gnome'))
                self.assertEqual(kde.call_count, int(kind == 'kde'))
                self.assertEqual(notes.call_count, int(kind == 'hyprland'))
                setup_desktop(None, None, no_extension=True)
                self.assertEqual(ext.call_count + kde.call_count + notes.call_count, int(kind != 'unknown'))

    def test_doctor_dispatch(self):
        for kind in ('gnome', 'kde', 'hyprland', 'unknown'):
            with self.subTest(kind=kind), ExitStack() as stack:
                stack.enter_context(patch('pcbridge.desktop.session.desktop_kind', return_value=kind))
                run = stack.enter_context(patch('pcbridge.cli.install.run', return_value=SimpleNamespace(stdout='', stderr='', returncode=0)))
                stack.enter_context(patch('pcbridge.cli.doctor.shutil.which', return_value='/stub'))
                stack.enter_context(patch('pcbridge.cli.doctor.udev_hint', return_value='stub'))
                stack.enter_context(patch('pcbridge.cli.doctor.Path', return_value=SimpleNamespace(exists=lambda: True)))
                stack.enter_context(patch('pcbridge.cli.doctor.os.access', return_value=True))
                ext = stack.enter_context(patch.object(Doctor, 'gnome_extension'))
                kde = stack.enter_context(patch.object(Doctor, 'plasma'))
                hypr = stack.enter_context(patch('pcbridge.cli.hyprland.doctor'))
                doc = Doctor(); doc.desktop()
                self.assertEqual(ext.call_count, int(kind == 'gnome'))
                self.assertEqual(kde.call_count, int(kind == 'kde'))
                self.assertEqual(hypr.call_count, int(kind == 'hyprland'))
                commands = [call.args[0][0] for call in run.call_args_list]
                self.assertEqual('gnome-shell' in commands, kind == 'gnome')
                self.assertEqual('plasmashell' in commands, kind == 'kde')
                if kind == 'unknown':
                    self.assertTrue(any(c.name == 'desktop backend' and c.status == 'fail' for c in doc.checks))


class HyprlandDoctorTests(unittest.TestCase):
    def inspect(self, *, ipc=True, version='0.56.2', tested=False, binary=True, protocol=True,
                locked=False, idle=5000, active=False, proof=False, portals=True):
        with tempfile.TemporaryDirectory() as raw, ExitStack() as stack:
            state = Path(raw)
            if active:
                import json, time
                (state / 'desktop_unlock.json').write_text(json.dumps(dict(schema_version=1, grant_id='g', revoke_epoch=1,
                    until=time.time()+60, hard_until=time.time()+60)))
            stack.enter_context(patch('pcbridge.desktop.session.hyprland_instance', return_value={'instance':'i'} if ipc else None))
            stack.enter_context(patch('pcbridge.desktop.session.platform_summary', return_value={'hyprland':version}))
            stack.enter_context(patch('pcbridge.desktop.session.TESTED_HYPRLAND_VERSIONS', frozenset({version}) if tested else frozenset()))
            discover = stack.enter_context(patch('pcbridge.native.discover_native_binary', return_value=Path('/stub')))
            if not binary:
                discover.side_effect = FileNotFoundError()
            probe = stack.enter_context(patch('pcbridge.native.diagnostics.handshake_capabilities', return_value=('', {
                'backend':'linux.hyprland.image-copy', 'capabilities':[{'name':'capture.monitor', 'status':'supported' if protocol else 'unavailable', 'reason':'protocol missing'}]})))
            stack.enter_context(patch('pcbridge.desktop.safety.screen_locked', return_value=locked))
            stack.enter_context(patch('pcbridge.desktop.idlewatch.read_idle_ms', return_value=idle))
            frame = stack.enter_context(patch('pcbridge.desktop.glowstate.read_on_current_outputs', return_value={'ready':True} if proof else None))
            systemctl = stack.enter_context(patch('pcbridge.cli.install.systemctl', return_value=SimpleNamespace(stdout='active' if portals else 'inactive')))
            doc = Doctor(fix=True); doc.cfg = SimpleNamespace(native=None, state_dir=state)
            hyprland.doctor(doc, 'desktop')
            self.assertEqual(probe.call_count, int(binary))
            self.assertEqual(frame.call_count, int(active and binary))
            self.assertTrue(all(call.args[0] == 'is-active' for call in systemctl.call_args_list))
            self.assertEqual(sorted(p.name for p in state.iterdir()), ['desktop_unlock.json'] if active else [])
            return {c.name:c for c in doc.checks}

    def test_ready_checks_do_not_claim_no_grant_frame(self):
        checks = self.inspect()
        self.assertEqual(checks['Hyprland version'].status, 'warn')
        self.assertEqual(checks['Hyprland visible frame'].status, 'info')
        self.assertIn('not been verified', checks['Hyprland visible frame'].detail)
        self.assertEqual(checks['Hyprland capture protocol'].status, 'ok')

    def test_failures_are_distinct(self):
        for kwargs, name in (({'ipc':False}, 'Hyprland IPC'), ({'binary':False}, 'Hyprland native helper'),
                             ({'protocol':False}, 'Hyprland capture protocol'), ({'locked':None}, 'Hyprland lock state'),
                             ({'idle':None}, 'Hyprland idle time'), ({'active':True}, 'Hyprland visible frame')):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.inspect(**kwargs)[name].status, 'fail')
        self.assertIn('even with force=true', self.inspect(idle=None)['Hyprland idle time'].detail)

    def test_known_version_active_frame_and_optional_services(self):
        checks = self.inspect(tested=True, active=True, proof=True, portals=False)
        self.assertEqual(checks['Hyprland version'].status, 'ok')
        self.assertEqual(checks['Hyprland visible frame'].status, 'ok')
        self.assertEqual(checks['Hyprland capture protocol'].status, 'ok')
        self.assertTrue(all(c.status == 'warn' for c in checks.values() if c.group == 'desktop integration'))
        self.assertEqual(self.inspect(version=None)['Hyprland version'].status, 'warn')

    def test_setup_guidance_is_independent_and_read_only(self):
        with patch('pcbridge.cli.install.say') as say, patch('pcbridge.cli.install.ok') as ok:
            hyprland.setup_notes()
        text = ' '.join(call.args[0] for call in say.call_args_list + ok.call_args_list)
        for requirement in ('native idle watcher', 'lock state', 'visible grant frame', 'force=true', 'independent', 'No GNOME extension'):
            self.assertIn(requirement, text)


class LeaseFileTests(unittest.TestCase):
    def test_fifo_is_rejected_without_blocking(self):
        import os, subprocess, sys

        with tempfile.TemporaryDirectory() as raw:
            os.mkfifo(Path(raw) / 'desktop_unlock.json')
            script = (
                "from pathlib import Path; from pcbridge.cli.hyprland import _lease; "
                "import sys; assert _lease(Path(sys.argv[1])) is None"
            )
            try:
                result = subprocess.run([sys.executable, '-c', script, raw],
                                        capture_output=True, text=True, timeout=2)
            except subprocess.TimeoutExpired:
                self.fail('lease inspection blocked on a FIFO')
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / 'target.json'
            target.write_text('{"schema_version":1,"grant_id":"valid"}')
            (root / 'desktop_unlock.json').symlink_to(target)
            self.assertIsNone(hyprland._lease(root))

    def test_size_cap_mapping_and_regular_lease(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            lease = root / 'desktop_unlock.json'
            lease.write_text('{"grant_id":"valid"}' + ' ' * 65536)
            self.assertIsNone(hyprland._lease(root))
            lease.write_text('[]')
            self.assertIsNone(hyprland._lease(root))
            lease.write_text('{"schema_version":1,"grant_id":"valid","revoke_epoch":2}')
            snapshot = hyprland._lease(root)
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.grant_id, 'valid')
            self.assertEqual(snapshot.revoke_epoch, 2)


if __name__ == '__main__':
    unittest.main()
