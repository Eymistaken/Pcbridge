# JARVIS.md — pcbridge'i kişisel asistana çevirme planı

> Bu belge bir **teklif**, ölçüm raporu değil. İçindeki "ölçüldü" işaretli
> satırlar 2026-08-21'de bu makinede gerçekten koşturuldu; geri kalanı
> tasarım önerisi ve sınanmadı. İkisini karıştırmamak için ayrı işaretlendi.

## Kısa cevap

Evet, mümkün — ve sandığından daha yakınsın. pcbridge zaten **eller**.
Jarvis olması için eksik olan **beyin**, **kuyruk** ve **telefona giden
kanal**. Üçü de bu makinede, ek fatura çıkarmadan kurulabiliyor.

Asıl istediğin şey — *"telefondan görev vereyim, ben yokken yapsın, bitince
ekran görüntüsüyle haber versin"* — üç parçaya ayrılıyor ve **üçünün de
altyapısı zaten yazılmış durumda**:

| İstediğin | Altyapı | Durum |
|---|---|---|
| Telefondan görev vermek | HTTP + OAuth 2.1 + Funnel | **Var, çalışıyor** |
| Sen yokken çalıştırmak | `jobs.py` arka plan işleri | **Var** (üstüne gözcü lazım) |
| Ekran görüntüsüyle kanıt | `shots.py` → `/shot/<token>.png` | **Var** (ömrü kısa) |
| Bitince telefona haber | — | **Yok.** Tek gerçek boşluk |
| Kendi modellerini takmak | `[agents.*]` TOML deseni | **Var**, ajan eklemek TOML işi |

---

## 1. "PC başında değilken" — ölçülmüş cevap

Bu senin en kritik sorun ve cevabı iki parçalı. `safety.py` ve `tools.py`
okundu:

**Ekran kilidi kapısından geçen araçlar** (`_guard()` → `gate.check()`):
`mouse`, `keyboard`, `screen_capture`, `ui_dump`, `ui_click`, `ui_set_text`,
`window_list`, `window_focus`, `computer_batch`, `computer_task`.
Ekran kilitliyse **hepsi reddediliyor** — tasarım gereği.

**Kapıdan geçmeyen araçlar** (yalnızca `gate.audit()` çağırıyorlar, yani
sadece kayda geçiyorlar): `shell_run`, `shell_run_background`, `fs_read`,
`fs_write`, `fs_list`, `fs_search`, `tmux_*`, `agent_run`, `job_*`.
Bunlar ekran kilitliyken de **bugün çalışır**.

Yani: "şu depoyu güncelle, testleri koştur, şu klasörü düzenle, Claude Code'a
şu işi ver" tarzı her şey **şu anda, hiçbir kod yazmadan, sen yokken
çalışıyor.** Kilit yalnızca *grafik arayüzü sürmeyi* engelliyor.

### Ve bu makinede kilit zaten düşmüyor (ölçüldü, 2026-08-21)

```
sleep-inactive-ac-type = 'nothing'   → prizdeyken hiç uyumuyor
idle-delay             = uint32 0    → ekran hiç kararmıyor
lock-enabled           = true        → ama kilit yalnızca ekran koruyucu
                                        devreye girerse kilitliyor
```

Ekran koruyucu hiç devreye girmediği için, **Super+L'ye elinle basmadığın
sürece oturum açık kalıyor ve GUI görevleri de sen yokken çalışıyor.**
Anlık durumu `SafetyGate`'in sorduğu yerden sen de sorabilirsin:

```bash
busctl --user --json=short call org.gnome.ScreenSaver \
  /org/gnome/ScreenSaver org.gnome.ScreenSaver GetActive
# bu makinede 2026-08-21'de: {"type":"b","data":[false]}  → kilitli değil
```

Çakışma koruması (`idle_guard_seconds = 60`) de tersini koruyor: makinenin
*başındayken* yazma eylemlerini reddediyor, yokken değil.

