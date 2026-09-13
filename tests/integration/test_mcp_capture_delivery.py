#!/usr/bin/env python3
"""A screenshot counts as delivered only when a client can decode it.

Task 3.5 separates two facts that used to be reported as one: the capture
succeeded, and the picture reached the client. A PNG sitting in `shots/` is not
the second. Every test here reads the image back out of an MCP result the way a
client does -- base64 inside an image content block -- decodes it and compares
the pixels with the fixture that produced them.

Offline by default. The fixture server (`delivery_fixture.py`) fakes only the
frames; cropping, scaling, staging, publishing, the tool result and FastMCP's
serialization are production code. `LiveNativeDelivery` needs
`PCBRIDGE_TEST_CAPTURE=1` and a built native helper: it starts the real server
over real stdio with the Rust backend, Python GI made unimportable and
`gnome-screenshot` replaced by a program that refuses to run. It briefly opens
a screen share; it sends no input.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from PIL import Image, ImageChops


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import delivery_fixture as fixture  # noqa: E402
from pcbridge import server as serverlib  # noqa: E402
from pcbridge import shots as shotslib  # noqa: E402
from pcbridge import tools as toolslib  # noqa: E402
from pcbridge.desktop import capture as capturelib  # noqa: E402


SHOT_LINE = re.compile(r"shot: `((?:m\d{1,2}|win)-[0-9a-f]{6})`")
LINK = re.compile(r"https://example\.invalid/shot/([A-Za-z0-9_-]+)\.png")
ORDER_LINE = re.compile(r"Goruntuler asagida bu sirayla: (.+)\.")


def decode(block) -> Image.Image:
    """What a client does with an image block."""
    if block.type != "image" or block.mimeType != "image/png":
        raise AssertionError(f"not a PNG image block: {block.type} {block.mimeType}")
    image = Image.open(io.BytesIO(base64.b64decode(block.data)))
    image.load()
    return image


def text_of(result) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")


def images_of(result) -> list:
    return [block for block in result.content if block.type == "image"]


class DeliveryChecks:
    def assert_fixture_images(self, result) -> list[str]:
        """Every shot the text names arrives as a decodable fixture image, in order."""
        self.assertFalse(result.is_error, text_of(result))
        self.assertEqual(result.content[0].type, "text", "the text block comes first")
        text = result.content[0].text
        ids = SHOT_LINE.findall(text)
        images = images_of(result)
        self.assertEqual(len(ids), len(fixture.MONITORS), text)
        self.assertEqual(len(images), len(ids), "one image per shot")

        order = ORDER_LINE.search(text)
        self.assertIsNotNone(order, "the pairing between ids and images must be stated")
        self.assertEqual(re.findall(r"`([^`]+)`", order.group(1)), ids)

        for shot_id, block in zip(ids, images):
            monitor = fixture.MONITORS[int(shot_id[1 : shot_id.index("-")]) - 1]
            image = decode(block)
            self.assertEqual(
                image.size,
                (fixture.LONG_EDGE, fixture.LONG_EDGE * monitor.height // monitor.width),
            )
            self.assertEqual(
                fixture.observed_quadrants(image),
                list(fixture.QUADRANTS[monitor.connector]),
                f"{shot_id} does not show {monitor.connector}'s fixture frame",
            )
        return ids


class InMemoryDelivery(DeliveryChecks, unittest.IsolatedAsyncioTestCase):
    async def test_a_client_decodes_every_image_and_sees_the_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            server = fixture.build(Path(raw))
            async with Client(server.mcp) as client:
                result = await client.call_tool("screen_capture", {"monitor": "all"})

            ids = self.assert_fixture_images(result)
            # The record a later `shot=` click maps through describes exactly
            # the picture the client decoded.
            for shot_id, block in zip(ids, images_of(result)):
                record = json.loads(
                    (server.store.dir / f"{shot_id}.json").read_text(encoding="utf-8")
                )
                self.assertEqual(tuple(record["scaled"]), decode(block).size)
                self.assertEqual(
                    Path(record["png"]).read_bytes(), base64.b64decode(block.data)
                )

    async def test_stdio_never_hands_out_a_link_that_nothing_serves(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            server = fixture.build(Path(raw), transport="stdio")
            async with Client(server.mcp) as client:
                result = await client.call_tool("screen_capture", {"monitor": "all"})

            text = text_of(result)
            self.assertNotIn("/shot/", text)
            self.assertEqual(server.store.stats()[0], 0, "no link token on stdio")
            for path in server.store.dir.glob("*.png"):
                self.assertIn(str(path), text, "stdio names the file instead")

    async def test_a_new_shot_id_avoids_the_ids_pcb_shot_already_uses(self) -> None:
        """`shot=` searches both directories, so an id may be used in neither."""
        with tempfile.TemporaryDirectory() as raw:
            server = fixture.build(Path(raw))
            agent_dir = server.config.agent_shot_path
            agent_dir.mkdir(parents=True)
            taken = agent_dir / "m1-aaaaaa.json"
            taken.write_text('{"id": "m1-aaaaaa"}', encoding="utf-8")
            suffixes = SimpleNamespace(
                token_hex=mock.Mock(side_effect=["aaaaaa", "bbbbbb"])
            )

            with mock.patch.object(capturelib, "secrets", suffixes):
                async with Client(server.mcp) as client:
                    result = await client.call_tool("screen_capture", {"monitor": "all"})

            self.assertEqual(
                SHOT_LINE.findall(text_of(result)), ["m1-bbbbbb", "m2-bbbbbb"]
            )
            self.assertEqual(taken.read_text(encoding="utf-8"), '{"id": "m1-aaaaaa"}')

    async def test_an_image_lost_before_delivery_is_an_error_not_a_success(self) -> None:
        def lose_the_second(shots) -> None:
            shots[1].path.write_bytes(b"truncated by a disk that filled up")

        with tempfile.TemporaryDirectory() as raw:
            server = fixture.build(Path(raw), after_capture=lose_the_second)
            async with Client(server.mcp) as client:
                result = await client.call_tool(
                    "screen_capture", {"monitor": "all"}, raise_on_error=False
                )

            self.assertTrue(result.is_error, "an undelivered image is not a success")
            error = result.structured_content["error"]
            self.assertEqual(error["code"], "IMAGE_DELIVERY_FAILED")
            self.assertEqual(error["category"], "capture")

            text = text_of(result)
            ids = SHOT_LINE.findall(text)
            self.assertEqual(len(ids), 2)
            self.assertIn(ids[1], error["message"])
            self.assertIn("ULASTIRILAMADI", text)
            self.assertEqual(result.structured_content["shots"], ids)

            images = images_of(result)
            self.assertEqual(len(images), 1, "the image that did arrive still does")
            first = fixture.MONITORS[0]
            self.assertEqual(
                fixture.observed_quadrants(decode(images[0])),
                list(fixture.QUADRANTS[first.connector]),
            )
            self.assertIn(("screen_capture_undelivered", {"shots": 1}), server.gate.events)

    async def test_a_long_batch_report_keeps_every_shot_line_with_its_image(self) -> None:
        """Only the batch report is trimmed, never the text naming each image.

        `tail_chars` keeps the end, so the shot lines were at risk only when
        the capture text after them passed the limit on its own. The long note
        below makes that happen; the long report checks the report is still
        trimmed.
        """
        report = "\n".join(
            f"  {number}. wait — a line of a very long batch report" for number in range(400)
        )
        note = "⚠️ " + "a very long warning after the shot lines " * 120
        self.assertGreater(len(report), toolslib.MAX_INLINE)
        self.assertGreater(len(note), toolslib.MAX_INLINE)

        with tempfile.TemporaryDirectory() as raw:
            server = fixture.build(Path(raw))
            server.runtime.capture_provider.oversize_note = lambda _shot: note
            with (
                mock.patch.object(toolslib.batchlib, "describe", return_value=report),
                mock.patch.object(
                    toolslib.appslib, "extension_focus_available", return_value=False
                ),
            ):
                async with Client(server.mcp) as client:
                    result = await client.call_tool(
                        "computer_batch",
                        {
                            "actions": json.dumps([{"a": "wait", "ms": 0}]),
                            "final": "screen_capture",
                        },
                    )

            self.assert_fixture_images(result)
            self.assertIn(
                "…(kirpildi)…", result.content[0].text, "the report itself is still trimmed"
            )


class HttpLinks(unittest.TestCase):
    def test_a_link_serves_the_published_png_until_its_ttl_then_404(self) -> None:
        from starlette.testclient import TestClient

        with tempfile.TemporaryDirectory() as raw:
            config = fixture.make_config(Path(raw))
            stores = []
            real_register = toolslib.register

            def register(mcp, cfg, jm, shot_store, transport="http"):
                stores.append(shot_store)
                return real_register(
                    mcp,
                    cfg,
                    jm,
                    shot_store,
                    transport=transport,
                    runtime=fixture.fixture_runtime(cfg),
                )

            with mock.patch.object(serverlib.toolsmod, "register", side_effect=register):
                mcp, _provider = serverlib.build_app(config, transport="http")
            store = stores[0]

            async def capture():
                async with Client(mcp) as client:
                    return await client.call_tool("screen_capture", {"monitor": "1"})

            result = asyncio.run(capture())
            text = text_of(result)
            tokens = LINK.findall(text)
            self.assertEqual(len(tokens), 1, text)
            (shot_id,) = SHOT_LINE.findall(text)
            suffix = shot_id.split("-")[1]
            (published,) = store.dir.glob(f"*-{suffix}-m1-*.png")
            (block,) = images_of(result)

            with TestClient(mcp.http_app(path=config.mcp_path)) as http:
                response = http.get(f"/shot/{tokens[0]}.png")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "image/png")
                self.assertIn("no-store", response.headers["cache-control"])
                self.assertEqual(response.content, published.read_bytes())
                self.assertEqual(response.content, base64.b64decode(block.data))

                expired = time.time() + config.desktop.shot_ttl_seconds + 1
                with mock.patch.object(shotslib.time, "time", return_value=expired):
                    gone = http.get(f"/shot/{tokens[0]}.png")
                self.assertEqual(gone.status_code, 404)
                self.assertTrue(published.exists(), "expiry ends the link, not the file")


class StdioWire(DeliveryChecks, unittest.TestCase):
    def test_real_pipes_carry_decodable_images(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            transport = StdioTransport(
                command=sys.executable,
                args=[str(HERE / "delivery_server.py"), raw],
                env=dict(os.environ),
                cwd=str(ROOT),
                keep_alive=False,
            )

            async def call():
                async with Client(transport) as client:
                    return await client.call_tool("screen_capture", {"monitor": "all"})

            result = asyncio.run(call())
            self.assert_fixture_images(result)
            self.assertNotIn("/shot/", text_of(result))


# ------------------------------------------------------------------- live
LIVE = os.environ.get("PCBRIDGE_TEST_CAPTURE") == "1"
PACKAGED_BINARY = (
    ROOT / "pcbridge" / "_native" / "x86_64-unknown-linux-gnu" / "pcbridge-native"
)
DEBUG_BINARY = ROOT / "rust" / "target" / "debug" / "pcbridge-native"
BLOCKED = "blocked by the capture delivery test"
SIZE_LINE = re.compile(
    r"\*\*(\d+) · (\S+?)(?: \(birincil\))?\*\* · (\d+)x(\d+) @ \((-?\d+), (-?\d+)\) → "
    r"(\d+)x(\d+)"
)


def processes(program: str) -> set[int]:
    """PIDs of this user's processes that run `program`.

    Matched against whole argv elements, never a substring of the joined
    command line: a shell whose command merely mentions the helper's name is
    not the helper. (A substring match once made this test mistake the shell
    that launched it for a running screencast helper and skip itself.)
    """
    found: set[int] = set()
    uid = os.getuid()
    wanted = Path(program).name
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            argv = [
                part.decode(errors="replace")
                for part in (entry / "cmdline").read_bytes().split(b"\0")
                if part
            ]
        except OSError:
            continue
        if not argv:
            continue
        if "/" in program:
            runs = argv[0] == program
        else:
            runs = Path(argv[0]).name.startswith("python") and any(
                Path(part).name == wanted for part in argv[1:3]
            )
        if runs:
            found.add(int(entry.name))
    return found - {os.getpid()}


def screencast_sessions() -> int:
    """How many Mutter ScreenCast sessions exist right now."""
    proc = subprocess.run(
        [
            "gdbus", "introspect", "--session",
            "--dest", "org.gnome.Mutter.ScreenCast",
            "--object-path", "/org/gnome/Mutter/ScreenCast/Session",
        ],
        capture_output=True, text=True, timeout=10, check=True,
    )
    return len(re.findall(r"^\s+node\s+\S+", proc.stdout, re.M))


def real_native_binary() -> tuple[Path | None, str]:
    """A helper that really reads the screen, or why there is none.

    `rust/target/debug/pcbridge-native` cannot be trusted by path alone:
    `test_native_revoke.py` rebuilds it with the test-harness feature, whose
    backend answers with fixtures and reads no screen. `--build-info` says.
    """
    from pcbridge.native.diagnostics import read_build_info

    for candidate in (PACKAGED_BINARY, DEBUG_BINARY):
        if not os.access(candidate, os.X_OK):
            continue
        info = read_build_info(candidate)
        if isinstance(info, dict) and info.get("test_harness") is False:
            return candidate, ""
    return None, (
        "no helper that reads the screen: run scripts/build-native.sh "
        "(the debug binary may be a test-harness build)"
    )


def live_config(root: Path, binary: Path) -> Path:
    """The example config, pointed at a scratch state and the Rust backend."""
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    for pattern, value in (
        (r"^state_dir = .*$", f'state_dir = "{root / "state"}"'),
        (r'^capture = "python"$', 'capture = "rust"'),
        (r'^binary_path = ""$', f'binary_path = "{binary}"'),
        (r'^agent_shot_dir = ""$', f'agent_shot_dir = "{root / "agent-shots"}"'),
        (r"^include_pointer = true$", "include_pointer = false"),
    ):
        text, count = re.subn(pattern, lambda _match, v=value: v, text, flags=re.M)
        if count != 1:
            raise AssertionError(f"config.example.toml changed shape: {pattern}")
    header = text.index("\n[desktop]\n")
    enabled = re.compile(r"^enabled = false$", re.M).search(text, header)
    if enabled is None or "\n[" in text[header + 1 : enabled.start()]:
        raise AssertionError("config.example.toml: [desktop] enabled not found")
    text = text[: enabled.start()] + "enabled = true" + text[enabled.end() :]
    path = root / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def hostile_environment(root: Path) -> dict[str, str]:
    """Python GI unimportable and gnome-screenshot unusable, for one server."""
    blocker = root / "no-gi" / "gi"
    blocker.mkdir(parents=True)
    (blocker / "__init__.py").write_text(
        f'raise ImportError("gi is {BLOCKED}")\n', encoding="utf-8"
    )
    shim = root / "no-screenshot"
    shim.mkdir()
    program = shim / "gnome-screenshot"
    program.write_text(
        f"#!/bin/sh\necho 'gnome-screenshot is {BLOCKED}' >&2\nexit 97\n",
        encoding="utf-8",
    )
    program.chmod(0o755)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "no-gi")
    env["PATH"] = f"{shim}{os.pathsep}{env.get('PATH', '')}"
    env.pop("PCBRIDGE_NATIVE_BIN", None)
    return env


@unittest.skipUnless(LIVE, "set PCBRIDGE_TEST_CAPTURE=1 to capture through the real server")
class LiveNativeDelivery(unittest.TestCase):
    @staticmethod
    def agreement(left: Image.Image, right: Image.Image) -> float:
        """Percentage of pixels that are exactly equal."""
        red, green, blue = ImageChops.difference(left, right).split()
        worst = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        return 100.0 * worst.histogram()[0] / (left.width * left.height)

    def test_the_real_server_delivers_native_frames_without_python_gi(self) -> None:
        binary, why = real_native_binary()
        if binary is None:
            self.skipTest(why)
        helpers_before = processes("screencast_helper.py")
        if helpers_before:
            # `desktop_lock` stops every helper of this user; one running now
            # belongs to a real grant, and this test must not close it.
            self.skipTest("a Python screencast helper is running (a real grant is open)")
        natives_before = processes(str(binary))
        sessions_before = screencast_sessions()

        with tempfile.TemporaryDirectory(prefix="pcb-delivery-live-") as raw:
            root = Path(raw)
            config = live_config(root, binary)
            env = hostile_environment(root)

            probe = subprocess.run(
                ["python3", "-c", "import gi"],
                env=env, capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertNotEqual(probe.returncode, 0, "python3 could still import gi")
            self.assertIn(BLOCKED, probe.stderr)

            transport = StdioTransport(
                command=sys.executable,
                args=["-m", "pcbridge.server", "--stdio", "-c", str(config)],
                env=env,
                cwd=str(ROOT),
                keep_alive=False,
            )

            async def scenario():
                async with Client(transport) as client:
                    caps = await client.call_tool("system_capabilities")
                    unlock = await client.call_tool(
                        "desktop_unlock",
                        {"minutes": 2, "reason": "capture delivery live test"},
                        raise_on_error=False,
                    )
                    if "Ekran yayını açık" not in text_of(unlock):
                        return caps, unlock, [], None, None
                    # Two captures: the first also starts the helper and the
                    # Mutter session, the second shows what a warm call costs.
                    shots = []
                    for _ in range(2):
                        started = time.time()
                        result = await client.call_tool(
                            "screen_capture", {"monitor": "all"}, raise_on_error=False
                        )
                        shots.append((result, time.time() - started))
                    lock = await client.call_tool("desktop_lock", raise_on_error=False)
                    after = await client.call_tool(
                        "screen_capture", {"monitor": "all"}, raise_on_error=False
                    )
                    return caps, unlock, shots, lock, after

            caps, unlock, shots, lock, after = asyncio.run(scenario())
            self.assertEqual(len(shots), 2, text_of(unlock))
            (shot, elapsed), (warm, warm_elapsed) = shots
            self.assertFalse(warm.is_error, text_of(warm))
            self.assertEqual(len(images_of(warm)), len(SHOT_LINE.findall(text_of(warm))))

            capture_monitor = caps.structured_content["capabilities"]["capture.monitor"]
            self.assertEqual(capture_monitor["backend"], "linux.mutter.pipewire")
            self.assertEqual(capture_monitor["state"], "supported")
            self.assertIn("Ekran yayını açık", text_of(unlock), text_of(unlock))

            self.assertFalse(shot.is_error, text_of(shot))
            text = shot.content[0].text
            ids = SHOT_LINE.findall(text)
            sizes = SIZE_LINE.findall(text)
            images = images_of(shot)
            self.assertGreaterEqual(len(ids), 1, text)
            self.assertEqual(len(images), len(ids))
            self.assertEqual(len(sizes), len(ids), text)

            shots_dir = root / "state" / "shots"
            report = []
            for shot_id, size, block in zip(ids, sizes, images):
                image = decode(block).convert("RGB")
                self.assertEqual(image.size, (int(size[6]), int(size[7])))
                colors = image.getcolors(maxcolors=1 << 20)
                distinct = len(colors) if colors is not None else 1 << 20
                self.assertGreater(distinct, 100, f"{shot_id} looks blank")
                record = json.loads((shots_dir / f"{shot_id}.json").read_text("utf-8"))
                self.assertEqual(tuple(record["scaled"]), image.size)
                self.assertEqual(
                    record["offset"], [int(size[4]), int(size[5])], "offset survives"
                )
                age = time.time() - float(record["taken_at"])
                self.assertTrue(0.0 <= age < 60.0, f"{shot_id} taken_at is {age:.1f} s old")
                report.append(
                    f"{shot_id} {size[1]} {size[2]}x{size[3]}->{image.size[0]}x"
                    f"{image.size[1]} png={len(base64.b64decode(block.data))}B "
                    f"colors={distinct}"
                )
            self.assertEqual(
                [entry.name for entry in shots_dir.glob(".staging-*")], [],
                "a finished capture leaves no staging directory",
            )
            # Each image must show its own monitor. On 2026-09-13 both came
            # back as the same screen -- sizes, colors and records all fine --
            # because one PipeWire stream was reused across nodes. Monitors
            # with a panel or window on only one of them never agree this well.
            decoded = [decode(block).convert("RGB") for block in images]
            for left in range(len(decoded)):
                for right in range(left + 1, len(decoded)):
                    same = self.agreement(decoded[left], decoded[right])
                    report.append(f"{ids[left]}~{ids[right]}={same:.2f}%")
                    self.assertLess(
                        same, 99.0,
                        f"{ids[left]} and {ids[right]} show the same picture",
                    )

            self.assertFalse(lock.is_error, text_of(lock))
            self.assertTrue(after.is_error, "a revoked grant must not deliver a frame")
            self.assertEqual(after.structured_content["error"]["category"], "safety")
            self.assertEqual(images_of(after), [])

        deadline = time.time() + 10.0
        while time.time() < deadline and processes(str(binary)) - natives_before:
            time.sleep(0.1)
        self.assertEqual(
            processes(str(binary)) - natives_before, set(),
            "the native helper outlived its server",
        )
        self.assertEqual(processes("screencast_helper.py"), set())
        self.assertLessEqual(screencast_sessions(), sessions_before)
        print(
            f"\nlive delivery: {len(ids)} shots · first call {elapsed * 1000:.0f} ms "
            f"(helper + session start) · warm call {warm_elapsed * 1000:.0f} ms · "
            + " · ".join(report),
            file=sys.stderr,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
