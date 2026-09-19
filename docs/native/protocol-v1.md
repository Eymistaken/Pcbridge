# Native IPC protocol v1

> Kurallar ve mimari: **[CLAUDE.md](../../CLAUDE.md)** · Sıradaki iş: **[WALKTHROUGH.md](../../WALKTHROUGH.md)**

Bu belge, Python host ile `pcbridge-native` child process'i arasındaki yerel
stdio sözleşmesini tanımlar. Bu pipe, MCP stdio taşımasından ayrıdır. Native
stdout yalnızca aşağıda tanımlanan framed response'ları taşır.

Executable Linux'ta monitör tablosunu okur (Task 3.1), Mutter ScreenCast +
PipeWire üzerinden tek monitör karesi alır (Task 3.3) ve seçildiğinde uinput
klavye (Task 5.2) ve pointer (Task 5.3) olaylarını üretir; erişilebilirlik
henüz native değil. Task 4.3'ten beri varsayılan capture backend'i `auto`
(paketlenmiş yardımcı varsa native). Input varsayılanı Gate 5'ten beri
(2026-09-19) `auto`: paketlenmiş yardımcı varsa klavye, fare ve pano
programları native, yoksa Python yolu (görünür şekilde). Native process
grant/revoke lifecycle'ına ek olarak GNOME session D-Bus üzerinden ekran
kilidini ve kullanıcı etkinliğini typed observation olarak izler; sonraki
backend'ler aynı fail-closed güvenlik sınırını kullanır.

## Frame biçimi ve sınırlar

Her frame şu sırayla kodlanır:

```text
4 byte unsigned big-endian JSON header length
JSON header, UTF-8
header.binary_len kadar binary payload
```

- JSON header en fazla 64 KiB'dir ve boş olamaz.
- Binary payload en fazla 128 MiB'dir.
- `binary_len`, request ve response header'larında zorunlu unsigned integer
  alanıdır.
- Okuma ve yazma işlemleri kısa I/O sonuçlarını normal kabul eder ve bütün
  bölümü tamamlar.
- İlan edilen boyut sınırları allocation öncesinde doğrulanır.
- Task 2.1 kontrol metotları binary payload kabul etmez ve bütün
  response'larında `binary_len: 0` kullanır.
- Binary payload taşıyan **tek request** `clipboard.write`'tır (Task 5.4): pano
  içeriği 64 KiB'lik header'a sığmaz ve bir log satırına ulaşabilecek bir
  header alanına konmamalıdır. Başka bir metoda binary gönderilirse
  `UNEXPECTED_BINARY` döner.

Temiz stdin EOF süreci başarıyla kapatır. Kısmi uzunluk alanı, kesik header,
kesik payload, geçersiz JSON veya geçersiz `binary_len` protokol hatasıdır;
süreç stderr'e yalnızca hata sınıfını yazar, exit code `2` ile kapanır ve
stdout'a serbest metin yazmaz.

## Handshake

İlk başarılı request `initialize` olmalıdır:

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

`client_version` boş olamaz. `state_dir` ve `runtime_dir` absolute path
olmalıdır; harness bu dizinleri açmaz veya oluşturmaz. İstemcinin
`supported_minor` listesi server'ın desteklediği minor `0` sürümünü içermelidir.

