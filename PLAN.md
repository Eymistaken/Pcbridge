# OPEN DECISIONS

İlk Linux migration’ını engelleyen açık mimari karar kalmadı. Aşağıdaki iki karar sonraki fazların kapsamını etkiliyor:

1. **Linux tamamlandıktan sonra Windows mu, macOS mu önce gelmeli?** Repo belgelerinde onaylanmış bir sıra bulamadım. Aşağıdaki W ve M fazlarına bu nedenle sıra numarası vermedim.
2. ~~**GNOME eklentisi ileride pencere kontrolü için genişletilebilir mi?**~~ **Karara bağlandı (2026-09-12, kullanıcı onayı): dar kapsamda evet.** Gerekçe: GNOME 46 + Wayland’de `Shell.Introspect` ve `Shell.Eval` kapalı (ölçüldü, “Access denied”), kabuğun içinden `Meta.Window.activate` bilinen tek temiz yol; ölçülen 6,6 saniyelik arama yolu kullanıcının tarayıcısında istenmeyen sekme açıyor. Dört koşul: (a) eklenti **tek** D-Bus yöntemi sunar, `ActivateWindow(hedef) -> bool` — taşıma/kapatma/boyutlandırma/pencere listesi yok; (b) yöntem `desktop_unlock.json` grant’ini kontrol eder ve izin kapalıyken reddeder; (c) GNOME araması silinmez, `degraded` fallback olarak kalır ve eklenti kurulu değilken davranış birebir aynıdır; (d) “hızlı” iddiası `audit.log`’un `ms` alanıyla, en az beş çağrının ortalamasıyla kanıtlanır. Ayrıntı ve kabul ölçütü `WALKTHROUGH.md` Adım 2’de.

Planın hazırlık incelemesinde kod değiştirilmedi, migration başlatılmadı ve masaüstü testleri çalıştırılmadı. İncelenen Pcbridge commit’i `22a58240`; Conduit commit’i `c4338f9e`. Conduit çalışma ağacındaki önceden bulunan değişikliklere dokunulmadı. Bu belge, kullanıcının 2026-09-07 tarihli isteğiyle önceki `PLAN.md` içeriğinin yerine kaydedildi; önceki `YAPILACAKLAR.md` silindi. **Güncel durum için `WALKTHROUGH.md`'ye bak:** Faz 0–2 tamamlandı, Gate 0/1/2 geçti.

# Pcbridge Native Core Implementation Plan

> Kurallar ve mimari: **[CLAUDE.md](CLAUDE.md)** · Sıradaki iş: **[WALKTHROUGH.md](WALKTHROUGH.md)**

**Uygulayıcı:** GPT-5.6 Sol  
**Hedef:** Pcbridge’in çok yollu execution modelini koruyarak Linux native altyapısını küçük, geri alınabilir adımlarla Rust’a taşımak.  
**İlk teslim noktası:** Python orchestration sınırları ayrılmış, sürümlü IPC üzerinden çalışan Rust monitor discovery + capture backend’i.  
**Yaklaşım:** Önce davranış sözleşmesi, sonra adapter, sonra native implementation, sonra parity, en son default değişikliği.

## Belge kullanımı ve ilerleme kaydı

- `AGENTS.md`, otomatik yüklenen kısa ve kendi başına yeterli proje rehberidir. Her session başında `PLAN.md`, `WALKTHROUGH.md` veya `CLAUDE.md` dosyasını bütünüyle okuma zorunluluğu yoktur. Task ayrıntısı veya geçmiş ölçüm gerektiğinde yalnızca ilgili bölüme başvurulur.
- Bu dosya implementation sözleşmesidir; günlük çalışma günlüğü değildir. Mimari kararları ve task kimliklerini koru. Kullanıcı talimatı ya da doğrulanmış repo gerçeği bir düzeltme gerektiriyorsa değişikliği gerekçesiyle kaydet.
- **İlerleme kaydı `WALKTHROUGH.md` dosyasındadır.** (2026-09-12 düzeltmesi: dosya `YAPILACAKLAR.md` adıyla oluşturulmuştu, `git mv` ile yeniden adlandırıldı ve migration dışındaki açık işler de aynı listeye alındı. Gerekçe: `window_focus` ölçümü ve imleç katmanı bulguları eski dosya değiştirilirken silinmiş, beş belge var olmayan içeriğe bağlantı verir hale gelmişti.)
- `WALKTHROUGH.md` içinde yapılan ve yapılacak işleri bu plandaki task kimlikleriyle takip et. En üstte kısa durum özeti, aktif iş, sıradaki uygulanabilir iş ve blocker'lar bulunsun. Planın tamamını kopyalama.
- Her task başında durumu `devam ediyor` olarak güncelle. Task sonunda değişen dosyaları, yapılanları, kalan adımları, çalıştırılan test komutlarını ve gerçek sonuçlarını, geçilen/başarısız gate'i, varsa commit kimliğini ve rollback notunu kaydet. Test çalışmadıysa `çalıştırılmadı` yaz; acceptance sağlanmadan `tamamlandı` işaretleme.
- Yarım kalan işte dosyayı yeni bir listeyle değiştirme; mevcut kaydı güncelle ve bir sonraki somut adımı yaz. Başarısız testleri ve açık kararları kayıttan silme. Durum seçenekleri: `bekliyor`, `devam ediyor`, `blokeli`, `tamamlandı`, `ertelendi`.
- **Durum özetini `AGENTS.md` içine kopyalama.** (2026-09-12 düzeltmesi: bu satır eskiden kopyalamayı istiyordu ve `AGENTS.md` fiilen ayrışmaya başlamıştı — aynı dosya bir kez `CLAUDE.md` kopyası olarak üretilip iki günde 83 satır ayrışmıştı.) `AGENTS.md` yalnızca `CLAUDE.md` ve `WALKTHROUGH.md` dosyalarına yönlendirir; durum, test çıktıları ve günlük geçmişi tek yerde, `WALKTHROUGH.md` içinde kalır.
- Migration planının bulunması, ilgisiz her kullanıcı isteğinde migration'a kendiliğinden başlama talimatı değildir. Mevcut kullanıcı görevinin kapsamında ilerle.
- `ADIMLAR.md` ve `UYGULAMA.md` içindeki eski `PLAN.md`/`YAPILACAKLAR.md` bölüm referansları geçmiş döneme aittir. Yeni migration sırasını bu referanslardan türetme; ölçülmüş makine gerçeklerini görevle ilgili olduğunda kullan.

## 1. Mevcut koddan doğrulanan önemli noktalar

Plan aşağıdaki gerçeklere dayanıyor:

| Bulgu | Plana etkisi |
|---|---|
| `tools.py` yaklaşık 2.100 satır; desktop nesneleri, izin zamanlayıcıları ve orchestration aynı registration closure’ında. | Tool kataloğunu baştan parçalamadan `DesktopRuntime` çıkarılacak. |
| `capture.py`, görüntü yakalamanın yanında shot kaydı, ölçekleme ve koordinat dönüşümü yapıyor. | İlk Rust migration yalnızca görüntünün edinilmesini değiştirecek. |
| `screencast_helper.py`, Mutter ScreenCast + Python GI/GStreamer kullanıyor. | İlk native backend doğrudan Rust D-Bus + PipeWire kullanacak; capture için Python GI/GStreamer gerekmeyecek. |
| `SYSTEM_PYTHON = "python3"` PATH üzerinden çözülüyor. | “Sistem Python’u kullanılıyor” varsayımı runtime’da garanti değil; missing dependency testine alınacak. |
| Açık screencast sırasında frame alma hatası otomatik olarak `gnome-screenshot`’a düşmüyor; hata dönüyor. | Fallback davranışı yorumlardan değil, çalışan koddan sabitlenecek. |
| `desktop_unlock`, şu anda uinput kullanılabilirliğini şart koşuyor. | Capture/accessibility izni input cihazından ayrılacak. |
| `capture.to_global()` ortak koordinat giriş noktası. | MCP, batch ve CLI bu girişten geçmeye devam edecek. |
| `mouse` eski shot için uyarıyor; `pcb-do` belirli durumlarda reddediyor. | İlk migration’da bunlar yanlışlıkla eşitlenmeyecek. |
| `SafetyGate`, bilinmeyen lock/activity durumunda bazı kontrolleri geçiriyor; batch focus okunamayınca takibi kapatabiliyor. | Bunlar parity’den ayrı safety task’larında güçlendirilecek. |
| Shell’den GUI açmayı caydıran docstring’ler ve isteğe bağlı blocklist var. | Çok yollu execution kararına uygun, ayrı bir davranış değişikliği yapılacak. |
| `PCBRIDGE_TEST_CAPTURE=1`, `test_real_hold()` üzerinden gerçek input da çalıştırıyor. | İlk task test izinlerini ayıracak. |
| Kurulu FastMCP `3.4.5`, `ToolResult.is_error` ve `structured_content` destekliyor. | MCP üzerinde ikinci bir custom hata protokolü kurulmayacak. |

Başlıca mevcut kaynaklar: [capture.py](pcbridge/desktop/capture.py), [screencast_helper.py](pcbridge/desktop/screencast_helper.py), [safety.py](pcbridge/desktop/safety.py), [batch.py](pcbridge/desktop/batch.py), [tools.py](pcbridge/tools.py).

### Conduit’ten alınacak ve alınmayacak şeyler

Referans olarak incelenen mevcut dosyalar:

- `~/Masaüstü/app/conduit/src-tauri/src/platform/linux/capture.rs`
- `~/Masaüstü/app/conduit/src-tauri/src/platform/linux/portal.rs`
- `~/Masaüstü/app/conduit/src-tauri/src/platform/linux/screen.rs`
- `~/Masaüstü/app/conduit/src-tauri/src/platform/linux/ax.rs`
- `~/Masaüstü/app/conduit/src-tauri/src/platform/linux/apps.rs`
- `~/Masaüstü/app/conduit/src-tauri/Cargo.toml`

**Alınacak fikirler:** PipeWire için ayrılmış thread, frame stride/channel conversion, session ile capture lifecycle ilişkisi, runtime kullanılabilirliğinin raporlanması.

**Devralınmayacak tasarımlar:**

- Tauri/GTK initialization’a bağlı display discovery.
- Global, süreç boyunca bırakılmayan session nesneleri.
- Monitor eşleşmeyince ilk stream’i seçmek.
- Bilinmeyen piksel formatını tahmin ederek yorumlamak.
- Frame timestamp olmadan “en yeni frame” döndürmek.
- Capture oturumunun açılmasını pointer izni kanıtı saymak.
- Pcbridge’in LANCZOS ölçeklemesini nearest-neighbor ile değiştirmek.

Güncel portal sözleşmesinde input yetkileri `Start` sonucundaki cihaz maskesiyle ayrılıyor. RemoteDesktop persistence da sürüme bağlı olarak mevcut; Conduit yorumlarındaki genel “persist edilemez” hükmü taşınmayacak. [RemoteDesktop sözleşmesi](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)

Portal session kapanışı ve `Closed` sinyali lifecycle’ın parçası olacak. [Session sözleşmesi](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Session.html)

## 2. Kararı verilmiş mimari

```text
MCP tools / CLI
       |
       v
Python DesktopRuntime
  ├─ SafetyGate / batch / shot registry / presentation
  ├─ Python compatibility providers
  └─ NativeClient
          |
          | versioned, framed local stdio IPC
          v
Persistent pcbridge-native process
  ├─ lifecycle / cancellation / lease enforcement
  └─ Linux platform modules
       ├─ Mutter / D-Bus
       ├─ PipeWire
       └─ later: uinput / AT-SPI / native state
```

### Katman sahipliği

| Sorumluluk | Karar |
|---|---|
| FastMCP, MCP tool registration | Python’da kalacak. |
| OAuth, HTTP, consent, `/shot` | Python’da kalacak. Bu migration OAuth mantığını değiştirmeyecek. |
| Shell, filesystem, jobs, coding-agent delegation | Python’da kalacak. |
| tmux/session orchestration | Python’da kalacak. |
| `computer_batch` parse, budget, focus policy, remaining actions | Python’da kalacak. |
| Desktop authorization policy, audit | Python’da kalacak. |
| Native kaynakların revoke/expiry sırasında kapatılması | Rust da bağımsız olarak uygulayacak. |
| Shot ID, disk metadata, TTL, MCP image delivery | Python’da kalacak. |
| Screenshot koordinatı → Pcbridge global koordinatı | Tek authoritative giriş: Python `capture.to_global()`. |
| Global koordinat → platform/device koordinatı | Native backend’de tek platform dönüşümü. |
| Display discovery + raw capture | İlk Rust migration. |
| Input injection | Capture default olduktan sonraki Rust migration. |
| Accessibility traversal/action | Daha sonraki Rust migration. |
| Clipboard | İlk aşamada çalışan Python yolu; input migration sonrasında native supervisor’a alınabilir. |
| App seçimi, launch/focus fallback orchestration | Python’da kalacak; mevcut native işlemler adapter arkasına alınacak. |
| Full Rust MCP server | Bu planın dışında. |
| GUI | Son aşamada, isteğe bağlı control plane. |

Buradaki iki koordinat dönüşümü aynı hesabın iki kopyası değildir:

1. `shot` üzerindeki pikselin desktop global noktasına çevrilmesi.
2. Bu global noktanın OS/input API’sinin istediği birime çevrilmesi.

Rust, `shot` ID çözümleyip monitor offset’ini ikinci kez uygulamayacak.

### Rust workspace: iki crate

```text
rust/
  Cargo.toml
  Cargo.lock
  rust-toolchain.toml
  crates/
    pcbridge-core/
      Cargo.toml
      src/
        lib.rs
        protocol.rs
        error.rs
        capability.rs
        display.rs
        frame.rs
        lease.rs
      tests/
    pcbridge-native/
      Cargo.toml
      src/
        lib.rs
        main.rs
        dispatch.rs
        lifecycle.rs
        platform/
          mod.rs
          linux/
            mod.rs
            display.rs
            session.rs
            capture.rs
            desktop_state.rs
      tests/
```

Sonraki modüller kendi task’ları geldiğinde oluşturulacak.

- `pcbridge-core`: OS bağımsız veri yapıları, doğrulama ve saf hesaplar.
- `pcbridge-native`: executable, IPC dispatch ve platform kaynaklarının sahibi.
- Ayrı Linux crate’i başlangıçta gerekmiyor. Windows/macOS geldiğinde de önce aynı crate altında platform modülleri kullanılacak.
- Tauri, React, GTK ve MCP bağımlılıkları Rust workspace’e eklenmeyecek.
- İlk toolchain `1.95.0`: bu makinede kurulu sürüm doğrulandı.
- Başlangıç bağımlılıkları: `serde`, `serde_json`, `thiserror`, `tracing`; Linux için `zbus`, `pipewire`; PNG encoding için yalnızca PNG özelliği açık `image`.
- Çözümlenen sürümler `Cargo.lock` ile sabitlenecek. Conduit’in lockfile’ı kopyalanmayacak.
- `pcbridge-core` için `unsafe` yasak. Platformda zorunlu `unsafe`, küçük bir modülde ve ownership gerekçesiyle sınırlandırılacak.

## 3. IPC contract — uygulayıcı bunu yeniden tasarlamayacak

### Transport ve framing

**Transport:** Python’ın başlattığı child process’in özel stdin/stdout pipe’ları.

Bu pipe, Python MCP server’ın dış stdio transport’undan ayrıdır. Native stdout hiçbir şekilde MCP stdout’una doğrudan bağlanmayacak.

**Her frame:**

```text
4 byte unsigned big-endian JSON header length
JSON header, UTF-8
header.binary_len kadar binary payload
```

Kurallar:

- Header en fazla 64 KiB.
- Binary payload en fazla 128 MiB.
- Capture’da bir response yalnızca bir monitor frame’i taşır.
- Request/response header’ında `binary_len` daima bulunur; yoksa protokol hatası.
- Kısmi read/write normal kabul edilir; `read_exact` davranışı uygulanır.
- Geçersiz uzunluk, kesik frame veya bilinmeyen major sürümde bağlantı kapatılır.
- PNG native pipe üzerinde base64 yapılmaz. MCP `ImageContent` için gereken base64 Python’da, son görüntüden üretilir.
- Native process’e çıktı dosyası yolu verilmez. Böylece native dosya yazımı ve istemci MEDIA/path sözleşmesi birbirine bağlanmaz.