Geriye üç pürüz kalıyor, üçü de küçük:

1. **Alışkanlıkla Super+L'ye basarsan** o günkü GUI görevleri sessizce
   reddedilir. Çözüm kod değil, *zamanlama*: görev telefondan geldiği anda
   `org.gnome.ScreenSaver.GetActive` sorulup **daha kabul aşamasında**
   "ekran kilitli, GUI görevleri çalışmaz" diye telefona dönmeli. Yirmi
   dakika sonra kuyruğun dibinde öğrenmek kötü.
2. **`desktop_unlock` süreli** (varsayılan 15 dk, tavan 120). Uzun bir GUI
   görevi ortasında izin bitebilir. Kuyruk yöneticisi görevi başlatırken
   *görevin tahmini süresine göre* izni açmalı ve bitince `desktop_lock`
   çekmeli — açık bırakmamalı.
3. **Monitör uykusu ≠ oturum kilidi.** Ekran fiziksel olarak uyusa bile
   `screen_capture` çalışır (PipeWire yayını monitöre bağlı değil). Bu
   *muhtemelen* doğru ama **ölçülmedi** — Faz 1'de bir kez sınanmalı.

---

## 2. Eksik olan beş parça

| # | Parça | Neden şu an yok | Büyüklük |
|---|---|---|---|
| 1 | **Ödev bırakma** | MCP soru-cevap. İstemci bağlı kalıp `job_status`'ı yoklamalı. Telefonu cebine koyunca kimse yoklamıyor. | ~300 satır |
| 2 | **Telefona push** | `notify` yalnızca `notify-send` — masaüstüne. Dışarı çıkan kanal yok. | ~30 satır |
| 3 | **Kanıt teslimi** | `ShotStore` tam bunun için yazılmış ama `shot_ttl_seconds = 300`. Beş dakika sonra bakarsan bağlantı ölmüş. | ayar + ~50 satır |
| 4 | **Hafıza** | Her oturum sıfırdan. | ~150 satır |
| 5 | **Kodlama dışı araçlar** | 34 aracın hepsi kabuk/dosya/masaüstü/ajan. Ama takvim/mail/drive Claude Code'da zaten bağlı — beyin onları miras alıyor. Kalan: müzik, ev, notlar. | TOML (aşağıda) |

---

## 3. Mimari

Tek ve büyük fikir: **beyin PC'nin içinde otursun.** O zaman beyin pcbridge'e
`--stdio` ile bağlanır — ağ yok, OAuth yok, Funnel yok. Dışarıya açılan tek
şey telefonun konuştuğu ince bir sohbet API'si olur, o da Funnel değil
**tailnet** üzerinden.

```
  TELEFON                              PC — zorinos
┌───────────────┐                ┌──────────────────────────────────────────┐
│  senin PWA'n  │◄── HTTPS ─────►│  pcbridged  (YENİ — beyin + kuyruk)      │
│  (tailnet)    │  tailscale     │    ├── görev kuyruğu   (SQLite)          │
│               │     serve      │    ├── hafıza          (SQLite + facts)  │
│  ntfy uygul.  │◄── push ───────┤    ├── zamanlayıcı     (proaktif)        │
└───────────────┘   (dışa doğru) │    └── sağlayıcı seçici                  │
                                 │           ├── claude -p   → abonelik      │
                                 │           ├── gemini -p   → ücretsiz kota │
                                 │           └── goose/ollama → RTX 3060     │
                                 │                    │                      │
                                 │                    │ MCP over stdio       │
                                 │                    ▼                      │
                                 │        pcbridge — 34 araç  ◄── MEVCUT     │
                                 └──────────────────────────────────────────┘
```

### Bunun getirdiği güvenlik kazancı