Başarılı response seçilen sürümü ve process kimliğini döndürür:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:1",
  "result": {
    "instance_id": "native-12345",
    "native_version": "0.1.0",
    "build_id": "2294156a1b2c",
    "platform": "linux",
    "features": ["display.snapshot", "capture.on_demand", "capture.session_open", "input.keyboard", "input.pointer", "clipboard"],
    "lease_bound": true
  },
  "binary_len": 0
}
```

`build_id` Task 4.1'de eklendi: `scripts/build-native.sh` ile derlenmiş
binary'de commit (Rust tarafında commit'lenmemiş değişiklik varsa `-dirty`),
başka derlemelerde `dev`. İsteğe bağlı bir alandır; eski yardımcılar göndermez
ve Python istemcisi yokluğunu kabul eder.

Bilinmeyen major sürüm `UNSUPPORTED_PROTOCOL` error response'ından sonra
bağlantıyı kapatır. Handshake sonrasında farklı minor sürüm kullanan request
`UNSUPPORTED_PROTOCOL_MINOR` döndürür. İkinci `initialize` isteği
`ALREADY_INITIALIZED` ile reddedilir.

## Metotlar

Harness aşağıdaki metotları kabul eder:

- `initialize`: sürümü ve zorunlu handshake alanlarını doğrular.
- `ping`: `{"pong": true}` döndürür; varsa `params.nonce` değerini aynen
  response'a ekler.
- `capabilities`: `backend: linux.mutter.pipewire`, `capture.monitor`,
  `input.keyboard`, `input.pointer`, `clipboard.read` ve `clipboard.write`
  durumunu döndürür. Capture durumu her
  istekte **çalışma
  zamanında**, ucuz bir
  denetimle belirlenir (Task 4.1): oturum veriyolunda
  `org.gnome.Mutter.ScreenCast` adının sahibi ve PipeWire soketi. İkisi de
  varsa `supported`; değilse `unavailable` + `reason_code`
  (`BACKEND_UNAVAILABLE` ya da `DEPENDENCY_MISSING`) + `reason`. Oturum,
  PipeWire akışı ya da paylaşım göstergesi açmaz, izin istemez. Task 4.1'e kadar
  sabit bir `supported` idi — capture'ın hiç çalışamayacağı makinede de.
  Klavye ve pointer denetimleri `/dev/uinput` düğümünün varlığını metadata
  üzerinden okur; default capability isteği aygıt açmaz. Düğüm varsa erişimin
  ancak ilk açık input isteğinde doğrulanacağını anlatan `degraded`, yoksa
  `DEPENDENCY_MISSING` döner.
- `display.snapshot`: Task 3.1 monitör tablosunu döndürür.
- `capture.frame`: Task 3.3 tek monitör PNG'sini binary payload olarak döndürür.
- `capture.session_open`: Task 4.3. Kare okumadan Mutter oturumunu mevcut
  düzendeki bütün monitörler için açar, yeniden kullanır ya da yeniden kurar.
  Parametreler `topology_id`, `session_id`, `grant_id`, `revoke_epoch`,
  `include_pointer`; izin, düzen ve oturum kuralları `capture.frame` ile aynı
  (yanlış izin `REVOKED`, eski düzen `DISPLAY_CHANGED`). Binary taşımaz. Sonuç:
  `outcome` (`opened` / `reused` / `recreated`), `monitors`, `include_pointer`,
  `backend`. `desktop_unlock` bunu çağırır, böylece paylaşım göstergesi izinle
  birlikte belirir. Bu metodu bilmeyen eski bir yardımcı `UNKNOWN_METHOD` döndürür;
  Python istemcisi o durumda oturumu ilk kareye bırakır.
- `input.keyboard.ensure`: Klavye aygıtını tembel açar. `grant_id`,
  `revoke_epoch` ve `hold_max_seconds` ister; bekleme süresini ve tutulmuş
  tuşları döndürür.
- `input.keyboard.key`, `input.keyboard.key_down`, `input.keyboard.key_up`:
  Aynı grant alanlarına ek olarak `combo` ister. Alias ve kombinasyon sırası
  Python provider ile aynıdır; sonuç güncel `held` listesini taşır.
- `input.keyboard.held`: Native tarafta tutulmuş tuşları okur.
- `input.keyboard.release_all`: Tutulmuş bütün tuşlara açık release gönderir ve
  bırakılan canonical adları döndürür. Cleanup yolu olduğu için yeni grant
  istemez.
- `input.keyboard.take_auto_released`: Monotonic hold zamanlayıcısının bıraktığı
  tuşları bir kez döndürüp temizler.
- `input.pointer.ensure`: Pointer aygıtını tembel açar. Grant alanlarına ek
  olarak `topology_id`, `pointer_speed` ve `pointer_max_ms` ister; mevcut
  konumu, tutulmuş düğmeleri ve bekleme süresini döndürür.
- `input.pointer.move`: Aynı ortak alanlara `x`, `y` ve isteğe bağlı `smooth`
  ekler. `x`/`y`, Python shot adapter'ının çözdüğü **global canvas**
  koordinatıdır. Metot `shot` veya `monitor` kabul etmez; offset'i ikinci kez
  eklemez. Canvas clamp ve global→device dönüşümü native tarafta bir kez yapılır.
- `input.pointer.click`: Ortak alanlarla `button` ve `count` alır.
- `input.pointer.drag`: Ortak alanlarla `x1`, `y1`, `x2`, `y2`, `button` alır;
  Python provider'ın minimum süreli smoothstep drag yolunu korur.
- `input.pointer.scroll`: Ortak alanlarla `amount` ve `horizontal` alır.
- `input.pointer.mouse_down`, `input.pointer.mouse_up`: Ortak alanlarla
  `button` alır ve native held-button durumunu günceller.
- `input.pointer.held`, `input.pointer.release_all`,
  `input.pointer.take_auto_released`, `input.pointer.position`: Sırasıyla
  native held state'i okur, açık release gönderir, monotonic zamanlayıcının
  bıraktıklarını alır ve bilinen son persisted konumu okur. Cleanup/read
  metotları yeni grant istemez.
- `clipboard.read`, `clipboard.write`, `clipboard.clear` (Task 5.4): Python'un
  hep kullandığı `wl-paste`/`wl-copy` programlarını aynı argümanlarla çalıştırır.
  Üçü de `grant_id` ve `revoke_epoch` ister. İzin yanlışsa program hiç
  çalışmadan `REVOKED` döner; bilinmeyen alan varsa `INVALID_PARAMS` döner.
  `read`, ilk sunulan tipi ve baytlarını **response'un binary payload'unda**
  döndürür (`{"empty": false, "mime": ...}`). Pano boşsa ya da okunamıyorsa
  `{"empty": true, "mime": null}` döner. Okuma süresince izin geri alınırsa
  içerik döndürülmez. `write` ek olarak `mime` alır; içerik **request'in**
  binary payload'udur, header'da hiç bulunmaz. `clear` panoyu boşaltır.
  Program başına 10 sn zaman aşımı vardır ve süre dolunca program öldürülür.
  `wl-copy`'nin stdout/stderr'i `/dev/null`'a gider, çünkü arka planda kalan pano
  sahibi bir boruyu açık tutardı. Hata kodları: program yoksa
  `DEPENDENCY_MISSING`, zaman aşımında `TIMEOUT`, program başarısızsa
  `EXECUTION_UNKNOWN`, 128 MiB'i aşan içerikte `UNSUPPORTED`. Yalnızca ilk MIME
  tipi saklanır; `capabilities` bunu `clipboard.read`/`clipboard.write`
  `limitations` alanında bildirir. Capability denetimi hiçbir programı
  çalıştırmaz, `PATH`'e ve Wayland soketine bakar. Ortak fixture:
  `tests/fixtures/native/clipboard_cases.json`.
- `cancel`: `params.target_id` alanını doğrular ve bugün `canceled: false`
  döndürür. Capture'ın kendi 1–8000 ms zaman aşımı ve lifecycle kapıları vardır;
  dispatcher henüz eşzamanlı request çalıştırmıyor.
- `shutdown`: framed başarı response'ını yazdıktan sonra process'i temizce
  kapatır.

Diğer metotlar `UNKNOWN_METHOD` döndürür ve bağlantı kullanılabilir kalır.

State değiştiren input istekleri helper'ın `initialize` sırasında bağlandığı
grant kimliği ve revoke epoch'u ile birebir eşleşir. Pointer istekleri ayrıca
güncel native display snapshot'ının `topology_id` değeriyle eşleşmelidir;
uyuşmazlık event injection'dan önce `DISPLAY_CHANGED` olur. Topoloji değişince
eski pointer aygıtı kapatılır ve yeni canvas geometry'siyle kurulur.
`pointer.json` mevcut `{x, y, t}` biçimini ve 300 saniyelik yaş kuralını korur;
aygıt açılışı state'i silmez. Revoke, shutdown veya event write hatası
tutulmuş tuş/düğmelere release gönderir. Hold zamanlayıcısı başka IPC isteği
beklemeden çalışır. Input request'leri process restart'ı üzerinden otomatik
tekrar edilmez; belirsiz bir write ikinci kez gönderilmez.

**Bir helper tek bir izne hizmet eder.** `initialize`'da okuduğu grant'e
bağlanır ve bir daha bağlanmaz; her `desktop_unlock` ise, bu süreçte ya da
başka bir süreçte, yeni bir `grant_id` yazar. Eski helper'ın gözcüsü o anda
kaynaklarını kapatır ve grant taşıyan bütün istekleri `REVOKED` olur. Python
tarafında `backends/rust.py` → `GrantBoundHelper` izin kimliği (grant id +
revoke epoch) değişince eski helper'dan release ister, onu kapatır ve yenisini
başlatır; istek yeni helper'a **bir kez** gider, tekrar oynatılmaz. Yakalama ve
input aynı sınıfı kullanır. Ölçüm ve düzeltme öncesi davranış: `WALKTHROUGH.md`
→ "Codex'in 5.2/5.3 işinin kontrolü".

## Komut satırı

Argümansız çalıştırma protokolü stdin/stdout üzerinde başlatır. Bunun dışında
yalnızca iki bayrak kabul edilir (Task 4.1). İkisi de protokol başlatmaz,
oturum veriyoluna, PipeWire'a ya da state dizinine dokunmaz ve `0` ile çıkar:

- `--version` → tek satır:
  `pcbridge-native 0.1.0 (build …, protocol 1.0, x86_64-unknown-linux-gnu, release)`
- `--build-info` → tek JSON nesnesi: `name`, `version`, `build_id`,
  `protocol {major, minor}`, `target`, `profile`, `test_harness`.

Başka her argüman stderr'e `unsupported command-line arguments` yazar ve `2`
ile çıkar. `test-harness` özelliğiyle derlenmiş binary ayrıca `--test-mode`
kabul eder ve `--build-info`'da `"test_harness": true` der: deterministik sahte
backend'le cevap veren, gerçek ekran okumayan bir derleme. `scripts/build-native.sh`
böyle bir binary'yi paketlemeyi reddeder, `doctor.sh` onu hata olarak işaretler.

## Task 3.1 metodu — `display.snapshot`

Sıralı monitör tablosunu ve düzen kimliğini döndürür. **Salt okunur metadata:**
piksel okumaz, cihaz açmaz, masaüstü grant'i istemez — host tarafındaki
`screen_info` gibi. Kurallar `pcbridge-core::display` içinde; bu metot yalnızca
Mutter `GetCurrentState` cevabını taşıyıp çözücüye veriyor.

```json
{
  "topology_id": "v1|0,0,1920,1080,1.0000,0,0|1920,0,1920,1080,1.0000,0,1",
  "canvas": [3840, 1080],
  "monitors": [
    {"index": 1, "connector": "DP-4", "x": 0, "y": 0, "width": 1920,
     "height": 1080, "scale": 1.0, "primary": false, "name": "…",
     "transform": 0, "serial": "…"}
  ]
}
```

Düzen çözülemezse `DISPLAY_MAPPING_UNKNOWN` döner ve **hiçbir şey tahmin
edilmez**: geçerli modu olmayan monitör, bilinmeyen connector, boş connector
listesi ve pozitif olmayan ölçek reddedilir. "İlk modu seç" ya da "ilk monitöre
düş" bir yakalamanın sessizce yanlış ekrana inmesinin yoludur.

**Oturum veriyolu bağlantısı tembeldir.** İlk `display.snapshot`'a kadar
kurulmaz. Ölçüldü 2026-09-12, release binary, `strace -e trace=connect`:
`initialize` → `capabilities` → `shutdown` **1** bağlantı yapıyor; aynı dizide
`capabilities` yerine `display.snapshot` olunca **2**. Yani bu metot tam olarak
bir bağlantı ekliyor ve yalnızca çağrıldığında.

> **Düzeltme:** Task 2.1 kaydı "connect syscall sayısı 0" diyor. O ölçüm Task
> 2.1 kodu için doğruydu; **Task 2.4** desktop-state sağlayıcısını ekleyince
> açılışta `/run/user/<uid>/bus`'a bir bağlantı kuruldu ve bu yeniden
> ölçülmemişti. Bugünkü taban çizgisi 1'dir; `display.rs` olmadan derlenmiş
> binary'de de 1 çıktı, yani artış bu task'tan gelmiyor.

Düzen değişikliği Mutter'ın `MonitorsChanged` sinyaliyle yakalanıyor: önbellek
zamanlayıcıyla değil, sinyalle geçersizleşiyor. Yani takılan bir monitör bir
sonraki snapshot'ta görünür, önbellek ömrü kadar sonra değil.

## Task 3.3 metodu — `capture.frame`

İstek tek bir monitörü, çağıranın bildiği düzeni ve `initialize` sırasında
bağlanan grant snapshot'ını açıkça adlandırır:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:18",
  "method": "capture.frame",
  "params": {
    "display_id": "mutter:DP-4",
    "topology_id": "v1|0,0,1920,1080,1.0000,0,0|1920,0,1920,1080,1.0000,0,1",
    "session_id": "capture-session-id",
    "grant_id": "grant-id",
    "revoke_epoch": 7,
    "timeout_ms": 8000,
    "freshness": "after_request",
    "include_pointer": true
  },
  "binary_len": 0
}
```

