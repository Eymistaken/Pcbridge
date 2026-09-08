# Native IPC protocol v1

Bu belge, Python host ile `pcbridge-native` child process'i arasındaki yerel
stdio sözleşmesini tanımlar. Bu pipe, MCP stdio taşımasından ayrıdır. Native
stdout yalnızca aşağıda tanımlanan framed response'ları taşır.

Task 2.1 executable'ı yalnızca protokol sınırını uygular. Desktop API'sine,
session D-Bus'a, Wayland'a veya PipeWire'a bağlanmaz. Python desktop backend
varsayılan ve çalışan tek backend olarak kalır.

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
    "features": []
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

## Çıkış ve log kuralları

- Temiz EOF, `shutdown` ve uyumsuz major sürüm bağlantıyı kaynak bırakmadan
  kapatır.
- stdout'ta log, banner veya panic metni bulunmaz.
- stderr'e frame içeriği, binary payload, path parametreleri veya başka özel
  veri yazılmaz.
- Executable `config.toml` okumaz ve parola ya da token almaz.