### Mesaj örnekleri

İlk handshake:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:1",
  "method": "initialize",
  "params": {
    "client_version": "pcbridge-build-id",
    "supported_minor": [0],
    "state_dir": "/absolute/private/state",
    "runtime_dir": "/absolute/private/runtime"
  },
  "binary_len": 0
}
```

Yanıt:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:1",
  "result": {
    "instance_id": "native-instance-id",
    "native_version": "pcbridge-build-id",
    "platform": "linux",
    "features": ["display.snapshot", "capture.on_demand"]
  },
  "binary_len": 0
}
```

Capture isteği:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:18",
  "method": "capture.frame",
  "params": {
    "display_id": "mutter:DP-1",
    "topology_id": "layout-fingerprint",
    "session_id": "capture-session-id",
    "grant_id": "grant-id",
    "revoke_epoch": "epoch-id",
    "timeout_ms": 8000,
    "freshness": "after_request"
  },
  "binary_len": 0
}
```

Başarılı yanıt, ardından PNG byte’ları:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:18",
  "result": {
    "display_id": "mutter:DP-1",
    "topology_id": "layout-fingerprint",
    "session_id": "capture-session-id",
    "frame_sequence": 42,
    "frame_timestamp_ns": 151412335,
    "frame_identity_source": "source_monotonic_clock",
    "pixel_size": [1920, 1080],
    "desktop_rect": [1920, 0, 1920, 1080],
    "stale_frames": 0,
    "wait_ms": 58.6,
    "encode_ms": 34.0,
    "backend": "linux.mutter.pipewire",
    "mime_type": "image/png"
  },
  "binary_len": 431000
}
```

Hata:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:18",
  "error": {
    "code": "PERMISSION_REQUIRED",
    "message": "Ekran paylaşımı izni gerekli.",
    "retryable": false,
    "category": "permission",
    "suggested_action": "approve_capture_permission",
    "permission_scope": "os.capture",
    "backend": "linux.portal"
  },
  "binary_len": 0
}
```

### Request ID ve concurrency

- ID: client instance kimliği + monoton artan sayaç.
- Python’da bir reader thread response’ları ID’ye göre pending request’lere dağıtır.
- Writer lock yalnızca frame yazımını korur.
- Yanıtların request sırasıyla gelmesi zorunlu değildir.
- Native tarafında capture işlemleri session başına sıralanır.
- Input işlemleri tek bir execution lane kullanır.
- `cancel`, `revoke`, `shutdown` capture/encoding kuyruğunun arkasında beklemez.
- Başlangıç sınırı: en fazla 16 pending request; fazlası `BUSY`.

### Timeout ve cancellation

| İşlem | Üst sınır |
|---|---:|
| Binary startup + initialize | 3 saniye |
| Status/capability snapshot | 2 saniye |
| Display discovery | 10 saniye |
| Mutter capture session kurulumu | 15 saniye |
| Tek frame bekleme | 8 saniye |
| Accessibility read/action | Mevcut 20/25 saniye sınırları |
| Normal shutdown | 2 saniye; sonra terminate/kill |
| Bir MCP çağrısının toplamı | 110 saniyeden kısa |

- Python, kalan deadline’ı IPC’ye gönderir.
- Batch adımları ve final screenshot aynı toplam deadline’ı paylaşır.
- Timeout’ta ID’ye yönelik `cancel` gönderilir.
- Capture cancellation sonucu geç gelen frame yayımlanmaz.
- Kısmen yürümüş input işlemi geri alınmış sayılmaz.
- Input timeout/crash sonucu kesin bilinmiyorsa `EXECUTION_UNKNOWN`; otomatik tekrar yasak.
- Release/cleanup girişimi yapılır; başarılı olduğu ölçülmeden “bütün tuşlar bırakıldı” denmez.

### Süreç ömrü

- Her uzun ömürlü Python MCP process’i en fazla bir native child tutar.
- İlk fazda kullanıcı başına paylaşılan daemon/socket kurulmaz.
- CLI process’i gerektiğinde kendi child’ını açar ve çıkarken kapatır.
- Desktop kapalıyken server açılışı native capture session başlatmaz.
- `initialize` ve capability sorgusu permission dialog açmaz.
- Python stdin’i kapanırsa native bütün kaynaklarını kapatıp çıkar.
- Native crash, Python server’ı veya shell/jobs araçlarını düşürmez.
- Read-only discovery için bir sonraki çağrıda en fazla bir restart yapılabilir.
- Capture/input crash sonrası aktif işlemler tekrarlanmaz; yeni capture session açık bir yeniden etkinleştirme adımı ister.
- Crash sırasında native session kimliği ve frame cache geçersizleşir.

### Binary discovery ve loglar

Sıra:

1. Açıkça verilmiş `PCBRIDGE_NATIVE_BIN`.
2. `[native] binary_path`.
3. Paketlenmiş `pcbridge/_native/<target>/pcbridge-native`.
4. Hiçbiri yoksa `NATIVE_NOT_FOUND`.

Geliştirme binary’si otomatik PATH taramasıyla bulunmayacak; test komutunda açıkça gösterilecek.

- Yanlış executable/protokol sürümü sessizce kabul edilmeyecek.
- Helper `config.toml` okumayacak; parola/token almayacak.
- Stderr ayrı thread ile sürekli boşaltılacak.
- Bellekte son 64 KiB tanı tutulacak; kalıcı log boyut sınırı olacak.
- IPC payload, görüntü, clipboard içeriği ve yazılan metin loglanmayacak.
- Major değişikliği uyumsuzdur. Minor değişiklikleri yalnızca ek alan/metot; negotiated feature olmadan yeni metot çağrılmayacak.

## 4. Ortak veri sözleşmeleri

### Native ve Python arasında

| Tip | Sorumluluk |
|---|---|
| `ProtocolVersion` | Major/minor negotiation. |
| `NativeError` / Python `DesktopError` | Stable code ve güvenli kullanıcı mesajı. |
| `CapabilitySnapshot` | Platform, backend, durum, sınırlama, doğrulama zamanı. |
| `DisplaySnapshot` | Tek bir topology kimliği ve o andaki monitor tablosu. |
| `DisplayInfo` | Stable backend ID, connector, public index, geometry, scale, transform. |
| `RawFrame` | Sahip olunan RGBA byte’ları, pixel boyutu, sequence, timestamp, session/topology kimliği. |
| `CapturedImage` | Native PNG byte’ları + raw frame metadata. |
| `DesktopGrant` | Grant ID, revoke epoch, `until`, `hard_until`. |
| `ExecutionContext` | Request ID, deadline, grant, cancellation. |
| `ElementRef` | Accessibility backend referansı + app/window/snapshot kimliği. |

Python provider interface’leri:

- `CaptureProvider`: `display_snapshot`, `start`, `capture_frame`, `stop`, `capabilities`, `close`.
- `InputProvider`: mevcut `InputBackend` public işlemleri.
- `AccessibilityProvider`: dump, window/focus read, native action, editable text.
- `DesktopStateProvider`: lock/activity observations.

Bunlar `pcbridge/desktop/contracts.py` içinde tanımlanacak. İlk task’ta kullanılmayan interface’ler boş implementation olarak üretilmeyecek.

### Capability modeli

Her capability için:

```text
state:
  supported
  unsupported
  unavailable
  permission_required
  degraded