`display_id` scoped ve en fazla 256 byte olmalıdır. `topology_id` boş olamaz ve
16 KiB ile sınırlıdır; `session_id` ile `grant_id` boş olamaz ve 256 byte ile
sınırlıdır. Yalnızca `freshness: "after_request"` kabul edilir. Production
`display_id` şeması `mutter:<connector>`'dır. Düzen kimliği güncel snapshot ile
eşleşmezse `DISPLAY_CHANGED`; connector çözülemezse
`DISPLAY_MAPPING_UNKNOWN`; grant kimliği veya revoke epoch helper'ın bağlandığı
snapshot ile eşleşmezse `REVOKED` döner. Hiçbirinde ilk monitöre düşülmez.

Başarı header'ının hemen ardından `binary_len` kadar ham PNG byte'ı gelir;
native pipe üzerinde base64 yoktur:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:18",
  "result": {
    "display_id": "mutter:DP-4",
    "topology_id": "v1|...",
    "session_id": "capture-session-id",
    "frame_sequence": 42,
    "frame_timestamp_ns": 151412335,
    "frame_identity_source": "source_monotonic_clock",
    "pixel_size": [1920, 1080],
    "desktop_rect": [0, 0, 1920, 1080],
    "stale_frames": 0,
    "include_pointer": true,
    "revoke_epoch": 7,
    "wait_ms": 58.6,
    "encode_ms": 34.0,
    "backend": "linux.mutter.pipewire",
    "mime_type": "image/png"
  },
  "binary_len": 248713
}
```

Frame identity'nin saat alanı `frame_identity_source` olmadan yorumlanmaz.

Reddedilen bir `capture.frame` **hiçbir zaman** binary payload taşımaz
(`binary_len: 0`), ve bir ret bağlantıyı kullanılamaz hale getirmez: sonraki
geçerli istek kareyi verir. İkisi de `capture_frame_ipc_live.rs`'te
(`PCBRIDGE_TEST_CAPTURE=1`) gerçek binary'ye karşı sınanıyor.
Üretici `SPA_META_Header` verirse sequence ve PTS değiştirilmeden taşınır ve
kaynak `spa_meta_header` olur. Ölçülen Mutter/GNOME 46 akışı bu metadata'yı
vermiyor; o durumda sequence PipeWire source ömrü boyunca yerel sayaç,
timestamp o source'un monotonic başlangıcından beri nanosaniye ve kaynak
`source_monotonic_clock` olur. Bu değerler tazelik kararı için kullanılmaz;
kare geldiğinde ayrı bir yerel `Instant` ile damgalanır.

Oturum, PipeWire thread'i ve display reader ilk gerçek capture isteğinde tembel
kurulur. `initialize`, `capabilities` ve `ping` ekran paylaşımı açmaz. Capture
başarısız olursa session/node eşlemesi kapatılır; sonraki deneme eski düğüm
kimliğini kullanmaz. Durum makinesi, kapanma tetikleri ve ölçümler:
**[capture.md](capture.md)**.

## Response eşleştirme ve hata zarfı

Her response request'in string `id` alanını taşır. İstemci birden çok request'i
cevap beklemeden gönderebilir ve response'ları ID ile eşleştirmelidir.

Hatalar aynı envelope içinde taşınır:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:2",
  "error": {
    "code": "UNKNOWN_METHOD",
    "message": "method 'example.unknown' is not available",
    "retryable": false,
    "category": "protocol"
  },
  "binary_len": 0
}
```

