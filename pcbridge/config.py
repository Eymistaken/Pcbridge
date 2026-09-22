"""Yapilandirma yukleme."""

from __future__ import annotations

import dataclasses
import datetime
import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib

from . import paths as pathslib

# The config file format version this code writes and understands. Files
# without a `config_version` key are version 1 (everything before 2.0).
CONFIG_VERSION = 2


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p))).resolve()


@dataclass
class AgentSpec:
    name: str
    enabled: bool = True
    description: str = ""
    command: list[str] = field(default_factory=list)
    resume_args: list[str] = field(default_factory=list)
    parser: str = "plain"
    # Bazi CLI'lar (ornegin agy) ciktilarini yalnizca gercek bir terminale
    # yaziyor; arka planda calistirilinca hicbir sey donmuyor. pty=true ise
    # komut `script` ile sahte bir terminale sarilir.
    pty: bool = False

    # -- model / effort secimi ----------------------------------------------
    # Hepsi opsiyonel. Hicbiri tanimlanmazsa ajan bugunku gibi davranir:
    # komuta model/effort bayragi eklenmez, CLI kendi varsayilanini kullanir.
    model_args: list[str] = field(default_factory=list)
    effort_args: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    # Yalnizca kullanici adini ACIKCA verdiyse secilebilir; varsayilan
    # doldurma buraya asla dusemez.
    restricted_models: list[str] = field(default_factory=list)
    blocked_models: list[str] = field(default_factory=list)
    efforts: list[str] = field(default_factory=list)
    # Model basina izinli effort listesi. Ajan geneli `efforts`i ezer.
    # Bos liste = bu model --effort bayragini KABUL ETMIYOR (agy'de
    # claude-sonnet-4-6 gibi); bayrak hic eklenmez.
    model_efforts: dict[str, list[str]] = field(default_factory=dict)
    default_model: str = ""
    default_effort: str = ""
    # Model basina varsayilan effort (sonnet -> medium, opus -> high).
    model_effort: dict[str, str] = field(default_factory=dict)
    effort_required_with_model: bool = False
    # Serbest metin -> kanonik ad. Hem modeller hem effort seviyeleri icin.
    aliases: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------- turetilmis
    @property
    def selectable_models(self) -> list[str]:
        """Varsayilan/otomatik secime acik modeller."""
        return list(self.models)

    @property
    def known_models(self) -> list[str]:
        """Ajanin tanidigi butun model adlari (engelliler dahil)."""
        return [*self.models, *self.restricted_models, *self.blocked_models]

    def efforts_for(self, model: str | None) -> list[str]:
        """Verilen modelin kabul ettigi effort listesi.

        `model_efforts` girdisi varsa o kazanir (bos liste dahil: "bu model
        --effort kabul etmiyor" demektir). Yoksa ajan geneli `efforts`.
        """
        if model is not None and model in self.model_efforts:
            return list(self.model_efforts[model])
        return list(self.efforts)


