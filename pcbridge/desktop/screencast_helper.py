#!/usr/bin/env python3
"""Ekran yakalama yardimcisi — SISTEM python3 ile calisir, venv'den IMPORT EDILMEZ.

NE YAPAR
    Mutter'in ekran PAYLASIMI arayuzunden (`org.gnome.Mutter.ScreenCast`) bir
    yayin acar ve o yayindan tek tek kare yakalar. Ekran goruntusu servisi
    degil, ekran paylasimi servisi -- ayrimin sebebi asagida.

NEDEN EKRAN GORUNTUSU SERVISI DEGIL (olculdu 2026-08-03)
    `gnome-screenshot` her cekimde BEYAZ FLAS patlatiyor ve ses cikariyor;
    flasi kendi ciziyor (ikilikte `cheese_flash_fire` var). XDG portal yolu da
    flas patlatiyor. `org.gnome.Shell.Screenshot` D-Bus arayuzu ise
    "Access denied" -- GNOME 46 cagirani suzuyor ve yalnizca kendi
    uygulamalarina izin veriyor.

    Ekran paylasimi yolunda flas yok: sistem bunu fotograf degil VIDEO
    saniyor. Olculen sureler (bu makine):

        gnome-screenshot   833 ms  · flas + ses
        XDG portal         497 ms  · flas + ses
        ScreenCast          73 ms  · sessiz          <-- bu

    Piksel karsilastirmasi: ScreenCast ciktisi ile gnome-screenshot'in ayni
    bolgesi **%99,8 birebir ayni**; kalan fark iki cekim arasinda ekranin
    kendisinin degismesi (akan metin). Sikistirma yok, kayip yok.

NEDEN AYRI VE KALICI SUREC
    1. `gi` (PyGObject) ve GStreamer venv'de yok; sistem python3'unde hazir.
       (`atspi_helper.py` de ayni sebeple ayri surec.)
    2. Bu yardimci ondan farkli olarak **uzun omurlu**: yayin acik kaldigi
       surece kareler ~73 ms'de geliyor, her cekimde kurulsaydi ~250 ms olurdu.
    3. Yan fayda ve bilincli tercih: yayin bu surecte yasiyor. Surec olunce
       yayin da oluyor -- "ekranim hala paylasiliyor mu" diye bir artik
       kalmiyor.

GOSTERGE
    Yayin acikken GNOME ust cubukta turuncu bir paylasim gostergesi cikariyor.
    Bu bir yan etki DEGIL, istenen sey: kullanici ajanin masaustune erisebilir
    durumda oldugunu oradan goruyor. Gostergenin cekilen karede de gorunecegini
    bilerek kabul ediyoruz.

PROTOKOL
    stdin'den satir satir JSON istek, stdout'a satir satir JSON cevap.
    Baska hicbir sey stdout'a yazilmaz (GLib/GStreamer gurultusu stderr'de).

        {"cmd": "start",   "monitors": ["DP-1", "DP-2"], "cursor": true}
        {"cmd": "capture", "monitor": "DP-1", "path": "/tmp/x.png"}
        {"cmd": "status"}
        {"cmd": "stop"}

    Cevap her zaman `ok` alani tasir; `ok: false` ise `error` da vardir.
"""

from __future__ import annotations

import json
import sys
import time

try:
    import gi

    gi.require_version("Gio", "2.0")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gio, GLib, Gst
except Exception as exc:  # noqa: BLE001 - bagimlilik yoksa duzgun rapor et
    print(json.dumps({"ok": False, "error": f"gi/Gst yuklenemedi: {exc}"}),
          flush=True)
    sys.exit(3)

SCD = "org.gnome.Mutter.ScreenCast"
SCD_PATH = "/org/gnome/Mutter/ScreenCast"

# Kare bekleme siniri. Yayin ayakta ama kare gelmiyorsa (kompozitor takildi,
# monitor uykuya girdi) burada kesiyoruz -- bir MCP cagrisi 110 saniyeden uzun
# bloklayamaz ve bu yol onun icinde.
FRAME_TIMEOUT_SECONDS = 8

# `RecordMonitor` icin imlec kipi: 0 gizli, 1 goruntuye gomulu, 2 ayri veri.
CURSOR_EMBEDDED = 1
CURSOR_HIDDEN = 0


class HelperError(RuntimeError):
    """Beklenen, kullaniciya donulebilir hata."""