## Deterministik test kipi

Deterministik fake backend yalnızca default dışı `test-harness` Cargo feature'ı
ile derlenebilir ve ayrıca `--test-mode` argümanı ister:

```bash
cargo test --workspace --all-targets \
  --features pcbridge-native/test-harness
```

Default production derlemesinde feature kapalıdır. Bu binary `--test-mode`
argümanını kabul etmez; production `capabilities` response'ı
`linux.mutter.pipewire` monitor capture desteğini her istekte çalışma zamanında
yoklar (Task 4.1): oturum veriyolunda `org.gnome.Mutter.ScreenCast` adının
sahibi ve PipeWire soketi. Biri yoksa `unavailable` + `reason_code` döner. Bu
sorgu izin istemez ya da oturum açmaz.

Test kipi sabit `test-native-instance` kimliği, `test` platformu ve
`test.fake` capability backend'i üretir. Fake capability açık bir desktop
desteği iddia etmez; `input.keyboard` ve `input.pointer` feature'ları gerçek
`/dev/uinput` yerine event üretmeyen sahte aygıtlara bağlıdır. `clipboard`
yalnızca testin `PCBRIDGE_TEST_WL_PASTE` ve `PCBRIDGE_TEST_WL_COPY` ile adını
verdiği programları çalıştırır. Bunlar verilmemişse `UNSUPPORTED` döner, yani
test kipi kullanıcının panosuna hiç ulaşmaz. Bu kip yalnızca byte-düzeyi
contract testleri içindir.