@dataclass
class DesktopSpec:
    """Masaustu (klavye/fare) kontrolu ayarlari.

    `enabled` VARSAYILAN OLARAK FALSE ve oyle kalmali: bu ozellik acik
    oturumdaki her uygulamaya erisim demek, kullanici bilincli olarak acsin.
    """

    enabled: bool = False
    # desktop_unlock(dakika) verilmezse bu kadar; tavan da asagida.
    unlock_default_minutes: int = 15
    unlock_max_minutes: int = 120
    unlock_notification: bool = True
    # KAYAN KIRA: izin son TARIHE degil son EYLEME bagli. Basarili her
    # masaustu cagrisi `until`i `simdi + bu deger`e cekiyor,
    # `unlock_default_minutes` sert tavan olarak duruyor. Ajanin "isim bitti"
    # olayi olmadigi icin `desktop_lock`u unutmasi yapisal; bu onu kodda
    # cozuyor. 0 = kapali (eski davranis: sabit son tarih).
    unlock_idle_seconds: int = 90
    # Acikca etkinlestirilirse, masaustu izni ACIKKEN `shell_run`/
    # `shell_run_background` engel listesindeki bir GUI uygulamasini baslatmaya
    # calisinca reddeder. Varsayilan false: shell ayri bir execution yoludur;
    # ozellikle calisan bir tarayiciya URL vermek yeni bir surec omru yaratmaz.
    block_gui_launch_in_shell: bool = False
    # Kabuktan baslatilmasi engellenecek uygulamalar. Adlar `.desktop`
    # tablosunda aranir, yani "Text Editor" yazmak `gnome-text-editor`i de
    # yakalar.
    gui_launch_blocklist: list[str] = field(default_factory=list)
    # Kullanici son girdisinden bu kadar saniye gecmediyse yazma eylemleri
    # reddedilir (telefon ile kullanicinin faresi kavga etmesin). force=true
    # ile bilincli olarak gecilebilir. 0 = kontrol kapali.
    idle_guard_seconds: int = 60
    # Saniyede en fazla kac eylem. Sonsuz donguye giren bir ajan makineyi
    # kilitleyemesin diye. 0 = sinirsiz.
    max_actions_per_second: int = 10
    # `monitor=` verilmeyen ve monitore ozel yorumlanan cagrilar icin.
    default_monitor: int = 1
    # Pano yoluyla metin yazildiktan sonra eski pano icerigi geri yuklensin mi.
    restore_clipboard: bool = True
    # Yalnizca bilgi/tani amacli: ham tus yolunun (raw=true) hangi duzende
    # yorumlanacagini soyler. Girdi gonderimini DEGISTIRMEZ.
    #
    # Not used for typing: text goes through the clipboard, so the layout
    # does not matter. Kept so existing config files still load.
    keyboard_layout: str = ""

    # -- fare hareketi (I bolumu) -------------------------------------------
    # Imlec hedefe ISINLANMAK yerine ara noktalardan gecerek gider. Piksel/sn.
    # 0 = isinla (eski davranis). `drag` bu ayardan bagimsiz olarak her zaman
    # ara noktalardan gecer -- sicrayan bir hareketi cogu uygulama surukleme
    # saymiyor.
    pointer_speed: int = 5000
    # Tek bir hareket en fazla bu kadar surer (ms); uzun mesafede hizi artirir.
    pointer_move_max_ms: int = 500
    # `hold` ile basili birakilan tus/dugme bu sureden sonra KENDILIGINDEN
    # birakilir. 0 = birakma (onerilmez: unutulan bir tus makineyi
    # kullanilamaz hale getirir ve ajan bunu fark etmez).
    hold_max_seconds: int = 120
    # Tiklamada dugme bu kadar ms basili kalir (Adim 8.3). Eskiden sabit
    # 30 ms'ydi; girdiyi 50 ms'lik tick'lerle yoklayan uygulamalar (oyunlar)
    # basma ile birakmanin ayni tick'e dustugu tiklamayi gormeyebiliyor.
    # Cagri basina `hold_ms` ile degistirilebilir. 0-150: cift tiklamada her
    # basis bu kadar surer ve iki basis 400 ms'lik esigin icinde kalmali.
    click_hold_ms: int = 60

    # -- ekran goruntusu (C bolumu) -----------------------------------------
    # Kirpma SONRASI uzun kenar. 3840x1080 tuval tek parca kuculturse her
    # monitor ~640x180 kaliyor ve buton yazilari okunmaz oluyor; bu yuzden once
    # monitor basina kirpiliyor, olcekleme ondan sonra. 0 = hic olcekleme.
    screenshot_scale_long_edge: int = 1536
    # Pixel-area cap of a scaled picture (Step 6 of 2.0); 0 = no cap. The
    # default is the area of a 16:9 picture at 1536 (1536x864).
    screenshot_max_pixels: int = 1536 * 864
    # /shot/<token>.png baglantisinin omru. Baglanti OAuth'tan BAGIMSIZ, yani
    # token'i olan herkes goruntuyu gorur -- kisa tutuluyor.
    shot_ttl_seconds: int = 300
    # Diskteki PNG'ler bu kadar saat sonra silinir (baglantilari coktan olmus
    # olur; bu yalnizca disk temizligi).
    shot_keep_hours: int = 24
    # Imlec goruntuye dahil edilsin mi. Varsayilan true: fareyi bir yere
    # gonderip "gercekten oraya gitti mi" diye bakmanin tek yolu bu.
    include_pointer: bool = True
    # Ekran nasil yakalansin (J bolumu):
    #   "screencast"       PipeWire ekran yayini — SESSIZ, flas yok
    #   "gnome-screenshot" eski yol — her cekimde beyaz flas + ses
    #   "auto"             screencast varsa o, yoksa gnome-screenshot
    # Yayin `desktop_unlock` ile acilir, `desktop_lock`/sure dolumu ile kapanir;
    # acikken GNOME ust cubukta paylasim gostergesi durur (istenen: ajanin
    # masaustune erisebildigi oradan gorunuyor).
    capture_backend: str = "auto"
    # `find_text` / `wait_for_text` (Adim 8.6) icin tesseract dilleri, `+` ile.
    # Her dilin verisi ayri paket: `tesseract-ocr-tur`, `tesseract-ocr-eng`.
    ocr_languages: str = "eng"

    # -- toplu eylem (E bolumu) ---------------------------------------------
    # computer_batch tek cagrida en fazla kac eylem alir.
    batch_max_actions: int = 40
    # Toplam sure butcesi (saniye). Tahmini bu butceyi asan plan HIC
    # baslamaz; baslayan plan butce dolunca siradaki eyleme baslamaz ve kalan
    # listeyi geri dondurur. 50, cunku istemcilerin cogu bir araci 60 sn'de
    # birakiyor (OLCULDU 2026-09-21: "did not respond within 60s"); aradaki
    # fark `final` adimi ve cevap icin pay. Yalnizca Claude Code gibi uzun
    # bekleyen bir istemci kullaniliyorsa 105'e kadar cikarilabilir.
    batch_budget_seconds: int = 50
    # Fare tiklamasindan sonra odak dogrulansin mi. OLCULDU: kor tiklama odagi
    # kaydiriyor ve sonraki tuslar yanlis pencereye gidiyor -- gelistirme
    # sirasinda masaustundeki 23 oge boyle copa gitti. Kapatmayin.
    batch_check_focus: bool = True

    # -- yerel gorsel ajan (F bolumu) ---------------------------------------
    # `computer_task` gorsel isi makinedeki bir ajana devrediyor. Varsayilanlar
    # KODA gomulu degil, buradan geliyor: yeni bir CLI eklendiginde ya da kota
    # dengesi degistiginde tek satirla degistirilebilsin.
    #
    # Bu UCLU BIRBIRINE AIT: model adi secilen ajanin listesinden gelmeli.
    # `_check_computer_task()` bunu YUKLEMEDE dogruluyor -- tutarsizlik cagri
    # aninda degil, servis acilirken patlasin.
    computer_task_agent: str = "claude"
    computer_task_model: str = ""
    computer_task_effort: str = ""
    # Ajana verilen adim butcesi. DISARIDAN ZORLANAMAZ: prompt'ta bir butce
    # olarak gider, arkasinda isin timeout'u ve pcb-do'nun hiz siniri durur.
    computer_task_max_steps: int = 25
    # `pcb-shot`un PNG yazdigi dizin. Bos ise $XDG_RUNTIME_DIR/pcbridge/shots
    # (mod 700, oturum kapaninca silinir), o da yoksa /tmp/pcb.
    agent_shot_dir: str = ""
    # KOORDINATLI bir eylem gonderilirken en yeni ekran goruntusu en fazla bu
    # kadar eski olabilir. 0 = kontrol kapali. Odak korumasindan FARKLI bir
    # tehlikeye bakiyor: goruntu bayatladiysa odak zaten hedef pencerede olmaz,
    # tiklama oraya duser ve "odak degisti" diye bir sey olmaz.
    agent_shot_max_age_seconds: int = 60
    # `shot` verilmeden koordinat gonderildiginde, yakinda KUCULTULMUS bir
    # cekim varsa ve koordinat onun icine dusuyorsa cagriyi reddet.
    ambiguous_coord_guard: bool = True
    # Ayni hedefe ust uste kacinci tiklamada dizi dursun. Ikisi gecer,
    # ucuncusu hic gonderilmez: ilk iki tiklama beklenen etkiyi yapmadiysa
    # ucuncusu de yapmaz, ajan ekrani yeniden okumali. 0 = kapali.
    repeat_click_limit: int = 3


