# Native IPC protocol v1

> Kurallar ve mimari: **[CLAUDE.md](../../CLAUDE.md)** · Sıradaki iş: **[WALKTHROUGH.md](../../WALKTHROUGH.md)**

Bu belge, Python host ile `pcbridge-native` child process'i arasındaki yerel
stdio sözleşmesini tanımlar. Bu pipe, MCP stdio taşımasından ayrıdır. Native
stdout yalnızca aşağıda tanımlanan framed response'ları taşır.

Executable henüz Wayland, PipeWire veya uinput üzerinden desktop işlemi yapmaz;
Python desktop backend varsayılan ve çalışan tek backend olarak kalır. Native
process grant/revoke lifecycle'ına ek olarak GNOME session D-Bus üzerinden ekran
kilidini ve kullanıcı etkinliğini typed observation olarak izler. Böylece sonraki
backend'ler aynı fail-closed güvenlik sınırını kullanabilir.

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
    "platform": "linux",
    "features": [],
    "lease_bound": true
  },
  "binary_len": 0
}
```

Bilinmeyen major sürüm `UNSUPPORTED_PROTOCOL` error response'ından sonra
bağlantıyı kapatır. Handshake sonrasında farklı minor sürüm kullanan request
`UNSUPPORTED_PROTOCOL_MINOR` döndürür. İkinci `initialize` isteği
`ALREADY_INITIALIZED` ile reddedilir.

## Task 2.1 metotları

Harness yalnızca aşağıdaki metotları kabul eder:

- `initialize`: sürümü ve zorunlu handshake alanlarını doğrular.
- `ping`: `{"pong": true}` döndürür; varsa `params.nonce` değerini aynen
  response'a ekler.
- `capabilities`: gerçek backend'de `protocol-only` ve boş capability listesi
  döndürür.
- `cancel`: `params.target_id` alanını doğrular. Bu task uzun işlem
  başlatmadığı için `canceled: false` döndürür.
- `shutdown`: framed başarı response'ını yazdıktan sonra process'i temizce
  kapatır.

Diğer metotlar `UNKNOWN_METHOD` döndürür ve bağlantı kullanılabilir kalır.
Her response request'in string `id` alanını taşır. İstemci birden çok request'i
cevap beklemeden gönderebilir ve response'ları ID ile eşleştirmelidir.

Hatalar aynı envelope içinde taşınır:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:2",
  "error": {
    "code": "UNKNOWN_METHOD",
    "message": "method 'capture.frame' is not available",
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
argümanını kabul etmez; production `capabilities` response'ı fake backend veya
desktop capability ilan etmez.

Test kipi sabit `test-native-instance` kimliği, `test` platformu ve
`test.fake` capability backend'i üretir. Fake capability açık bir desktop
desteği iddia etmez. Bu kip yalnızca byte-düzeyi contract testleri içindir.

## Python supervisor yaşam döngüsü

Task 2.2'deki `NativeClient`, helper'ı MCP stdio taşımasından ayrı üç pipe ile
ve yalnızca ilk native request geldiğinde başlatır. Binary arama sırası sabittir:

1. `PCBRIDGE_NATIVE_BIN` ortam değişkeni,
2. `[native].binary_path`,
3. `pcbridge/_native/<target>/pcbridge-native` paket yolu.

İlk iki explicit yol geçersizse daha düşük öncelikli bir binary'ye sessizce
düşülmez; `NATIVE_NOT_FOUND` döner. Helper çalışma anında indirilmez, derlenmez
ve `PATH` içinde aranmaz. `[native].capture = "python"` varsayılandır; Task 2.2
runtime capture seçimini değiştirmez.

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

### İlk native opt-in runbook'u

İlk kez `[native] capture = "rust"` veya `"auto"` seçilmeden önce:

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