## Python supervisor yaşam döngüsü

Task 2.2'deki `NativeClient`, helper'ı MCP stdio taşımasından ayrı üç pipe ile
ve yalnızca ilk native request geldiğinde başlatır. Binary arama sırası sabittir:

1. `PCBRIDGE_NATIVE_BIN` ortam değişkeni,
2. `[native].binary_path`,
3. `pcbridge/_native/<target>/pcbridge-native` paket yolu.

İlk iki explicit yol geçersizse daha düşük öncelikli bir binary'ye sessizce
düşülmez; `NATIVE_NOT_FOUND` döner. Helper çalışma anında indirilmez, derlenmez
ve `PATH` içinde aranmaz. Task 4.3'ten beri `[native].capture = "auto"`
varsayılandır: helper bulunur ve `capture.monitor` destekleniyorsa native yol,
değilse `system_capabilities`'te görünür bir `degraded` gerekçesiyle Python
yolu. `[desktop] capture_backend = "gnome-screenshot"` açıkça seçilmişse `auto`
o seçimi korur ve Python yolunda kalır. `rust` seçimi düşmez, hata verir.

Task 5.2'de `[native].input` seçimi eklenmiştir. Varsayılan `python`, input'u
mevcut Python provider'da tutar ve native helper'ı input için başlatmaz. Task
5.3'te açık `rust` seçimi keyboard ve pointer çağrılarını helper'a yollar;
clipboard Task 5.4 tamamlanana kadar Python provider'da kalır. Helper yoksa
veya `/dev/uinput` açılamıyorsa sessiz fallback yapılmaz.