@dataclass
class NativeSpec:
    """Private native helper selection.

    `auto` since Task 4.3, after the Linux parity gate passed: the packaged
    native helper when it is there, the Python helper otherwise.
    """

    capture: str = "auto"
    # `auto` since Gate 5 (2026-09-19): keyboard, pointer and clipboard
    # programs in the native helper when it is packaged, Python otherwise,
    # visibly. `rust` forbids the fallback, `python` keeps the old path.
    input: str = "auto"
    # `auto` since Task 6.3 (2026-09-19): accessibility reads (Task 6.2) and
    # clicks and text (Task 6.3) through the native helper when it is
    # packaged, the Python helper otherwise, visibly. Live on the GTK4 test
    # window both gave the same answers for every case.
    accessibility: str = "auto"
    binary_path: Path | None = None


@dataclass
class Config:
    public_url: str
    host: str
    port: int
    mcp_path: str

    password: str
    static_token: str
    access_token_ttl: int
    refresh_token_ttl: int
    auth_code_ttl: int
    max_failed_attempts: int
    lockout_seconds: int
    manual_redirect: bool

    default_workdir: Path
    state_dir: Path

    max_output_chars: int
    default_job_timeout: int
    max_sync_timeout: int

    agents: dict[str, AgentSpec]
    # Ekran goruntusu arac sonucunda GORUNTU BLOGU olarak da gonderilsin mi.
    # "true" (varsayilan) | "false" | "auto" -- degerlendirme
    # `tools._want_inline()`te.
    #
    # Varsayilan "auto" IDI: Gemini Spark'a giden function-response kanali
    # yalnizca METIN tasiyor ve goruntu blogu gelince bozuluyordu, "auto" da
    # HTTP'de goruntuyu kapatarak onu koruyordu. Spark artik hedef degil ve
    # HTTP'den gelen de goren bir istemci (Codex --url, Claude Code
    # --transport http), yani goruntuyu kapatmak artik goreni kor birakmak
    # olurdu. "auto" GERI DONUS YOLU olarak duruyor: goruntu isleyemeyen bir
    # istemci cikarsa false ya da auto yapilir.
    inline_images: str = "true"
    # Ajan adi verilmediginde ve model hicbir ajana ait degilse kullanilir.
    default_agent: str = "claude"
    desktop: DesktopSpec = field(default_factory=DesktopSpec)
    native: NativeSpec = field(default_factory=NativeSpec)
    # audit.log bu boyutu asinca `.1`'e devredilir. 0 = donderme kapali.
    # Masaustu araclarindan sonra kabuk/ajan/dosya araclari da kayit tuttugu
    # icin dosya artik hizli buyuyor.
    audit_max_bytes: int = 5_000_000
    source_path: Path | None = None
    # Where the file was found: "explicit" (-c), "env" ($PCBRIDGE_CONFIG),
    # "xdg" (~/.config/pcbridge) or "legacy" (<repo>/config.toml).
    source_kind: str = ""
    config_version: int = 1
    # English, one line each: unknown keys, a loose file mode, a legacy
    # location. Logged at startup and shown by `pcbridge doctor`.
    warnings: list[str] = field(default_factory=list)

    # -- turetilmis ---------------------------------------------------------
    @property
    def mcp_url(self) -> str:
        return self.public_url.rstrip("/") + self.mcp_path

    @property
    def jobs_dir(self) -> Path:
        return self.state_dir / "jobs"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "oauth.db"

    @property
    def pointer_pos_file(self) -> Path:
        """Son imlec konumu. SURECLER ARASI paylasiliyor: `pcb-do` her
        cagrisinda yeni bir surec ve son konumu bilmeyen bir surec hareketi
        isinlatmak zorunda kalir."""
        return self.state_dir / "pointer.json"

    @property
    def audit_log(self) -> Path:
        return self.state_dir / "audit.log"

    @property
    def shot_search_dirs(self) -> list[Path]:
        """`shot="m2-a1b2c3"` kimligi hangi dizinlerde aranir.

        IKI DIZIN VAR ve bilincli: MCP sunucusu goruntuleri
        `state_dir/shots`a, `pcb-shot` ise `$XDG_RUNTIME_DIR/pcbridge/shots`a
        yaziyor (mod 700, tmpfs, oturum kapaninca siliniyor). Ikisi de
        arandigi icin MCP'den cekilen goruntuye kabuktan tiklanabiliyor ve
        `pcb-shot` cekimine `mouse` ile dokunulabiliyor -- ajan hangi yoldan
        baktigini hatirlamak zorunda kalmiyor.

        Dizinleri YARATMAZ: burasi saf hesap, mkdir cagiranin isi.
        """
        seen: list[Path] = []
        for d in (self.state_dir / "shots", self.agent_shot_path):
            # Ayni dizin iki kez aranmasin: `agent_shot_dir` state_dir/shots'a
            # ayarlanmis olabilir.
            if d not in seen:
                seen.append(d)
        return seen

    @property
    def agent_shot_path(self) -> Path:
        """`pcb-shot`un PNG yazdigi dizin (yaratmadan, yalnizca yol).

        Varsayilan `$XDG_RUNTIME_DIR/pcbridge/shots`. `UYGULAMA.md` `/tmp/pcb`
        diyor; sapma bilincli: /tmp herkese okunur (mod 775), $XDG_RUNTIME_DIR
        ise yalnizca kullaniciya acik ve oturum kapaninca siliniyor. Ekran
        goruntusu bu projenin en gizlilik-hassas ciktisi.
        """
        configured = (self.desktop.agent_shot_dir or "").strip()
        if configured:
            return _expand(configured)
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        return Path(runtime) / "pcbridge" / "shots" if runtime else Path("/tmp/pcb")