backend
scope
reason_code
limitations[]
observed_at
evidence: probe | operation
usable_now
```

Ayrı top-level authorization durumu:

```text
desktop_enabled
grant_remaining_seconds
hard_remaining_seconds
screen_lock_state
revoke_epoch
```

Böylece “capture implementasyonu kullanılabilir” ile “Pcbridge masaüstü izni şu an açık” ayrılır.

Zorunlu capability anahtarları:

- `capture.monitor`
- `capture.window`
- `input.pointer`
- `input.keyboard`
- `accessibility.read`
- `accessibility.action`
- `window.list`
- `window.focus`
- `window.move_resize`
- `clipboard.read`
- `clipboard.write`
- `user_activity`
- `screen_lock`

Örnek kararlar:

- GNOME AT-SPI window list: **degraded**, yalnızca görünür accessibility uygulamaları.
- GNOME aramasıyla focus: **degraded**, native activation değil.
- Kullanılabilir move/resize backend’i yok: **unsupported**.
- GI eksik legacy capture + çalışan screenshot fallback: **degraded**.
- Portal capture açık, pointer verilmemiş: capture ve pointer farklı durumlar.
- Capability probe hiçbir mouse/keyboard olayı göndermeyecek.

### Hata taxonomy

Kodlar mesaj metninden türetilmeyecek.

| Kategori | Başlangıç kodları |
|---|---|
| `safety` | `DESKTOP_DISABLED`, `GRANT_REQUIRED`, `GRANT_EXPIRED`, `REVOKED`, `SCREEN_LOCKED`, `LOCK_STATE_UNKNOWN`, `USER_ACTIVE`, `ACTIVITY_UNKNOWN`, `RATE_LIMITED` |
| `permission` | `PERMISSION_REQUIRED`, `PERMISSION_DENIED`, `DEVICE_NOT_GRANTED` |
| `capability` | `UNSUPPORTED`, `BACKEND_UNAVAILABLE`, `DEPENDENCY_MISSING` |
| `capture` | `FRAME_TIMEOUT`, `STALE_FRAME`, `FRAME_FORMAT_UNSUPPORTED`, `FRAME_TOO_LARGE`, `DISPLAY_CHANGED`, `DISPLAY_MAPPING_UNKNOWN`, `IMAGE_DELIVERY_FAILED` |
| `coordinate` | `SHOT_NOT_FOUND`, `SHOT_INVALID`, `SHOT_STALE`, `AMBIGUOUS_COORDINATE` |
| `accessibility` | `TARGET_MISMATCH`, `ELEMENT_STALE`, `ELEMENT_AMBIGUOUS`, `ACTION_UNSUPPORTED`, `TEXT_MISMATCH` |
| `execution` | `TIMEOUT`, `CANCELLED`, `EXECUTION_UNKNOWN`, `BUSY` |
| `ipc` | `NATIVE_NOT_FOUND`, `PROTOCOL_MISMATCH`, `NATIVE_CRASHED`, `INVALID_FRAME` |

Her hata `code`, `message`, `category`, `retryable`, `suggested_action` taşıyacak. Gerektiğinde `permission_scope`, `backend`, `execution_state` eklenecek.

`retryable=true`, otomatik yeniden execution izni değildir.

(2026-09-19 düzeltmesi, Task 6.3: `accessibility` kategorisine `TEXT_MISMATCH` eklendi. Gerekçe: GTK4'te en fazla 5 karakter tutan bir alana yazınca uygulama "başarılı" cevap verip 5 karakter tutuyor (ölçüldü), yani yazılan metin geri okunup karşılaştırılıyor. Alan değişti ama istenen metni tutmuyor. Mevcut kodlardan hiçbiri bunu söylemiyordu: `TARGET_MISMATCH` hedefin değiştiğini söyler ve yeni `ui_dump` önerir (hedef değişmedi), `ACTION_UNSUPPORTED` hiçbir şey yapılmadığını ima eder (alan yazıldı), `EXECUTION_UNKNOWN` sonucun bilinmediğini söyler (biliniyor). Python yardımcısı bu durumu önce kodsuz bildiriyordu ve sağlayıcı onu `TARGET_MISMATCH` sayıyordu.)

(2026-09-13 düzeltmesi, Task 3.5: `capture` kategorisine `IMAGE_DELIVERY_FAILED` eklendi. Gerekçe: Task 3.5 capture başarısı ile görüntünün istemciye bütün olarak ulaşmasını ayırıyor; yayımlanmış bir çekimin PNG'si okunamadığında ya da boyutu kaydıyla uyuşmadığında bu bir frame hatası değil, ama başarı da değil. Mevcut kodlardan biri seçilseydi ya yanlış katmanı (`INVALID_FRAME`, IPC) ya da hiçbir şey söylemeyen `EXECUTION_UNKNOWN`'u işaret ederdi.)

## 5. Capture için verilmiş kararlar

### İlk backend

**GNOME/Mutter ScreenCast + doğrudan PipeWire.**

Gerekçe: Pcbridge’in çalışan Linux yolunu korur; aynı anda portal input mimarisi ve capture migration yapılmaz. Portal capture daha sonra ayrı backend olarak eklenir.

- Mutter D-Bus bağlantısı native process’in sahibi olduğu kalıcı bağlantı.
- Monitorler connector üzerinden `RecordMonitor` ile bağlanır.
- Stream event aboneliği `Start` öncesinde kurulur.
- Session izni açıkken yaşar; her screenshot’ta yeniden kurulmaz.
- İlk sürümde **OnDemand**: capture talebi geldiğinde PipeWire consumer etkinleşir; istekten sonra alınan geçerli frame döner; consumer bırakılır.
- Session açık tutulması, sürekli frame biriktirmek anlamına gelmez.
- Frame tüketimi için ayrılmış PipeWire thread.
- `RawFrame`: tightly packed RGBA8, alpha 255, owned buffer.
- İlk sürümde yalnızca doğrulanmış packed RGB/BGR formatları ve CPU erişilebilir buffer.
- DMA-BUF/EGL, HDR ve GPU zero-copy kapsam dışı.
- Stride, chunk offset/size, overflow, buffer boyu ve format doğrulanır.
- Bilinmeyen format tahmin edilmez.
- Native lossless PNG encode eder; Python son crop/resize/publish işlemlerini yapar.
- `Image.LANCZOS`, 1536 default ve mevcut oversize uyarısı korunur.
- `monitor="window"` ilk native capture’ın parçası değildir; legacy yol açıkça raporlanır.
- `include_pointer` değişikliği session’ın kontrollü yeniden kurulmasını gerektirebilir; eski frame’ler atılır.

### Fallback politikası

İki ayrı ayar katmanı kullanılacak:

- Mevcut `[desktop] capture_backend`: `auto | screencast | gnome-screenshot`.
- Yeni `[native] capture`: `python | rust | auto`.

Anlamları:

| Ayar | Davranış |
|---|---|
| `native.capture=python` | Mevcut Python implementation. |
| `native.capture=rust` | Rust screencast zorunlu; sessiz Python screencast dönüşü yok. |
| `native.capture=auto` | Yeni grant/session başında Rust denenir; uygun başlangıç hatalarında legacy provider seçilebilir. |
| `desktop.capture_backend=gnome-screenshot` | Capture implementation ayarından bağımsız açık legacy screenshot seçimi. |
| `desktop.capture_backend=screencast` | Monitor capture’da screenshot fallback yasak. |
| `desktop.capture_backend=auto` | Session kurulamazsa screenshot fallback mümkün; kullanılan yol görünür. |

Ek kurallar:

- Permission denial, revoke, lock, yanlış topology veya geçersiz koordinat nedeniyle fallback yapılmaz.
- Aktif session ortasında backend değiştirilmez.
- Bir `all` capture sonucu farklı backend’lerin kısmi görüntülerinden oluşturulmaz.
- Native failure ile GUI input aynı çağrı içinde başka backend’e otomatik tekrar gönderilmez.
- `window` capture’ın mevcut legacy davranışı ayrı operation olarak korunur; native monitor desteği var diye native window desteği iddia edilmez.

---

# Phase 0 — Güvenli ve tekrarlanabilir baseline

## Task 0.1 — Gerçek capture ve input test izinlerini ayır

**Amaç:** Capture testi çalıştırılırken yanlışlıkla klavye/fare girdisi gönderilmesini önlemek.  
**Ön koşul:** Yok.

**İncelenecek mevcut dosyalar:**

- `tests/test_desktop.py`
- `tests/test_e2e.py`
- `CLAUDE.md`

**Değiştirilecek/oluşturulacak:**

- Değiştir: `tests/test_desktop.py`, `CLAUDE.md`
- Yeni: `tests/test_test_safety.py`

**Korunacak/compatibility:** Mevcut test senaryoları ve assertion’lar silinmez; yalnızca opt-in sınırları düzeltilir.

**Implementation:**

1. `test_real_hold` ve gerçek uinput kullanan her test için `PCBRIDGE_TEST_INPUT=1` zorunlu yap.
2. Gerçek batch için hem `PCBRIDGE_TEST_INPUT=1` hem `PCBRIDGE_TEST_BATCH=1` iste.
3. `PCBRIDGE_TEST_CAPTURE=1` yalnızca capture açsın.
4. Alt süreçte device constructor’ını “çağrılırsa fail” sentinel’iyle değiştirerek capture-only seçimin input açmadığını doğrula.
5. Dokümantasyondaki birleşik test komutunu düzelt.

**Testler:** `tests/test_test_safety.py`, mevcut desktop suite; bütün real opt-in değişkenleri unset.

**Acceptance:** Capture-only test seçimi hiçbir uinput constructor/write çağrısı yapmıyor; default suite gerçek input çalıştırmıyor.

**Rollback:** Yeni test seçici geri alınabilir; güvenli komut sınırı korunmadan gerçek capture suite çalıştırılmaz.

**Yapılmayacak:** Gerçek desktop input denemesi, runtime refactor, native kod.

## Task 0.2 — Public contract ve backend parity fixture’larını oluştur

**Amaç:** Migration’ın karşılaştırılacağı, private config’e bağlı olmayan baseline üretmek.  
**Ön koşul:** 0.1.

**İncelenecek:**

- `tests/test_desktop.py`, `tests/test_models.py`, `tests/test_e2e.py`
- `pcbridge/desktop/capture.py`, `monitors.py`, `batch.py`, `uitree.py`
- `pcbridge/tools.py`, `pcbridge/cli/shot.py`, `pcbridge/cli/do.py`

**Yeni dosyalar:**

- `tests/contracts/__init__.py`
- `tests/contracts/test_capture_contract.py`
- `tests/contracts/test_coordinate_contract.py`
- `tests/contracts/test_mcp_contract.py`
- `tests/fixtures/native/display_cases.json`
- `tests/fixtures/native/coordinate_cases.json`
- `docs/native/baseline.md`

**Implementation:**

1. Mevcut güvenli testleri çalıştır; gerçek assertion sayısını ve hataları kaydet.
2. Test config’lerini `config.example.toml` veya sentetik `Config` üzerinden kur.
3. MCP tool adları, input schema, defaults, annotations ve content block sırasını snapshot olarak sabitle.
4. İki monitor, primary sağda, portrait, ölçekleme, crop-before-resize, shot lookup, `--out`, stale/ambiguous koordinat fixture’larını ekle.
5. Synthetic renkli canvas ve küçük metin fixture’ları kullan; gerçek screenshot’ı repoya koyma.
6. Native adapter eklendiğinde aynı contract testlerinin provider factory ile çalışabileceği düzeni kur.

**Testler:** Mevcut iki script + `unittest` contract suite.

**Acceptance:** Baseline private config, çalışan server, gerçek agent ve desktop olmadan tekrar üretilebiliyor.

**Rollback:** Yalnızca test/docs commit’i geri alınır.

**Yapılmayacak:** Önceden başarısız testi gizlemek, test beklentisini açıklamasız değiştirmek, canlı `test_e2e.py` çalıştırmak.

---

# Phase 1 — Python sınırlarını temizle

## Task 1.1 — `DesktopRuntime` ve Python provider adapter’ını çıkar

**Amaç:** Tool registration ile desktop kaynak sahipliğini ayırmak.  
**Ön koşul:** 0.2.

**İncelenecek:**

- `pcbridge/tools.py`
- `pcbridge/desktop/ops.py`
- `pcbridge/desktop/screencast.py`
- `pcbridge/cli/__init__.py`, `shot.py`, `do.py`, `lock.py`
- `pcbridge/server.py`

**Değiştirilecek/oluşturulacak:**

- Yeni: `pcbridge/desktop/contracts.py`
- Yeni: `pcbridge/desktop/runtime.py`
- Yeni: `pcbridge/desktop/backends/__init__.py`
- Yeni: `pcbridge/desktop/backends/python.py`
- Yeni: `tests/contracts/test_runtime_contract.py`
- Değiştir: Yukarıdaki caller dosyaları.

**Implementation:**

1. `DesktopRuntime`, input/tree/capture provider ve `SafetyGate` nesnelerinin sahibi olsun.
2. `tools.py` içindeki screencast timer yönetimini runtime’a taşı.
3. `computer_task` heartbeat orchestration’ını Python’da tut; runtime grant touch arayüzünü kullansın.
4. MCP ve CLI aynı runtime factory’yi kullansın.
5. `DeviceOps` bağımlılıklarını constructor üzerinden alsın.
6. Server lifecycle çıkışında ve CLI `finally` bloğunda idempotent `close()` bağla.

**Korunacak:** Tool adları, batch `Ops`, gate sırası, session environment repair, shot dizinleri.

**Interface:** `DesktopRuntime.close()`, `capture_provider`, `input_provider`, `accessibility_provider`, `gate`.

**Test/acceptance:** Baseline suite geçiyor; runtime oluşturmak capture/input başlatmıyor; iki runtime birbirinin response’ını tüketmiyor.

**Rollback:** Caller’lar eski construction yoluna alınır; legacy modüller yerinde durur.

**Yapılmayacak:** Bütün `tools.py` dosyasını tool başına modüllere bölmek; OAuth/jobs refactor.

## Task 1.2 — Runtime capability ve typed error katmanını ekle

**Amaç:** “Tool var” ile “bu makinede kullanılabilir” ayrımını kurmak.  
**Ön koşul:** 1.1.

**İncelenecek:**

- `pcbridge/desktop/safety.py`
- `pcbridge/desktop/input.py`
- `pcbridge/desktop/capture.py`
- `pcbridge/desktop/uitree.py`
- `pcbridge/desktop/apps.py`
- `pcbridge/tools.py`

**Yeni/değişen:**

- Yeni: `pcbridge/desktop/errors.py`
- Yeni: `pcbridge/desktop/capabilities.py`
- Yeni: `tests/contracts/test_capabilities.py`
- Değiştir: `contracts.py`, `runtime.py`, `backends/python.py`

**Implementation:**

1. Yukarıdaki taxonomy ve capability tiplerini oluştur.
2. Legacy exception’ları provider sınırında typed hataya çevir; mesaj substring’iyle classification yapma.
3. Capture, pointer, keyboard, accessibility ve window durumlarını ayrı probe et.
4. Eksik input cihazının capture/accessibility capability’sini kapatmasını önle.
5. Probe sonucu ile son gerçek operation sonucunu ayrı evidence olarak sakla.
6. Cache’i dependency/session/topology değişiminde geçersizleştir.

**Korunacak:** Türkçe kullanıcı mesajları; shell/filesystem/job yetki ayrımı.

**Test/acceptance:** “Capture supported + pointer permission_required” ve “AT-SPI list degraded + move_resize unsupported” sentetik olarak temsil ediliyor; probe input göndermiyor.

**Rollback:** Provider eski hata adapter’ına dönebilir.

**Yapılmayacak:** Portal prompt, window focus denemesi, yeni native backend.

## Task 1.3 — MCP capability ve hata sonuçlarını doğal FastMCP semantics ile sun

**Amaç:** Agent’ın permission ve backend hatalarında doğru sonraki adımı seçmesi.  
**Ön koşul:** 1.2.

**İncelenecek:**

- `pcbridge/tools.py`
- `pcbridge/server.py`
- `requirements.txt`
- `tests/test_e2e.py`
- Kurulu FastMCP: `fastmcp/tools/base.py`

**Yeni/değişen:**

- Yeni: `pcbridge/desktop/presentation.py`
- Yeni: `tests/contracts/test_mcp_errors.py`
- Değiştir: `tools.py`, `requirements.txt`, `KULLANIM.md`, `CLAUDE.md`

**Implementation:**

1. `system_capabilities` adlı read-only, side effect oluşturmayan MCP tool ekle.
2. Başlangıçta doğrulanan FastMCP `3.4.5` sürümünü sabitle; aynı commit’te genel dependency upgrade yapma.
3. Desktop execution hatalarında `ToolResult(content=..., structured_content=..., is_error=True)` kullan.
4. Eski insan tarafından okunabilir metni content içinde koru; structured error’ı ayrıca taşı.
5. Dinamik desktop response’larında otomatik `outputSchema` çıkarımını açıkça kapat; wire-level testle doğrula.
6. `screen_capture` hata dallarının tek `TextContent` yerine tutarlı sonuç üretmesini sağla.
7. `computer_batch` final capture başarısızsa tamamlanmış action raporunu korusun; action’ları tekrar etmesin.

**Compatibility:** Eski tool adları/input parametreleri korunur. Hataların `isError=true` olması bilinçli, belgelenmiş semantics düzeltmesidir. Başarılı metin/görüntü sırası korunur.

**Testler:** In-memory FastMCP client ile hata content’i, `isError`, structured data ve batch partial sonucu.

**Acceptance:** Output schema hatası yok; permission hatası `pcbridge.desktop`, `os.capture` veya `os.pointer` scope’unu ayırıyor.

**Rollback:** Presentation adapter geri alınır; iç typed errors kalabilir.

**Yapılmayacak:** OAuth error formatını değiştirmek; bütün shell/job çıktısını custom JSON envelope’a sarmak.

## Task 1.4 — Çok yollu execution sözleşmesini düzelt

**Amaç:** Deterministik shell/filesystem/accessibility yollarını ürünün normal davranışı yapmak.  
**Ön koşul:** 0.2; 1.1 ile bağımsız uygulanabilir.

**İncelenecek/değiştirilecek:**

- `pcbridge/tools.py`
- `pcbridge/server.py`
- `pcbridge/config.py`
- `config.example.toml`
- `skills/computer-use/SKILL.md`
- `KULLANIM.md`, `CLAUDE.md`
- `tests/test_desktop.py`

**Yeni test:** `tests/contracts/test_execution_paths.py`

**Implementation:**

1. Shell docstring’lerindeki mutlak “GUI uygulaması açma” yasağını kaldır.
2. `ui_dump` açıklamasındaki “görüntü okuyamazsın” gibi eski istemci varsayımlarını kaldır.
3. `block_gui_launch_in_shell` varsayılanını `false` yap; kullanıcının açıkça verdiği `true` ve blocklist’i okumaya devam et.
4. Process lifetime isteyen app launch ile mevcut Chrome oturumuna URL gönderme arasındaki farkı belgeye yaz.
5. `desktop_unlock` açıklamasını yalnızca Pcbridge grant’i olduğunu söyleyecek şekilde düzelt.
6. Permission hatası üzerine başka execution yoluna geçmenin kullanıcı görevini ve mevcut izin kapsamını koruması gerektiğini belirt.

**Korunacak:** Shell audit, timeout, workdir çözümü, background jobs ve explicit kullanıcı config’i.

**Test/acceptance:** Mock `shell_run` Chrome URL komutunu varsayılan config’te geçiriyor; explicit blocklist hâlâ uygulanıyor; desktop kapalıyken shell/filesystem/job araçları kullanılabiliyor.

**Rollback:** Default ve açıklama commit’i geri alınır.

**Yapılmayacak:** Shell komutunu otomatik GUI tıklamasına çevirmek; Chrome’u gerçek masaüstünde açmak; web extraction’ı GUI başarı kanıtı saymak.

---

# Phase 2 — Native process, IPC ve revoke temeli

## Task 2.1 — Rust workspace ve executable protocol harness

**Amaç:** Native API’lere dokunmadan transport sınırını doğrulamak.  
**Ön koşul:** 0.2.

**İncelenecek:** `requirements.txt`, `run.sh`, `pcbridge/desktop/screencast.py`.

**Oluşturulacak:**

- Yukarıda tanımlanan iki crate’in manifest ve giriş dosyaları.
- `rust/crates/pcbridge-core/src/protocol.rs`
- `rust/crates/pcbridge-core/src/error.rs`
- `rust/crates/pcbridge-native/src/dispatch.rs`
- `rust/crates/pcbridge-native/tests/ipc_protocol.rs`
- `docs/native/protocol-v1.md`
- `.gitignore` içine `rust/target/`.

**Implementation:**

1. Toolchain ve workspace’i kur.
2. Framing/limit/handshake contract’ını uygula.
3. Yalnızca `initialize`, `ping`, `capabilities`, `cancel`, `shutdown` metotlarını ekle.
4. Test modunda deterministik fake response üret; production backend bunu kullanamasın.
5. EOF ve malformed frame sonrası temiz exit testlerini ekle.
6. stdout’a yalnızca framed response yazıldığını doğrula.

**Korunacak:** Python hâlâ varsayılan ve tek çalışan desktop backend.

**Acceptance:** Partial frame, fazla büyük payload, unknown method/version ve concurrent ID testleri geçiyor; executable desktop’a bağlanmıyor.

**Rollback:** Workspace bağımsız commit olarak geri alınır.

**Yapılmayacak:** PipeWire, Tauri, input, daemon/socket kurulumu.

## Task 2.2 — Python `NativeClient` supervisor

**Amaç:** Native crash’in MCP transport’unu ve diğer execution yollarını etkilememesi.  
**Ön koşul:** 1.1, 2.1.

**Yeni/değişen:**

- Yeni: `pcbridge/native/__init__.py`
- Yeni: `pcbridge/native/protocol.py`
- Yeni: `pcbridge/native/client.py`
- Yeni: `pcbridge/native/discovery.py`
- Yeni: `tests/contracts/test_native_client.py`
- Değiştir: `pcbridge/config.py`, `config.example.toml`

**Implementation:**

1. Binary discovery sırasını uygula.
2. Reader/writer/stderr thread’lerini ve pending request map’ini ekle.
3. Deadline, cancellation, pending limit ve shutdown escalation’ı uygula.
4. EOF/crash durumunda bütün pending future’ları typed hata ile tamamla.
5. Request’leri restart sonrası yeniden göndermeyi engelle.
6. Job child process’lerine native IPC descriptor’larının geçmesini önle.
7. `[native] capture="python"` default’unu ve binary path ayarını gerçekten parse et.

**Testler:** Fake helper ile stderr flood, out-of-order response, timeout, kill, invalid protocol, missing binary.

**Acceptance:** Helper yokken server ve non-desktop tools başlıyor; native stdout MCP stdout’unu bozmuyor; child shutdown sonrası reap ediliyor.

**Rollback:** `native.capture=python`.

**Yapılmayacak:** Native binary’yi otomatik indirmek; runtime’da Cargo build çalıştırmak.

## Task 2.3 — Atomik grant ve süreçler arası revoke

**Amaç:** MCP/CLI process’lerinin eski grant veya açık native session ile erişimi sürdürmesini önlemek.  
**Ön koşul:** 1.1, 2.2.

**İncelenecek:**

- `pcbridge/desktop/safety.py`
- `pcbridge/cli/lock.py`
- `pcbridge/desktop/screencast.py`
- `systemd/pcbridge.service`
- GNOME eklentisinin `state.js` dosyası.

**Yeni/değişen:**

- Yeni: `pcbridge/desktop/lease.py`
- Yeni: `pcbridge/native/registry.py`
- Yeni: `rust/crates/pcbridge-core/src/lease.rs`
- Yeni: `rust/crates/pcbridge-native/src/lifecycle.rs`
- Yeni: `tests/contracts/test_lease_contract.py`
- Yeni: `tests/integration/test_native_revoke.py`
- Değiştir: `safety.py`, `runtime.py`, `cli/lock.py`.

**Contract:**

- `desktop_unlock.json` mevcut `until`, `hard_until`, `reason`, `granted`, `granted_by` alanlarını korur.
- Yeni alanlar: `schema_version`, `grant_id`, `revoke_epoch`.
- Unix’te lock ayrı sabit lockfile üzerinde alınır; JSON aynı dizinde temp + atomic replace ile yazılır.
- Kullanıcı/desktop session runtime dizini `0700`; registry dosyaları `0600`.
- Aynı desktop session’daki bütün native helper’lar ortak revoke epoch’u izler.
- Native helper grant oluşturamaz veya süresini uzatamaz.

**Implementation:**

1. Python read-modify-write işlemlerini process lock altında atomik yap.
2. `desktop_lock` önce grant’i kapatsın ve global revoke epoch’u değiştirsin; sonra kaynak kapatsın.
3. Native watchdog en fazla 200 ms aralıkla epoch ve grant geçerliliğini kontrol etsin.
4. Native her dispatch öncesinde tekrar doğrulasın.
5. Registry’de PID yanında process başlangıç kimliği ve instance ID tut; PID reuse durumunda yanlış süreci öldürme.
6. Legacy helper’lar için mevcut `kill_helpers()` geçiş boyunca kalsın.
7. Eski grant dosyası Python’da okunabilir; yeni native session için fresh grant gerekir.
8. İlk native opt-in öncesinde eski stdio process’lerini kapatma ve izinleri revoke etme adımını runbook’a ekle.

**Test/acceptance:** İki Python + iki fake native process senaryosunda revoke sonrası yeni işlem başlamıyor; idle helper kaynakları ≤1 saniyede bırakıyor; late heartbeat grant’i diriltmiyor; GNOME görsel state reader’ı bozulmuyor.

**Rollback:** Önce revoke; bütün helper’ları kapat; Python backend’e dön. Eski aktif grant’i geri yükleme.

**Yapılmayacak:** OAuth grant’lerini buraya taşımak; shell/jobs yetkisini desktop grant’e bağlamak; PID adına göre geniş process kill.

## Task 2.4 — Native lock/activity observations ve safety ayrımı

**Amaç:** Capture session’ın kilitli ekranda veya izni bitince çalışmasını engellemek.  
**Ön koşul:** 2.3.

**İncelenecek:** `pcbridge/desktop/safety.py`, `pcbridge/desktop/batch.py`.

**Yeni/değişen:**

- Yeni: `rust/crates/pcbridge-native/src/platform/linux/desktop_state.rs`
- Yeni: `tests/contracts/test_desktop_state.py`
- Yeni: `rust/crates/pcbridge-native/tests/desktop_state.rs`
- Değiştir: `runtime.py`, `safety.py`, `backends/python.py`.

**Implementation:**

1. GNOME ScreenSaver ve Mutter IdleMonitor okumalarını typed observation olarak sun.
2. `known_locked`, `known_unlocked`, `unknown` durumlarını bool’a indirgeme.
3. Varsayılan policy’de bilinmeyen lock durumunda desktop read/write reddedilsin.
4. Bilinmeyen activity durumunda write reddedilsin; mevcut `force=true` yalnızca activity kontrolünü aşabilsin.
5. Native aktif kaynaklar varken lock sinyalini izle; bağlantı kaybını unknown sayıp kaynakları kapat.
6. `desktop_unlock` için uinput zorunluluğunu kaldır; grant sonucu capability sınırlamalarını bildirsin.
7. Activity kontrolünü batch/görev başında tut; kendi input’unu kullanıcı etkinliği sayan mid-batch kontrol ekleme.

**Compatibility:** Güvenlik belirsizliğinde önceki permissive davranış bilinçli olarak sıkılaştırılır; desktop default `false` değişmez.

**Acceptance:** Capture-only makine unlock olabilir; pointer yine unavailable kalır. `force` lock/revoke/expiry’yi aşamaz.

**Rollback:** Native observation provider Python’a döner; yeni fail-closed policy silinmez.

**Yapılmayacak:** Fiziksel input dinlemek; `/dev/input` izni istemek; tüm SafetyGate’i Rust’ta yeniden yazmak.

---

# Phase 3 — İlk Rust subsystem: monitor discovery ve capture

## Task 3.1 — Native display snapshot

**Amaç:** Capture ve input’un aynı monitor gerçeğini kullanması.  
**Ön koşul:** 2.1, 2.2.

**İncelenecek:**

- `pcbridge/desktop/monitors.py`
- `tests/test_desktop.py`
- Conduit `linux/screen.rs`.

**Yeni/değişen:**

- `rust/crates/pcbridge-core/src/display.rs`
- `rust/crates/pcbridge-native/src/platform/linux/display.rs`
- `rust/crates/pcbridge-native/tests/display_contract.rs`
- `tests/contracts/test_display_contract.py`
- `pcbridge/desktop/contracts.py`

**Implementation:**

1. Mutter `GetCurrentState` cevabını zbus ile oku.
2. Current mode, logical size, scale, rotation, connector ve primary alanlarını mevcut Python kurallarıyla çöz.
3. Public index’i `(x,y)` sırasına göre 1’den başlat.
4. Geometry/scale/transform değişiminden `topology_id` üret.
5. `MonitorsChanged` ile cache’i invalidate et.
6. Native capture session’a bu snapshot’ı bağla.
7. İlk sürümde doğrulanmayan geometry’yi `DISPLAY_MAPPING_UNKNOWN` olarak reddet.

**Korunacak:** Primary sağda olsa da monitor 2; selector `1`, `"DP-1"`, `"primary"`, `"all"` davranışları.

**Test/acceptance:** Python ve Rust aynı fixture’dan aynı tabloyu üretiyor; GTK/Tauri/display window gerekmiyor.

**Rollback:** Python monitor provider.

**Yapılmayacak:** İlk monitor fallback’i; monitor sırasını primary-first yapmak; aynı anda mixed-DPI davranışı genişletmek.

## Task 3.2 — Mutter capture session lifecycle

**Amaç:** Python GI olmadan kalıcı, kapatılabilir screencast session.  
**Ön koşul:** 2.3, 2.4, 3.1.

**İncelenecek:** `pcbridge/desktop/screencast.py`, `screencast_helper.py`; Conduit `linux/portal.rs`.

**Yeni/değişen:**

- `rust/crates/pcbridge-native/src/platform/linux/session.rs`
- `rust/crates/pcbridge-native/tests/capture_session.rs`
- `docs/native/capture.md`

**Implementation:**

1. `CaptureSession` state machine: `Closed → Starting → Ready → Stopping → Closed`; failure ayrı durum.
2. `CreateSession`, connector başına `RecordMonitor`, stream sinyali aboneliği, `Start` sırasını uygula.
3. Connector → stream identity eşlemesini session içinde tut.
4. Partial startup failure’da oluşturulan kaynakların tamamını kapat.
5. Cursor değişiminde kontrollü session recreate yap.
6. Revoke, lock, timeout, EOF ve compositor bağlantı kaybında session’ı kapat.
7. Capability sorgusunun session açmadığını test et.

**Korunacak:** Unlock ile paylaşım göstergesinin açılması; izin kapanınca session’ın kapanması.

**Acceptance:** Fake D-Bus ile erken signal, missing stream, double start/stop ve revoke-during-start senaryoları geçiyor.

**Rollback:** `native.capture=python`.

**Yapılmayacak:** RemoteDesktop pointer permission istemek; portal persistence; buffered capture.

## Task 3.3 — OnDemand PipeWire frame alma ve güvenli PNG encoding

**Amaç:** İlk gerçek native frame pipeline’ı.  
**Ön koşul:** 3.2.

**İncelenecek:** Mevcut `screencast_helper.py`; Conduit `linux/capture.rs`.

**Yeni/değişen:**

- `rust/crates/pcbridge-core/src/frame.rs`
- `rust/crates/pcbridge-native/src/platform/linux/capture.rs`
- `rust/crates/pcbridge-core/tests/frame_conversion.rs`
- `rust/crates/pcbridge-native/tests/capture_worker.rs`

**Implementation:**

1. PipeWire loop’unu tek dedicated thread’de sahiplen.
2. Session/node kimliğini taşıyan OnDemand capture request’i gönder.
3. Frame’in format, dimensions, stride, chunk offset ve byte sınırlarını doğrula.
4. BGRx/RGBx/BGRA/RGBA’yı owned RGBA8’e çevir; bilinmeyen formatı reddet.
5. En fazla 32 milyon pixel ve IPC payload sınırını allocation öncesi uygula.
6. İstekten sonra alınmış geçerli frame’i sequence/timestamp ile döndür.
7. Frame callback’ini PNG encoding sırasında bloklama.
8. Timeout/cancel/revoke sonrasında buffer ve stream’i bırak.
9. PNG’yi binary IPC payload olarak gönder.

**Testler:** Padded stride, nonzero chunk offset, truncated buffer, channel order, alpha, overflow, unsupported format, late frame ve cancellation.

**Acceptance:** Synthetic pixel dönüşümü beklenen byte’larla birebir eşleşiyor; decode edilen PNG doğru; success response boş/siyah placeholder üretmiyor.

**Rollback:** Native provider kapatılır.

**Yapılmayacak:** Nearest-neighbor resize, GPU capture, DMA-BUF fallback, arbitrary format guessing.

PipeWire frame tüketimi ve stream lifecycle için implementation sırasında bu resmi kaynak kullanılacak: [PipeWire video capture tutorial](https://pipewire.pages.freedesktop.org/pipewire/page_tutorial5.html).

> **Ölçümle netleşen timestamp sözleşmesi (2026-09-12):** Mutter/GNOME 46
> portal akışı `SPA_META_Header` sağlamıyor; ayrıca bu düğümde
> `pw_stream_get_time_n().now` `0` dönüyor. Header varsa üretici sequence/PTS'si
> aynen taşınır. Yoksa source-yerel sequence ve source başlangıcından beri
> monotonic nanosaniye taşınır; hangi alanın geldiği
> `frame_identity_source` ile zorunlu olarak belirtilir. Unix zamanı veya frame
> yaşı uydurulmaz. Tazelik her durumda ayrı yerel receipt `Instant` ile ölçülür.

## Task 3.4 — Rust capture’ı mevcut Python shot pipeline’ına bağla

**Amaç:** Shot ID ve public davranışı koruyarak acquisition backend’ini değiştirmek.  
**Ön koşul:** 1.1, 1.2, 3.3.

**İncelenecek:**

- `pcbridge/desktop/capture.py`, `monitors.py`
- `pcbridge/tools.py`
- `pcbridge/cli/shot.py`
- `pcbridge/shots.py`

**Yeni/değişen:**

- Yeni: `pcbridge/desktop/backends/rust.py`
- Yeni: `tests/contracts/test_capture_backend_selection.py`
- Değiştir: `capture.py`, `runtime.py`, `config.py`, `config.example.toml`.

**Implementation:**

1. Rust adapter `CapturedImage` üretir; public `Shot` üretmez.
2. Python aynı `_write_crop`/resize/final PNG yolunu kullanır.
3. Native frame’in bağlı olduğu display snapshot’ı kullan; capture sonrasında farklı tabloyla metadata kurma.
4. Mevcut shot ID formatını, iki lookup dizinini ve `taken_at` alanını koru.
5. `taken_at` gerçek frame edinim zamanından gelsin.
6. Yukarıdaki backend/fallback tablosunu tek selector’da uygula.
7. `screen_info` backend adını tahminden değil gerçek provider/session durumundan alsın.
8. İlk sürümde stream boyutu ile beklenen logical boyut farklıysa açık hata ver; sessiz scale varsayma.

**Compatibility:** `screencast=` gibi mevcut Python çağrı yüzeyi compatibility adapter ile yaşar. `monitor="window"` legacy operation kalır.

**Acceptance:** Aynı capture contract suite Python ve Rust fixture provider’larında geçiyor; shot→global sonuçları aynı.

**Rollback:** `native.capture=python`; shot metadata migration gerektirmez.

**Yapılmayacak:** Shot ID’yi Rust’a taşımak; Python capture dosyasını silmek; default backend değiştirmek.

## Task 3.5 — Screenshot artifact ve MCP image delivery bütünlüğü

**Amaç:** Capture başarısı ile görüntünün gerçekten istemciye ulaşmasını ayırmak ve ikisini doğrulamak.  
**Ön koşul:** 1.3, 3.4.

**İncelenecek:**

- `pcbridge/shots.py`
- `pcbridge/tools.py`
- `pcbridge/cli/shot.py`
- `pcbridge/server.py`

**Yeni/değişen:**

- Yeni: `tests/integration/test_mcp_capture_delivery.py`
- Yeni: `tests/contracts/test_shot_artifacts.py`
- Değiştir: `capture.py`, `shots.py`, `presentation.py`, `cli/shot.py`.

**Implementation:**

1. PNG ve metadata’yı private staging dizininde oluştur.
2. Bütün target monitorler başarılıysa final path’lere atomik yayımla.
3. Partial failure’da incomplete artifact/metadata bırakma.
4. ID collision durumunda mevcut dosyayı overwrite etme; yeni suffix üret.
5. Stdio sonucunda text ilk, ardından her shot için gerçek `ImageContent` ver.
6. HTTP `/shot` token/TTL yolunu koru.
7. CLI `--json`, absolute path ve `--out` metadata kopyasını koru.
8. Inline image okunamazsa delivery failure bildir; bunu capture success diye gizleme.
9. Batch özetini kırparken shot kimliği ve image eşleşmesini kesme.

**Test/acceptance:** MCP client’ı PNG’yi decode edip fixture içeriğini doğruluyor; path başka vision aracına aktarılmadan inline görüntü kullanılabiliyor; HTTP token expire oluyor; stdio için ölü HTTP URL üretilmiyor.

**Rollback:** Artifact/presentation adapter geri alınır; native capture ayrı kalır.

**Yapılmayacak:** Conduit MEDIA marker’ı eklemek; görüntünün yalnızca diskte bulunmasını end-to-end başarı saymak; OAuth route mantığını değiştirmek.

---

# Phase 4 — Paketleme, parity ve default değişikliği

## Task 4.1 — Native binary build/package ve tanı

**Amaç:** Geliştirici makinesinde çalışan native backend’in kurulabilir olması.  
**Ön koşul:** 3.4.

**İncelenecek:**

- `install.sh`, `run.sh`, `doctor.sh`
- `requirements.txt`
- `systemd/pcbridge.service`
- `KURULUM.md`

**Yeni/değişen:**

- Yeni: `scripts/build-native.sh`
- Yeni: `.github/workflows/native.yml`
- Yeni: `docs/native/packaging.md`
- Yeni: `tests/integration/test_native_packaging.py`
- Değiştir: `install.sh`, `doctor.sh`, `.gitignore`, `KURULUM.md`.

**Implementation:**

1. Release binary’yi build ID ve protocol sürümüyle üret.
2. Başlangıç Linux artifact hedefini `x86_64-unknown-linux-gnu` olarak sınırla.
3. Ubuntu 24.04 tabanında build/test yap; runtime PipeWire dependency’sini listele.
4. Build-time header/toolchain bağımlılıklarını runtime gereksinimlerinden ayır.
5. Paketleme smoke testini yeni bir dizinden, repo cwd’sine güvenmeden çalıştır.
6. `doctor.sh` native binary/version/protocol/backend/capability bilgisini raporlasın; permission istemesin.
7. Native bulunmazsa Python kurulumunun çalışmaya devam ettiğini doğrula.
8. Kurulum sırasında çalışan service’i otomatik restart etme.

**Acceptance:** Native capture için `python3-gi`, GStreamer ve `pipewiresrc` gerekmiyor; legacy accessibility GI dependency’si ayrıca raporlanıyor.

**Rollback:** Paketlenmiş binary seçilmez; Python provider.

**Yapılmayacak:** Rust toolchain’i runtime zorunluluğu yapmak; unsigned otomatik update sistemi; GUI paketi.

## Task 4.2 — Gerçek Linux capture parity gate

**Amaç:** Native capture’ın referans makinede gerçekten çalıştığını ölçmek.  
**Ön koşul:** 0.1, 3.5, 4.1.

**Yeni dosyalar:**

- `tests/live/test_capture_parity.py`
- `docs/native/verification-linux.md`

**Implementation:**

1. Kullanıcı capture testine izin verdikten sonra static test pattern’i görünür yap.
2. İki monitorü ayrı ayrı ve `all` olarak iki backend’den yakala.
3. `include_pointer=true/false`, 1536 scale ve full-size capture ölç.
4. Frame tazeliğini değişen sequence/test pattern ile doğrula.
5. Lock, expiry, revoke, Python process exit ve native crash senaryolarını çalıştır.
6. Python GI import’unun mümkün olmadığı kontrollü ortamda Rust capture’ı doğrula.
7. Her backend için en az 30 warm capture ve 5 session startup ölç.
8. Stdio ve izole HTTP client üzerinde image decode/delivery doğrula.
9. Test sonunda bütün test session’larını kapat.

**Ölçülebilir acceptance:**

- Monitor kimliği, offset, output size ve shot→global fixture sonuçları eşleşiyor.
- Static, değişmeyen crop’ta decoded pixel farkı açıklanabilir; hedef en az `%99,5` eşleşme.
- Her dönen OnDemand frame request sonrasına ait.
- Capture/session error rate test serisinde `0`.
- Warm capture p95, aynı koşuldaki legacy sessiz capture’ın `1,5×` değerini aşmıyor; aşarsa default gate kapanır.
- Revoke/expiry sonrası frame teslimi yok.
- Kaynak kapandıktan sonra paylaşım göstergesinin kaybolduğu gözlemleniyor.
- Shell/jobs araçları native failure sırasında kullanılabiliyor.

**Rollback:** Revoke → native shutdown → `native.capture=python`.

**Yapılmayacak:** Mouse click/typing; web extraction ile GUI doğrulama; özel ekran görüntülerini Git’e eklemek.

## Task 4.3 — Rust capture’ı varsayılan yap

**Amaç:** Parity kanıtı sonrası kontrollü rollout.  
**Ön koşul:** 4.2 başarılı.

**Değiştirilecek:**

- `pcbridge/config.py`
- `config.example.toml`
- `README.md`, `KURULUM.md`, `KULLANIM.md`
- `docs/native/verification-linux.md`
- `tests/contracts/test_capture_backend_selection.py`

**Implementation:**

1. `[native] capture` default’unu `python` → `auto` yap.
2. Packaged, compatible native backend mevcutsa ilk tercih Rust olsun.
3. Fallback olduğunda result/capability/audit kullanılan backend’i açıkça göstersin.
4. Kullanıcının `python` veya `gnome-screenshot` seçimini koru.
5. Yeni stdio process ve service process için rollout talimatını ayrı yaz.
6. Çalışan job’ları kontrol etmeden service restart yapma.

**Acceptance:** Temiz kurulum Rust capture seçiyor; missing binary senaryosu Python’a görünür degraded fallback yapıyor; eski MCP tool contract’ları geçiyor.

**Rollback:** `native.capture=python`, ardından uygun process’leri kontrollü yeniden başlat.

**Yapılmayacak:** Legacy implementation silmek; aynı commit’te input default değiştirmek.

---

# Phase 5 — Input migration ve batch safety

## Task 5.1 — Input parity fixture’ları ve batch safety açıklarını kapat

**Amaç:** Native input’tan önce mevcut korumaları ölçülebilir hale getirmek.  
**Ön koşul:** 1.1, 2.3, 2.4. Capture ile paralel geliştirilebilir; rollout 4.3 sonrasında.

**İncelenecek:**

- `pcbridge/desktop/input.py`
- `pcbridge/desktop/batch.py`
- `pcbridge/desktop/ops.py`
- `pcbridge/cli/do.py`
- `tests/test_desktop.py`

**Yeni/değişen:**

- `tests/contracts/test_input_contract.py`
- `tests/contracts/test_batch_safety.py`
- `tests/fixtures/native/input_events.json`
- `pcbridge/desktop/execution.py`
- `batch.py`, `runtime.py`, `ops.py`.

**Implementation:**

1. Key combo, hold/release, pointer path, scroll direction, drag ve auto-release event fixture’larını çıkar.
2. Batch’e device bilmeyen `before_action`/cancellation hook’u ekle.
3. Her action öncesi grant/lock/revoke/deadline kontrolü yap; activity’yi yeniden sorgulama.
4. Focus check etkinse focus okunamaması veya doğrulanamaması halinde devam etme.
5. Aynı desktop session’daki write sequence’lerini processler arası execution lock ile sırala.
6. Lock bekledikten sonra safety check’i tekrar yap.
7. Rate limit muhasebesini aynı lock altında paylaş; batch action gap korunur.
8. Stop/budget/error/revoke halinde held input cleanup’ı doğrula.

**Korunacak:** `expect_focus`, action count budget, remaining actions, `super` sonrası raw typing, başarılı batch sonunda bilinçli hold.

**Acceptance:** Mock event log’da revoke/focus failure sonrasında tek bir ek key/click yok; batch hâlâ MCP ve gerçek cihaz import etmiyor.

**Rollback:** Native input başlamaz; safety düzeltmesi ayrı commit olarak korunur.

**Yapılmayacak:** Batch motorunu Rust’a taşımak; safety testini gerçek input ile yürütmek.

## Task 5.2 — Rust keyboard ve held-key lifecycle

**Amaç:** Klavye injection ve tuş bırakma güvencesini native process’e taşımak.  
**Ön koşul:** 4.3, 5.1.

**Yeni/değişen:**

- `rust/crates/pcbridge-native/src/platform/linux/input.rs`
- `rust/crates/pcbridge-native/tests/keyboard_contract.rs`
- `pcbridge/desktop/backends/rust.py`
- `pcbridge/config.py`, `config.example.toml`

**Implementation:**

1. Rust `evdev` uinput wrapper’ı kullan; custom ioctl binding yazma.
2. Mevcut key alias/combo contract’ını uygula.
3. `held()` ve `take_auto_released()` semantiğini koru.
4. Monotonic hold timer ekle; başka çağrı gelmesini bekleme.
5. Shutdown/revoke/error sırasında açıkça release gönder.
6. `native.input=python` default’unu ekle; keyboard migration test modunda seçilsin.
7. IPC input request’lerini otomatik tekrar etmeyi yasakla.

**Test/acceptance:** Golden event dizileri aynı; timer sahte saatle doğrulanıyor; hiçbir default test `/dev/uinput` açmıyor.

**Rollback:** Önce release/revoke, sonra Python input seç.

**Yapılmayacak:** Türkçe metni ASCII keycode ile “çözmek”; portal input’a geçmek; Python klavyeyi silmek.

## Task 5.3 — Rust pointer, motion path ve native koordinat adapter’ı

**Amaç:** Pointer injection’ı shot mapping’i bozmadan taşımak.  
**Ön koşul:** 5.2.

**İncelenecek:** `input.py` içindeki `_make_pointer`, `move_path`, `_clamp`, pointer state read/write, drag/scroll.

**Yeni/değişen:**

- `rust/crates/pcbridge-core/src/input.rs`
- `rust/crates/pcbridge-core/tests/pointer_path.rs`
- `rust/crates/pcbridge-native/tests/pointer_contract.rs`
- Native Linux `input.rs`
- Python Rust adapter.

**Implementation:**

1. Mevcut absolute-device event setini koru: `BTN_TOUCH`/`BTN_TOOL_PEN` ekleme.
2. `move_path`, speed=0, min/max duration ve drag interpolation parity’sini uygula.
3. Global→device dönüşümünü tek native fonksiyonda tut.
4. Caller’dan `shot` veya `monitor` kabul etme; yalnızca çözülmüş global nokta al.
5. `pointer.json` uyumluluğunu, yaşı ve cihaz açılırken state’in silinmemesini koru.
6. Display topology değişince device geometry’yi güvenli biçimde yeniden kur.
7. Capture’dan gelen koordinat için topology uyuşmazlığını injection öncesinde reddet.

**Acceptance:** Golden path/event sequence geçiyor; monitor 2 için ikinci kez offset eklenmiyor; raw global clamp’in mevcut davranışı korunuyor.

**Rollback:** Release/revoke sonrası Python input.

**Yapılmayacak:** Gerçek pointer pozisyonunu bildiğini varsaymak; Conduit’in “ilk stream” fallback’i; native core’a shot registry eklemek.

## Task 5.4 — Text/clipboard adapter’ı ve input default gate

**Amaç:** Türkçe metin, clipboard restore ve native input entegrasyonunu tamamlamak.  
**Ön koşul:** 5.3.

**İncelenecek:**

- `input.py`: `_wl_read`, `_wl_copy`, `_clipboard_save`, `_clipboard_restore`, `type_text`
- `batch.py`: `_auto_raw`
- `tests/test_desktop.py`

**Yeni/değişen:**

- `pcbridge/desktop/clipboard.py`
- `rust/crates/pcbridge-native/src/platform/linux/clipboard.rs`
- `tests/contracts/test_clipboard_contract.py`
- `tests/live/test_input_parity.py`

**Implementation — ayrı küçük commit’ler:**

1. Clipboard işlemlerini Python’da ayrı interface arkasına çıkar; davranışı değiştirme.
2. Rust’ta aynı `wl-copy`/`wl-paste` programlarını yöneten adapter ekle. GNOME’da doğrulanmamış wlr-data-control library’sini zorunlu yapma.
3. Copy ownership process’inin stdout/stderr pipe’larını açık bırakma tuzağını önle.
4. Tek MIME restore sınırlamasını koru ve capability limitation olarak bildir.
5. `type_text` orchestration’ını Python’da bırak: clipboard set → native paste combo → gerekli restore.
6. Raw typing ve overview davranışını koru.
7. Açık input opt-in ile boş editor’da Turkish Unicode, modifiers, move→görsel doğrulama→click, drag, expiry/revoke testlerini çalıştır.
8. Keyboard ve pointer birlikte parity sağlamadan `native.input=auto` default yapma.

**Acceptance:** Türkçe metin birebir okunuyor; clipboard restore fixture’ı geçiyor; held input süre sonunda bırakılıyor; pointer hotspot hedefte beklenen ≤1 global pixel injection sapmasını sağlıyor.

**Rollback:** Input ve clipboard provider’ları birlikte Python’a alınır; aktif held state önce bırakılır.

**Yapılmayacak:** Clipboard içeriğini audit’e yazmak; fiziksel klavye düzenini değiştirmek; gerçek input testini CI’a açmak.

---

# Phase 6 — Accessibility ve window/application sınırı

## Task 6.1 — Element target bütünlüğünü Python’da sabitle

**Amaç:** Yanlış uygulama veya aynı isimli yanlış node üzerinde action yapılmasını önlemek.  
**Ön koşul:** 1.1, 1.2.

**İncelenecek:**

- `pcbridge/desktop/uitree.py`
- `pcbridge/desktop/atspi_helper.py`
- `tests/test_desktop.py`

**Yeni/değişen:**

- `tests/contracts/test_accessibility_contract.py`
- `tests/fixtures/native/accessibility_cases.json`
- `uitree.py`, `atspi_helper.py`

**Implementation:**

1. Dump sonucuna backend/app/window/snapshot identity ekle.
2. Kısa public ID’leri koru; aynı dump içindeki hash collision’ı sessiz overwrite etme.
3. `_resolve` içinde açık app bulunamazsa focused app’e düşmeyi kaldır.
4. Path değişince role/name aramasını yalnızca aynı hedef içinde yap.
5. Birden fazla eşleşme varsa `ELEMENT_AMBIGUOUS`; ilk eşleşmeyi seçme.
6. `focused_window()` ve `windows()` çağrılarının son dump registry’sini değiştirmemesini koru.
7. Native action yoksa koordinat fallback yapma.

**Compatibility:** `ui_dump → #id → ui_click/ui_set_text` korunur. Güvensiz ambiguity/fallback davranışları bilinçli olarak sıkılaştırılır.