Şu anda (ölçüldü) **Funnel açık**: Tailscale adresin internete bakıyor ve
arkasında bu makinede uzaktan kod çalıştırma var. (Bu belgede adres
`KURULUM.md`'deki gibi maskeli yazılıyor — depo herkese açık.)
Yukarıdaki mimaride Funnel'a **gerek kalmıyor** — `tailscale serve` aynı
hostname'e geçerli Let's Encrypt sertifikası veriyor ama **yalnızca
tailnet'e** yayınlıyor. Telefonda Tailscale açıksa erişirsin, internetteki
kimse erişemez.

Funnel'ı yine de açık tutmak istersen tek gerekçesi kalır: **Claude mobil
uygulamasının** özel connector olarak bağlanması (Anthropic'in bulutu
makineye ulaşabilmeli). Kendi uygulamanı yazdığın anda o gerekçe düşüyor.

---

## 4. Beyin: hiç LLM kodu yazmadan

En büyük kısayol bu. `pcbridged`'in içine ajan döngüsü, araç çağırma,
token yönetimi **yazmana gerek yok**. Çünkü:

- `connect.sh --apply` pcbridge'i zaten Claude Code'a MCP sunucusu olarak
  kaydediyor.
- `claude -p "<görev>"` başsız çalışıyor ve o 34 aracın hepsini görüyor.
- `agent_run` bunu zaten yapıyor — `jobs.py` çıktıyı `claude_stream_json`
  ile ayrıştırıyor.

Yani `pcbridged` şu kadar şey: **HTTP API + SQLite kuyruk + alt süreç
gözcüsü + push.** Beyin, abonelikten beslenen `claude -p`.

**Ölçüldü (2026-08-21):** `claude mcp list` çıktısında
`pcbridge: .../python -m pcbridge.server --stdio — ✔ Connected`.
Yani bu kısayol teoride değil, şu anda çalışır durumda. `[agents.claude]`
komutunda `--dangerously-skip-permissions` de zaten var, başsız koşumda izin
sorusuna takılmıyor.

### Kendi modellerini takmak

Projenin en iyi tasarım kararı burada karşılığını veriyor: *"ajan tanımları
koda değil `config.toml`'a yazılır."* Yeni bir beyin eklemek = yeni bir
`[agents.*]` bloğu.

**Gemini (ücretsiz kota).** Gemini CLI MCP sunucularını destekliyor ve
kişisel Google hesabıyla girişte ücretsiz katman **günde 1000 istek /
dakikada 60 istek** veriyor. **Ölçüldü: `gemini` bu makinede zaten kurulu**
(`~/.nvm/versions/node/v20.20.2/bin/gemini`) — yani bu, kurulum değil,
`config.toml`'a bir blok yazma işi. Ajan bloğu kabaca:

```toml
[agents.gemini]
enabled = true
description = "Gemini CLI - Google's terminal agent, free tier"
command = ["gemini", "-p", "{prompt}", "--yolo"]
parser  = "plain"
default_model = "gemini-flash"
```

**Yerel model (RTX 3060 12 GB — ölçüldü, ollama kurulu değil).** Goose ya da
benzeri bir MCP istemcisi Ollama ile konuşuyor. 12 GB'a kuantize 8–14B
sınıfı sığar; "notlarıma şunu ekle", "hangi işler koşuyor" gibi ucuz ve sık
işler için yeter, GUI sürmeye ve çok adımlı planlamaya **yetmez**. Doğru
kullanımı *yönlendirici*: yerel model işi sınıflandırsın, ağır olanı
`claude -p`'ye devretsin.

Bu üçlü aynı `models.py` çözümleyicisinden geçtiği için — saf fonksiyon, I/O
yok — model/effort/alias mantığını yeniden yazmana gerek yok.

---

## 5. Telefon uygulaması — Kotlin

> **Düzeltme (bu belgenin ilk hâlinde yanlıştı).** İlk sürümde PWA
> önerilmişti ve gerekçesi maliyetti: "Play Store 25 USD, Apple 99 USD".
> **Android'de bu gerekçe geçersiz** — kendi APK'nı `adb install` ile
> kurmak bedava ve mağazayla hiç işin olmuyor. Aşağısı düzeltilmiş hâli.
> (Varsayım: telefon Android. iPhone'sa tablo tersine dönüyor, en altta.)

**Mimari değişmiyor.** İstemci PWA da olsa Kotlin de olsa `pcbridged`'in
HTTP/WebSocket API'siyle konuşuyor. Yani bu bir *sıralama* kararı, mimari
kararı değil — API sabit kalıyor, istemciyi istediğin zaman değiştirirsin.

### Maliyet: Kotlin de sıfır

| Yol | Ücret |
|---|---|
| `adb install` ile kendi APK'nı kurmak | **0** — ve kalıcı, süresi dolmuyor |
| Play Store'a yüklemek | 25 USD tek sefer — **gerekmiyor** |
| Android Studio, SDK, Kotlin | 0 |

Google'ın 2026'da başlattığı **geliştirici doğrulaması** yan yükleme için de
geliyor ama seni üç ayrı sebepten etkilemiyor:

- ADB kurulumları **açıkça muaf**: *"As a developer, you are free to install
  apps without verification with ADB."*
- Küresel yayılım **2027 ve sonrası**; 2026'nın Eylül'ündeki bölgesel tarih
  Brezilya, Endonezya, Singapur ve Tayland için — Türkiye listede yok.
- Yine de tıkanırsan **limited distribution account** var: hobiciler için
  ücretsiz, kimlik istemiyor, 20 cihaza kadar.

### Kotlin'in PWA'nın yapamadığı şeyleri

Asıl gerekçe maliyet değil, bunlar. Ve tam da senin "Jarvis" tarifine
oturuyorlar:

| Yetenek | Ne işe yarar |
|---|---|
| **Paylaş menüsü** (`ACTION_SEND`) | Herhangi bir uygulamadan bir bağlantıyı/fotoğrafı "Jarvis'e gönder" → PC'de iş olur. PWA'nın yapamadığı en değerli şey bu. |
| **Asistan rolü** (`VoiceInteractionService`) | Güç tuşuna basılı tutunca ya da köşeden kaydırınca **senin uygulaman** açılır. Gerçek Jarvis hissi burada. |
| **Foreground service** | Kalıcı WebSocket: koşan görevin çıktısı canlı akar, bildirim çubuğunda "3 görev çalışıyor" durur. |
| **Widget / Quick Settings karesi** | Ana ekrandan tek dokunuşla görev, bildirim panelinden "masaüstü izni kapat". |
| **Çevrimdışı kuyruk** | Tailnet yokken verdiğin görev sıraya girer, bağlanınca gider. |
| **On-device STT** | Sonradan ses istersen Android'in kendi tanıyıcısı bedava ve yerel. |

Bedeli: ilk çalışan sürüm PWA'da ~400 satır, Kotlin + Compose'da kabaca
**2000+ satır**, ve her değişiklikte yeniden derleyip kurmak gerekiyor —
sayfayı yenilemek gibi değil. Yani iterasyon döngüsü başta daha yavaş.

### Push: Google'a bağlanmadan

Kotlin uygulamasında bildirim için üç yol var, sırasıyla:

1. **UnifiedPush + ntfy dağıtıcısı** — açık protokol, FCM yok, Google hesabı
   yok. ntfy uygulaması dağıtıcı olarak kurulu olur, senin uygulaman ona
   kaydolur, `pcbridged` kendi ntfy'ına POST atar. Senin işletim sistemi
   tercihlerine en uyan yol bu.
2. **Foreground service + WebSocket** — hiç aracı yok, ama telefon uyurken
   teslimat garantisi zayıf; pil optimizasyonu listesinden uygulamayı
   çıkarman gerekir.
3. **FCM** — en güvenilir teslimat, karşılığında Firebase projesi ve Google
   bağımlılığı.

Hangisi olursa olsun **ekran görüntüsü ekli bildirim** destekleniyor: kanıt
bir bağlantı değil, doğrudan bildirimin içindeki resim olarak gelir.

### Ağ tarafı aynı

```bash
tailscale serve --bg 8766          # pcbridged'in portu, YALNIZCA tailnet
# → https://<makinen>.tailXXXX.ts.net  (Let's Encrypt, Funnel değil)
```

Kotlin uygulaması bu adrese normal HTTPS ile bağlanır — sertifika geçerli
olduğu için sertifika sabitleme, self-signed uğraşı, `networkSecurityConfig`
istisnası **yok**. Telefonda Tailscale açık olmalı, o kadar.

### Sıralama önerisi: API önce, uygulama sonra

Kotlin'e ilk gün başlama. Sebebi inatçılık değil, **API sözleşmesi henüz
yok**: `pcbridged`'in `/task`, `/jobs`, `/stream` uçları ilk iki haftada
birkaç kez değişecek ve her değişiklikte Kotlin tarafını yeniden yazmak
zorunda kalırsın.

Önerilen sıra:

1. `pcbridged`'i yaz, **`curl` + ntfy** ile sür. Telefonda uygulama yok ama
   görev verip ekran görüntülü bildirim alıyorsun — istediğin şey zaten
   çalışıyor.
2. API oturunca (uçlar bir hafta değişmediyse) **Kotlin'e başla.** Artık
   sabit bir hedefe yazıyorsun.
3. PWA'yı tamamen atlayabilirsin. Tek işlevi "API'yi elle denemek için bir
   ekran" olurdu; onu `curl` zaten yapıyor.

### iPhone olsaydı

Tablo tersine dönüyordu: kendi imzaladığın uygulama **7 günde bir** yeniden
kurulmak zorunda (ücretsiz Apple hesabıyla), kalıcı olması için yıllık
99 USD. Orada PWA gerçekten doğru cevap olurdu. Android'de değil.

**Kanıt akışı** şöyle kuruluyor ve büyük kısmı hazır:

```
görev biter
   → screen_capture  → state_dir/shots/*.png   (mevcut)
   → ShotStore.publish()                        (mevcut)
   → ntfy'a PNG'yi ekleyerek gönder             (yeni, ~30 satır)
```

Tek ayar: `shot_ttl_seconds = 300` "rapor" görüntüleri için çok kısa.
Raporluk görüntüler ayrı bir havuza, uzun ömürle yazılmalı — `shot_keep_hours
= 24` zaten diskteki temizliği hallediyor.

---

## 6. Kodlama dışı araçlar — yarısı zaten hazır

**Ölçüldü (2026-08-21):** `claude mcp list` çıktısında pcbridge'in yanında
şunlar da bağlı: **Google Drive ✔, Google Calendar ✔, Gmail ✔** (ayrıca
Canva ve Netlify, kimlik doğrulaması bekliyor).

Bu önemli, çünkü beyin `claude -p` olduğunda **bu araçları da miras alıyor.**
Yani "takvimime bak, o toplantıdan önce şu dosyayı hazırla ve bana mail at"
için yazman gereken kod: **sıfır.** (Başsız `claude -p` koşumunun bu
connector'ları gerçekten gördüğü Faz 1'de bir kez sınanmalı — beklenti evet,
aynı yapılandırmadan okuyorlar.)

Geriye kalan boşluk yerel/donanımsal olanlar: müzik, ev otomasyonu, notlar.
`shell_run` sandığından fazlasını çözüyor: `playerctl` (müzik),
`notify-send`, `rclone`, `yt-dlp`. Ajan bunları zaten çağırabiliyor.

Ama Jarvis'in *bilmesi* için aracın adı ve açıklaması olmalı — ajan araç
seçerken yalnızca docstring okuyor. Projenin kendi kuralını buraya da
uygulamak en temizi: **araç eklemek Python değil TOML işi olsun.**

```toml
[tools.muzik]
description = "Control music playback: play, pause, next, previous."
command = ["playerctl", "{action}"]
args = { action = ["play", "pause", "next", "previous"] }
read_only = false
```

`tools.py` içinde bu blokları gezip `mcp.tool` olarak kaydeden ~80 satırlık
bir kayıt döngüsü, `[agents.*]` için yazılmış olanın aynısı.

---

## 7. Maliyet

| Kalem | Tutar |
|---|---|
| Claude aboneliği | **zaten var** — `claude -p` oradan yiyor |
| Gemini CLI ücretsiz katman | 0 (günde 1000 istek) |
| Yerel model (RTX 3060) | 0 (+ elektrik) |
| Tailscale | 0 (kişisel plan) |
| ntfy | 0 (ücretsiz veya kendi sunucun) |
| Kotlin uygulaması (`adb install`) | 0 (mağaza ücreti gerekmiyor) |
| Barındırma | 0 (her şey senin PC'nde) |
| **Toplam ek fatura** | **0** |

Tek gerçek "maliyet" **kota**: `CLAUDE.md` günlük limitin bir kez tükendiğini
yazıyor (2026-08-03). Bu yüzden yönlendirme önemli — her "saat kaç" sorusu
Opus'a gitmemeli. `[agents.claude]` bloğundaki `default_model = "sonnet"`
zaten bu refleksle konmuş; `pcbridged` de aynı mantığı görev seviyesinde
uygulamalı.

---

## 8. Yol haritası

Her faz kendi başına işe yarar bir şey bırakıyor; ortada kesersen elinde
yarım bir şey kalmıyor.

### Faz 0 — bugün, kod yazmadan (30 dakika)
Claude mobil uygulamasına pcbridge'i **özel connector** olarak ekle
(claude.ai → Ayarlar → Connectors → Add custom connector → Funnel adresin).
Mobil uygulamadan eklenemiyor, web'den ekleyip senkronlanmasını bekleyeceksin.
Bu Jarvis değil ama *bu akşam* telefondan görev verip sonucunu görürsün ve
geri kalan her şeyi neye göre tasarlayacağını öğrenirsin.

### Faz 1 — `pcbridged`: ödev bırak, haberini al
Kalbi bu. İçerik:
- SQLite görev kuyruğu (`state_dir` altında, `jobs.py` deseninde)
- `POST /task` → görev al, `job_id` dön, kapan
- Gözcü: iş bitince `claude -p` çıktısını topla, `screen_capture` çek,
  `ShotStore.publish()`, ntfy'a görüntüyle birlikte gönder
- Kabul anında ön kontrol: ekran kilitli mi, `desktop_unlock` gerekli mi
- Görev bitince `desktop_lock`

Bittiğinde: telefondan görev veriyorsun, telefonu cebine koyuyorsun, bitince
ekran görüntüsüyle bildirim geliyor. **Bu zaten Jarvis.**

### Faz 2 — kendi uygulaman (Kotlin)
API bir hafta değişmeden durduktan **sonra** başla. İlk sürüm dar tutulmalı:
görev gönder, görev listesi, canlı çıktı, UnifiedPush ile bildirim.
Paylaş menüsü ve asistan rolü ikinci sürüme. `tailscale serve` ile yayınla,
Funnel'ı kapat.

### Faz 3 — sağlayıcı katmanı
`[agents.gemini]` bloğu; ardından Ollama + goose. `pcbridged`'e "hangi görev
hangi beyne" kuralı.

### Faz 4 — hafıza
`facts.md` + SQLite konuşma geçmişi; sistem istemine enjeksiyon. Küçük ama
Jarvis hissini asıl bu veriyor.

### Faz 5 — proaktif
Zamanlayıcı: disk doluyor, yedek başarısız, uzun iş takıldı, sabah özeti.
`system_status` çoğu veriyi zaten üretiyor.

### Faz 6 — `[tools.*]` TOML araçları
Müzik, ev otomasyonu, notlar. Takvim/mail/drive'ı buraya yazma — beyin
onları Claude Code'un mevcut connector'larından zaten görüyor.

---

## 9. Karar bekleyen üç şey

1. **Funnel açık mı kalsın?** Claude mobil uygulamasını kullanmaya devam
   edeceksen evet; kendi PWA'na geçtiğinde kapat. İkisini birden istiyorsan
   en azından `[auth] static_token`'ı boş tut ve token ömürlerini kısalt.
2. **Ekranı hiç kilitlemeyecek misin?** Şu anki ayarlarla oturum açık
   kalıyor ve GUI görevleri çalışıyor. Bunun bedeli fiziksel: masana oturan
   herkes açık oturumunu bulur. Ev makinesinde çoğu kişi bunu kabul eder ama
   **bilinçli** kabul etmeli.
3. **Nested oturum yolu araştırılsın mı?** `gnome-extension/nested.sh`
   zaten iç içe GNOME kabuğu başlatabiliyor. Teoride "hep açık bir işçi
   masaüstü" kurulabilir — fiziksel oturum kilitliyken bile GUI görevleri
   orada koşar. **Sınanmadı** ve `nested.sh`'in kendi notları uyarıyor:
   sanal monitörler gerçek DP-1/DP-2 değil, imleç farklı davranıyor, ve
   nested oturum arkasında yetim süreç bırakıyor (bir keresinde 298 tane
   birikip `inotify` limitini doldurmuş). Faz 1–5 bittikten sonra bakılacak
   bir konu, önce değil.

---

## Kapsam dışı (bilinçli)

- **Sesli mod.** İstemedin. İstersen sonradan eklenir ve yine bedava:
  Whisper RTX'te yerel, Piper'ın Türkçe sesi var (`tr_TR-dfki-medium`).
  Android'in kendi tanıyıcısı da bedava ve cihazda çalışıyor. Faz 1–2'yi
  geciktirecek bir şey değil.
- **iOS.** Kotlin oraya taşınmıyor; KMP ile iş mantığı paylaşılır ama arayüz
  yeniden yazılır. Şimdilik kapsam dışı.
- pcbridge'in araç yüzeyini değiştirmek — `pcbridged` **üstüne** biniyor,
  içine değil. 34 aracın davranışı aynı kalıyor.
- Makineyi Funnel dışında herhangi bir yolla internete açmak.

---

## Kaynaklar

- [Claude özel connector'ları (remote MCP)](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) — Free/Pro/Max/Team/Enterprise, mobil uygulamada çalışıyor, ekleme web'den yapılıyor
- [Claude mobilde remote MCP kurulumu](https://dev.to/zhizhiarv/how-to-set-up-remote-mcp-on-claude-iosandroid-mobile-apps-3ce3)
- [Gemini CLI kotaları](https://google-gemini.github.io/gemini-cli/docs/quota-and-pricing.html) — kişisel hesap: 1000 istek/gün, 60 istek/dakika
- [Gemini CLI (MCP desteği)](https://github.com/google-gemini/gemini-cli)
- [Goose + Ollama ile yerel MCP](https://localaimaster.com/blog/goose-ollama-local-agent)
- [Piper sesleri (Türkçe dahil)](https://github.com/rhasspy/piper/blob/master/VOICES.md)
- [Tailscale ücretsiz plan](https://tailscale.com/docs/account/manage-plans/free-plans-discounts)
- [Android geliştirici doğrulaması](https://developer.android.com/developer-verification) — küresel yayılım 2027+, hobici hesabı ücretsiz/20 cihaz
- [Doğrulama SSS](https://developer.android.com/developer-verification/guides/faq) — *"you are free to install apps without verification with ADB"*
- [UnifiedPush — ntfy dağıtıcısı](https://unifiedpush.org/users/distributors/ntfy/) — FCM'siz push
