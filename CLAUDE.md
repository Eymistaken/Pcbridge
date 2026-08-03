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

**Sıradaki iş [YAPILACAKLAR.md](YAPILACAKLAR.md).** Şu an orada duran görev:
ajanın makineyi kullandığını gösteren **görsel bir GNOME 46 eklentisi**
(ekran kenarlarında çerçeve efekti, değişen ve yöne dönen imleç).

Ölçülmüş makine gerçekleri **bu dosyada**, aşağıda. Faz H/I/J'nin sonuçları ve
neyin neden böyle yapıldığı `PLAN.md` 9b–9d bölümlerinde.

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
./.venv/bin/python tests/test_desktop.py    # masaustu, 398 kontrol, GIRDI GONDERMEZ
```

Gerçek cihazlarla masaüstü testleri (uinput'a yazar, AT-SPI okur) — varsayılan
olarak atlanırlar:

```bash
PCBRIDGE_TEST_CAPTURE=1 PCBRIDGE_TEST_ATSPI=1 PCBRIDGE_TEST_BATCH=1 \
  ./.venv/bin/python tests/test_desktop.py   # 431 kontrol
```

`PCBRIDGE_TEST_CAPTURE=1` ekranı diske yazar **ve** kısa süreliğine ekran
yayını açar (üst çubukta paylaşım göstergesi belirir); `PCBRIDGE_TEST_BATCH=1`
gerçek tıklama gönderir.

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
capture.py    ekran goruntusu: iki backend -- yayin (sessiz) / gnome-screenshot
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
- **Yayın `desktop_unlock` ile açılır, `desktop_lock`/süre dolumuyla kapanır.**
  Açıkken GNOME üst çubukta paylaşım göstergesi durur — bu istenen bir şey
  (kullanıcı ajanın masaüstüne erişebildiğini oradan görüyor) ve **çekilen
  karede de görünür**. Yayın `screencast_helper.py` sürecinde yaşıyor: süreç
  ölünce paylaşım da ölüyor.
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
- **stdio'da oturum ortamı bozuk gelebilir.** Ölçüldü: Codex'in başlattığı
  süreçte `DBUS_SESSION_BUS_ADDRESS` genişletilmemiş bir literal olarak geldi
  (`$DBUS_SESSION_BUS_ADDRESS`) ve masaüstü araçlarının **tamamı** çöktü —
  `busctl` bağlanamıyor, monitör tablosu okunamıyor. `desktop/session.py`
  `ensure_session_env()` bunu `/run/user/<uid>/` altındaki soketlerden onarıyor;
  `server.py` ve `cli/__init__.py` girişte çağırıyor. Yeni bir giriş noktası
  eklersen **oradan da çağır**.
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
| `YAPILACAKLAR.md` | **Sıradaki iş** — şu an: görsel GNOME 46 eklentisi |
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
