"""Ajan / model / effort cozumleyicisi.

Burasi bilincli olarak **saf**: hicbir I/O yapmaz, surec baslatmaz, global
duruma dokunmaz. Tek girdisi `Config`, tek ciktisi bir `Resolution`. Sunucu
ayakta olmadan `tests/test_models.py` ile tablo surucusu test edilebilmesinin
tek sebebi bu.

Kurallar koda degil `config.toml`'a gomulu; yeni bir CLI eklendiginde bu
dosyanin degismesi gerekmez.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import AgentSpec, Config

# Kanonik effort siralamasi (dusukten yukseye). Kirpma (clamp) bunun uzerinden
# yapilir; bir ajanin kabul etmedigi seviye en yakin ALT seviyeye indirilir.
EFFORT_ORDER: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max", "ultracode")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_LETTER_DIGIT = re.compile(r"(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])")


def normalize(text: str) -> str:
    """Serbest metni eslestirme anahtarina cevir.

    Kucuk harfe indirir, alfanumerik olmayan her seyi (nokta, tire, alt cizgi,
    noktalama) ayirac sayar ve harf-rakam sinirlarini boler. Sonucta sunlar
    ayni anahtara gider:

        "Gemini 3.6 Flash" == "gemini-3.6-flash" == "gemini_3_6_flash"
        "Opus5"            == "opus 5"
        "claude sonnet 4.6"== "claude-sonnet-4-6"

    Bu sayede yalnizca GERCEKTEN farkli adlar icin alias yazmak gerekir.
    """
    s = _NON_ALNUM.sub(" ", text.strip().lower())
    s = _LETTER_DIGIT.sub(" ", s)
    return " ".join(s.split())


@dataclass
class Resolution:
    """Cozumleme sonucu. `error` doluysa is BASLATILMAMALIDIR."""

    agent: str
    model: str | None = None
    effort: str | None = None
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def headline(self) -> str:
        """Is ozetinin ilk satiri: `ajan: claude · model: opus · effort: high`."""
        parts = [f"ajan: {self.agent}"]
        parts.append(f"model: {self.model}" if self.model else "model: (CLI varsayilani)")
        if self.effort:
            parts.append(f"effort: {self.effort}")
        return " · ".join(parts)


# ---------------------------------------------------------------------------
# Ad cozumleme
# ---------------------------------------------------------------------------


def agent_efforts(spec: AgentSpec) -> list[str]:
    """Ajanin herhangi bir modelde kabul ettigi butun effort seviyeleri."""
    seen = list(spec.efforts)
    for values in spec.model_efforts.values():
        for v in values:
            if v not in seen:
                seen.append(v)
    return seen


def _lookup(spec: AgentSpec) -> dict[str, str]:
    """normalize edilmis serbest metin -> kanonik ad.

    Oncelik sirasi (sonraki oncekini ezer): kanonik effort adlari, ajanin
    effort listesi, model adlari, `aliases`. Alias en sonda cunku yapilandirmayi
    yazan kisinin niyeti her seyin ustundedir.
    """
    table: dict[str, str] = {}
    for eff in EFFORT_ORDER:
        table[normalize(eff)] = eff
    for eff in agent_efforts(spec):
        table[normalize(eff)] = eff
    for name in spec.known_models:
        table[normalize(name)] = name
    for raw, canon in spec.aliases.items():
        # Alias degeri de kanoniklestirilsin ("Opus 5" -> "opus" yazilmis olsun)
        table[normalize(raw)] = table.get(normalize(canon), canon)
    return table


def _canon(spec: AgentSpec, text: str) -> str | None:
    return _lookup(spec).get(normalize(text))


def _agents_for_model(cfg: Config, text: str) -> list[str]:
    """Bu model adini tanıyan ajanlar; `default_agent` varsa basta."""
    hits: list[str] = []
    for name, spec in cfg.agents.items():
        if not spec.enabled:
            continue
        canon = _canon(spec, text)
        if canon is not None and canon in spec.known_models:
            hits.append(name)
    hits.sort(key=lambda n: 0 if n == cfg.default_agent else 1)
    return hits


def _any_model_selection(cfg: Config) -> bool:
    """Herhangi bir acik ajanda model/effort secimi yapilandirilmis mi?"""
    return any(
        s.enabled and (s.model_args or s.effort_args) for s in cfg.agents.values()
    )


def _enabled_names(cfg: Config) -> str:
    return ", ".join(n for n, s in cfg.agents.items() if s.enabled) or "-"


def _model_menu(cfg: Config) -> str:
    """Butun ajanlarin secilebilir modelleri -- hata mesajlarinda gosterilir."""
    rows = []
    for name, spec in cfg.agents.items():
        if not spec.enabled or not spec.selectable_models:
            continue
        rows.append(f"  {name}: {', '.join(spec.selectable_models)}")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Effort kirpma
# ---------------------------------------------------------------------------


def _clamp_effort(effort: str, allowed: list[str]) -> tuple[str, str | None]:
    """Istenen effort'u ajanin kabul ettigi en yakin ALT seviyeye indir.

    Sessiz dusurme yok: bir seviye degistiyse aciklamasi da doner.
    """
    if effort in allowed:
        return effort, None

    ordered = [e for e in EFFORT_ORDER if e in allowed]
    if not ordered:
        # Ajan effort listesini kanonik siralamayla eslestiremiyoruz; ilk
        # tanimliya dus, ama bunu soyle.
        pick = allowed[0]
        return pick, f"'{effort}' bu ajanda tanimli degil, '{pick}' kullanildi."

    if effort not in EFFORT_ORDER:
        pick = ordered[-1]
        return pick, f"'{effort}' bilinen bir seviye degil, '{pick}' kullanildi."

    want = EFFORT_ORDER.index(effort)
    lower = [e for e in ordered if EFFORT_ORDER.index(e) < want]
    pick = lower[-1] if lower else ordered[0]
    return pick, (
        f"'{effort}' bu ajanda yok (kabul edilenler: {', '.join(ordered)}), "
        f"'{pick}' kullanildi."
    )


# ---------------------------------------------------------------------------
# Ana cozumleyici
# ---------------------------------------------------------------------------


def resolve(
    cfg: Config,
    agent: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> Resolution:
    """Serbest metin ajan/model/effort istegini kanonik bir secime cevir.

    Adimlar: normalize + alias -> ajani cikar -> politika -> varsayilanlari
    doldur -> kirp -> zorunlulugu denetle.
    """
    notes: list[str] = []
    model_text = (model or "").strip() or None
    effort_text = (effort or "").strip() or None
    model_explicit = model_text is not None

    # -- 1/2. Ajani belirle --------------------------------------------------
    if agent:
        key = agent.strip()
        spec = cfg.agents.get(key)
        if spec is None:
            # Ajan adi da normalize edilerek aransin ("Claude Code" -> claude)
            for name in cfg.agents:
                if normalize(name) == normalize(key):
                    key, spec = name, cfg.agents[name]
                    break
        if spec is None:
            return Resolution(
                agent=key,
                error=f"'{agent}' tanimli degil. Kullanilabilir ajanlar: {_enabled_names(cfg)}",
            )
        if not spec.enabled:
            return Resolution(
                agent=key,
                error=f"'{key}' config.toml'da devre disi. Acik olanlar: {_enabled_names(cfg)}",
            )
        agent_name = key
    elif model_text:
        hits = _agents_for_model(cfg, model_text)
        if hits:
            agent_name = hits[0]
        elif not _any_model_selection(cfg):
            # Hicbir ajanda model secimi yapilandirilmamis. Bu bir hata degil;
            # asagida istek yok sayilir ve bu durum not olarak dusulur.
            agent_name = cfg.default_agent
        else:
            return Resolution(
                agent=cfg.default_agent,
                error=(
                    f"'{model_text}' hicbir ajanin model listesinde yok.\n"
                    f"Secilebilir modeller:\n{_model_menu(cfg)}\n"
                    "Kisitli bir model istiyorsan ajani da acikca belirt."
                ),
            )
    else:
        agent_name = cfg.default_agent

    spec = cfg.agents.get(agent_name)
    if spec is None:
        return Resolution(
            agent=agent_name,
            error=f"'{agent_name}' tanimli degil. Kullanilabilir ajanlar: {_enabled_names(cfg)}",
        )

    # Model/effort secimi yapilandirilmamis ajan -> eski davranis aynen surer.
    if not spec.model_args and not spec.effort_args:
        if model_text or effort_text:
            notes.append(
                f"'{agent_name}' icin model/effort secimi yapilandirilmamis "
                "(config.toml'da model_args yok); istek yok sayildi."
            )
        return Resolution(agent=agent_name, notes=notes)

    # -- 1. Model adini kanoniklestir ---------------------------------------
    chosen: str | None = None
    if model_text:
        canon = _canon(spec, model_text)
        if canon is None or canon not in spec.known_models:
            return Resolution(
                agent=agent_name,
                error=(
                    f"'{model_text}' {agent_name} icin gecerli bir model degil.\n"
                    f"Secilebilir: {', '.join(spec.selectable_models) or '-'}"
                    + (
                        f"\nYalnizca acikca istenirse: {', '.join(spec.restricted_models)}"
                        if spec.restricted_models
                        else ""
                    )
                ),
            )
        chosen = canon

    # -- 3. Politika ---------------------------------------------------------
    if chosen and chosen in spec.blocked_models:
        return Resolution(
            agent=agent_name,
            error=(
                f"'{chosen}' devre disi ve hicbir kosulda secilemez "
                "(config.toml -> blocked_models). "
                f"Kullanilabilir: {', '.join(spec.selectable_models) or '-'}"
            ),
        )

    # -- 4. Varsayilanlari doldur -------------------------------------------
    if chosen is None:
        chosen = spec.default_model or None
        if chosen and chosen in spec.restricted_models:
            # config.py yuklemede engelliyor; burada da savunma amacli duruyor.
            return Resolution(
                agent=agent_name,
                error=(
                    f"Yapilandirma hatasi: '{agent_name}' varsayilan modeli "
                    f"'{chosen}' kisitli listede. Varsayilan doldurma kisitli bir "
                    "modele dusemez."
                ),
            )

    allowed = spec.efforts_for(chosen)

    eff: str | None = None
    if effort_text:
        eff = _canon(spec, effort_text) or normalize(effort_text)
    else:
        eff = (spec.model_effort.get(chosen or "") or spec.default_effort) or None

    # -- 5. Kirp -------------------------------------------------------------
    if eff and not allowed:
        # Bu model --effort bayragini hic kabul etmiyor (agy'de claude-*).
        if effort_text:
            notes.append(
                f"'{chosen}' --effort kabul etmiyor; istenen '{eff}' yok sayildi."
            )
        eff = None
    elif eff and eff not in allowed:
        eff, note = _clamp_effort(eff, allowed)
        if note:
            notes.append(note)

    # -- 6. Zorunlulugu denetle ---------------------------------------------
    if spec.effort_required_with_model and chosen and allowed and not eff:
        return Resolution(
            agent=agent_name,
            error=(
                f"'{agent_name}' modelle birlikte effort da istiyor ama hicbiri "
                f"cozulemedi. '{chosen}' icin gecerli seviyeler: "
                f"{', '.join(allowed)}. (Effort'suz cagri CLI tarafinda hata verir.)"
            ),
        )

    return Resolution(agent=agent_name, model=chosen, effort=eff, notes=notes)


def build_args(spec: AgentSpec, res: Resolution) -> list[str]:
    """Cozulen secimi CLI bayraklarina cevir. Deger yoksa bayrak da eklenmez."""
    out: list[str] = []
    if res.model and spec.model_args:
        out += [a.replace("{model}", res.model) for a in spec.model_args]
    if res.effort and spec.effort_args:
        out += [a.replace("{effort}", res.effort) for a in spec.effort_args]
    return out


# ---------------------------------------------------------------------------
# Karsi tarafa (Gemini'ye) anlatim
# ---------------------------------------------------------------------------


def describe_agent(spec: AgentSpec) -> list[str]:
    """`list_agents` icin model tablosu satirlari.

    Model listesi arac aciklamasindan tahmin edilmesin diye VERI olarak doner.
    """
    if not spec.model_args and not spec.effort_args:
        return ["  - model secimi: yapilandirilmamis (CLI kendi varsayilanini kullanir)"]

    out: list[str] = []
    default = spec.default_model or "(CLI varsayilani)"
    default_eff = spec.model_effort.get(spec.default_model, "") or spec.default_effort
    out.append(
        f"  - varsayilan: `{default}`"
        + (f" · effort `{default_eff}`" if default_eff else "")
    )
    if spec.selectable_models:
        out.append("  - modeller:")
        for m in spec.selectable_models:
            efforts = spec.efforts_for(m)
            eff_txt = ", ".join(efforts) if efforts else "effort YOK"
            star = spec.model_effort.get(m, "")
            out.append(
                f"    - `{m}` — effort: {eff_txt}"
                + (f" (varsayilan {star})" if star else "")
            )
    if spec.restricted_models:
        out.append(
            "  - yalnizca acikca istenirse: "
            + ", ".join(f"`{m}`" for m in spec.restricted_models)
        )
    if spec.blocked_models:
        out.append(
            "  - engelli (secilemez): " + ", ".join(f"`{m}`" for m in spec.blocked_models)
        )
    if spec.effort_required_with_model:
        out.append("  - not: bu ajanda model verilince effort da zorunlu")
    return out


def model_hint(cfg: Config) -> str:
    """`agent_run`'in `model` parametresi icin gecerli degerler (Ingilizce)."""
    parts = []
    for name, spec in cfg.agents.items():
        if not spec.enabled or not spec.selectable_models:
            continue
        parts.append(f"{name}: " + " | ".join(spec.selectable_models))
    return "; ".join(parts)


def effort_hint(cfg: Config) -> str:
    """`agent_run`'in `effort` parametresi icin gecerli degerler (Ingilizce)."""
    parts = []
    for name, spec in cfg.agents.items():
        if not spec.enabled:
            continue
        efforts = agent_efforts(spec)
        if efforts:
            parts.append(f"{name}: " + " | ".join(efforts))
    return "; ".join(parts)
