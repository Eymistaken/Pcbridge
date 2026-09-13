# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Proje

pcbridge, kullanıcının Linux masaüstünü (Zorin OS / GNOME 46 / Wayland) uzaktan
sürülebilir hale getiren kişisel bir MCP sunucusu. 34 araç: kodlama ajanlarına
iş verme, arka plan işleri, tmux, kabuk/dosya, ve `[desktop]` altında sanal
klavye/fare + ekran okuma.

**İki taşıma, tek sunucu:** stdio (`--stdio`; Claude Code, Codex, Claude
Desktop — **ağ yok, OAuth yok**, sunucuyu istemci başlatır) ve HTTP (telefon /
başka makine — HTTPS + OAuth 2.1 + Tailscale Funnel, isteğe bağlı). Kurulum
komutlarını `./connect.sh` üretir.

Proje Gemini Spark için başlamıştı; **artık hedef değil.** Mimarinin
"görüntü yerine metin" tercihleri (`ui_dump`, `/shot` bağlantıları) o çağdan
kalma ve **kazanç oldukları için duruyorlar** — daha ucuz ve ıskalamıyorlar.

**Sıradaki iş [WALKTHROUGH.md](WALKTHROUGH.md).** Depodaki tek yapılacak-iş
listesi odur: yol haritası, her adımın kabul ölçütü ve şimdiye kadar ne
yapıldığının kaydı. Native migration'ın implementation sözleşmesi ayrı bir
dosyada: [PLAN.md](PLAN.md). **Durum özetini başka dosyaya kopyalama.**

`gnome-extension/` altında isteğe bağlı bir **GNOME 46 kabuk eklentisi** var:
masaüstü izni açıkken her monitörün kenarlarında yumuşak beyaz bir çerçeve
gösteriyor ve açık pencereyi öne alan tek, dar `ActivateWindow` D-Bus
yöntemini sunuyor. Grant dosyasını yalnızca **okuyor**; listeleme, taşıma,
kapatma ve boyutlandırma sunmuyor.

Ölçülmüş makine gerçekleri **bu dosyada**, aşağıda. Faz H/I/J'nin sonuçları ve
neyin neden böyle yapıldığı `PLAN.md` 9b–9d bölümlerinde.

## Komutlar

```bash
systemctl --user restart pcbridge      # kod degistiyse SART -- CALISAN ISLERI OLDURUR
journalctl --user -u pcbridge -f       # canli log
./doctor.sh                            # 35 baslikta tani
./run.sh                               # on planda calistir (hata ayiklama)
./run.sh --check                       # yalnizca config'i dogrula ve cik
scripts/build-native.sh                # native yardimci: release derle, pcbridge/_native'e kur (istege bagli)
```