Supervisor'ın reader, writer ve stderr drainer thread'leri birbirinden
ayrıdır. Request ID'leri process yeniden başlasa bile tekrar kullanılmaz ve
response'lar geliş sırasına göre değil ID ile eşleştirilir. Aynı anda en fazla
16 request bekleyebilir; sınırdaki yeni request `BUSY` döner. Yerel olarak
frame'e dönüştürülemeyen bir request `INVALID_FRAME` döndürür fakat sağlıklı
helper process'ini kapatmaz.

`initialize` sonucundaki `instance_id`, `native_version`, `platform` ve
`features` alanlarının tamamı kullanılabilirlik ilanından önce doğrulanır.
Major/minor uyuşmazlığı `PROTOCOL_MISMATCH`, bozuk envelope veya handshake
`INVALID_FRAME`, EOF ve beklenmeyen process çıkışı `NATIVE_CRASHED` olur.
Process kaybı o nesilde bekleyen bütün request'leri tamamlar; hiçbir request
yeni process üzerinde otomatik olarak yeniden oynatılmaz. Sonraki yeni request
helper'ı yeniden başlatabilir.

Deadline dolunca bekleyen request tablodan atomik olarak çıkarılır ve
best-effort `cancel` gönderilir. `close()` önce framed `shutdown` dener; iki
saniyelik sınırın ardından sırasıyla terminate ve kill uygular, her durumda
child process'i toplar. Job child process'leri native pipe descriptor'larını
miras alamaz.

