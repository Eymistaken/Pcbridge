# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Proje

pcbridge, kullanıcının Linux masaüstünü (Zorin OS / GNOME 46 / Wayland) uzaktan
sürülebilir hale getiren kişisel bir MCP sunucusu. 33 araç: kodlama ajanlarına
iş verme, arka plan işleri, tmux, kabuk/dosya, ve `[desktop]` altında sanal
klavye/fare + ekran okuma.

**İki taşıma, tek sunucu:** stdio (`--stdio`; Claude Code, Codex, Claude
Desktop — **ağ yok, OAuth yok**, sunucuyu istemci başlatır) ve HTTP (telefon /
başka makine — HTTPS + OAuth 2.1 + Tailscale Funnel, isteğe bağlı). Kurulum
komutlarını `./connect.sh` üretir.

Proje Gemini Spark için başlamıştı; **artık hedef değil.** Mimarinin
"görüntü yerine metin" tercihleri (`ui_dump`, `/shot` bağlantıları) o çağdan
kalma ve **kazanç oldukları için duruyorlar** — daha ucuz ve ıskalamıyorlar.

**Aktif çalışma yönergesi [YAPILACAKLAR.md](YAPILACAKLAR.md).** Görev listesi,
onaylanmış kararlar ve son ölçümler orada; bir işe başlamadan önce oku.

## Komutlar

```bash
systemctl --user restart pcbridge      # kod degistiyse SART -- CALISAN ISLERI OLDURUR
journalctl --user -u pcbridge -f       # canli log
./doctor.sh                            # 35 baslikta tani
./run.sh                               # on planda calistir (hata ayiklama)
./run.sh --check                       # yalnizca config'i dogrula ve cik
```