**Acceptance:** Chrome hedefi kaybolunca Shell UI’a action gönderilmiyor; duplicate “Close” scenario’su yanlış node seçmiyor.

**Rollback:** Native a11y migration başlamaz; target doğrulama düzeltmesi korunur.

**Yapılmayacak:** AT-SPI extents üzerinden tıklamak; browser flags’i kullanıcıdan habersiz değiştirmek.

**Not (2026-09-19, uygulandı):** 4. ve 5. maddeler daha sıkı uygulandı. AT-SPI her öğeye kalıcı bir kimlik veriyor: uygulamanın D-Bus adı ve öğenin nesne yolu. GTK4'te ölçüldü: araya düğüm eklenince nesne yolları korunuyor, yeniden yaratılan öğe yeni yol alıyor. Bu yüzden yol kayınca rol/ad ile **aranmıyor**; aynı nesne aynı hedef içinde aranıyor. Bulunamazsa `ELEMENT_STALE`, ad/rol değiştiyse `TARGET_MISMATCH` dönüyor. `ELEMENT_AMBIGUOUS` üç yerde kalıyor: birden fazla öğeye uyan kısa kimlik, birden fazla uygulamaya uyan kısmi ad ve iki öğeye aynı yolu veren araç. `ElementRef` = backend + uygulama veriyolu adı + pid + pencere yolu + öğe yolu + snapshot. Task 6.2'nin "native referans"ı bu çift.