def locate_config(explicit: str | None = None) -> tuple[Path, str]:
    """Find the config file and say where it came from.

    Search order, first hit wins:

        1. an explicit path (-c / --config)
        2. $PCBRIDGE_CONFIG
        3. $XDG_CONFIG_HOME/pcbridge/config.toml  (~/.config/pcbridge)
        4. <repository>/config.toml               (legacy, before 2.0)

    When both 3 and 4 exist, the XDG file wins. The legacy file is left as it
    is: older stdio processes that are still running keep reading it.
    """
    if explicit:
        p = _expand(explicit)
        if not p.exists():
            raise SystemExit(f"Config file not found: {p}")
        return p, "explicit"

    env = os.environ.get("PCBRIDGE_CONFIG")
    if env:
        return _expand(env), "env"

    xdg = pathslib.config_file()
    if xdg.exists():
        return xdg, "xdg"

    if pathslib.LEGACY_REPO_CONFIG.exists():
        return pathslib.LEGACY_REPO_CONFIG, "legacy"

    raise SystemExit(
        f"No pcbridge config file found. Run `pcbridge setup` to create one at {xdg}, "
        "or copy config.example.toml there and set its mode to 0600."
    )


def find_config(explicit: str | None = None) -> Path:
    return locate_config(explicit)[0]


# Keys each section may contain. Anything else earns a warning, never a crash:
# a typo must not take the non-desktop tools down with it.
_TOP_KEYS = {
    "config_version", "public_url", "host", "port", "mcp_path", "default_agent",
    "inline_images", "auth", "paths", "limits", "server", "native", "desktop",
    "agents", "tools",
}
_SECTION_KEYS: dict[str, set[str]] = {
    "auth": {
        "password", "static_token", "access_token_ttl", "refresh_token_ttl",
        "auth_code_ttl", "max_failed_attempts", "lockout_seconds", "manual_redirect",
    },
    "paths": {"state_dir", "default_workdir"},
    "limits": {"max_output_chars", "default_job_timeout", "max_sync_timeout", "audit_max_bytes"},
    "server": {"inline_images"},
    "native": {"capture", "input", "accessibility", "binary_path"},
    "tools": {"profile"},
}
_HINTS = {
    ("limits", "default_agent"): "`default_agent` belongs at the top of the file, before any [section]",
}


def _unknown_key_warnings(raw: dict[str, Any]) -> list[str]:
    desktop_keys = {f.name for f in dataclasses.fields(DesktopSpec)}
    agent_keys = {f.name for f in dataclasses.fields(AgentSpec)} - {"name"}
    out: list[str] = []

    def note(section: str, key: str) -> None:
        where = f"[{section}] " if section else ""
        hint = _HINTS.get((section, key))
        out.append(
            f"Unknown config key {where}`{key}` is ignored"
            + (f"; {hint}." if hint else "; check the spelling against config.example.toml.")
        )

    for key in raw:
        if key not in _TOP_KEYS:
            note("", key)
    for section, allowed in _SECTION_KEYS.items():
        for key in raw.get(section) or {}:
            if key not in allowed:
                note(section, key)
    for key in raw.get("desktop") or {}:
        if key not in desktop_keys:
            note("desktop", key)
    for name, spec in (raw.get("agents") or {}).items():
        for key in spec or {}:
            if key not in agent_keys:
                note(f"agents.{name}", key)
    return out


def _mode_warning(path: Path) -> str | None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None
    if mode & 0o077:
        return (
            f"{path} holds the password and static token but is readable by other users "
            f"(mode {mode:o}); run `pcbridge doctor --fix` or `chmod 600 {path}`."
        )
    return None


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

# Defaults that changed in a config_version. When a file of an older version
# relied on the old default, the migration writes the old value out, so the
# effective settings do not move under the user's feet.
_PINNED_DEFAULTS: dict[int, list[tuple[str, str, str]]] = {
    # version 2: OCR defaults to English only; version 1 read Turkish too.
    2: [
        ("desktop", "ocr_languages", '"tur+eng"'),
        # version 2 caps the area of scaled pictures; version 1 did not.
        ("desktop", "screenshot_max_pixels", "0"),
    ],
}


def _apply_version_defaults(raw: dict[str, Any], version: int) -> None:
    """Give an older file the defaults of its own version.

    An unmigrated version-1 file must mean exactly what it meant before 2.0,
    so a default that changed later is filled in with its old value here.
    """
    for newer in range(version + 1, CONFIG_VERSION + 1):
        for section, key, value in _PINNED_DEFAULTS.get(newer, []):
            table = raw.setdefault(section, {})
            if isinstance(table, dict) and key not in table:
                table[key] = tomllib.loads(f"v = {value}")["v"]


@dataclass
class MigrationResult:
    source: Path
    dest: Path
    from_version: int
    to_version: int
    backup: Path | None
    changed: bool


def _file_version(raw: dict[str, Any]) -> int:
    try:
        return int(raw.get("config_version", 1))
    except (TypeError, ValueError):
        return 1


