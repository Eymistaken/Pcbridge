"""Yapilandirma yukleme."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


DEFAULT_CONFIG_NAMES = ("config.toml",)


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
    keyboard_layout: str = "tr+intl"

    # -- ekran goruntusu (C bolumu) -----------------------------------------
    # Kirpma SONRASI uzun kenar. 3840x1080 tuval tek parca kuculturse her
    # monitor ~640x180 kaliyor ve buton yazilari okunmaz oluyor; bu yuzden once
    # monitor basina kirpiliyor, olcekleme ondan sonra. 0 = hic olcekleme.
    screenshot_scale_long_edge: int = 1280
    # /shot/<token>.png baglantisinin omru. Baglanti OAuth'tan BAGIMSIZ, yani
    # token'i olan herkes goruntuyu gorur -- kisa tutuluyor.
    shot_ttl_seconds: int = 300
    # Diskteki PNG'ler bu kadar saat sonra silinir (baglantilari coktan olmus
    # olur; bu yalnizca disk temizligi).
    shot_keep_hours: int = 24
    # Imlec goruntuye dahil edilsin mi. Varsayilan true: fareyi bir yere
    # gonderip "gercekten oraya gitti mi" diye bakmanin tek yolu bu.
    include_pointer: bool = True

    # -- toplu eylem (E bolumu) ---------------------------------------------
    # computer_batch tek cagrida en fazla kac eylem alir.
    batch_max_actions: int = 40
    # Toplam sure butcesi (saniye). MCP cagrisi 110 saniyeyi asamaz; aradaki
    # fark cevabin hazirlanmasi ve `final` adimi icin pay. Butce dolunca batch
    # siradaki eyleme HIC BASLAMAZ, kalan listeyi geri dondurur.
    batch_budget_seconds: int = 90
    # Fare tiklamasindan sonra odak dogrulansin mi. OLCULDU: kor tiklama odagi
    # kaydiriyor ve sonraki tuslar yanlis pencereye gidiyor -- gelistirme
    # sirasinda masaustundeki 23 oge boyle copa gitti. Kapatmayin.
    batch_check_focus: bool = True

    # -- yerel gorsel ajan (F bolumu) ---------------------------------------
    # `computer_task` gorsel isi makinedeki bir ajana devrediyor. Varsayilanlar
    # KODA gomulu degil, buradan geliyor: yeni bir CLI eklendiginde ya da kota
    # dengesi degistiginde tek satirla degistirilebilsin.
    computer_task_agent: str = "antigravity"
    computer_task_model: str = "gemini-3.6-flash"
    computer_task_effort: str = "high"
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
    # "auto" | "true" | "false" -- degerlendirme `tools._want_inline()`te.
    #
    # NEDEN UC DEGERLI: bir sunucu iki farkli istemci sinifina bakiyor.
    # Gemini Spark'a giden function-response kanali yalnizca METIN tasiyor ve
    # goruntu blogu gelince bozuluyor; Claude Code / Codex ise goruntuyu
    # okuyabiliyor (H0.1'de olculdu: gizli deger goruntuden birebir okundu).
    # "auto" bu ayrimi tasimaya bakarak yapiyor -- Spark HTTP'den, yerel
    # istemciler stdio'dan geliyor.
    inline_images: str = "auto"
    # Ajan adi verilmediginde ve model hicbir ajana ait degilse kullanilir.
    default_agent: str = "claude"
    desktop: DesktopSpec = field(default_factory=DesktopSpec)
    # audit.log bu boyutu asinca `.1`'e devredilir. 0 = donderme kapali.
    # Masaustu araclarindan sonra kabuk/ajan/dosya araclari da kayit tuttugu
    # icin dosya artik hizli buyuyor.
    audit_max_bytes: int = 5_000_000
    source_path: Path | None = None

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
    def audit_log(self) -> Path:
        return self.state_dir / "audit.log"


def find_config(explicit: str | None = None) -> Path:
    if explicit:
        p = _expand(explicit)
        if not p.exists():
            raise SystemExit(f"Yapilandirma bulunamadi: {p}")
        return p

    env = os.environ.get("PCBRIDGE_CONFIG")
    if env:
        return _expand(env)

    here = Path(__file__).resolve().parent.parent
    for name in DEFAULT_CONFIG_NAMES:
        cand = here / name
        if cand.exists():
            return cand

    cand = _expand("~/.config/pcbridge/config.toml")
    if cand.exists():
        return cand

    raise SystemExit(
        "config.toml bulunamadi.\n"
        f"  cp {here / 'config.example.toml'} {here / 'config.toml'}\n"
        f"  chmod 600 {here / 'config.toml'}\n"
        "sonra dosyayi duzenleyin."
    )


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
                f"{tag}: `models`/`default_model` tanimli ama `model_args` yok. "
                'Bayrak sozdizimini ekleyin: model_args = ["--model", "{model}"]'
            )
        if spec.default_model:
            if spec.default_model in spec.blocked_models:
                raise SystemExit(
                    f"{tag}: `default_model = \"{spec.default_model}\"` ayni zamanda "
                    "`blocked_models` icinde."
                )
            if spec.default_model in spec.restricted_models:
                raise SystemExit(
                    f"{tag}: `default_model = \"{spec.default_model}\"` "
                    "`restricted_models` icinde. Kisitli modeller yalnizca acikca "
                    "istendiginde secilebilir, varsayilan olamaz."
                )
            if spec.models and spec.default_model not in spec.models:
                raise SystemExit(
                    f"{tag}: `default_model = \"{spec.default_model}\"` `models` "
                    f"listesinde yok. Liste: {', '.join(spec.models) or '-'}"
                )

        for key in (*spec.model_effort, *spec.model_efforts):
            if known and key not in known:
                raise SystemExit(
                    f"{tag}: `{key}` bilinmeyen bir model. Model kimligini "
                    "dogrulayin (Antigravity icin: `agy models`). "
                    f"Tanimli: {', '.join(sorted(known))}"
                )

        # Model basina varsayilan effort, o modelin kabul ettigi listede olmali.
        for model, effort in spec.model_effort.items():
            allowed = spec.efforts_for(model)
            if not allowed:
                raise SystemExit(
                    f"{tag}: `model_effort.\"{model}\" = \"{effort}\"` ama bu model "
                    "hic effort kabul etmiyor (`model_efforts` bos)."
                )
            if effort not in allowed:
                raise SystemExit(
                    f"{tag}: `model_effort.\"{model}\" = \"{effort}\"` gecersiz. "
                    f"Bu modelin kabul ettikleri: {', '.join(allowed)}"
                )


def load_config(explicit: str | None = None) -> Config:
    path = find_config(explicit)
    with path.open("rb") as fh:
        raw: dict[str, Any] = tomllib.load(fh)

    auth = raw.get("auth", {})
    paths = raw.get("paths", {})
    limits = raw.get("limits", {})
    server = raw.get("server", {})

    # `[server] inline_images` asil yazim. Ama bugune kadar butun sunucu
    # ayarlari (public_url, host, port, mcp_path) KOKTE duruyor, o yuzden kokteki
    # yazim da kabul ediliyor -- kullanicinin dosyayi ikiye bolmesi gerekmesin.
    # TOML'da `true` bool gelir, `"auto"` string; ikisi de ayni yoldan gecsin
    # diye str()'ye cevrilip kucultuluyor.
    inline_images = str(
        server.get("inline_images", raw.get("inline_images", "auto"))
    ).strip().lower()
    if inline_images not in ("auto", "true", "false"):
        raise SystemExit(
            f"[server] ({path}): `inline_images` = {inline_images!r} gecersiz. "
            'Gecerli degerler: "auto" (stdio\'da acik, HTTP\'de kapali), true, false.'
        )

    password = os.environ.get("PCBRIDGE_PASSWORD") or auth.get("password", "")
    static_token = os.environ.get("PCBRIDGE_STATIC_TOKEN") or auth.get(
        "static_token", ""
    )

    public_url = (
        os.environ.get("PCBRIDGE_PUBLIC_URL") or raw.get("public_url", "")
    ).rstrip("/")

    if not public_url:
        raise SystemExit("config.toml icinde `public_url` zorunlu.")
    if not public_url.startswith("https://") and "localhost" not in public_url:
        raise SystemExit(
            "`public_url` https:// ile baslamali (Gemini Spark yalnizca HTTPS kabul ediyor)."
        )
    if len(password) < 12:
        raise SystemExit(
            "`auth.password` en az 12 karakter olmali. Uzun ve tahmin edilemez bir parola secin."
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
            f"`default_agent = \"{default_agent}\"` ama boyle bir [agents.*] blogu yok. "
            f"Tanimli ajanlar: {', '.join(agents) or '-'}"
        )

    desktop_raw = raw.get("desktop") or {}
    desktop = DesktopSpec(
        enabled=bool(desktop_raw.get("enabled", False)),
        unlock_default_minutes=int(desktop_raw.get("unlock_default_minutes", 15)),
        unlock_max_minutes=int(desktop_raw.get("unlock_max_minutes", 120)),
        idle_guard_seconds=int(desktop_raw.get("idle_guard_seconds", 60)),
        max_actions_per_second=int(desktop_raw.get("max_actions_per_second", 10)),
        default_monitor=int(desktop_raw.get("default_monitor", 1)),
        restore_clipboard=bool(desktop_raw.get("restore_clipboard", True)),
        keyboard_layout=str(desktop_raw.get("keyboard_layout", "tr+intl")),
        screenshot_scale_long_edge=int(
            desktop_raw.get("screenshot_scale_long_edge", 1280)
        ),
        shot_ttl_seconds=int(desktop_raw.get("shot_ttl_seconds", 300)),
        shot_keep_hours=int(desktop_raw.get("shot_keep_hours", 24)),
        include_pointer=bool(desktop_raw.get("include_pointer", True)),
        # Bu uc satir E bolumunde ATLANMISTI: alanlar DesktopSpec'te vardi ve
        # config.example.toml'da belgeliydi ama buradan okunmuyordu, yani
        # config.toml'a yazilan deger hicbir sey yapmiyordu. F0 sirasinda
        # fark edildi.
        batch_max_actions=int(desktop_raw.get("batch_max_actions", 40)),
        batch_budget_seconds=int(desktop_raw.get("batch_budget_seconds", 90)),
        batch_check_focus=bool(desktop_raw.get("batch_check_focus", True)),
        computer_task_agent=str(desktop_raw.get("computer_task_agent", "antigravity")),
        computer_task_model=str(
            desktop_raw.get("computer_task_model", "gemini-3.6-flash")
        ),
        computer_task_effort=str(desktop_raw.get("computer_task_effort", "high")),
        computer_task_max_steps=int(desktop_raw.get("computer_task_max_steps", 25)),
        agent_shot_dir=str(desktop_raw.get("agent_shot_dir", "")),
        agent_shot_max_age_seconds=int(
            desktop_raw.get("agent_shot_max_age_seconds", 60)
        ),
    )
    if desktop.unlock_default_minutes > desktop.unlock_max_minutes:
        raise SystemExit(
            f"[desktop] ({path}): `unlock_default_minutes` "
            f"({desktop.unlock_default_minutes}) `unlock_max_minutes` "
            f"({desktop.unlock_max_minutes}) degerini asamaz."
        )
    # 0 = olcekleme yok; negatif ya da minicik bir deger sessizce okunmaz
    # goruntu uretmesin.
    if desktop.screenshot_scale_long_edge and desktop.screenshot_scale_long_edge < 320:
        raise SystemExit(
            f"[desktop] ({path}): `screenshot_scale_long_edge` "
            f"({desktop.screenshot_scale_long_edge}) ya 0 (olcekleme yok) ya da "
            "en az 320 olmali."
        )
    # Bir MCP cagrisi 110 saniyeyi asamaz; butce ondan buyuk olursa arac
    # cevabini hazirlayamadan kesilir. Sessizce kirpmak yerine soyluyoruz.
    if not 1 <= desktop.batch_budget_seconds <= 105:
        raise SystemExit(
            f"[desktop] ({path}): `batch_budget_seconds` "
            f"({desktop.batch_budget_seconds}) 1-105 arasinda olmali "
            "(MCP cagrisi 110 saniyeyi asamiyor, gerisi cevap icin pay)."
        )
    if desktop.batch_max_actions < 1:
        raise SystemExit(
            f"[desktop] ({path}): `batch_max_actions` en az 1 olmali."
        )
    if desktop.computer_task_max_steps < 1:
        raise SystemExit(
            f"[desktop] ({path}): `computer_task_max_steps` en az 1 olmali."
        )
    if desktop.shot_ttl_seconds < 10:
        raise SystemExit(
            f"[desktop] ({path}): `shot_ttl_seconds` "
            f"({desktop.shot_ttl_seconds}) en az 10 olmali; daha kisasi "
            "baglantiyi telefonda acmaya yetmez."
        )

    state_dir = _expand(paths.get("state_dir", "~/.local/state/pcbridge"))
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
        source_path=path,
    )