## Task 6.2 — Rust AT-SPI read/window/focus provider

**Amaç:** Accessibility okumalarını Python GI’den ayırmak.  
**Ön koşul:** 4.3, 6.1.

**Yeni/değişen:**

- `rust/crates/pcbridge-native/src/platform/linux/accessibility.rs`
- `rust/crates/pcbridge-native/tests/accessibility_read.rs`
- `pcbridge/desktop/backends/rust.py`
- `pcbridge/config.py`, `config.example.toml`

**Implementation:**

1. Accessibility bus discovery ve AT-SPI D-Bus proxy’lerini zbus ile kur.
2. Traversal depth, node count ve toplam deadline sınırı uygula.
3. Role/name/states/actions/editable alanlarını ortak contract’a çevir.
4. GTK uygulama GAction’larını gerçek element action’ı sanmayan mevcut filtreyi koru.
5. Window enumeration ve focused observation’ı traversal’dan ayrı uygula.
6. Native referansları client/snapshot bazında sakla; başka process’in ID’sini kabul etme.
7. `native.accessibility=python` default’u ile başlat.

**Acceptance:** İki provider aynı fixture suite’i geçiyor; Electron boş tree durumu degraded olarak doğru hedef adıyla dönüyor; GUI thread/GTK initialization yok.

**Rollback:** Python `UiTree` provider.

**Yapılmayacak:** Window enumeration’ı bütün GNOME pencerelerini görüyormuş gibi sunmak; action migration’ı aynı commit’e katmak.

**Not (2026-09-19, uygulandı):**
- 6\. madde (native referansları snapshot bazında saklamak) Task 6.3'e taşındı. Okuma istekleri içeri referans taşımıyor. Referansı kabul edecek ilk istek eylem olacak ve sakladığı döküme o yazacak. (Task 6.3'te uygulandı; oradaki nota bakın.)
- Snapshot kimliği iki okuyucuda da Python'da (`uitree.dump_from_response`) üretiliyor. (Task 6.3'ten beri native dökümün snapshot'ını helper veriyor: eylem o dökümü bu kimlikle anıyor.)
- İzin yokken pencere listesi Python yardımcısından okunuyor, çünkü native yardımcı yalnızca bir izne bağlı yaşıyor. `screen_info` izinden önce pencereleri gösteriyor.

## Task 6.3 — Rust accessibility actions ve parity

**Amaç:** Native action ve editable text yolunu koruyarak GI action helper’ını değiştirmek.  
**Ön koşul:** 5.1, 6.2.

**Yeni/değişen:**

- Native Linux `accessibility.rs`
- `rust/crates/pcbridge-native/tests/accessibility_actions.rs`
- `tests/live/test_accessibility_parity.py`

**Implementation:**

1. `ElementRef` target identity’yi action anında doğrula.
2. AT-SPI Action çağrısını kullan; true/false native sonucu kontrol et.
3. EditableText işlemlerini method contract’ına uygun uygula; Python GI wrapper’ının byte-length ayrıntısını körlemesine D-Bus metoduna taşıma.
4. Türkçe metin için son karakter sayısı ve mümkünse geri okunan metni doğrula.
5. Timeout sonrası action replay yapma.
6. Read-only testten ayrı input/action opt-in altında boş uygulamada parity ölç.
7. Başarılı olursa ayrı commit ile accessibility default’unu `auto` yap.

**Acceptance:** Native action uinput gerektirmiyor; cursor hareketi zorunlu değil; yanlış target/stale element reddediliyor; text truncation yok.

**Rollback:** Python accessibility provider; eldeki native element registry geçersizleştirilir ve yeni `ui_dump` istenir.