def _set_in_section(text: str, section: str, line: str) -> str:
    """Add `line` at the top of `[section]`, creating the section if needed."""
    header = re.compile(rf"^\[{re.escape(section)}\][ \t]*(#.*)?$", re.M)
    m = header.search(text)
    if m:
        end = text.index("\n", m.end()) + 1 if "\n" in text[m.end():] else len(text)
        return text[:end] + line + "\n" + text[end:]
    sep = "" if text.endswith("\n") else "\n"
    return f"{text}{sep}\n[{section}]\n{line}\n"


def migrate_text(text: str) -> tuple[str, int]:
    """Bring config text to CONFIG_VERSION. Comments and layout are kept.

    Returns the new text and the version it started from. Pure: no I/O.
    """
    raw = tomllib.loads(text)
    start = _file_version(raw)
    if start >= CONFIG_VERSION:
        return text, start
    out = text
    for version in range(start + 1, CONFIG_VERSION + 1):
        for section, key, value in _PINNED_DEFAULTS.get(version, []):
            if key not in (raw.get(section) or {}):
                out = _set_in_section(out, section, f"{key} = {value}")
    stamp = (
        f"# Written by pcbridge {CONFIG_VERSION}.x migration; see config.example.toml.\n"
        f"config_version = {CONFIG_VERSION}\n\n"
    )
    if "config_version" in raw:
        out = re.sub(r"(?m)^config_version\s*=.*$", f"config_version = {CONFIG_VERSION}", out, count=1)
    else:
        out = stamp + out
    # The result must still parse; never write a file that would not load.
    tomllib.loads(out)
    return out, start