Testler (hepsi düz betik; `.venv`'de pytest **kurulu değil**):

```bash
./.venv/bin/python tests/test_models.py     # cozumleyici + ajan cikti ayristiricilari, sunucu gerekmez
./.venv/bin/python tests/test_desktop.py    # masaustu, 312 kontrol, GIRDI GONDERMEZ
```

Gerçek cihazlarla masaüstü testleri (uinput'a yazar, AT-SPI okur) — varsayılan
olarak atlanırlar:

```bash
PCBRIDGE_TEST_CAPTURE=1 PCBRIDGE_TEST_ATSPI=1 PCBRIDGE_TEST_BATCH=1 \
  ./.venv/bin/python tests/test_desktop.py
```

Uçtan uca (sunucu ayakta olmalı; parola verilmezse OAuth adımları 401 döner ve
testin bozulduğunu sanırsın):

```bash
export PCBRIDGE_TEST_PASSWORD="$(./.venv/bin/python -c 'import sys; sys.path.insert(0,"."); from pcbridge.config import load_config; print(load_config().password)')"
export PCBRIDGE_TEST_STATIC="$(./.venv/bin/python -c 'import sys; sys.path.insert(0,"."); from pcbridge.config import load_config; print(load_config().static_token or "")')"
./.venv/bin/python tests/test_e2e.py        # 189 gecer + 4 ATLA
```

Tek bir kontrolü koşturmak için dosyalar pytest ile toplanabilir yazıldı:
`./.venv/bin/pip install pytest && ./.venv/bin/python -m pytest tests/test_desktop.py -k monitor -q`.

Masaüstünü elle sürmek (MCP'den bağımsız kabuklar):

```bash
./bin/pcb-shot --monitor 2               # PNG yazar + global ofseti soyler -> Read ile BAK
./bin/pcb-do --dry-run '<json>'          # ayristirir, calistirmaz
./.venv/bin/python -m pcbridge.cli.lock  # masaustu iznini kapat
```

Yerel istemci kaydı: `./connect.sh` (yazdırır) / `./connect.sh --apply` (yapar).
Uzaktan erişim: `bridgeac` / `bridgekapat` / `bridgedurum`
(= `./remote.sh start|stop|status`). Masaüstü izni acil kapatma: `bridgekilit`.
**`bridgekapat` stdio istemcilerini durdurmaz** — onlar sunucuyu kendileri
başlatıyor.

**Servis açılışta başlıyor** (`systemctl --user enable`), ama **tünel
başlamıyor**. Ayrım bilinçli: servis yalnızca `127.0.0.1`'i dinliyor, makineyi
internete açan şey tünel. Ayrıca stdio istemcileri servisi hiç kullanmıyor —
sunucuyu kendileri başlatıyor, servis kapalıyken de çalışırlar.

## Mimari

`server.py` → `build_app()` FastMCP örneğini kurar, `tools.register()` bütün
araçları yazar, iki ASGI ara katmanı ve `/consent`, `/healthz`, `/shot/<token>`
rotaları eklenir. Katmanlar:

| Katman | Dosya | Rol |
|---|---|---|
| Yapılandırma | `config.py` | TOML → dataclass; `_check_agents()` model/effort tuzaklarını **yüklemede** yakalayıp servisi durdurur |
| Kimlik | `auth.py` | Tam bir mini OAuth 2.1 sunucusu (DCR + PKCE + refresh) + onay sayfası; SQLite |
| Araçlar | `tools.py` | 33 MCP aracının tamamı. Yeni araç **buraya** yazılır |
| İşler | `jobs.py` | Arka plan süreçleri + ajan çıktı ayrıştırıcıları (`plain`, `claude_stream_json`, `agy_json`) |
| Çözümleyici | `models.py` | Ajan/model/effort seçimi. **Saf fonksiyon**, I/O yok; kurallar config'de |
| Masaüstü | `desktop/` | GUI katmanı, aşağıda |
| CLI kabukları | `cli/`, `bin/` | `pcb-shot` / `pcb-do` — MCP'den bağımsız, ajan Bash'ten çağırır |

**Ajan çağrıları senkron değil.** `agent_run` bir `job_id` döner; Claude Code
bir görevde 10 dakika harcayabilir ve hiçbir MCP çağrısı 110 saniyeden uzun
bloklayamaz. Uzun her şey `jm.start()` ile arka plana gider.

**Ajan tanımları koda değil `config.toml`'a yazılır.** `[agents.*]` blokları
komutu, resume sözdizimini, parser'ı, `pty`yi ve model/effort politikasını
taşır; yeni bir CLI eklemek için Python dosyasına dokunulmaz.

### `desktop/` katmanı

```
monitors.py   monitor tablosu -- KOORDINAT UZAYININ TEK KAYNAGI
input.py      uinput sanal klavye + mutlak fare
capture.py    ekran goruntusu: yakala -> monitor basina KIRP -> sonra olcekle
uitree.py     erisilebilirlik agaci -> metin, kararli #id'ler
atspi_helper.py  AT-SPI yardimcisi: SISTEM python3'u, ayri surec, JSON protokolu
apps.py       uygulama baslatma, pencere one alma
batch.py      toplu eylem motoru -- `Ops` protokolu uzerinden, MCP'yi TANIMAZ
ops.py        `Ops`un gercek cihazlara baglanan uygulamasi
safety.py     GUVENLIK KAPISI -- her GUI araci buradan gecer
```

İki tasarım kararı ısrarla korunuyor:

- **Bütün iç API global tuval koordinatı kullanır** (bu makinede 0–3839 ×
  0–1079). `monitor=` ofseti yalnızca `monitors.to_global()` içinde eklenir ve
  yalnızca `tools.py` sınırında çağrılır. İki yerde yapılırsa biri unutulur ve
  **sessizce 1920 piksel sola tıklanır** — hata hiçbir yerde görünmez.
- **`batch.py` gerçek cihazları tanımaz.** Bağımlılık tek yönlü (`ops` →
  `batch`), böylece bütçe/durma mantığı gerçek tıklama göndermeden test
  edilebiliyor ve aynı uygulama hem `computer_batch` hem `bin/pcb-do`
  tarafından paylaşılıyor.

### `safety.SafetyGate` — beş kat

`[desktop] enabled` → ekran kilidi (`org.gnome.ScreenSaver`) → süreli izin
(`desktop_unlock`, durum **diskte**) → çakışma koruması (`Mutter.IdleMonitor`)
→ hız sınırı + denetim kaydı. Reddin gerekçesi kullanıcıya **aynen** döner,
o yüzden gerekçe ne yapılacağını söylesin.

`SafetyGate.audit()` yalnızca masaüstüne ait değil: `shell_run`, `agent_run`,
`fs_*`, `tmux_send` de buraya yazıyor. Kural: **ne yapıldığı yazılır, İÇERİK
yazılmaz** — komut evet çıktısı hayır, dosya yolu evet içeriği hayır, metin
uzunluğu evet metnin kendisi hayır.

## Değişmez kurallar

**MCP araçları**

- Docstring ve `Field(description=…)` **İngilizce** — istemci araç seçerken
  yalnızca bunları okuyor. Kullanıcıya dönen metinler Türkçe.
- Docstring "ne zaman kullanılır"ı söylesin, sadece "ne yapar"ı değil.
- Dönüş tipi `str`, çıktı `jobslib.tail_chars(metin, 4000)` ile kırpılmış.
  Görüntü de dönüyorsa tip **`list[ContentBlock]`** (çıplak `-> list` FastMCP'ye
  outputSchema ürettirir ve çağrı patlar).
- `readOnlyHint` / `destructiveHint` doğru işaretlensin (yanlış `readOnlyHint`
  tehlikeli bir aracı sessizce çalıştırır).
- **110 saniyeden uzun bloklama yok.**
- Yol parametreleri `_resolve_dir` / `_resolve_file` ile çözülsün.

**Dokunma**

- `server.py`'deki `MetadataNormalizer` (RFC 8414 issuer'ında sondaki eğik
  çizgi) ve `BasicAuthFormShim` (SDK `client_id`'yi form gövdesinde arıyor,
  Google yalnızca Basic başlığında gönderiyor) — Google OAuth akışının
  çalışmasının tek sebebi bunlar. Diğer istemcilerde etkisizler, **dursunlar.**
- `auth.py`'ın OAuth mantığı — görev açıkça istemedikçe.
- `config.toml`'daki `[agents.antigravity]` bloğu — `agent_run` hedefi olarak
  çalışıyor.

**Güvenlik**

- `config.toml` parola ve statik token içeriyor; `.gitignore`'da ve öyle
  kalacak. İçeriğini **loglama, ekrana basma, commit etme.**
- `[desktop] enabled` varsayılanı `false`. **Değiştirme.**
- Her yeni ayar `config.example.toml`'a **yorumuyla** eklenir ve
  `config.py`'de fiilen **okunur** (bir kere atlandı: alanlar tanımlıydı,
  config'e yazılan değer hiçbir şey yapmıyordu).
- Sudo isteyen kurulum adımları kullanıcıya söylenerek yapılır.

**Etrafından dolaşma:** X11'e geçmek, `--dangerously-skip-permissions`'ı
kaldırmak, sırları loga basmak, `[desktop] enabled`'ı varsayılan açık yapmak —
bunlar çözüm değil. Planla çelişen bir gerçekle karşılaşırsan **uydurma**:
kullanıcıya söyle, gerekçesiyle düzelt, sonra devam et.

## Ölçülmüş makine gerçekleri (tahmin etme)

Bu projede "hata vermedi" kanıt sayılmıyor. Aşağıdakiler fiilen ölçüldü:

- **Zorin OS 18.1 = Ubuntu 24.04 + GNOME Shell 46, Wayland.** X11 seçenek değil.
- İki monitör, 1920×1080, ölçek 1.0. Numaralandırma **x konumuna göre soldan
  sağa**: DP-2 (x=0) → `monitor=1`, DP-1 (x=1920, **birincil**) → `monitor=2`.
  GNOME üst çubuğu sağdaki monitörde.
- **Klavye düzeni `tr+intl`.** uinput ham keycode gönderir → ASCII bozulur.
  Metin girişinin varsayılan yolu **`wl-copy` + Ctrl+V**. Tuş
  *kombinasyonları* (Return, ctrl+v, oklar) keycode düzeyinde düzenden bağımsız.
- **`wl-copy` `capture_output=True` ile asılır** — panonun sahibi olarak arka
  planda yaşamaya devam eder, borular EOF vermez. Yazma yolunda `DEVNULL` şart.
- **udev kural dosyasının numarası işlevsel.** `uaccess` builtin'i
  `73-seat-late.rules`'ta; kendi kuralın 73'ten **önce** gelmeli — bu yüzden
  `60-pcbridge-uinput.rules`.
- **Mutlak fare cihazına `BTN_TOUCH`/`BTN_TOOL_PEN` eklenmemeli** — cihazı
  dokunmatik ekran yapar, kompozitör tek çıkışa bağlar, ikinci monitöre
  ulaşamazsın. `ABS_X + ABS_Y + BTN_LEFT` tüm tuvale eşleniyor (sapma ≤1 px).
- **`Shell.Introspect` kapalı** (GNOME 46, "Access denied"). Pencere listesi ve
  odak yalnızca AT-SPI'dan.
- **AT-SPI `get_extents` koordinatları yanlış.** Tıklama `Action.do_action` ile
  yapılır; `Action` yoksa koordinata **düşülmez**, açıkça hata dönülür.
- **AT-SPI Electron'un penceresini görür, içini görmez.** Vesktop'ta `ui_dump`
  0 düğüm — orada tek yol görüntü.
- **uinput olayı `IdleMonitor`'ü sıfırlıyor** (104227 ms → 151 ms). "Kullanıcı
  makinede mi" kontrolü bir eylem dizisinin **içinde** yapılamaz; yalnızca dizi
  veya görev başında.
- **GNOME overview açıkken Wayland panosu bloklanıyor** — `super` sonrası gelen
  `type` eylemleri kendiliğinden ham tuş yoluna geçer (`_auto_raw`).
- **Pencere öne alma: AT-SPI ve D-Bus yolları kapalı.** Çalışan tek yol GNOME
  araması (`super` + ad + `Return`), ~6,5 saniye.
- **`systemctl --user stop/restart pcbridge` çalışan işleri de öldürür.**
  `start_new_session` oturum grubunu ayırıyor ama cgroup'u değil. Yani acil
  durdurma gerçekten çalışıyor **ve** restart uzun bir ajan işini keser —
  restart'tan önce `job_list`.
- **`[desktop] enabled = false` yalnızca masaüstü araçlarını kapatır.**
  `shell_run`, `agent_run`, `fs_*`, `tmux_*` bu kapıdan geçmez. Bilinçli:
  koruma engelleme değil, `audit.log`'a iz bırakma.
- **Ekran görüntüsünün maliyeti sürücüye göre 20–30 kat değişiyor** — `agy`'de
  tek görüntü ~40 bin girdi jetonu, **Claude'da ~1200–1900**. `ui_dump` yine de
  daha ucuz (~0,1 sn, birkaç yüz jeton) ve koordinat kullanmadığı için
  **ıskalayamaz**. GTK'da metin yolu tercih edilir; görüntü, ağacın boş geldiği
  yerler için yedek.
- **Claude Code araç sonucundaki görüntüyü gerçekten okuyor** (ölçüldü: bilinen
  içerikli PNG'deki gizli değer birebir geri geldi). Anthropic API uzun kenarı
  1568'e indirdiği için `screenshot_scale_long_edge = 1280` korunuyor — o
  sınırın altında modelin gördüğü piksel ile raporlanan ölçek aynı kalıyor.
- **stdio'da hiçbir `@mcp.custom_route` rotası yok** (HTTP sunucusu yok):
  `/shot/<token>.png`, `/healthz`, `/consent`, `/.well-known/*`. `screen_capture`
  orada bağlantı yerine dosya yolu döner. OAuth'u da fastmcp kendisi atlıyor.
- **Görüntü dönen araçlarda dönüş tipi `list[ContentBlock]` olmalı.** Çıplak
  `-> list` yazılırsa FastMCP outputSchema üretir ve çağrı
  `"outputSchema defined but no structured output returned"` ile patlar.
- **venv'deki `pcbridge.pth` repo yolunu `sys.path`'e ekliyor**, bu yüzden
  `python -m pcbridge.server` herhangi bir dizinden çalışır; istemci kayıtları
  `cwd` istemiyor.

## ⚠️ Bu makinede test etmenin tehlikesi

**Bu depoda çalışırken pcbridge'in kontrol edeceği makinenin üzerindesin.**
uinput tıklaması/tuşu odaktan bağımsız gider — bir `type` testi senin
terminaline yazabilir ve Enter'a basabilir.

1. Girdi testini önce boş bir pencerede yap (`gnome-text-editor`).
2. Fare testinde önce `mousemove`, sonra ekran görüntüsüyle konumu **doğrula**,
   ancak ondan sonra `click`.
3. Acil durdurma: `systemctl --user stop pcbridge` (sanal cihazlar sürecin
   içinde yaşıyor, ayrı daemon yok).
4. Uzun/tekrarlı girdi denemelerini kullanıcıya haber vermeden başlatma.
5. Tıklamadan sonra **odağın kaydığını varsay.**
6. **Ekran görüntün bayatlar.** Koordinat çıkardıktan sonra araya iş sokma
   (`[desktop] agent_shot_max_age_seconds`, varsayılan 60 sn).

İki gerçek kaza da aynı kökten geldi: doğrulanmamış bir varsayıma göre
tıklamak. 2026-08-02'de masaüstündeki 23 öğe çöpe gitti; 2026-08-03'te 69
saniye bayatlamış bir görüntüye göre yapılan tıklama başka uygulamaya düştü.
Her ikisinin karşılığı koda girdi (`batch_check_focus`, yaş kontrolü) — koruma
hasarı sınırlamak için, hatayı sıfırlamıyor.

## Yeni araç / yeni ajan ekleme

**Araç:** `tools.py` → `register()` içine yaz → ayar gerekiyorsa `config.py` +
`config.example.toml` (ikisi birlikte) → `tests/test_e2e.py`'ye kontrol →
`systemctl --user restart pcbridge` → test et. GUI aracıysa `_guard()`/
`SafetyGate.check()`'ten geçmeli ve `gate.audit(...)` ile kaydedilmeli.

Gemini tarafında ek bir adım var: **servisi yeniden başlatmak yetmez**, Gemini
araç listesini önbelleğe alıyor (Connected Apps → senkronizasyonu yenile;
görünmezse uygulamayı kaldır–ekle).

**Ajan:** yalnızca `config.toml`. Önce elle dene: `codex exec "merhaba" | cat`
— boru sonunda hiçbir şey çıkmıyorsa CLI TTY istiyor demektir, `pty = true`.
Model kimliğini **tahmin etme, CLI'a sor** (`agy models`); tahminlerin hepsi
yanlış çıktı. Effort ajan geneli olmayabilir — `model_efforts` ile ifade et,
boş liste "bayrağı hiç ekleme" demektir. systemd birimine `ANTHROPIC_MODEL` /
`CLAUDE_CODE_EFFORT_LEVEL` **ekleme**: işler `os.environ.copy()` ile başlıyor
ve o değişken `--effort` bayrağını sessizce etkisiz kılıyor.

## Belge haritası

| Dosya | Ne |
|---|---|
| `YAPILACAKLAR.md` | **Aktif çalışma yönergesi** — görev listesi, kararlar, son ölçümler |
| `KULLANIM.md` | Kullanıcıya dönük araç kataloğu + izin haritası — **güncel tutulmalı** |
| `config.example.toml` | Ayarların belgelenmiş hâli; projenin asıl referansı |
| `README.md` | Kurulum, istemcilere bağlanma, güvenlik değerlendirmesi, sorun giderme |
| `GELISTIRME.md` | Yeni araç eklemenin uzun anlatımı + protokol tuzakları — geçmiş kayıt |
| `PLAN.md`, `UYGULAMA.md` | Geçmiş kayıt: neyin neden böyle yapıldığı. Okuma zorunlu değil, **silme** |

## Çalışma tarzı

- Kendi görev listeni çıkar, kullanıcıya onaylat, sonra başla.
- **Her adımdan sonra fiilen test et.** Çalıştığını görmeden sonrakine geçme.
- Ölçmediğin şeyi "çalışıyor" diye yazma; `IdleMonitor`, ekran görüntüsü veya
  pencere başlığıyla doğrula.
- Bölüm bitince commit at.
- Dosya düzenleme komutu önerirken `nano` kullanma; kullanıcının `edit` takma
  adı var (`gnome-text-editor`), root için `edit admin:///yol`.