class ScreenCastHelper:
    def __init__(self) -> None:
        Gst.init(None)
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.session: str | None = None
        self.nodes: dict[str, int] = {}      # connector -> pipewire node id
        self.started_at: float = 0.0

    # ------------------------------------------------------------- D-Bus
    def _call(self, path: str, iface: str, method: str,
              args: "GLib.Variant | None" = None, ret: str = "()"):
        try:
            return self.bus.call_sync(
                SCD, path, iface, method,
                args if args is not None else GLib.Variant("()", ()),
                GLib.VariantType(ret), Gio.DBusCallFlags.NONE, 10000, None,
            )
        except GLib.Error as exc:
            raise HelperError(f"{method} basarisiz: {exc.message}") from exc

    # ------------------------------------------------------------- yayin
    def start(self, monitors: list[str], cursor: bool = True) -> dict:
        if self.session is not None:
            return {"ok": True, "already": True, "monitors": sorted(self.nodes)}
        if not monitors:
            raise HelperError("monitor listesi bos")

        self.session = self._call(
            SCD_PATH, SCD, "CreateSession", GLib.Variant("(a{sv})", ({},)), "(o)"
        ).unpack()[0]

        loop = GLib.MainLoop()
        pending: dict[str, str] = {}     # stream path -> connector

        def on_added(conn, sender, path, iface, signal, params):
            connector = pending.pop(path, None)
            if connector is not None:
                self.nodes[connector] = params.unpack()[0]
            if not pending:
                loop.quit()

        for connector in monitors:
            stream = self._call(
                self.session, f"{SCD}.Session", "RecordMonitor",
                GLib.Variant("(sa{sv})", (connector, {
                    "cursor-mode": GLib.Variant(
                        "u", CURSOR_EMBEDDED if cursor else CURSOR_HIDDEN),
                })), "(o)",
            ).unpack()[0]
            pending[stream] = connector
            self.bus.signal_subscribe(
                SCD, f"{SCD}.Stream", "PipeWireStreamAdded", stream, None,
                Gio.DBusSignalFlags.NONE, on_added,
            )

        self._call(self.session, f"{SCD}.Session", "Start")
        GLib.timeout_add_seconds(10, lambda: (loop.quit(), False)[1])
        loop.run()

        if pending:
            eksik = sorted(pending.values())
            self.stop()
            raise HelperError(f"PipeWire dugumu gelmedi: {', '.join(eksik)}")

        self.started_at = time.time()
        return {"ok": True, "monitors": sorted(self.nodes),
                "nodes": dict(self.nodes)}

    def stop(self) -> dict:
        vardi = self.session is not None
        if self.session is not None:
            try:
                self._call(self.session, f"{SCD}.Session", "Stop")
            except HelperError:
                pass          # oturum zaten dusmus olabilir
        self.session = None
        self.nodes = {}
        self.started_at = 0.0
        return {"ok": True, "was_open": vardi}

    def status(self) -> dict:
        return {
            "ok": True,
            "open": self.session is not None,
            "monitors": sorted(self.nodes),
            "seconds": round(time.time() - self.started_at, 1) if self.started_at else 0,
        }

    # ------------------------------------------------------------ yakalama
    @staticmethod
    def _build_pipeline(node: int, path: str) -> "Gst.Pipeline":
        """Boru hatti ELLE kuruluyor, `Gst.parse_launch` METNI ile degil.

        parse_launch bir kabuk degil ama kabuga benziyor: dosya yolunu oraya
        gomunce kacis kurallari tutmuyor. Ilk denemede `GLib.shell_quote`
        eklenen tirnaklar dosya ADININ parcasi oldu ve yazma basarisiz oldu.
        Elle kurmada `location` dogrudan ozellik olarak atandigi icin yolda
        bosluk, tirnak, `!` ne olursa olsun sorun cikmaz.
        """
        elems = {}
        for name in ("pipewiresrc", "videoconvert", "pngenc", "filesink"):
            el = Gst.ElementFactory.make(name)
            if el is None:
                raise HelperError(
                    f"GStreamer ogesi bulunamadi: {name}. "
                    "Kurulum: sudo apt install gstreamer1.0-pipewire "
                    "gstreamer1.0-plugins-good"
                )
            elems[name] = el

        elems["pipewiresrc"].set_property("path", str(node))
        elems["pipewiresrc"].set_property("num-buffers", 1)
        elems["filesink"].set_property("location", path)

        pipeline = Gst.Pipeline.new("pcbridge-shot")
        sira = [elems["pipewiresrc"], elems["videoconvert"],
                elems["pngenc"], elems["filesink"]]
        for el in sira:
            pipeline.add(el)
        for a, b in zip(sira, sira[1:]):
            if not a.link(b):
                raise HelperError(f"GStreamer baglantisi kurulamadi: {a.name} -> {b.name}")
        return pipeline

    def capture(self, monitor: str, path: str) -> dict:
        if self.session is None:
            raise HelperError("yayin acik degil (once `start`)")
        node = self.nodes.get(monitor)
        if node is None:
            raise HelperError(
                f"'{monitor}' yayinda yok. Acik olanlar: {', '.join(sorted(self.nodes)) or '(hicbiri)'}"
            )

        t0 = time.perf_counter()
        pipeline = self._build_pipeline(node, path)
        pipeline.set_state(Gst.State.PLAYING)
        msg = pipeline.get_bus().timed_pop_filtered(
            FRAME_TIMEOUT_SECONDS * Gst.SECOND,
            Gst.MessageType.EOS | Gst.MessageType.ERROR,
        )
        pipeline.set_state(Gst.State.NULL)

        if msg is None:
            raise HelperError(
                f"{FRAME_TIMEOUT_SECONDS} saniyede kare gelmedi "
                f"(monitor uykuda ya da yayin dustu)"
            )
        if msg.type == Gst.MessageType.ERROR:
            err, _ = msg.parse_error()
            raise HelperError(f"kare yakalanamadi: {err.message}")

        return {"ok": True, "path": path, "monitor": monitor,
                "ms": round((time.perf_counter() - t0) * 1000)}


def main() -> int:
    helper = ScreenCastHelper()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": f"bozuk JSON: {exc}"}), flush=True)
            continue

        cmd = str(req.get("cmd", "")).strip().lower()
        try:
            if cmd == "start":
                out = helper.start(
                    [str(m) for m in (req.get("monitors") or [])],
                    cursor=bool(req.get("cursor", True)),
                )
            elif cmd == "capture":
                out = helper.capture(str(req.get("monitor", "")),
                                     str(req.get("path", "")))
            elif cmd == "status":
                out = helper.status()
            elif cmd == "stop":
                out = helper.stop()
            elif cmd == "quit":
                helper.stop()
                print(json.dumps({"ok": True, "bye": True}), flush=True)
                return 0
            else:
                out = {"ok": False, "error": f"bilinmeyen komut: {cmd!r}"}
        except HelperError as exc:
            out = {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - yardimci olmemeli
            out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        print(json.dumps(out), flush=True)
    # stdin kapandi: yayin bu surecle birlikte olsun.
    helper.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