def _backup(path: Path, now: datetime.datetime | None = None) -> Path:
    ts = (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.backup-{ts}")
    n = 1
    while dest.exists():
        dest = path.with_name(f"{path.name}.backup-{ts}-{n}")
        n += 1
    shutil.copy2(path, dest)
    os.chmod(dest, 0o600)
    return dest


def migrate_config(source: Path, dest: Path | None = None) -> MigrationResult:
    """Copy or upgrade a config file to the current version at `dest`.

    `dest` defaults to the XDG location. Every file that is about to be
    rewritten gets a timestamped backup next to it first, and the written file
    is mode 0600 because it holds secrets. The source is never modified unless
    it is also the destination.
    """
    dest = dest or pathslib.config_file()
    text = source.read_text(encoding="utf-8")
    new_text, start = migrate_text(text)
    backup = None
    if dest.exists():
        if dest.read_text(encoding="utf-8") == new_text:
            os.chmod(dest, 0o600)
            return MigrationResult(source, dest, start, CONFIG_VERSION, None, False)
        backup = _backup(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    tmp = dest.with_name(dest.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    os.replace(tmp, dest)
    os.chmod(dest, 0o600)
    return MigrationResult(source, dest, start, CONFIG_VERSION, backup, True)


def _check_agents(agents: dict[str, AgentSpec], path: Path) -> None:
    """Model/effort yapilandirmasindaki sessiz tuzaklari yuklemede yakala.

    Buradaki her kontrol, gecmiste fiilen yasanmis bir hataya karsilik geliyor:
    yanlis yazilmis model kimligi (`claude-sonnet-4.6` vs `claude-sonnet-4-6`),
    varsayilanin kisitli bir modele dusmesi, ya da `model_args` unutuldugu icin
    butun model ayarlarinin sessizce yok sayilmasi.
    """
    where = f"({path})"
    for name, spec in agents.items():
        tag = f"[agents.{name}] {where}"
        known = set(spec.known_models)

        if not spec.model_args and (spec.models or spec.default_model):
            raise SystemExit(
                f"{tag}: `models`/`default_model` are set but `model_args` is missing. "
                'Add the flag syntax: model_args = ["--model", "{model}"]'
            )
        if spec.default_model:
            if spec.default_model in spec.blocked_models:
                raise SystemExit(
                    f"{tag}: `default_model = \"{spec.default_model}\"` is also in "
                    "`blocked_models`."
                )
            if spec.default_model in spec.restricted_models:
                raise SystemExit(
                    f"{tag}: `default_model = \"{spec.default_model}\"` is in "
                    "`restricted_models`. Restricted models can only be chosen when asked for "
                    "by name; they cannot be the default."
                )
            if spec.models and spec.default_model not in spec.models:
                raise SystemExit(
                    f"{tag}: `default_model = \"{spec.default_model}\"` is not in "
                    f"`models`. The list: {', '.join(spec.models) or '-'}"
                )

        for key in (*spec.model_effort, *spec.model_efforts):
            if known and key not in known:
                raise SystemExit(
                    f"{tag}: `{key}` is an unknown model. Check the model id "
                    "(for Antigravity: `agy models`). "
                    f"Defined: {', '.join(sorted(known))}"
                )

        # Model basina varsayilan effort, o modelin kabul ettigi listede olmali.
        for model, effort in spec.model_effort.items():
            allowed = spec.efforts_for(model)
            if not allowed:
                raise SystemExit(
                    f"{tag}: `model_effort.\"{model}\" = \"{effort}\"` but this model "
                    "accepts no effort at all (`model_efforts` is empty)."
                )
            if effort not in allowed:
                raise SystemExit(
                    f"{tag}: `model_effort.\"{model}\" = \"{effort}\"` is invalid. "
                    f"This model accepts: {', '.join(allowed)}"
                )


def _check_computer_task(
    desktop: "DesktopSpec", agents: dict[str, AgentSpec], path: Path
) -> None:
    """`[desktop] computer_task_*` uclusu birbiriyle tutarli mi?

    NEDEN YUKLEMEDE: bu uclu (ajan, model, effort) bir butun. Ajan degistirilip
    model eski ajanınkinde birakilirsa hata yalnizca `computer_task` CAGRILINCA
    ciktiya dusuyordu -- yani aylar sonra, bir GUI isi tam baslarken. `_check_agents`
    ile ayni felsefe: yapilandirma tuzagi servisi ACILISTA durdursun.
    """
    where = f"[desktop] ({path})"
    name = desktop.computer_task_agent
    if not name:
        return
    spec = agents.get(name)
    if spec is None:
        raise SystemExit(
            f"{where}: `computer_task_agent = \"{name}\"` but there is no such "
            f"[agents.*] block. Defined: {', '.join(agents) or '-'}"
        )
    if not spec.enabled:
        raise SystemExit(
            f"{where}: `computer_task_agent = \"{name}\"` is disabled "
            "(`enabled = false`), so computer_task can never run."
        )

    model = desktop.computer_task_model
    if model and model not in spec.known_models:
        raise SystemExit(
            f"{where}: `computer_task_model = \"{model}\"` is not a model of `{name}`. "
            f"Selectable: {', '.join(spec.selectable_models) or '-'}. "
            "If you changed the agent, change the model too (or leave it empty "
            "to use the agent's own default)."
        )

    effort = desktop.computer_task_effort
    if effort:
        allowed = spec.efforts_for(model or spec.default_model or None)
        if allowed and effort not in allowed:
            raise SystemExit(
                f"{where}: `computer_task_effort = \"{effort}\"` is invalid for this "
                f"model. Accepted: {', '.join(allowed)}"
            )


def load_config(explicit: str | None = None) -> Config:
    path, source_kind = locate_config(explicit)
    try:
        with path.open("rb") as fh:
            raw: dict[str, Any] = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{path} is not valid TOML: {exc}. Fix that line and restart pcbridge.")
    except OSError as exc:
        raise SystemExit(f"Cannot read the config file {path}: {exc.strerror}.")

    warnings = _unknown_key_warnings(raw)
    mode_note = _mode_warning(path)
    if mode_note:
        warnings.append(mode_note)
    if source_kind == "legacy":
        warnings.append(
            f"Using the legacy config file {path}; run `pcbridge setup` to move it to "
            f"{pathslib.config_file()}."
        )
    config_version = _file_version(raw)
    _apply_version_defaults(raw, config_version)
    if config_version > CONFIG_VERSION:
        warnings.append(
            f"{path} has config_version {config_version}, newer than this pcbridge "
            f"understands ({CONFIG_VERSION}); unknown settings are ignored."
        )

    auth = raw.get("auth", {})
    paths = raw.get("paths", {})
    limits = raw.get("limits", {})
    server = raw.get("server", {})
    native_raw = raw.get("native") or {}

    # `[server] inline_images` asil yazim. Ama bugune kadar butun sunucu
    # ayarlari (public_url, host, port, mcp_path) KOKTE duruyor, o yuzden kokteki
    # yazim da kabul ediliyor -- kullanicinin dosyayi ikiye bolmesi gerekmesin.
    # TOML'da `true` bool gelir, `"auto"` string; ikisi de ayni yoldan gecsin
    # diye str()'ye cevrilip kucultuluyor.
    inline_images = str(
        server.get("inline_images", raw.get("inline_images", "true"))
    ).strip().lower()
    if inline_images not in ("auto", "true", "false"):
        raise SystemExit(
            f"[server] ({path}): `inline_images` = {inline_images!r} is invalid. "
            'Valid values: true (default), false, '
            '"auto" (on for local sessions, off over HTTP).'
        )

    password = os.environ.get("PCBRIDGE_PASSWORD") or auth.get("password", "")
    static_token = os.environ.get("PCBRIDGE_STATIC_TOKEN") or auth.get(
        "static_token", ""
    )

    public_url = (
        os.environ.get("PCBRIDGE_PUBLIC_URL") or raw.get("public_url", "")
    ).rstrip("/")

    if not public_url:
        raise SystemExit(f"{path}: `public_url` is required (for local use: http://localhost:8765).")
    if not public_url.startswith("https://") and "localhost" not in public_url:
        raise SystemExit(
            f"{path}: `public_url` must start with https:// (remote OAuth clients require HTTPS), "
            "or point at localhost."
        )
    if len(password) < 12:
        raise SystemExit(
            f"{path}: `auth.password` must be at least 12 characters. Pick a long, unguessable one."
        )

    agents: dict[str, AgentSpec] = {}
    for name, spec in (raw.get("agents") or {}).items():
        agents[name] = AgentSpec(
            name=name,
            enabled=bool(spec.get("enabled", True)),
            description=str(spec.get("description", "")),
            command=[str(x) for x in spec.get("command", [])],
            resume_args=[str(x) for x in spec.get("resume_args", [])],
            parser=str(spec.get("parser", "plain")),
            pty=bool(spec.get("pty", False)),
            model_args=[str(x) for x in spec.get("model_args", [])],
            effort_args=[str(x) for x in spec.get("effort_args", [])],
            models=[str(x) for x in spec.get("models", [])],
            restricted_models=[str(x) for x in spec.get("restricted_models", [])],
            blocked_models=[str(x) for x in spec.get("blocked_models", [])],
            efforts=[str(x) for x in spec.get("efforts", [])],
            model_efforts={
                str(k): [str(x) for x in v]
                for k, v in (spec.get("model_efforts") or {}).items()
            },
            default_model=str(spec.get("default_model", "")),
            default_effort=str(spec.get("default_effort", "")),
            model_effort={
                str(k): str(v) for k, v in (spec.get("model_effort") or {}).items()
            },
            effort_required_with_model=bool(
                spec.get("effort_required_with_model", False)
            ),
            aliases={str(k): str(v) for k, v in (spec.get("aliases") or {}).items()},
        )

    _check_agents(agents, path)

    default_agent = str(raw.get("default_agent", "claude"))
    if agents and default_agent not in agents:
        raise SystemExit(
            f"`default_agent = \"{default_agent}\"` but there is no such [agents.*] block. "
            f"Defined agents: {', '.join(agents) or '-'}"
        )

    desktop_raw = raw.get("desktop") or {}
    desktop = DesktopSpec(
        enabled=bool(desktop_raw.get("enabled", False)),
        unlock_default_minutes=int(desktop_raw.get("unlock_default_minutes", 15)),
        unlock_max_minutes=int(desktop_raw.get("unlock_max_minutes", 120)),
        unlock_notification=bool(desktop_raw.get("unlock_notification", True)),
        unlock_idle_seconds=int(desktop_raw.get("unlock_idle_seconds", 90)),
        block_gui_launch_in_shell=bool(
            desktop_raw.get("block_gui_launch_in_shell", False)
        ),
        gui_launch_blocklist=[
            str(x).strip()
            for x in (desktop_raw.get("gui_launch_blocklist") or [])
            if str(x).strip()
        ],
        idle_guard_seconds=int(desktop_raw.get("idle_guard_seconds", 60)),
        max_actions_per_second=int(desktop_raw.get("max_actions_per_second", 10)),
        default_monitor=int(desktop_raw.get("default_monitor", 1)),
        restore_clipboard=bool(desktop_raw.get("restore_clipboard", True)),
        keyboard_layout=str(desktop_raw.get("keyboard_layout", "")),
        pointer_speed=int(desktop_raw.get("pointer_speed", 5000)),
        pointer_move_max_ms=int(desktop_raw.get("pointer_move_max_ms", 500)),
        hold_max_seconds=int(desktop_raw.get("hold_max_seconds", 120)),
        click_hold_ms=int(desktop_raw.get("click_hold_ms", 60)),
        screenshot_scale_long_edge=int(
            desktop_raw.get("screenshot_scale_long_edge", 1536)
        ),
        screenshot_max_pixels=int(desktop_raw.get("screenshot_max_pixels", 1536 * 864)),
        shot_ttl_seconds=int(desktop_raw.get("shot_ttl_seconds", 300)),
        shot_keep_hours=int(desktop_raw.get("shot_keep_hours", 24)),
        include_pointer=bool(desktop_raw.get("include_pointer", True)),
        capture_backend=str(desktop_raw.get("capture_backend", "auto")).strip().lower(),
        # Bu uc satir E bolumunde ATLANMISTI: alanlar DesktopSpec'te vardi ve
        # config.example.toml'da belgeliydi ama buradan okunmuyordu, yani
        # config.toml'a yazilan deger hicbir sey yapmiyordu. F0 sirasinda
        # fark edildi.
        ocr_languages=str(desktop_raw.get("ocr_languages", "eng")).strip(),
        batch_max_actions=int(desktop_raw.get("batch_max_actions", 40)),
        batch_budget_seconds=int(desktop_raw.get("batch_budget_seconds", 50)),
        batch_check_focus=bool(desktop_raw.get("batch_check_focus", True)),
        computer_task_agent=str(desktop_raw.get("computer_task_agent", "claude")),
        computer_task_model=str(desktop_raw.get("computer_task_model", "")),
        computer_task_effort=str(desktop_raw.get("computer_task_effort", "")),
        computer_task_max_steps=int(desktop_raw.get("computer_task_max_steps", 25)),
        agent_shot_dir=str(desktop_raw.get("agent_shot_dir", "")),
        ambiguous_coord_guard=bool(
            desktop_raw.get("ambiguous_coord_guard", True)
        ),
        repeat_click_limit=int(desktop_raw.get("repeat_click_limit", 3)),
        agent_shot_max_age_seconds=int(
            desktop_raw.get("agent_shot_max_age_seconds", 60)
        ),
    )
    if desktop.repeat_click_limit and desktop.repeat_click_limit < 2:
        raise SystemExit(
            f"[desktop] ({path}): `repeat_click_limit` "
            f"({desktop.repeat_click_limit}) must be 0 (off) or at least 2; "
            "with 1 not even the first click would be sent."
        )
    if desktop.unlock_default_minutes > desktop.unlock_max_minutes:
        raise SystemExit(
            f"[desktop] ({path}): `unlock_default_minutes` "
            f"({desktop.unlock_default_minutes}) must not exceed `unlock_max_minutes` "
            f"({desktop.unlock_max_minutes})."
        )
    # 0 = kayan kira kapali. Cok kucuk bir deger izni ajan daha ikinci
    # cagrisini yapamadan dusururdu: `window_focus` GNOME arama yedeginde
    # ~6,7 saniye, ekran goruntusu ~1,5 saniye suruyor (bu makinede olculdu).
    if desktop.unlock_idle_seconds and desktop.unlock_idle_seconds < 10:
        raise SystemExit(
            f"[desktop] ({path}): `unlock_idle_seconds` "
            f"({desktop.unlock_idle_seconds}) must be 0 (no sliding window) or "
            "at least 10."
        )
    # 0 = olcekleme yok; negatif ya da minicik bir deger sessizce okunmaz
    # goruntu uretmesin.
    if desktop.screenshot_scale_long_edge and desktop.screenshot_scale_long_edge < 320:
        raise SystemExit(
            f"[desktop] ({path}): `screenshot_scale_long_edge` "
            f"({desktop.screenshot_scale_long_edge}) must be 0 (no scaling) or "
            "at least 320."
        )
    if desktop.screenshot_max_pixels and desktop.screenshot_max_pixels < 320 * 180:
        raise SystemExit(
            f"[desktop] ({path}): `screenshot_max_pixels` "
            f"({desktop.screenshot_max_pixels}) must be 0 (no cap) or at least "
            f"{320 * 180}."
        )
    # Bir MCP cagrisi 110 saniyeyi asamaz; butce ondan buyuk olursa arac
    # cevabini hazirlayamadan kesilir. Sessizce kirpmak yerine soyluyoruz.
    if not 1 <= desktop.batch_budget_seconds <= 105:
        raise SystemExit(
            f"[desktop] ({path}): `batch_budget_seconds` "
            f"({desktop.batch_budget_seconds}) must be between 1 and 105 "
            "(an MCP call cannot exceed 110 s; the rest is left for the answer)."
        )
    if not re.fullmatch(r"[A-Za-z_]+(\+[A-Za-z_]+)*", desktop.ocr_languages):
        raise SystemExit(
            f"[desktop] ({path}): `ocr_languages` ({desktop.ocr_languages!r}) "
            "must be tesseract language codes joined with `+` (for example: eng+deu)."
        )
    if desktop.batch_max_actions < 1:
        raise SystemExit(
            f"[desktop] ({path}): `batch_max_actions` must be at least 1."
        )
    # 0 = isinlama. Cok dusuk bir hiz ekranin bir ucundan digerine gitmeyi
    # dakikalara cikarir ve bir MCP cagrisi 110 saniyeyi asamaz.
    if desktop.pointer_speed and not 200 <= desktop.pointer_speed <= 100_000:
        raise SystemExit(
            f"[desktop] ({path}): `pointer_speed` ({desktop.pointer_speed}) must be 0 "
            "(jump) or between 200 and 100000 px/s."
        )
    if not 20 <= desktop.pointer_move_max_ms <= 5000:
        raise SystemExit(
            f"[desktop] ({path}): `pointer_move_max_ms` "
            f"({desktop.pointer_move_max_ms}) must be between 20 and 5000 ms."
        )
    # 0 kapatir; cok kisa bir sure `hold`u kullanilamaz yapar (tut, sonra ayri
    # bir cagriyla tikla arasinda ag gecikmesi var).
    if not 0 <= desktop.click_hold_ms <= 150:
        raise SystemExit(
            f"[desktop] ({path}): `click_hold_ms` ({desktop.click_hold_ms}) "
            "must be between 0 and 150 (both presses of a double click must fit in the "
            "400 ms threshold; for one long press pass `hold_ms` in the call)."
        )
    if desktop.hold_max_seconds and not 5 <= desktop.hold_max_seconds <= 3600:
        raise SystemExit(
            f"[desktop] ({path}): `hold_max_seconds` ({desktop.hold_max_seconds}) "
            "must be 0 (no automatic release) or between 5 and 3600 seconds."
        )
    if desktop.capture_backend not in ("auto", "screencast", "gnome-screenshot"):
        raise SystemExit(
            f"[desktop] ({path}): `capture_backend` ({desktop.capture_backend!r}) "
            "must be auto, screencast or gnome-screenshot."
        )
    if desktop.computer_task_max_steps < 1:
        raise SystemExit(
            f"[desktop] ({path}): `computer_task_max_steps` must be at least 1."
        )
    _check_computer_task(desktop, agents, path)
    if desktop.shot_ttl_seconds < 10:
        raise SystemExit(
            f"[desktop] ({path}): `shot_ttl_seconds` "
            f"({desktop.shot_ttl_seconds}) must be at least 10; anything shorter "
            "is not enough time to open the link on a phone."
        )

    native_capture = str(native_raw.get("capture", "auto")).strip().lower()
    if native_capture not in ("python", "rust", "auto"):
        raise SystemExit(
            f"[native] ({path}): `capture` ({native_capture!r}) "
            "must be python, rust or auto."
        )
    native_input = str(native_raw.get("input", "auto")).strip().lower()
    if native_input not in ("python", "rust", "auto"):
        raise SystemExit(
            f"[native] ({path}): `input` ({native_input!r}) "
            "must be python, rust or auto."
        )
    native_accessibility = str(native_raw.get("accessibility", "auto")).strip().lower()
    if native_accessibility not in ("python", "rust", "auto"):
        raise SystemExit(
            f"[native] ({path}): `accessibility` ({native_accessibility!r}) "
            "must be python, rust or auto."
        )
    native_binary = str(native_raw.get("binary_path", "")).strip()
    native = NativeSpec(
        capture=native_capture,
        input=native_input,
        accessibility=native_accessibility,
        binary_path=_expand(native_binary) if native_binary else None,
    )

    state_dir = (
        _expand(paths["state_dir"]) if paths.get("state_dir") else pathslib.state_home()
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "jobs").mkdir(parents=True, exist_ok=True)

    return Config(
        public_url=public_url,
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 8765)),
        mcp_path=str(raw.get("mcp_path", "/mcp")),
        password=password,
        static_token=static_token,
        access_token_ttl=int(auth.get("access_token_ttl", 43200)),
        refresh_token_ttl=int(auth.get("refresh_token_ttl", 7776000)),
        auth_code_ttl=int(auth.get("auth_code_ttl", 300)),
        max_failed_attempts=int(auth.get("max_failed_attempts", 8)),
        lockout_seconds=int(auth.get("lockout_seconds", 900)),
        manual_redirect=bool(
            os.environ.get("PCBRIDGE_MANUAL_REDIRECT")
            or auth.get("manual_redirect", False)
        ),
        default_workdir=_expand(paths.get("default_workdir", "~")),
        state_dir=state_dir,
        max_output_chars=int(limits.get("max_output_chars", 12000)),
        default_job_timeout=int(limits.get("default_job_timeout", 1800)),
        max_sync_timeout=int(limits.get("max_sync_timeout", 120)),
        audit_max_bytes=int(limits.get("audit_max_bytes", 5_000_000)),
        inline_images=inline_images,
        agents=agents,
        default_agent=default_agent,
        desktop=desktop,
        native=native,
        source_path=path,
        source_kind=source_kind,
        config_version=config_version,
        warnings=warnings,
    )