Helper'a yalnızca grafik oturum için gereken izinli ortam değişkenleri
aktarılır; adında `PASSWORD`, `TOKEN`, `SECRET` veya `API_KEY` bulunan değerler
engellenir. stderr sürekli boşaltılır fakat stdout'a ya da MCP yanıtına
yazılmaz; bellekte yalnızca son 64 KiB tutulur.

## Grant, revoke ve process registry

`state_dir/desktop_unlock.json` geriye uyumlu alanları korur:
`until`, `hard_until`, `reason`, `granted` ve `granted_by`. Yeni yazılan grant
ayrıca `schema_version: 1`, rastgele bir `grant_id` ve monoton
`revoke_epoch` taşır. Python süreçleri eski, yalnızca `until` içeren grant'i
okuyabilir; native helper yalnızca yeni biçimli, aktif ve sert tavanı geçmemiş
grant'e bağlanabilir.

Python read-modify-write işlemleri ayrı ve sabit `desktop_unlock.lock`
üzerinde Unix advisory lock alır. JSON aynı dizindeki `0600` geçici dosyaya
yazılıp `fsync` sonrasında atomik replace ile yayımlanır. State dizini `0700`,
state ve lock dosyaları `0600` tutulur. `desktop_lock` önce epoch'u artırıp
`until` ile `hard_until` alanlarını sıfırlar; kaynak cleanup'ı bu görünür revoke
noktasından sonra başlar. Eski heartbeat, yakaladığı `grant_id` ve epoch artık
eşleşmediği için yeni grant'i uzatamaz.

Native helper initialize sırasında o anki grant kimliğine bir kez bağlanır.
Her korumalı dispatch dosyayı yeniden doğrular; ayrıca 100 ms lease watchdog
grant değişimi, revoke, expiry, eksik veya bozuk state halinde açık native
kaynakları fail-closed kapatır. Lease watchdog, D-Bus gözleminden ayrı bir thread'de
çalışır; takılan bir session servisi revoke süresini uzatamaz. Aynı helper daha
sonra açılan grant'e bağlanmaz; yeni native session gerekir. Native protokolünde
grant oluşturma veya uzatma metodu yoktur.

## Desktop state ve fail-closed kuralları

Ekran kilidi boolean değil, üç durumlu bir observation'dır:
`known_locked`, `known_unlocked` veya `unknown`. Kullanıcı etkinliği de `known`
ya da `unknown` durumunu ve yalnızca `known` iken negatif olmayan `idle_ms`
değerini taşır. Python ve Rust katmanları bu durumları boolean'a indirgemez.

- Ekran kilidi `known_locked` ise read ve write işlemleri `SCREEN_LOCKED` ile
  reddedilir.
- Ekran kilidi `unknown` ise read ve write işlemleri `LOCK_STATE_UNKNOWN` ile
  reddedilir.
- Etkinlik `unknown` ise write işlemi `ACTIVITY_UNKNOWN` ile reddedilir.
- Kullanıcı idle guard eşiğinden daha etkinse write işlemi `USER_ACTIVE` ile
  reddedilir.