Testler (hepsi düz betik; `.venv`'de pytest **kurulu değil**):

```bash
./.venv/bin/python tests/test_models.py     # cozumleyici + ajan cikti ayristiricilari, sunucu gerekmez
./.venv/bin/python tests/test_desktop.py    # masaustu, GIRDI GONDERMEZ
./.venv/bin/python tests/test_test_safety.py # live-test secici guvenligi
```

Gerçek testler varsayılan olarak atlanır. Yalnızca ekran yakalamayı açmak için:

```bash
PCBRIDGE_TEST_CAPTURE=1 ./.venv/bin/python tests/test_desktop.py
```

Bu seçim ekranı diske yazar ve kısa süreliğine ekran yayını açar (üst çubukta
paylaşım göstergesi belirir), fakat uinput aygıtı açmaz. Gerçek uinput testleri
ayrı izin ister:

```bash
PCBRIDGE_TEST_INPUT=1 ./.venv/bin/python tests/test_desktop.py
```

Gerçek batch için iki izin birlikte gerekir; `PCBRIDGE_TEST_BATCH=1` tek başına
yeterli değildir:

```bash
PCBRIDGE_TEST_INPUT=1 PCBRIDGE_TEST_BATCH=1 \
  ./.venv/bin/python tests/test_desktop.py
```

AT-SPI gerçek okuma testleri ayrıca `PCBRIDGE_TEST_ATSPI=1` ister. Bütün live
testleri bilinçli olarak birlikte çalıştırmak için dört bayrağı da açıkça ver:

```bash
PCBRIDGE_TEST_CAPTURE=1 PCBRIDGE_TEST_INPUT=1 \
PCBRIDGE_TEST_ATSPI=1 PCBRIDGE_TEST_BATCH=1 \
  ./.venv/bin/python tests/test_desktop.py
```

Uçtan uca (sunucu ayakta olmalı; parola verilmezse OAuth adımları 401 döner ve
testin bozulduğunu sanırsın):

**⚠️ `test_e2e.py` 12. bölüm GERÇEK bir `claude -p` çalıştırır ve KOTA YAKAR.**
Bir kere kullanıcının günlük limitini bitirdi (2026-08-03). Günlük koşumda
`PCBRIDGE_TEST_NO_AGENT=1` verin; ajan kapsamını `test_models.py` 11. bölüm
sunucusuz olarak zaten kontrol ediyor.

```bash
export PCBRIDGE_TEST_PASSWORD="$(./.venv/bin/python -c 'import sys; sys.path.insert(0,"."); from pcbridge.config import load_config; print(load_config().password)')"
export PCBRIDGE_TEST_STATIC="$(./.venv/bin/python -c 'import sys; sys.path.insert(0,"."); from pcbridge.config import load_config; print(load_config().static_token or "")')"
PCBRIDGE_TEST_NO_AGENT=1 ./.venv/bin/python tests/test_e2e.py   # 231 gecer + 9 ATLA
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
(= `./remote.sh start|stop|status`). Masaüstü izni acil kapatma: `bridgekilit` — izni kapatır **ve** hangi süreç
açmış olursa olsun ekran yayınını durdurur.
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
| Araçlar | `tools.py` | 34 MCP aracının tamamı. Yeni araç **buraya** yazılır |
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
capture.py    ekran goruntusu: iki backend -- yayin (sessiz) / gnome-screenshot
              + cekim kaydi (`<id>.json`) ve KOORDINAT DONUSUMUNUN TEK GECIDI
screencast.py PipeWire ekran yayini; yardimci sureci surer, omrunu yonetir
screencast_helper.py  yayin + kare: SISTEM python3 (gi/Gst yok venv'de), KALICI
uitree.py     erisilebilirlik agaci -> metin, kararli #id'ler
atspi_helper.py  AT-SPI yardimcisi: SISTEM python3'u, ayri surec, JSON protokolu
apps.py       uygulama baslatma, pencere one alma
batch.py      toplu eylem motoru -- `Ops` protokolu uzerinden, MCP'yi TANIMAZ
ops.py        `Ops`un gercek cihazlara baglanan uygulamasi
safety.py     GUVENLIK KAPISI -- her GUI araci buradan gecer
```

İki tasarım kararı ısrarla korunuyor:

- **Bütün iç API global tuval koordinatı kullanır** (bu makinede 0–3839 ×
  0–1079). Dışarıdan gelen koordinatın hangi uzayda olduğunu çözen **tek yer**
  `capture.to_global()`: `monitor=` verilmişse monitör ofsetini ekler (tam
  çözünürlük), `shot=` verilmişse o çekimin ofsetini **ve ölçeğini** uygular,
  ikisi de yoksa koordinat zaten globaldir. `tools.py` ve `ops.py` yalnızca
  oraya dizin listesini bağlar; ikisi birlikte verilirse çağrı reddedilir.
  Dönüşüm iki yerde yapılırsa biri unutulur ve **sessizce 1920 piksel sola
  tıklanır** — hata hiçbir yerde görünmez.
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
- Düz metin dönüş tipi `str`; uzun çıktı `jobslib.tail_chars(metin, 4000)` ile
  kırpılmış. Görüntü de dönüyorsa başarı tipi `list[ContentBlock]`.
- Dinamik masaüstü araçlarında `@mcp.tool(output_schema=None, ...)` açıkça
  yazılır. Masaüstü execution hataları okunabilir eski metni `content` içinde,
  kararlı alanları `structuredContent.error` içinde taşıyan
  `ToolResult(..., is_error=True)` döndürür. Scope, Pcbridge izni için
  `pcbridge.desktop`; işletim sistemi izinleri için `os.capture`, `os.pointer`,
  `os.keyboard`, `os.accessibility`, `os.window` veya `os.session` olur.
- FastMCP `3.4.5` sürümüne sabittir. `ToolResult` ve `output_schema=None`
  davranışını doğrulamadan sürümü değiştirme.
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
- **İmleç ışınlanmıyor, ara noktalardan geçiyor** (`input.move_path`). Ölçüldü:
  48 adımlık bir hareketin **48 ABS_X + 48 ABS_Y olayının tamamı** cihazın kendi
  event node'undan okundu, `SYN_DROPPED` yok — kernel ara noktaları
  birleştirmiyor. `time.sleep(0.008)` fiilen 8,07 ms (sapma +0,08 ms), yani adım
  aralığı öngörülebilir. Gerçek süreler: 960 px → 186 ms, köşegen → 498 ms
  (tavan), 80 px → 61 ms (taban). `pointer_speed = 0` eski ışınlamayı geri
  getirir; `drag` bundan **bağımsız** olarak ara nokta üretir.
  Ölçerken tuzak: olayları hareket boyunca **paralel okumazsan** evdev istemci
  kuyruğu taşar ve 96 olayın 11'i görünür — sayım yanlış çıkar, kod değil.
- **Son imleç konumu DİSKE yazılıyor** (`state_dir/pointer.json`), çünkü
  `pcb-do`'nun her çağrısı yeni bir süreç. Yazılmazsa her `pcb-do` hareketi
  ışınlanır — **gerçekten yaşandı, kullanıcı fark etti.** İlk düzeltme de
  yetmedi: `_pointer()` cihazı açtıktan sonra `_pos = None` yapıyordu ve
  `move()` ilk iş cihazı açıyor, yani diskten okunan konum hemen siliniyordu.
  Cihaz yaratmak imleci oynatmaz; o satırlar artık `_read_pos()` çağırıyor.
- **İlk hareket yine de sıçrayabilir.** Wayland'de imlecin gerçek konumu
  dışarıdan sorulamıyor: disk kaydı yoksa, 5 dakikadan eskiyse ya da kullanıcı
  arada fareyi eliyle oynattıysa başlangıç yanlış bilinir. En kötü ihtimalle
  bir sıçrama olur, hedef yine doğrudur.
- **Basılı tutulan tuş `hold_max_seconds` sonunda kendiliğinden bırakılır.**
  `release` unutulursa makine kullanılamaz hale gelir ve **ajanın bunu göreceği
  bir kanal yok** — kendi gönderdiği tuşun hâlâ basılı olduğunu soramaz. Tembel
  kontrol (bir sonraki çağrıda bak) yetmez: bir sonraki çağrı hiç gelmeyebilir.
  Cihaz yok edilince kernel'in basılı tuşları bırakıp bırakmadığı **ölçülemedi**
  (destroy ile event node da kayboluyor), bu yüzden `close()` önce açıkça
  bırakıyor.
- **`Shell.Introspect` kapalı** (GNOME 46, "Access denied"). Pencere listesi ve
  odak yalnızca AT-SPI'dan.
- **Connector adları KARARLI DEĞİL.** Ölçüldü 2026-09-12: geometri hiç
  değişmeden `DP-2`/`DP-1` iken `DP-4`/`DP-3` oldu; hem Mutter D-Bus hem
  `xrandr --listmonitors` aynı şeyi söyledi. Sıra ve numaralandırma
  etkilenmedi (soldan sağa, x=0 → `monitor=1`, x=1920 birincil → `monitor=2`),
  ama `monitor="DP-1"` gibi **ada göre seçim artık bu makinede çözülmüyor**.
  Bu yüzden `topology_id` connector adını içermiyor; kalıcı kimlik için
  monitörün `serial` alanı var (bu makinedeki iki panel aynı model, yalnızca
  seri numarası ayırıyor).
- **Ekran düzeni kimliği tek yerde: `monitors.topology_id()`.** Kanonik dize,
  hash değil — çarpışma yok ve Rust tarafıyla birebir karşılaştırılabiliyor.
  `transform` içinde, çünkü 180 derece dönüş genişlik/yüksekliği değiştirmez
  ama koordinat eşlemesini değiştirir.
- **Mantıksal boyut yarım pikselde SIFIRDAN UZAĞA yuvarlanır** (960,5 → 961).
  Python'ın yerleşik `round()`'u bankacı yuvarlaması yapar ve 960 verirdi,
  Rust'ın `f64::round()`'u vermez; kural iki tarafta da açıkça yazıldı yoksa
  kesirli ölçekte bir piksel sessizce ayrışırdı. **Mutter'ın kendi yarım-sınır
  davranışı ÖLÇÜLMEDİ** — bu makinede iki monitör de ölçek 1.0, bölme her
  zaman tam.
- **AT-SPI'da parola alanının rolü `password text` ve başka işaret yok.**
  Ölçüldü 2026-09-12: `Atspi.role_get_name(Atspi.Role.PASSWORD_TEXT)` →
  `'password text'`; adında "password" geçen **tek** rol bu ve paroloya özel
  bir `StateType` **yok** (`SENSITIVE` ilgisiz — "etkin" demek). Yani kapı
  role bakmak zorunda. Alan `ui_dump`'ta görünüyor, çünkü liste `editable`
  düğümleri alıyor: ajan kimliği alabiliyor, o yüzden kapı gerekli.
- **AT-SPI `get_extents` koordinatları yanlış.** Tıklama `Action.do_action` ile
  yapılır; `Action` yoksa koordinata **düşülmez**, açıkça hata dönülür.
- **AT-SPI Electron'un penceresini görür, içini görmez.** Vesktop'ta `ui_dump`
  0 düğüm — orada tek yol görüntü.
- **uinput olayı `IdleMonitor`'ü sıfırlıyor** (104227 ms → 151 ms). "Kullanıcı
  makinede mi" kontrolü bir eylem dizisinin **içinde** yapılamaz; yalnızca dizi
  veya görev başında.
- **GNOME overview açıkken Wayland panosu bloklanıyor** — `super` sonrası gelen
  `type` eylemleri kendiliğinden ham tuş yoluna geçer (`_auto_raw`).
- **Pencere öne alma: dar GNOME eklentisi + arama yedeği.** GERÇEK oturumda
  ölçüldü 2026-09-12, `batch_step.ms`: fiilen odak değiştiren dört çağrı
  **5, 5, 5, 6 ms** (ortalama 5,2); günün on focus adımının tamamı 3–6 ms,
  ortalama **4,4 ms**. Taban çizgisi 2026-09-02'de altı çağrıda ortalama
  **6701,3 ms** idi — aynı ölçüm noktası, **~1500 kat**. Nested kabuk 6,8 ms
  göstermişti, yani nested burada abartmış.
  Eklenti yoksa veya hedef kapalıysa GNOME araması (`super` + ad + `Return`)
  aynen kalır.
- **Eklentinin `ActivateWindow`'u grant'i her çağrıda yeniden okuyor ve
  `until`'e bakması yeterli.** Ölçüldü: izin kapalıyken gerçek oturumda
  `b false` döndü ve hiçbir pencere etkinleşmedi. `until` tek başına güvenli,
  çünkü `lease.revoke()` hem `until` hem `hard_until`'ı sıfırlıyor ve
  `touch()` `until = min(hard_until, …)` tutuyor — yani `until > now`
  Python'ın kapısından daha geniş olamaz.
- **Eklenti `skip-taskbar` pencerelerini hedef saymıyor ve belirsiz adı
  reddediyor.** Ölçüldü: `Desktop Icons 1` (masaüstü arka plan penceresi) →
  `false`; iki pencereye birden uyan `Desktop Icons` → `false`; olmayan hedef
  → `false`. Üçünde de pcbridge arama yedeğine düşer.
- **`systemctl --user stop/restart pcbridge` çalışan işleri de öldürür.**
  `start_new_session` oturum grubunu ayırıyor ama cgroup'u değil. Yani acil
  durdurma gerçekten çalışıyor **ve** restart uzun bir ajan işini keser —
  restart'tan önce `job_list`.
- **`[desktop] enabled = false` yalnızca masaüstü araçlarını kapatır.**
  `shell_run`, `agent_run`, `fs_*`, `tmux_*` bu kapıdan geçmez. Bilinçli:
  koruma engelleme değil, `audit.log`'a iz bırakma.
- **Shell ve desktop ayrı execution yollarıdır.** Yeni bir GUI süreci shell'de
  başlarsa pcbridge service ömrünü paylaşır; süreç desktop'a ait ve sonradan
  bulunabilir kalacaksa `window_focus` kullanılır. Çalışan Chrome'a URL vermek
  gibi deterministik handoff komutları shell'in normal kullanımındadır.
  `block_gui_launch_in_shell` varsayılanı `false`; kullanıcı açıkça `true`
  yapmışsa ve blocklist eşleşirse eski engel davranışı korunur.
- **Permission hatasında görev ve mevcut kapsam korunur.** Başka execution
  yoluna geçmek daha geniş desktop izni istemek anlamına gelmez;
  `system_capabilities` ile zaten kullanılabilir olan yol seçilir.
- **Ekran görüntüsü artık sessiz alınıyor: Mutter ScreenCast (PipeWire).**
  `gnome-screenshot` her çekimde **beyaz flaş + ses** çıkarıyor ve flaşı kendi
  çiziyor (ikilikte `cheese_flash_fire`), yani `flash=false` geçirmek çözmezdi.
  XDG portal da flaş patlatıyor. `org.gnome.Shell.Screenshot` D-Bus arayüzü
  **"Access denied"** — GNOME 46 çağıranı süzüyor. Ekran *paylaşımı* yolunda
  flaş yok: sistem bunu fotoğraf değil video sayıyor.
  Ölçüldü: 833 ms (gnome-screenshot) · 497 ms (portal) · **240 ms (yayın)**.
  Uçtan uca fark daha küçük (2,5 sn → 1,5 sn); kalanı Pillow'da, iki yolda da
  aynı. **Asıl kazanç sessizlik, hız ikincil.**
  Kayıp yok: yayın çıktısı ile `gnome-screenshot`'ın aynı bölgesi **%99,8
  birebir aynı**.
- **Yayını kapatmak, onu AÇAN süreci bulmayı gerektiriyordu.** Yardımcı
  süreç (`screencast_helper.py`) onu başlatan sürecin `Popen` tutamağında
  yaşıyor, yani `ScreenCast.close()` yalnızca **kendi** yayınını kapatabilir.
  Bu bir boşluk bırakmıştı: `bridgekilit` (`cli.lock`) ayrı bir süreç, izin
  dosyasını kapatıyor ama başka bir sürecin yayınına dokunamıyordu. Aynısı
  telefondan gelen `desktop_lock` için de geçerliydi — servis kendi yayınını
  kapatır, aynı anda çalışan bir `--stdio` istemcisininki açık kalırdı.
  **Belirtisi:** izin kapalı (`desktop_unlock.json` → `until: 0`) ama üst
  çubuktaki paylaşım göstergesi duruyor. 2026-09-06'da kullanıcı gördü ve
  sordu. Gösterge "ajan ekranını görebiliyor" demek; acil kapatmadan sonra
  durması ya erişimin sürdüğü ya da göstergenin yalan söylediği anlamına
  gelir — ikisi de kabul edilemez. `screencast.kill_helpers()` artık
  `/proc`'u tarayıp **kendi kullanıcımızın** ve cmdline'ında yardımcının
  **tam yolu** geçen bütün süreçleri sonlandırıyor; `desktop_lock` ve
  `cli.lock` ikisi de çağırıyor.
- **Yayın `desktop_unlock` ile açılır, `desktop_lock`/süre dolumuyla kapanır**
  (iki yolda da; native yol Task 4.3'ten beri `capture.session_open` ile, kare
  okumadan — `docs/native/capture.md`).
  Açıkken GNOME üst çubukta paylaşım göstergesi durur — bu istenen bir şey
  (kullanıcı ajanın masaüstüne erişebildiğini oradan görüyor) ve **çekilen
  karede de görünür**. Yayın `screencast_helper.py` sürecinde yaşıyor: süreç
  ölünce paylaşım da ölüyor.
- **PipeWire build başlıkları kurulu.** Ölçüldü 2026-09-12:
  `libpipewire-0.3-dev` 1.0.5 ve `libclang-dev` kurulu;
  `pkg-config --modversion libpipewire-0.3` → `1.0.5`, `libspa-0.2` → `0.2`.
  `pipewire` Rust sandığı bindgen kullandığı için ikisi build gereksinimi.
  Paketlenmiş binary'nin runtime gereksinimleri Task 4.1'de ayrıca ayrılacak.
- **`cargo test` ilk başarısız HEDEFTEN sonra durur.** Sonraki test ikilileri
  hiç çalışmaz ve "geçti" gibi görünmez, **hiç görünmez**. Bir kez yanıltıcı
  oldu: bir mutasyon denemesinde ikinci test dosyasının sonuçları eksik geldi.
  Doğrulama `--no-fail-fast` ile yapılır.
- **PipeWire düğüm numaraları geri dönüşümlü ve sırası KARARLI DEĞİL.**
  Ölçüldü 2026-09-12, aynı istekle 11 koşum: `DP-4`/`DP-3` bir koşumda
  `[83, 82]`, başka bir koşumda `[69, 72]` düğümlerini aldı — yani hangi
  monitörün düğümünün önce geldiği değişiyor. "İlk gelen sinyal ilk
  kaydettiğim monitördür" varsayımı sessizce yanlış ekranı yakalamak demek
  olurdu. Eşleme `RecordMonitor`'ün döndürdüğü **stream nesne yoluna** göre
  yapılır, geliş sırasına göre değil.
- **Rust'ın ScreenCast oturumu ölçüldü ve Python yardımcısından hızlı.**
  2026-09-12, gerçek Mutter, iki monitör, veriyolu bağlantısı ölçümün dışında:
  oturum açma (CreateSession + 2×RecordMonitor + Start + iki sinyal)
  **2,6–4,8 ms** (ortalama 3,6), kapatma **0,8–1,4 ms**, imleç kipi değişimi
  (tam yeniden kurulum) **3,8–6,4 ms** — Python yardımcısında aynı değişim
  ~113 ms olarak kaydedilmişti. İki sayı aynı ölçüm noktasından alınmadı
  (Python'unki yardımcı sürece JSON gidiş dönüşünü de içeriyor). Ayrıntı:
  `docs/native/capture.md`. **Task 4.3'ten beri varsayılan `auto`:**
  paketlenmiş yardımcı varsa native yol, yoksa Python yolu; geri dönüş
  `system_capabilities` ve `screen_capture` sonucunda görünür. Geri almak:
  `[native] capture = "python"`.
- **Native capture'ı debug binary ile ölçme; asıl maliyet Python'un PNG
  kaydı.** Ölçüldü 2026-09-13, gerçek Mutter, 1920×1080, yük ~1,0, governor
  `powersave`: `capture.frame` monitör başına debug binary ile **~1915 ms**
  (`encode_ms` ~1755, `wait_ms` ~145), release ile **271–294 ms** (`encode_ms`
  ~200, `wait_ms` 60–68). `rust/Cargo.toml`'da `[profile]` yok, yani debug
  derlemede PNG kodlayıcı optimizasyonsuz. Aynı karenin Python tarafı: çözme
  ~20 ms, 1536'ya küçültme ~40 ms, `save(optimize=True)` **~1000 ms** — ve bu
  Python backend'inde de aynen ödeniyor. MCP üzerinden iki monitörlük
  `screen_capture` debug binary ile 6,0–6,3 sn sürdü; sıcak çağrı soğuk kadar
  yavaştı, yani süre oturum açılışından gelmiyor.
- **Tek bir PipeWire akışı başka düğüme YENİDEN BAĞLANMIYOR.** Ölçüldü
  2026-09-13 (PipeWire 1.0.5, WirePlumber 0.4.17): native kaynak tek bir
  `pw_stream`'i tutup her çekimde başka düğüme `connect` ettiğinde akış **ilk
  bağlandığı düğümde kaldı** — DP-3 önce istenince bütün DP-4 istekleri
  DP-3'ün görüntüsünü döndürdü; boyut, PNG ve zaman damgası hepsi geçerliydi.
  Çekim başına yeni akış bunu çözdü (iki istek sırasında 8/8 doğru monitör).
  Hiçbir test yakalamamıştı çünkü bütün canlı testler yalnızca **ilk**
  monitörü okuyordu; artık `each_connector_gets_its_own_monitors_frame` iki
  monitörü çapraz sınıyor. Düğümü kimliğiyle hedeflemek libpipewire'da
  eskimiş (`target.object` = `object.serial` isteniyor); bir yükseltme bunu
  bozarsa yol registry'den serial okumak. Ayrıntı: `docs/native/capture.md`.
- **Rust capture eski yolla aynı pikselleri veriyor.** Ölçüldü 2026-09-13
  (Task 4.2, iki monitörü kaplayan statik desen, release): tam boyutta ve
  1536'da **%100,000** eşleşme; tazelik 12/12; sıcak `capture()` p95 oranı
  native/eski **1,34–1,42** (native karede ~35–40 ms yavaş, oturum açılışında
  ~60 ms hızlı); revoke ve süre dolumu sonrası kare yok. Gerçek masaüstünde
  MCP düzeyinde iki monitör: eski 4.684 ms, native 5.113 ms (**1,09×**);
  sürenin ~%99'u Python'un PNG kaydı (gerçek içerikte ~2,2 sn/monitör), MCP
  katmanı ~30 ms. Ayrıntı: `docs/native/verification-linux.md`.
- **Ekran görüntüsünün maliyeti sürücüye göre 20–30 kat değişiyor** — `agy`'de
  tek görüntü ~40 bin girdi jetonu, **Claude'da ~1200–1900**. `ui_dump` yine de
  daha ucuz (~0,1 sn, birkaç yüz jeton) ve koordinat kullanmadığı için
  **ıskalayamaz**. GTK'da metin yolu tercih edilir; görüntü, ağacın boş geldiği
  yerler için yedek.
- **Görüntü koordinatını artık model çevirmiyor.** Her çekim PNG'nin yanına
  `<id>.json` olarak kaydediliyor (`m2-a1b2c3`), ve `mouse` / `computer_batch`
  / `pcb-do` bir `shot=` alıp ofseti **ve** ölçeği kendisi uyguluyor. Eskiden
  metinde formül veriliyordu (`ofset + görüntü_x / ölçek`) ve zayıf modeller
  bunu tutturamıyordu: sistematik olarak hedefin kenarına tıklıyor, bazen
  ofset/ölçek bilgisini tamamen kaybediyorlardı. Kayıt **diskte**, çünkü
  `pcb-do`'nun her çağrısı yeni bir süreç (`pointer.json` ile aynı sebep);
  iki dizin de aranıyor (`state_dir/shots` + `pcb-shot`'ınki), yani MCP'den
  çekilen görüntüye kabuktan tıklanabiliyor. Kimlik doğrudan dosya adına
  dönüştüğü için biçimi **süzülüyor** (`SHOT_ID_RE`) — süzülmeseydi
  `shot="../.."` dizin dışına çıkardı. `shot` ile `monitor` birlikte
  verilemez: farklı uzaylar, sessizce birini seçmek tam da bu katmanın
  önlemeye çalıştığı hata olurdu.
- **`shot` unutulursa çağrı reddediliyor, tahmin edilmiyor.** `shot` da
  `monitor` da verilmemişse koordinat global sayılır — ama yakında
  **küçültülmüş** bir çekim varsa ve koordinat onun kutusuna düşüyorsa bu
  büyük olasılıkla unutulmuş bir kimliktir ve eylem sessizce yanlış ekrana
  düşerdi (1536'lık bir görüntüden okunan (640, 360) sağdaki düğmeyi değil
  **sol ekranın ortasını** gösterir). Belirsizlik çözülemez — (640, 360)
  gerçekten de geçerli bir global koordinat — o yüzden `expect_focus`
  desenindeki karar tekrarlandı: **niyeti söylet.** Red mesajı iki çıkış yolu
  veriyor (`shot=` ya da `monitor=`), ikisi de zaten var olan parametreler.
  Ölçüt üç koşulun kesişimi (yakın + küçültülmüş + kutu içinde), yani sağ
  ekrana yapılan global çağrılar etkilenmiyor. `[desktop]
  ambiguous_coord_guard = false` ile kapatılabilir.
- **`screenshot_scale_long_edge` 1536** (2026-09-06'da 1280'den yükseltildi).
  Ölçüldü: 1280'de küçük yazıdaki Türkçe diakritikler (ğ, ş) bulanıklaşıp
  kayboluyor ve kelime tahmin edilerek okunuyor; 1536'da doğrudan okunuyor.
  Bedeli görüntü başına ~540 jeton. **1568 tavanı hâlâ geçerli ve artık daha
  sert:** API o sınırın üstünü kendisi küçültüyor, yani model indirilmiş
  karedeki pikseli söylerken sunucu kayıtlı ölçeği uygular ve aradaki fark
  (1920 için 1,22 kat) sessizce koordinata girer. Eskiden bu yalnızca
  "raporlanan ölçek yanıltıcı olur" demekti; hesabı model yaptığı için
  zararsızdı. `screen_capture` ve `pcb-shot` 1568 üstünde uyarı basıyor.
- **`--out` ile alınan çekimin kaydı arama dizinine de yazılıyor.** `pcb-do`
  ayrı bir süreç ve `--out`u bilemez; kopyalanmasaydı `pcb-shot --out /baska`
  ile alınan görüntünün kimliği "böyle bir çekim yok" derdi (fiilen yaşandı).
  Kayıtta PNG'nin **mutlak** yolu duruyor, yani görüntü nerede olursa olsun
  bulunuyor.
- **Claude Code araç sonucundaki görüntüyü gerçekten okuyor** (ölçüldü: bilinen
  içerikli PNG'deki gizli değer birebir geri geldi). Anthropic API uzun kenarı
  1568'e indirdiği için `screenshot_scale_long_edge = 1280` korunuyor — o
  sınırın altında modelin gördüğü piksel ile raporlanan ölçek aynı kalıyor.
- **stdio'da hiçbir `@mcp.custom_route` rotası yok** (HTTP sunucusu yok):
  `/shot/<token>.png`, `/healthz`, `/consent`, `/.well-known/*`. `screen_capture`
  orada bağlantı yerine dosya yolu döner. OAuth'u da fastmcp kendisi atlıyor.
- **Dinamik masaüstü araçlarında `output_schema=None` olmalı.** Aksi halde
  FastMCP metin/görüntü veya hata sonucuyla çelişen bir outputSchema üretip
  çağrıyı `"outputSchema defined but no structured output returned"` hatasıyla
  sonlandırabilir.
- **stdio'da oturum ortamı bozuk gelebilir.** Ölçüldü: Codex'in başlattığı
  süreçte `DBUS_SESSION_BUS_ADDRESS` genişletilmemiş bir literal olarak geldi
  (`$DBUS_SESSION_BUS_ADDRESS`) ve masaüstü araçlarının **tamamı** çöktü —
  `busctl` bağlanamıyor, monitör tablosu okunamıyor. `desktop/session.py`
  `ensure_session_env()` bunu `/run/user/<uid>/` altındaki soketlerden onarıyor;
  `server.py` ve `cli/__init__.py` girişte çağırıyor. Yeni bir giriş noktası
  eklersen **oradan da çağır**.
- **`systemctl --user restart pcbridge` senin MCP araclarini GUNCELLEMEZ.**
  Olculdu 2026-08-21: kod degistirilip servis yeniden baslatildiktan sonra
  `desktop_unlock` cagrisi hala ESKI bicimde durum dosyasi yazdi
  (`hard_until` yok). Sebep: bu oturumun MCP baglantisi `--stdio` ile
  baslatilmis AYRI bir surece gidiyor (o gun 5 tane vardi, en eskisi bir
  onceki gunden) ve o surec kendisini baslatan istemci kapanana kadar
  yasiyor. Ayni anda systemd birimi yeni kodu kosturuyordu — yani iki farkli
  surum ayni durum dosyasina yaziyordu. `ps -eo pid,ppid,lstart,args | grep
  pcbridge` ikisini de gosteriyor; `--stdio` argumani olan satirlar servis
  DEGIL. Kod degisikligini kendi arac cagrilarinla dogrulamaya calisma:
  ya istemciyi yeniden baslat, ya da servise HTTP + statik token ile git
  (`tests/test_e2e.py`'nin kalibi).
- **venv'deki `pcbridge.pth` repo yolunu `sys.path`'e ekliyor**, bu yüzden
  `python -m pcbridge.server` herhangi bir dizinden çalışır; istemci kayıtları
  `cwd` istemiyor.

### GNOME kabuk eklentisi tarafı (ölçüldü 2026-08-04)

- **GNOME 45+ eklenti kodunu ESM önbelleğinde tutuyor.** `gnome-extensions
  disable/enable` JS'i **yeniden okumaz**; kabuğun yeniden başlaması gerekir ve
  Wayland'de bu **çıkış/giriş** demek. Geliştirme bu yüzden nested kabukta:
  `gnome-extension/nested.sh`.
- **`rm ~/.local/share/gnome-shell/extensions/<uuid>` çalışan eklentiyi
  DURDURMAZ** — aynı sebep. Acil geri alma `gnome-extensions disable <uuid>`;
  o anında etki ediyor. Bir kez yanlış söylendi ve kullanıcı makineyi yeniden
  başlatmak zorunda kaldı.
- **Her nested kabuk koşumu ~13 oturum servisi bırakıyor** (gvfsd,
  tracker-miner, dconf-service, at-spi…). `dbus-run-session`'ın kurduğu veriyolu
  başlatıyor, kabuk ölünce onlar yaşamaya devam ediyor. ~20 koşumda 298 yetim
  süreç birikti ve **`fs.inotify.max_user_instances` (128) DOLDU**. O noktada
  `Gio.FileMonitor` yeni izleyici yaratamıyor ve bunu **sessizce** yapıyor —
  belirtisi "eklentinin durum izleyicisi öldü" ve `test_state.js` 23/23 iken
  11/23'e düşmesi oldu, kod hiç değişmemişti. `nested.sh` artık her koşumda
  topluyor; ayırt etme ölçütü güvenli: nested oturumlar `/tmp/dbus-*`, gerçek
  oturum `/run/user/<uid>/bus`.
- **`Meta.CursorTracker.set_pointer_visible(false)` gerçek imleci gizliyor ve
  gizli kalıyor** (gerçek oturum, gerçek donanım). Görsel kanıt: gizli/görünür
  kareleri arasındaki fark tam olarak imlecin bulunduğu noktada, 13×21 px.
  Ama imleç katmanı fiziksel fareyle tıklamayı bozdu — ayrıntı ve devam yolu
  `WALKTHROUGH.md`'de.
- **`Clutter.Canvas` mutter çatalında YOK**; çizim `St.DrawingArea` + Cairo.
  GJS'de Cairo bağlamı `cr.$dispose()` ile bırakılmazsa sızıyor.
- **GNOME'un monitör sırası pcbridge'inkiyle aynı değil.** `Main.layoutManager.monitors`
  bu makinede `#0` = birincil (x=1920), `#1` = x=0; pcbridge soldan sağa
  numaralıyor. Eklenti indeks değil **geometri** kullanıyor, o yüzden etkilenmiyor.
- **`Gio.FileMonitor`'ün varsayılan hız sınırı 800 ms** — o pencere içindeki
  ardışık dosya değişiklikleri birleşiyor. Durum dosyasının hızlı açılıp
  kapanması tek olay olarak görünür.
- **STATİK çerçevenin maliyeti ölçüm gürültüsünün altında**: boşta CPU kapalı
  %0,55 / açık %0,45–0,50, RSS farkı +0,08 MB. `top` örneklemesi nested kabukta
  çok gürültülü; ölçüm `/proc/<pid>/stat`'tan CPU zamanı farkıyla yapılmalı.
- **SÜREKLİ animasyon o bedavalığı bitiriyor.** Nefes animasyonu (şeritleri
  yavaşça inceltip geri açan) nested kabukta **%19** CPU yaktı. Sebep seçilen
  özellik DEĞİL: aynı animasyon saydamlıkla denendi, **%23,7** çıktı. Maliyet
  büyük saydam şeritlerin 60 fps yeniden harmanlanmasından geliyor; statik
  çerçevenin bedava olmasının sebebi de tam olarak hiçbir şeyin değişmemesiydi.
  Nested bu sayıyı abartıyor olabilir (nested bir pencereye çiziyor, gerçek
  kabuk onu bir kez daha kompozitliyor) — gerçek oturumda ÖLÇÜLMEDİ.
- **`Clutter.PropertyTransition`'a aktöre eklenmeden `set_from`/`set_to` verme.**
  Geçiş o anda özelliğin tipini bilmiyor, aralık boş kalıyor ve özellik **0'a**
  düşüyor (ölçüldü: 15 ölçek örneğinin hepsi 0.000, şerit görünmez oldu).
  `actor.ease()` zincirlemesi doğrulanmış yol.

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
| `WALKTHROUGH.md` | **Depodaki tek yapılacak-iş listesi:** yol haritası, kabul ölçütleri, biten işlerin kaydı + `window_focus` ölçümü ve imleç katmanının kurtarılmış bulguları |
| `docs/native/protocol-v1.md` | Python/native stdio framing, handshake, control metotları ve test harness sınırı |
| `docs/native/capture.md` | Native Mutter ScreenCast oturumu: durum makinesi, kapanma tetikleri, Python yardımcısından farklar, ölçümler |
| `docs/native/packaging.md` | Native yardımcının derlenmesi, paketi, çalışma zamanı bağımlılıkları, `doctor.sh` tanısı ve CI |
| `docs/native/verification-linux.md` | Rust capture'ın gerçek masaüstünde eski yolla karşılaştırması: parity, tazelik, gecikme, revoke ve çökme senaryoları (Task 4.2) |
| `ADIMLAR.md` | Ekran görüntüsü koordinatları / temizliği üçlüsünün adım adım kaydı: ne bitti, ne bekliyor, neden |
| `KULLANIM.md` | Kullanıcıya dönük araç kataloğu + izin haritası — **güncel tutulmalı** |
| `config.example.toml` | Ayarların belgelenmiş hâli; projenin asıl referansı |
| `README.md` | **İngilizce**, GitHub vitrini: genel bakış, mimari, güvenlik özeti |
| `KURULUM.md` | Türkçe elkitabı: kurulum, istemcilere bağlanma, güvenlik değerlendirmesi, sorun giderme |
| `gnome-extension/README.md` | Kabuk eklentisi: kurulum, acil geri alma, nested geliştirme döngüsü |
| `GELISTIRME.md` | Yeni araç eklemenin uzun anlatımı + protokol tuzakları — geçmiş kayıt |
| `PLAN.md` | **Yürürlükteki** native migration sözleşmesi: faz/task kimlikleri, acceptance, gate'ler. Sıradaki işi buradan değil `WALKTHROUGH.md`'den oku |
| `UYGULAMA.md` | Geçmiş kayıt: neyin neden böyle yapıldığı. Okuma zorunlu değil, **silme** |
| `AGENTS.md` | Codex ve diğer ajanlar için giriş noktası — **ince**, buraya yönlendiriyor. Kopyasını çıkarma: bir kez çıkarıldı, iki günde 83 satır ayrıştı; durum bloğu da bir kez kopyalandı ve aynı desene girdi |
| `KURALLAR.md` | Masaüstü araçlarının davranış sözleşmesi (O/K/Y sınıfları). Taslak; kayan kira gibi bazı maddeleri uygulandı |
| `GOREV-kurallar.md` | `KURALLAR.md`'yi uygulamak için yazılmış görev tarifi — geçmiş kayıt |
| `JARVIS.md` | pcbridge'i kişisel asistana çevirme **teklifi**. Ölçüm değil tasarım; "ölçüldü" satırları ayrı işaretli |

## Çalışma tarzı

- Kendi görev listeni çıkar, kullanıcıya onaylat, sonra başla.
- **Her adımdan sonra fiilen test et.** Çalıştığını görmeden sonrakine geçme.
- Ölçmediğin şeyi "çalışıyor" diye yazma; `IdleMonitor`, ekran görüntüsü veya
  pencere başlığıyla doğrula.
- Bölüm bitince commit at.
- Dosya düzenleme komutu önerirken `nano` kullanma; kullanıcının `edit` takma
  adı var (`gnome-text-editor`), root için `edit admin:///yol`.