**Yapılmayacak:** Native action başarısız olunca gizli coordinate click.

**Not (2026-09-19, uygulandı):**
- **Gerçek test penceresinde ölçülenler (GTK4 4.14).** Bunlar tasarımı belirledi:
  - Devre dışı bir düğmede `DoAction` `false` döndü, hiçbir şey tıklanmadı.
  - En fazla 5 karakter tutan alana `SetTextContents` `true` döndü ve 5 karakter tuttu.
  - `InsertText(0, "ğüş", 2)` girdi alanına üç harfin üçünü de yazdı: GTK4'ün girdi alanı uzunluğu hiç kullanmıyor.
  - `GetText(0, -1)` boş metin döndü.

  Bu yüzden 3. madde `SetTextContents` ile uygulandı: bütün metin değişiyor, uzunluk parametresi yok. 2. ve 4. madde de uygulamanın cevabına güvenmiyor. `false` bir hatadır (`ACTION_UNSUPPORTED`). Yazılan metin `CharacterCount` + `GetText(0, sayı)` ile geri okunup bütünüyle karşılaştırılıyor. Aynı değilse yeni `TEXT_MISMATCH` dönüyor (taxonomy düzeltmesi yukarıda).
- **Python yardımcısı da aynı kurallara geçirildi.** İki yardımcı ortak fixture'da aynı cevabı ve aynı mesajı vermeli. Python yardımcısı da `DoAction`'ın `false` cevabını hata sayıyor ve metni geri okuyor. Canlı testte ölçüldü: PyGObject'te `get_text_iface()` düğümün kendisini döndürüyor, `ti.get_text(0, n)` ise `Atspi.Accessible.get_text()` olup TypeError veriyor. Doğru çağrı `Atspi.Text.get_text(düğüm, 0, n)`. Sahte AT-SPI bunu gizlemişti; artık GI gibi davranıyor.
- **6. madde (6.2'den taşınan referans kaydı) uygulandı.** Helper son 8 dökümünün kaydını tutuyor ve her dökümü kendi `snapshot` kimliğiyle döndürüyor. Eylem yalnızca snapshot + nesne yolu taşıyor; kimliğin geri kalanını helper kendi kaydından alıyor. Başka helper'ın (başka süreç ya da yeni izin) dökümü reddediliyor. Rollback satırındaki "registry geçersizleştirilir" böylece kendiliğinden sağlanıyor.
- **Kimlik veriyolu adı + nesne yolu.** D-Bus'ta bu çift tek bir nesnedir. Aramada aynı nesneyle ikinci kez karşılaşmak ikinci aday bulmak değildir. Python yardımcısı yalnızca yolu karşılaştırıyor ve iki kez gördüğü yolu reddediyor; uygulamanın kendi veriyolundaki her düğümde ikisi aynı sonucu veriyor. Belirsiz kalan tek durum aynı yolun aynı dökümde iki ayrı veriyolunda görünmesi (`ELEMENT_AMBIGUOUS`).
- **Zaman aşımları.** `DoAction` ve `SetTextContents` 5 sn bekliyor, okumalar 2 sn. Hedef 8 sn içinde bulunamazsa `TIMEOUT`, hiçbir şey gönderilmemiş. Gönderilip cevaplanmayan çağrı `EXECUTION_UNKNOWN`: tekrarlanmıyor. Python tarafında isteğin kendi zaman aşımı ve helper'ın çökmesi de aynı kodu alıyor.
- **Ölçümler (canlı, test penceresi, release helper).** Tıklama native 13 ms, Python 52 ms. Metin yazma native 5 ms, Python 50 ms. Döküm native 17–32 ms, Python 84–137 ms. Helper eylem sırasında `/dev/uinput` açmıyor (sürecin `/proc/<pid>/fd` listesiyle doğrulandı).
- **Doğrulanamayan tek kontrol:** izin, hedef bulunduktan sonra eylemden hemen önce bir kez daha doğrulanıyor. Bu aralığa deterministik olarak girilemediği için kontrol mutasyon testiyle sınanmadı.
- **7. madde:** varsayılan ayrı bir commit ile `auto` yapıldı. Paketlenmiş yardımcı yeniden kuruldu ve kurulu yardımcıyla canlı test 20/20 geçti. Servis boştayken yeniden başlatıldı; `system_capabilities` artık `accessibility.read`/`accessibility.action` için `linux.atspi.native` bildiriyor. `doctor.sh` seçimi gösteriyor ve erişilebilirlik yöntemleri olmayan eski bir yardımcıyı uyarıyor: `auto` her yardımcıyı alır, eskisi `ui_dump`'ı bozardı.

AT-SPI API ve interface ayrıntıları için resmi referans: [AT-SPI documentation](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/).

## Task 6.4 — App/window orchestration’ını capability arkasına al

**Amaç:** Launch/focus/window-list işlemlerinin doğru backend ve doğrulama ile çalışması.  
**Ön koşul:** 1.4, 6.2.

**İncelenecek/değiştirilecek:**

- `pcbridge/desktop/apps.py`
- `pcbridge/desktop/ops.py`
- `pcbridge/tools.py`
- `pcbridge/desktop/batch.py`
- `WALKTHROUGH.md`

**Yeni:**

- `tests/contracts/test_window_operations.py`

**Implementation:**

1. İç işlemleri `resolve_application`, `launch_application`, `activate_window`, `observe_focus` olarak ayır.
2. Public `window_focus` tek tool olarak kalsın; kapalı uygulamayı açma yeteneği korunur.
3. Hedef zaten focused ise input gönderme.
4. Native activation desteklenmiyorsa mevcut GNOME arama yolunu açık `degraded` fallback olarak kullan.
5. Sonucu gerçek app/window observation ile doğrula; yalnızca launch exit code’u başarı sayma.
6. Batch device need ve cost hesabını seçilen focus yolundan al.
7. `window_focus`, `computer_task`, `DeviceOps.focus` aynı orchestration’ı kullansın.
8. `window.move_resize` GNOME’da implementation yoksa `unsupported` kalsın.

**Acceptance:** Zaten focused hedef için `super` gönderilmiyor; cold launch korunuyor; yanlış arama sonucu focus success sayılmıyor.

**Rollback:** Eski `apps.focus` adapter’ı.

**Yapılmayacak:** Eklentiyi genel kontrol servisine dönüştürmek — D2 yalnızca tek bir `ActivateWindow` yöntemi için gevşetildi; doğrulanmamış `<1 saniye` native focus iddiası.

**Not (2026-09-12):** Hızlı focus yolunun kendisi bu task’tan önce, `WALKTHROUGH.md` Adım 2’de ele alınıyor. Adım 2 tamamlanmışsa bu task “hızlı yolu tasarla” değil, “çalışan yolu capability arkasına al ve doğrula” işidir.

**Not (2026-09-19, uygulandı):**
- **1. ve 7. madde.** `apps.py`'de dört iç işlem var: `resolve_application`, `launch_application`, `activate_window` (eklenti), `observe_focus`. Bunları `bring_to_front` tek sırada birleştiriyor. `window_focus`, `computer_task(app=…)` ve toplu `focus`/`launch` eylemleri (`DeviceOps`, yani `pcb-do` de) aynı işlemlerden geçiyor. `apps.focus()` eski imzasıyla duruyor; rollback noktası o.
- **Sıra:**
  1. Eklenti.
  2. Hedef zaten öndeyse hiçbir tuş gönderilmez (3. madde).
  3. Uygulama kapalıysa `gtk-launch` ile açılır; tuş gönderilmez.
  4. Açık ama eklentinin öne alamadığı pencere için GNOME araması (4. madde, `degraded`).

  Kapalı uygulamayı açmak (2. madde) artık aramaya bağlı değil.
- **Ölçülen iki gerçek başlatma yolunu belirledi.** Arka plandaki bir süreçten `gtk-launch` ile başlatılan pencere 0,68 sn'de odağı aldı. Ama D-Bus ile etkinleşmeyen uygulama çağıranın cgroup'unda kaldı; servisten çağrılınca `systemctl --user restart pcbridge` uygulamayı da öldürürdü. Bu, `window_focus`'un vaadinin tam tersi ve toplu `launch` eyleminde zaten vardı. Uygulama artık `systemd-run --user --scope` ile kendi `app-pcbridge-*.scope`'una giriyor (+60 ms).
- **Aramaya yalnızca kurulu bir uygulamanın adı yazılıyor.** Bu makinede arama sağlayıcıları Claude sohbetlerini, dosyaları, uçbirim sekmelerini ve ayarları da tarıyor, uygulama bulunamazsa Enter web aramasına düşüyor. Ad `find()`'in turlarıyla çözülüyor. Aynı turda birden fazla uygulamaya uyan ad (bu makinede "Desktop" üç girdiye uyuyor) `ELEMENT_AMBIGUOUS`, uygulama olmayan ad `TARGET_MISMATCH` alıyor; ikisinde de `execution_state=not_started` ve hiçbir tuş gitmiyor. Odak okunamazsa `BACKEND_UNAVAILABLE`, yine hiçbir şey gönderilmeden. Yeni hata kodu yok.
- **5. madde.** Doğrulama uygulamanın kimliğiyle yapılıyor: AT-SPI uygulama adı `.desktop` girdisinin ikili adına, kimliğine ya da adına denk olmalı; `gnome-terminal-server` için ikili adının öneki de sayılıyor. Başlık tek başına ancak pencerenin süreci başka hiçbir kurulu uygulamaya ait değilse yetiyor (LibreOffice `soffice` olarak çalışıyor). Böylece başlığı aranan metin olan web araması sekmesi başarı sayılmıyor. `launch` başarısı pencerenin listede görülmesi; `gtk-launch`'ın 0 dönmesi yetmiyor. Görülmezse `EXECUTION_UNKNOWN`. Aramadan sonra yanlış pencere öndeyse de `EXECUTION_UNKNOWN`: tuşlar gitti, bir şey açılmış olabilir, tekrarlanmıyor.
- **6. madde.** Cihaz ihtiyacı Adım 2'den beri seçilen yoldan geliyor. Artık maliyet de öyle: eklenti varken `focus` 200 ms, yokken 7000 ms. Yavaş adımlar (başlatma, arama) motorun verdiği kalan süreye kendileri bakıyor ve sığmıyorsa `BudgetExceeded` ile hiç başlamıyor. Böylece iyimser tahmin MCP tavanını aşamaz. `launch` maliyeti 300 ms'den 1500 ms'ye çıktı, çünkü artık pencereyi bekliyor.
- **8. madde.** `window.move_resize` `unsupported` kaldı (`test_capabilities.py`).
- `window_focus` denetim kaydı artık `path` ve `ms` taşıyor. 2026-09-02 ölçümü yalnızca toplu eylemin `batch_step`'inden yapılabilmişti.
- **Kabul.**
  - Zaten öndeki hedefe tuş gitmiyor: sözleşme testinde ve canlı olarak.
  - Soğuk başlatma korunuyor: canlı, tuşsuz.
  - Yanlış arama sonucu başarı sayılmıyor: sözleşme testinde, web araması sekmesi senaryosuyla. Canlı üretilmedi, çünkü kullanıcının tarayıcısında sekme açmak gerekirdi.

  Eklenti yolu değişmedi. Canlı testte kapatıldı ki arkasındaki yollar koşsun; kendi ölçümü 2026-09-12'de yapılmıştı. "Yapılmayacak" satırına uyuldu: eklentiye dokunulmadı.

---

# Phase 7 — Capture kapsamını genişlet

Bu phase, ilk native subsystem için zorunlu değildir.

## Task 7.1 — Mixed scale, negatif origin ve topology güvenliği

**Amaç:** Bugünkü scale=1 referansının ötesine güvenli geçiş.  
**Ön koşul:** 4.3, 5.3.

**İncelenecek:** `capture.py`, `monitors.py`, `input.py`, native display/frame/input modülleri.

**Yeni/değişen:**

- `tests/fixtures/native/mixed_scale_cases.json`
- `tests/contracts/test_coordinate_v2.py`
- `rust/crates/pcbridge-core/tests/geometry.rs`
- `capture.py`, native `display.rs`, `frame.rs`, `input.rs`.

**Contract:**

- Native platform origin ile Pcbridge canvas origin ayrı alanlardır.
- Pcbridge canvas origin bounding rectangle’ın sol üstüne normalize edilir.
- Linux/macOS desktop unit logical; Windows backend phase’inde fiziksel desktop unit açıkça etiketlenir.
- Public monitor index yine `(x,y)` sırasıyla 1’den başlar.
- Shot metadata v2 ek alanları: `source_pixel_size`, `scale_xy`, `topology_id`, `coordinate_space`.
- Mevcut `size`, `scaled`, `offset`, `scale` alanları korunur.
- `scale`, compatibility için x oranıdır; v2 dönüşüm iki ekseni ayrı kullanır.
- Legacy metadata reader desteklenir; topology bilinmiyorsa karmaşık layout üzerinde güvenli hareket iddia edilmez.

**Implementation:**

1. 1.25×, 1.5×, 2×, portrait, negative origin ve gap fixture’larını ekle.
2. Raw pixel → logical capture geometry eşlemesini tanımlı oranlarla uygula.
3. `capture.to_global()` içinde v2 dönüşümü ekle; caller’larda matematik ekleme.
4. Hotplug/rotation/scale değişiminde eski shot’ın input için kullanımını reddet.
5. Monitor arası boşluğu ve out-of-image shot noktasını reddet.
6. Eski process’ler kapatılmadan mixed-scale rollout yapma.

**Acceptance:** Round-trip hatası fixture matrisinde ≤1 desktop unit; eşit ölçekli mevcut iki monitor sonucu değişmiyor.

**Rollback:** Yeni layout desteğini kapat; eski shot’ları yeniden kullanmak yerine fresh screenshot al.

**Yapılmayacak:** Python ve Rust’ta aynı shot dönüşümünü ayrı ayrı implement etmek.

**Not (2026-09-20, uygulandı):**
- **İki uzay ayrıldı.** Kompozitörün kendi koordinatı `Monitor.platform` alanında duruyor; tuval her zaman (0,0)'dan başlıyor ve tablo okunurken bir kez öteleniyor (Python `_normalize_origin`, Rust `resolve`). Negatif bir tuval koordinatı sanal farenin mutlak ekseninde gösterilemez ve kırpma kutusu görüntünün dışına düşerdi. Bu makinede öteleme sıfır, yani sonuç değişmiyor.
- **Aynı geometri, kaymış origin = aynı düzen.** `topology_id` tuval koordinatından üretildiği için kompozitör bütün ekranları eşit miktarda kaydırdığında kimlik değişmiyor; hiçbir çekim gereksiz yere geçersizleşmiyor. İki dilde de fixture'la sabitlendi.
- **Çekim kaydı v2.** `source_pixel_size`, `desktop_size`, `scale_xy` ve `coordinate_space` eklendi; `size`, `scaled`, `scale`, `offset` aynen duruyor. Dönüşüm artık "masaüstü birimi / yazılan piksel", her eksen ayrı: ölçekli monitörde de doğru ve tek oranın uzun kenarda bıraktığı bir piksellik kayma yok. `desktop_size` plandaki dört alanın üstüne eklendi, çünkü dönüşüm kırpmanın masaüstü birimindeki boyutunu gerektiriyor; monitörün ölçeğini kayda yazmak da aynı bilgiyi başka yoldan taşırdı. Eski (v1) kayıtlar aynı cevabı vermeye devam ediyor ve testle sabitlendi.
- **Karışık ölçek artık tanımlı.** Yakalanan karenin ham piksel boyutu ya mantıksal boyut ya da mantıksal boyut × ölçek olabilir; başkası **ölçeklenmez, reddedilir**. Tek görüntü veren `gnome-screenshot` yedeğinde oran ancak bütün monitörler aynı ölçekteyse çözülebiliyor; farklı ölçeklerde hangi pikselin hangi monitöre ait olduğu o görüntüden bilinemez ve çağrı reddediliyor (yayın yolu monitör başına ayrı kare verdiği için etkilenmiyor).
- **Reddedilen iki yeni durum:** hiçbir monitörün üstüne düşmeyen koordinat (monitörler arası boşluk, köşe boşluğu, tuval dışı) ve verilen çekimin görüntüsünün dışındaki piksel. İkisi de eskiden sessizce çevriliyordu. Monitör tablosu okunamıyorsa kontrol atlanıyor: doğrulanamayan bir şey yüzünden çalışan bir çağrıyı reddetmek yanlış olurdu.
- **Ölçüldü (bu makine, eşit ölçekli iki monitör).** Kayıt: `offset [1920,0]`, `size [1920,1080]`, `scaled [1536,864]`, `scale 0.8` — 7.1 öncesiyle birebir aynı. Görüntünün (0,0) noktası (1920,0), ortası (2880,540), son pikseli (3839,1079). Tuval 3840x1080, platform origin (0,0). Canlı capture parity (11 test) ve `test_capture_default` (3 test) geçti.
- **Doğrulanmayan:** kesirli ve 2× ölçekli donanım bu makinede yok. Fixture'daki beş düzen elle hesaplandı ve iki dil aynı tabloyu üretiyor, ama gerçek bir HiDPI ekranda ölçülmedi. Aynı sebeple `Mutter`'ın yarım piksel sınırındaki davranışı hâlâ ölçülmedi.
- Mutasyon denemesi: 15 bozulmanın 15'i yakalandı (9 Python, 3 Rust, 3 kayıt/kontrol).

## Task 7.2 — Ayrı XDG ScreenCast portal backend’i

**Amaç:** Mutter dışındaki Linux desktop’ları için capture genişletmesi.  
**Ön koşul:** 7.1.

**Yeni/değişen:**

- `rust/crates/pcbridge-native/src/platform/linux/portal.rs`
- `rust/crates/pcbridge-native/tests/portal_session.rs`
- `tests/live/test_portal_capture.py`
- `config.py`, `config.example.toml`, native capture selector.

**Implementation:**

1. İlk portal implementation yalnızca ScreenCast istesin; pointer/keyboard permission istemesin.
2. Request response aboneliğini method çağrısından önce kur.
3. Kullanıcı prompt’unu yalnızca açık session başlatma adımında aç; status polling’de açma.
4. Cancel/deny/timeout/Closed sonuçlarını ayrı sınıflandır.
5. `persist_mode=0` ile başla; restore token saklama.
6. Stream geometry ve identity mevcutsa monitor eşleştir; eksikse ilk monitorü seçme.
7. Portal sürümü sunuyorsa `pipewire-serial` ile hedefle; eski sürümde node ID’yi session lifecycle ile sınırla.
8. Revoke/lock/EOF’ta session kapat.

**Acceptance:** Capture izni pointer capability’sini supported yapmıyor; kullanıcı reddettiğinde her screenshot çağrısı yeniden dialog açmıyor.

**Rollback:** Mutter backend.

**Yapılmayacak:** Bu task’ta portal input, libei, persistence veya bütün Linux compositor’ları için destek iddiası.

Stream geometry’nin pixel boyutuyla aynı olmak zorunda olmadığı ve yeni sürümlerde stream serial desteği resmi sözleşmede belirtiliyor. [ScreenCast sözleşmesi](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)

## Task 7.3 — Buffered capture

**Amaç:** Ölçülmüş ihtiyaç varsa repeated capture latency’sini azaltmak.  
**Ön koşul:** 4.3; 7.2 zorunlu değil.

**Yeni/değişen:**

- `rust/crates/pcbridge-core/src/capture_mode.rs`
- `rust/crates/pcbridge-native/tests/frame_freshness.rs`
- `tests/live/test_capture_modes.py`
- Native capture worker ve config.

**Implementation:**

1. `capture_mode=on_demand|buffered`; default OnDemand.
2. Buffered modda monitor başına yalnızca bir son frame tut.
3. En fazla 10 FPS iste; frame timestamp/sequence zorunlu.
4. Normal snapshot için en fazla 250 ms yaş kabul et.
5. Input sonrası doğrulamada action tamamlanmasından sonra alınmış frame iste.
6. Stream durunca eski frame’i fresh timestamp ile döndürme.
7. Idle CPU, compositor CPU, RSS ve p95 latency’yi OnDemand ile karşılaştır.

**Acceptance:** Frozen stream `STALE_FRAME`; revoke cache’i temizliyor; memory monitor sayısıyla sınırlı; latency kazancı ve kaynak maliyeti raporlu.

**Rollback:** `capture_mode=on_demand`.

**Yapılmayacak:** Buffered’ı ölçüm olmadan default yapmak.

## Task 7.4 — Adaptive mode

**Amaç:** Buffered kazanımını yalnızca aktif kullanım sırasında almak.  
**Ön koşul:** 7.3’ün ölçümleri fayda gösteriyor.

**Yeni/değişen:**

- `rust/crates/pcbridge-core/tests/adaptive_capture.rs`
- `capture_mode.rs`, native capture worker, config docs.

**Implementation:**

1. Son 2 saniyede üç capture request’i gelirse Buffered’a geç.
2. 5 saniye request gelmezse OnDemand’a dön.
3. Mod değişiminde session/frame kimliklerini koru; stale buffer döndürme.
4. Revoke/lock/expiry bütün modların üzerinde olsun.
5. Threshold’ları önce sabit tut; kullanıcı ayarı çoğaltma.

**Acceptance:** Sahte saatle bütün geçişler deterministik; mod geçişi başına ikinci portal dialog yok; kaynak maliyeti ölçülmüş.

**Rollback:** OnDemand.

**Yapılmayacak:** ML tabanlı tahmin, background screenshot history, video recording.

---

# Phase 8 — Legacy retirement

## Task 8.1 — Python screencast helper’ını emekliye ayır

**Amaç:** Artık kullanılmayan GI/GStreamer capture yolunun bakım yükünü kaldırmak.  
**Ön koşul:** Rust default en az iki sürüm döngüsü kullanılmış; 4.2 gate hâlâ geçiyor; paket rollback’i doğrulanmış.

**İncelenecek:**

- `pcbridge/desktop/screencast.py`
- `pcbridge/desktop/screencast_helper.py`
- `doctor.sh`, `install.sh`
- `tests/test_desktop.py`
- `CLAUDE.md`, `KURULUM.md`

**Implementation — ayrı commit’ler:**

1. Bütün import/caller’ları `rg` ile doğrula.
2. `screencast.py` public adapter’ını önce native provider’a yönlendir.
3. Sonraki commit’te kullanılmayan `screencast_helper.py` dosyasını kaldır.
4. GStreamer capture dependency/probe’larını kaldır.
5. `gnome-screenshot` fallback’ini ve Python shot/coordinate pipeline’ını koru.
6. Legacy process cleanup’ını ancak eski helper process’leri artık destek kapsamından çıktıktan sonra kaldır.

**Acceptance:** Aktif hiçbir caller silinen helper’a gitmiyor; capture regression ve install smoke suite geçiyor.

**Rollback:** Retirement commit’ini revert et veya önceki paket sürümüne dön; önce revoke uygula.

**Yapılmayacak:** `capture.py` dosyasını silmek; accessibility henüz GI kullanıyorsa `python3-gi` gereksinimini tamamen kaldırmak; geçmiş plan belgelerini silmek.

## Task 8.2 — Diğer legacy backend’ler için bağımsız retirement gate

**Amaç:** Input/accessibility kodunu capture ile birlikte topluca silmemek.  
**Ön koşul:** İlgili subsystem için kendi parity, default ve iki sürüm gate’i.

**Dosyalar:** `input.py`, `uitree.py`, `atspi_helper.py`, `backends/python.py`, ilgili test ve kurulum belgeleri.

**Implementation:**

1. Her subsystem için ayrı caller envanteri çıkar.
2. Compatibility facade ile implementation’ı ayır.
3. Yalnızca kullanılmayan implementation’ı bir commit’te kaldır.
4. Ortak contract testlerini native backend üzerinde koru.
5. Config’te eski selector için açık migration mesajı ver; bilinmeyen değeri sessizce `auto` yapma.

**Acceptance:** Her silme ayrı review/revert edilebilir; public tool/CLI contract’ı aynı.

**Rollback:** İlgili tek retirement commit’i revert edilir.

**Yapılmayacak:** “Rust yüzdesini artırmak” gerekçesiyle Python orchestration silmek.

---

# Phase W — Windows

**Durum:** D1 yanıtına göre sıraya alınacak. Linux capture ve input gate’leri tamamlanmadan başlamaz.

Windows’a yalnızca native modül eklemek yeterli değildir: mevcut `jobs.py`, `bash`, `script -qec` ve Unix process-group cancellation kullanıyor. Bu nedenle desktop port ile Python host uyumluluğu ayrı task’lardır.

## Task W.1 — Python host/process boundary

**Ön koşul:** D1’de Windows’un sıraya alınması; Phase 5 tamam.

**İncelenecek:** `pcbridge/jobs.py`, `tmuxctl.py`, `tools.py`, `config.py`, `desktop/session.py`, `native/registry.py`.

**Yeni:**

- `pcbridge/host.py`
- `tests/contracts/test_host_processes.py`
- `tests/integration/test_windows_jobs.py`

**Adımlar:**

1. Shell argv, process launch/cancel, PTY desteği ve process lock’u `HostServices` interface’ine al.
2. Linux implementation’ı davranış değiştirmeden bağla.
3. Windows için PowerShell tabanlı shell seçimini açık config/documentation ile ekle.
4. Jobs/coding-agent delegation Python’da kalsın; process-tree cancellation için Windows Job Object wrapper kullan.
5. İlk Windows release’te tmux ve unsupported PTY agent’larını structured unavailable/unsupported olarak bildir.
6. WSL’ye sessiz geçiş yapma.

**Test/acceptance:** Fake agent job start/status/output/cancel Windows CI’da geçiyor; Linux baseline değişmiyor.

**Rollback:** Windows host adapter devre dışı.

**Yapılmayacak:** Shell/jobs motorunu Rust’a taşımak; tmux klonu yazmak.

## Task W.2 — Windows display/capture provider

**Yeni yollar:**

- `rust/crates/pcbridge-native/src/platform/windows/mod.rs`
- `rust/crates/pcbridge-native/src/platform/windows/display.rs`
- `rust/crates/pcbridge-native/src/platform/windows/capture.rs`
- `rust/crates/pcbridge-native/tests/windows_geometry.rs`
- `tests/live/test_windows_capture.py`

**Adımlar:**

1. Başlangıç desteğini Windows 11 x64 ile sınırla.
2. DPI awareness’ı native process açılışında ayarla.
3. Display discovery için Win32 monitor API’lerini, capture için Windows Graphics Capture kullan.
4. WinRT/D3D kaynaklarını dedicated owner thread’de tut.
5. Row pitch, BGRA conversion, resize ve device-loss senaryolarını ortak `RawFrame` contract’ına bağla.
6. İkinci monitor, farklı DPI, negative OS origin, lock ve display disconnect testlerini çalıştır.

**Acceptance:** Ortak capture/coordinate/delivery suite geçiyor; Linux-only dependency Windows build’e girmiyor.

**Rollback:** Windows capture unavailable; Python non-desktop yolları çalışır.

**Yapılmayacak:** Linux Python capture’ına sahte fallback; driver/input migration’ını aynı commit’e katmak.

## Task W.3 — Windows input, state ve accessibility

**Yeni yollar:**

- `platform/windows/input.rs`
- `platform/windows/desktop_state.rs`
- `platform/windows/clipboard.rs`
- `platform/windows/accessibility.rs`
- `platform/windows/apps.rs`
- `tests/live/test_windows_input.py`
- `tests/live/test_windows_accessibility.py`

Bu yollar `rust/crates/pcbridge-native/src/` altındadır.

**Implementation sırası — her madde ayrı migration task/commit grubu:**

1. `SendInput`, virtual desktop absolute mapping ve Unicode text.
2. Lock/session state, activity observation, held-input cleanup.
3. Win32 clipboard ownership.
4. UI Automation read, ardından Invoke/Value/Text action contract’ları.
5. Window listing ve activation; focus sonucu gözlemle doğrulama.
6. Paketleme ve açık opt-in parity gate.

**Acceptance:** UIPI/elevation farkı typed failure olarak raporlanıyor; otomatik elevation yok; partial input sonucu replay edilmiyor; element ID flow korunuyor.

**Rollback:** İlgili capability kapatılır; tam native core kapatılmaz.

**Yapılmayacak:** Protected/secure desktop erişimi veya privilege escalation.

`SendInput` için UIPI ve gönderilen event sayısı sınırlamaları acceptance testlerinin parçası olacak. [Microsoft SendInput documentation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)

---

# Phase M — macOS

**Durum:** D1 yanıtına göre sıraya alınacak. GUI bu fazın ön koşulu değildir.

## Task M.1 — macOS host ve izin kimliği

**Ön koşul:** D1’de macOS’un sıraya alınması; Phase 5 tamam.

**İncelenecek:** `jobs.py`, `config.py`, `desktop/session.py`, `native/discovery.py`, varsa `host.py`.

**Yeni/değişen:**

- `pcbridge/host.py` macOS adapter’ı.
- `tests/integration/test_macos_jobs.py`
- `docs/native/macos-packaging.md`

**Adımlar:**

1. Başlangıç runtime tabanını macOS 14+ olarak sınırla.
2. Bash/PTY invocation’ını macOS `script` davranışından bağımsız Python host adapter’ında uygula.
3. tmux kuruluysa mevcut logic’i kullan; yoksa unavailable.
4. Native executable için sabit bundle/izin kimliği ve imza stratejisini paketleme contract’ına koy.
5. Build path değişimlerinin TCC iznini etkilediği senaryoyu test et.
6. Permission sorgusu ile permission request’i ayır.

**Acceptance:** Headless Python server GUI olmadan başlayabiliyor; capability sorgusu TCC prompt açmıyor.

**Rollback:** macOS native provider kapalı.

**Yapılmayacak:** TCC veritabanını düzenlemek; GUI’yi izin almanın zorunlu yolu yapmak.

## Task M.2 — macOS display/capture provider

**Yeni yollar:**

- `rust/crates/pcbridge-native/src/platform/macos/mod.rs`
- `.../macos/display.rs`
- `.../macos/capture.rs`
- `rust/crates/pcbridge-native/tests/macos_geometry.rs`
- `tests/live/test_macos_capture.py`

Buradaki `.../macos/`, aynı `rust/crates/pcbridge-native/src/platform/macos/` dizinidir.

**Adımlar:**

1. CoreGraphics display metadata ve ScreenCaptureKit kullan.
2. Desktop logical points ile backing pixels’i ayrı alanlarda tut.
3. Y ekseni/origin dönüşümünü tek platform adapter’ında yap.
4. Screenshot permission denial, monitor disconnect, sleep/wake ve cursor seçimini doğrula.
5. Native PNG → Python shot pipeline’ını değişmeden kullan.

**Acceptance:** Retina/non-Retina iki monitor testi ve ortak coordinate suite geçiyor.

**Rollback:** Capability unavailable; non-desktop yollar korunur.

**Yapılmayacak:** macOS private API veya görüntünün pixel boyutunu logical geometry sanmak.

Capture implementation’ın resmi dayanağı: [ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit).

## Task M.3 — macOS input/accessibility/clipboard

**Yeni yollar:**

- `rust/crates/pcbridge-native/src/platform/macos/input.rs`
- `.../macos/accessibility.rs`
- `.../macos/clipboard.rs`
- `.../macos/desktop_state.rs`
- `.../macos/apps.rs`
- `tests/live/test_macos_input.py`
- `tests/live/test_macos_accessibility.py`

**Implementation sırası — ayrı commit grupları:**

1. CoreGraphics input ve held-state cleanup.
2. Accessibility permission ve AX element read.
3. AX native action ve text setting.
4. NSPasteboard adapter.
5. NSWorkspace/application operations.
6. Lock/activity observations ve bütün platform parity gate’i.

**Acceptance:** Screen Recording ile Accessibility izinleri ayrı raporlanıyor; direct AX action varsa cursor click gerekmiyor; revoke bütün aktif kaynakları kapatıyor.

**Rollback:** İlgili provider seçimi devre dışı.

**Yapılmayacak:** Permission verildiğini input/capture sonucundan tahmin etmek; private API ile window kontrolü zorlamak.

---

# Phase G — İsteğe bağlı control plane GUI

**Ön koşul:** Native IPC, capabilities, grant/revoke ve en az Linux default backend stabil. Windows/macOS’un ikisinin de bitmesi zorunlu değil.

GUI’nin ürün kapsamı bu migration’ın default gate’lerine dahil değildir. İlk GUI sürümü yalnızca durum, izin, backend seçimi ve tanı gösterecek.

## Task G.1 — Python control-plane endpoint

**Amaç:** GUI’nin doğrudan native policy sahibi olmaması.

**İncelenecek:** `runtime.py`, `capabilities.py`, `lease.py`, `server.py`.

**Yeni:**

- `pcbridge/control.py`
- `tests/contracts/test_control_plane.py`

**Adımlar:**

1. Status/capability, timed unlock, revoke ve diagnostic snapshot operasyonlarını tanımla.
2. Unix’te kullanıcıya özel socket; Windows’ta kullanıcı ACL’li named pipe kullan.
3. Bu endpoint’i mevcut public HTTP/OAuth route’larına karıştırma.
4. GUI kapandığında server/native session yaşamını policy belirlesin.
5. Revoke’u GUI thread’i veya açık pencereye bağımlı yapma.

**Acceptance:** Yetkisiz kullanıcı bağlanamıyor; GUI olmadan aynı CLI/MCP işlemleri çalışıyor.

**Rollback:** Control endpoint kapanır; MCP/CLI devam eder.

**Yapılmayacak:** İkinci SafetyGate veya ikinci native daemon yaratmak.

## Task G.2 — Tauri + React control plane

**Yeni yollar:**

- `ui/package.json`
- `ui/src/App.tsx`
- `ui/src/lib/control.ts`
- `ui/src/components/Capabilities.tsx`
- `ui/src/components/DesktopPermission.tsx`
- `ui/src-tauri/Cargo.toml`
- `ui/src-tauri/src/lib.rs`

**Adımlar:**

1. Mevcut grant/capability contract’ını göster.
2. Timed unlock, revoke ve explicit permission retry kontrollerini bağla.
3. Degraded backend nedenini ve gerçek permission scope’unu göster.
4. Disconnect/crash durumunda eski “izin açık” bilgisini canlıymış gibi sunma.
5. GUI shutdown ile native resource shutdown senaryolarını ayrı test et.

**Acceptance:** GUI kapalıyken Pcbridge tam headless çalışıyor; panic/revoke control endpoint ve CLI’den çalışıyor.

**Rollback:** GUI paketi kaldırılır.

**Yapılmayacak:** MCP server’ı Tauri’ye taşımak; screenshot editor, workflow builder, chat uygulaması veya Conduit UI klonu.

---

# Test komutları ve ortak çalışma sözleşmesi

Aşağıdaki komutlar **uygulama sırasında** kullanılacak; bu plan hazırlanırken çalıştırılmadı.

Güvenli mevcut baseline:

```bash
env -u PCBRIDGE_TEST_CAPTURE -u PCBRIDGE_TEST_INPUT -u PCBRIDGE_TEST_ATSPI -u PCBRIDGE_TEST_BATCH ./.venv/bin/python tests/test_desktop.py
```

```bash
./.venv/bin/python tests/test_models.py
```

Yeni Python contract suite:

```bash
./.venv/bin/python -m unittest discover -s tests/contracts -p 'test_*.py' -v
```

Native integration, geliştirme binary’si açıkça seçilerek:

```bash
PCBRIDGE_NATIVE_BIN="$PWD/rust/target/debug/pcbridge-native" ./.venv/bin/python -m unittest discover -s tests/integration -p 'test_native_*.py' -v
```

Rust:

```bash
cargo fmt --manifest-path rust/Cargo.toml --all --check
```

```bash
cargo test --manifest-path rust/Cargo.toml --workspace --locked
```

```bash
cargo clippy --manifest-path rust/Cargo.toml --workspace --all-targets --locked -- -D warnings
```

Capture-only gerçek test, yalnızca explicit opt-in sonrasında:

```bash
PCBRIDGE_TEST_CAPTURE=1 ./.venv/bin/python tests/live/test_capture_parity.py
```

Input testleri bundan ayrıdır:

```bash
PCBRIDGE_TEST_INPUT=1 ./.venv/bin/python tests/live/test_input_parity.py
```

Ek kurallar:

- Yeni testler standart `unittest` ile yazılacak; pytest migration’ı yapılmayacak.
- Live test dosyaları flag yoksa işlem yapmadan skip edecek.
- Default CI live testleri çalıştırmayacak.
- `tests/test_e2e.py` doğrudan günlük gate olmayacak: çalışan server ve gerçek agent çağrısı riski var. Mock backend’li, geçici config’li transport integration testleri esas alınacak.
- Mevcut bir baseline hatası çıkarsa kaydedilecek ve ilgili bug ayrı commit’te giderilecek; native migration başarısı sayılmayacak.
- Her riskli migration’da aynı fixture’ları iki backend tüketmeli; yeni implementation’ın kendi çıktısından üretilmiş beklenen sonuç test sayılmaz.

# A. Dependency graph

```text
0.1 -> 0.2

0.2 -> 1.1 -> 1.2 -> 1.3
0.2 -> 1.4

0.2 -> 2.1
1.1 + 2.1 -> 2.2
1.1 + 2.2 -> 2.3 -> 2.4

2.1 + 2.2 -> 3.1
2.3 + 2.4 + 3.1 -> 3.2 -> 3.3
1.1 + 1.2 + 3.3 -> 3.4
1.3 + 3.4 -> 3.5

3.4 -> 4.1
0.1 + 3.5 + 4.1 -> 4.2 -> 4.3

1.1 + 2.3 + 2.4 -> 5.1
4.3 + 5.1 -> 5.2 -> 5.3 -> 5.4

1.1 + 1.2 -> 6.1
4.3 + 6.1 -> 6.2
5.1 + 6.2 -> 6.3
1.4 + 6.2 -> 6.4

4.3 + 5.3 -> 7.1 -> 7.2
4.3 -> 7.3 -> 7.4

4.3 + two-release gate -> 8.1
subsystem parity/default + two-release gate -> 8.2

D1 + Phase 5 -> W.1 -> W.2 -> W.3
D1 + Phase 5 -> M.1 -> M.2 -> M.3

stable Linux runtime + IPC + capabilities -> G.1 -> G.2
D2 -> optional future GNOME activation task
```

# B. Critical path

En kısa güvenilir yol:

```text
0.1
→ 0.2
→ 1.1
→ 1.2
→ 2.1
→ 2.2
→ 2.3
→ 2.4
→ 3.1
→ 3.2
→ 3.3
→ 3.4
```

Bu noktada **temiz Python/native sınırı + ilk çalışan Rust capture subsystem’i** vardır.

Kullanıcıya varsayılan olarak sunulması için ayrıca:

```text
1.3 + 3.5 + 4.1
→ 4.2
→ 4.3
```

Input, accessibility, buffered/adaptive capture, portal genişletmesi ve GUI bu ilk teslimin ön koşulu değildir.

# C. Deferred work

Bilinçli olarak ilk migration dışında:

- Windows/macOS implementation; D1’e bağlı sonraki fazlar.
- GNOME eklentisiyle hızlı activation; D2’ye bağlı.
- Tauri + React GUI.
- Full Rust MCP server.
- OAuth/HTTP rewrite.
- Shell/filesystem/jobs/tmux motorlarının Rust’a taşınması.
- Batch ve yüksek seviye SafetyGate’in Rust’a taşınması.
- Shared native daemon ve çok client’lı native socket mimarisi.
- Portal input/libei.
- Portal restore-token persistence.
- DMA-BUF/EGL, GPU zero-copy, HDR/video capture.
- Arbitrary region-crop public API.
- Clipboard multi-MIME fidelity genişletmesi.
- Fiziksel input dinleyerek agent/human ayrımı.
- Otomatik güncelleme sistemi.

# D. Risk register

| Risk | Olasılık | Etki | Mitigation |
|---|---|---|---|
| Rust deneyiminin sınırlı olması | Yüksek | Yüksek | İki crate, küçük task, safe wrapper, ortak fixture, exact toolchain; karmaşık generic/framework yok. |
| AI-generated native code’un derlenip yanlış çalışması | Yüksek | Çok yüksek | Golden pixel/event testleri, fault injection ve gerçek gözlem gate’i. |
| `unsafe`/FD/buffer ownership hataları | Orta | Çok yüksek | Core’da unsafe yasağı; OwnedFd/RAII; küçük FFI sınırı; offset/stride/overflow testleri. |
| Wayland izinlerinin birbirine karıştırılması | Yüksek | Yüksek | Capture/input grant ayrımı; `permission_scope`; explicit session lifecycle; capability probe input göndermez. |
| Yanlış monitor eşlemesi | Orta | Çok yüksek | Connector/stable identity + topology; sıra veya ilk stream fallback’i yok. |
| Stale frame’e fresh shot ID verilmesi | Orta | Çok yüksek | Frame timestamp/sequence/session kimliği; OnDemand request barrier; frozen-stream testi. |
| Çoklu stdio process’lerinin revoke sonrası yaşaması | Yüksek | Çok yüksek | Ortak revoke epoch, native watchdog, registry identity, legacy cleanup geçişi. |
| Input timeout sonrası double execution | Orta | Çok yüksek | Otomatik replay yasağı; `EXECUTION_UNKNOWN`; release/cleanup. |
| Python/Rust coordinate regression | Orta | Çok yüksek | Tek shot→global path; paylaşılan fixture; native dönüşümün ayrı ve dar olması. |
| Accessibility yanlış uygulama/node seçimi | Orta | Çok yüksek | Snapshot/app/window identity; ambiguity reddi; native action; coordinate fallback yok. |
| Paketleme ve sistem library farkları | Yüksek | Yüksek | Ubuntu 24.04 build tabanı; runtime dependency smoke test; explicit Python fallback. |
| Cross-platform API farkları | Yüksek | Yüksek | Platformlar ayrı gate; unsupported/degraded açık; Linux varsayımlarını genelleştirmeme. |
| FastMCP output schema/transport regression | Orta | Yüksek | Kurulu sürümü sabitleme; wire-level content/error/image testleri. |
| Legacy kodun erken silinmesi | Orta | Yüksek | Parity → default → iki sürüm → retirement sırası. |
| Service restart ile çalışan agent job’ının kesilmesi | Yüksek | Yüksek | Restart öncesi job inventory; stdio/service ayrımı; otomatik restart yok. |
| Testin gerçek masaüstüne input göndermesi | Yüksek | Çok yüksek | Task 0.1; ayrı input opt-in; boş editor; move→gözlem→click sırası. |
| Native log üzerinden içerik sızması | Orta | Yüksek | Payload/text/clipboard/frame log yasağı; sınırlandırılmış metadata audit. |
| Sürekli capture kaynak tüketimi | Orta | Orta | OnDemand default; Buffered/Adaptive yalnızca ölçüm sonrası. |

# E. Verification gates

| Gate | Gerekli kanıt | Başarısızsa |
|---|---|---|
| **Gate 0 — Güvenli baseline** | Mevcut tests + contract fixture’ları; capture/input opt-in ayrılmış. | Refactor başlamaz. |
| **Gate 1 — Python boundary** | Tool/CLI contract aynı; runtime lifecycle testleri; capabilities ve errors doğru. | Native entegrasyon yapılmaz. |
| **Gate 2 — IPC/lifecycle** | Framing, timeout, crash, cancellation, EOF, multi-process revoke testleri. | Gerçek native capture açılmaz. |
| **Gate 3 — Rust capture parity** | Pixel/metadata/coordinate/freshness + MCP delivery; GI olmadan gerçek capture. | Rust default olmaz; Python silinmez. |
| **Gate 4 — Default rollout** | Paketleme smoke, görünür fallback, yeni stdio/service process doğrulaması. | `native.capture=python`. |
| **Gate 5 — Input parity** | Golden events, batch safety, gerçek boş-editor/input ölçümleri, release/revoke. | Rust input default olmaz. |
| **Gate 6 — Accessibility parity** | Target identity, stable IDs, native action, Unicode text, gerçek read/action testi. | Python accessibility korunur. |
| **Gate 7 — Retirement** | İki sürüm, açık blocker yok, rollback paketi, sıfır aktif caller. | Legacy implementation silinmez. |
| **Gate W / M** | İlgili OS üzerinde package + host + native + live parity. | Cross-platform supported iddiası yapılmaz. |
| **Gate G** | GUI kapalıyken MCP/CLI çalışıyor; revoke GUI’den bağımsız. | GUI yayınlanmaz. |

**Gate başarısızlığı sonraki destructive migration’ı durdurur.** Feature flag ile kapalı geliştirme devam edebilir; başarısız gate atlanmış sayılmaz.

# F. IMPLEMENTER INSTRUCTIONS — GPT-5.6 Sol

1. `AGENTS.md` içindeki proje bağlamını ve güncel migration özetini kullan. Her session başında başka belgeyi tamamen okuma ritüeli ekleme; task ayrıntısı gerektiğinde bu plandaki ilgili bölüme, ölçüm gerektiğinde ilgili mevcut belgeye başvur.
2. Bu planı task sırasıyla uygula; açık D1/D2 kararlarını varsayım olarak kapatma.
3. Her task öncesi listelenen dosyaların hâlâ aynı sorumluluğu taşıdığını doğrula.
4. Repo planla çelişiyorsa körlemesine taşıma yapma; farkı, etkisini ve önerilen düzeltmeyi bildir.
5. Riskli subsystem’de önce contract testini ekle, sonra implementation’ı değiştir.
6. Her task tek migration sınırı taşısın. Büyük task’ların numaralı alt adımlarını küçük commit’lere böl.
7. Refactor, yeni feature, native migration ve public semantics değişikliğini aynı commit’e sıkıştırma.
8. Eski implementation’ı parity/default/retirement gate’leri tamamlanmadan silme.
9. Başarısız testleri gizleme, silme veya yalnızca yeni koda uysun diye değiştirme.
10. `config.toml` içeriğini yazdırma, loglama veya commit etme.
11. OAuth, `MetadataNormalizer`, `BasicAuthFormShim` ve agent model politikalarını kapsam dışında tut.
12. Native input request’ini crash/timeout sonrasında otomatik tekrar etme.
13. `capture.to_global()` dışında shot koordinat matematiği yazma.
14. Accessibility action başarısızlığını gizli coordinate click ile telafi etme.
15. Shell/filesystem/jobs/delegation yollarını desktop grant’e bağlama; çok yollu execution’ı koru.
16. Gerçek input testlerini explicit opt-in olmadan çalıştırma. Capture opt-in’ini input izni sayma.
17. Gerçek mouse testinde önce move, sonra görüntüyle doğrulama, ardından click uygula.
18. Service restart öncesinde çalışan işleri kontrol et; mevcut stdio bağlantısının yeni kodu kullandığını varsayma.
19. Kod, identifier, yorum ve İngilizce belgelerde American English kullan. Serialized/public alanları yalnızca yazım nedeniyle yeniden adlandırma.
20. Her task sonunda değişen dosyaları, çalıştırılan komutları, gerçek sonuçları, geçilen gate’i ve rollback yolunu özetle.
21. “Çalışıyor” demek için ölçüm göster. Capture dosyası oluşması, image delivery veya GUI görevinin tamamlanmasıyla aynı şey değildir.
22. İlk hedefi büyütme: **Python orchestration + sürümlü IPC + güvenilir Linux Rust capture.** Diğer fazlara ancak ilgili gate geçince ilerle.
23. İlerlemeyi `WALKTHROUGH.md` içinde tut; yaptıklarını ve yapacaklarını task kimlikleri, gerçek test sonuçları, gate durumu ve sonraki somut adımla kaydet. Her task sonunda ve oturum devrinde güncelle.
24. Durum özetini `AGENTS.md` veya başka bir dosyaya **kopyalama**; `AGENTS.md` yalnızca yönlendirir. Depoda tek yapılacak-iş listesi `WALKTHROUGH.md`'dir.