- `force=true` yalnızca etkinlik kontrolünü atlar; kilit, grant, revoke ve expiry
  kontrollerini atlamaz.
- Batch ve task akışı etkinliği başlangıçta bir kez kontrol eder. Devam eden bir
  batch içinde etkinlik tekrar okunmaz.

Native lock watcher `org.gnome.ScreenSaver.ActiveChanged` sinyalini dinler ve
bağlantı kaybını `unknown` kabul eder. Aktif native kaynak, kilitli veya bilinmeyen
bir observation geldiğinde kapanır. D-Bus method çağrıları sonlu timeout kullanır.

Kapanma artık bir bayrağı sıfırlamakla kalmıyor: grant'e bağlı kaynaklar
`Lifecycle::register_fail_closed` ile kaydoluyor ve watchdog thread'i revoke ya
da kilit kenarında onlara **kapan** diyor. Bildirim kenar tetiklemeli — revoke
edilmiş bir grant kayıtlı kaynakları saniyede on kez uyandırmaz — ve istek
yolunda revoke'u önce fark eden taraf kenarın sahibi olur, yani kapatma iki kez
çalışmaz. Kaynağın kilidi meşgulse watchdog beklemez; uçuştaki işlem bir sonraki
kapı noktasında kendini iptal eder.

`desktop_unlock` grant oluşturmak için uinput desteği istemez. Ekran capture
kullanılabilir, pointer veya keyboard kullanılamaz durumdaysa grant yine açılır;
structured result `pcbridge.desktop.grant` tipini, grant kimliğini, authorization
snapshot'ını ve `capability_limitations` haritasını döndürür. Bu sınırlamalar grant
verildiğini gizlemez ve kullanılamayan girdiyi destekleniyor gibi ilan etmez.

Her helper `runtime_dir/pcbridge/native/` altında `0600` bir kayıt taşır;
dizinler `0700` olur. Kayıt PID, process başlangıç kimliği ve native instance ID
içerir. Cleanup sinyal göndermeden önce PID ile başlangıç kimliğini yeniden
eşleştirir; yeniden kullanılmış PID'ye sinyal göndermez. Geçiş süresince Python
screencast için exact executable path ve aynı UID kullanan legacy
`kill_helpers()` ayrıca korunur.

### Native yola geçiş runbook'u

Task 4.3 varsayılanı `auto` yaptı, yani `[native]` bölümü olmayan bir kurulum
**bir sonraki başlatmada** native yola geçer; ayrıca bir seçim yapılması
gerekmez. Çalışan süreçler kendiliğinden geçmez. Güncellemeden sonraki ilk
yeniden başlatmada (ya da `rust`/`auto` elle seçildiğinde):

1. `./.venv/bin/python -m pcbridge.cli.lock` çalıştırarak grant'i revoke edin.
2. MCP istemcisinin açtığı eski `python -m pcbridge.server --stdio`
   process'lerini istemciyi kapatarak sonlandırın; yalnızca systemd servisini
   durdurmanın stdio process'lerini durdurmadığını varsayın.
3. `systemctl --user stop pcbridge` ile service process'ini ve cgroup'undaki
   işleri kapatın.
4. Eski helper kayıtlarını inceleyin; yalnızca PID ile process başlangıç
   kimliği eşleşen kayıtların kapanmış olduğunu doğrulayın.
5. Native seçimini yaptıktan sonra yeni service ve yeni stdio process'lerini
   başlatın. Eski aktif grant'i geri yüklemeyin; gerekirse yeni
   `desktop_unlock` çağrısı oluşturun.

Rollback sırası da revoke → native helper'ları kapat →
`[native] capture = "python"` → yeni process'leri başlat şeklindedir.

## Çıkış ve log kuralları

- Temiz EOF, `shutdown` ve uyumsuz major sürüm bağlantıyı kaynak bırakmadan
  kapatır.
- stdout'ta log, banner veya panic metni bulunmaz.
- stderr'e frame içeriği, binary payload, path parametreleri veya başka özel
  veri yazılmaz.
- Executable `config.toml` okumaz ve parola ya da token almaz.
