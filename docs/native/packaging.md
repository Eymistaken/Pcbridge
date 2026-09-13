# Native yardımcı: derleme, paket ve tanı

> Kurallar ve mimari: **[CLAUDE.md](../../CLAUDE.md)** · Sıradaki iş: **[WALKTHROUGH.md](../../WALKTHROUGH.md)**

`pcbridge-native`, Python sunucusunun yanında çalışan özel bir yardımcı süreç
(protokol: [protocol-v1.md](protocol-v1.md), capture: [capture.md](capture.md)).
Bu belge onun nasıl derlendiğini, nereye konduğunu, çalışmak için neye ihtiyaç
duyduğunu ve bir kurulumun nasıl teşhis edildiğini anlatıyor (Task 4.1).

## Derlemek ile çalıştırmak ayrı

| | Derlemek için | Çalıştırmak için |
|---|---|---|
| Rust | 1.95.0 (`rust/rust-toolchain.toml`), rustup | — |
| Sistem paketleri | `libpipewire-0.3-dev`, `libspa-0.2-dev`, `libclang-dev`, `pkg-config` | `libpipewire-0.3-0t64`, `libc6` (≥ 2.39), `libgcc-s1` |
| Oturum | — | PipeWire soketi; oturum veriyolunda `org.gnome.Mutter.ScreenCast` |
| `python3-gi`, GStreamer, `pipewiresrc` | — | **gerekmez** |

Çalışma zamanı kütüphaneleri ölçüldü (2026-09-13, release, `readelf -d`):
`libpipewire-0.3.so.0`, `libgcc_s.so.1`, `libc.so.6`, `ld-linux-x86-64.so.2`.
En yüksek glibc sembol sürümü `GLIBC_2.39` (`objdump -T`): Ubuntu 24.04'te
derlenen binary daha eski bir glibc'de (ör. 22.04) **çalışmaz**. İlk artifact
hedefi yalnızca `x86_64-unknown-linux-gnu`.

Erişilebilirlik (`ui_dump`, `ui_click`) ve Python ekran yayını hâlâ
`python3-gi` istiyor. Bu native capture'dan bağımsız bir bağımlılık;
`doctor.sh` onu ayrıca söylüyor.

## Derleme

```bash
scripts/build-native.sh --check   # yalnizca gereksinimler
scripts/build-native.sh           # derle, dogrula, kur
```

Betik şunları yapar, başka hiçbir şey yapmaz:

1. Gereksinimlere bakar; eksik varsa kurulum komutunu yazıp `3` ile çıkar.
2. `PCBRIDGE_BUILD_ID`'yi ayarlar: commit'in ilk 12 hanesi, `rust/` altında
   commit'lenmemiş değişiklik varsa `-dirty`. Zaman damgası yok — aynı
   kaynaktan iki derleme aynı kimliği taşır.
3. **`rust/` içinden** `cargo build --release --locked -p pcbridge-native
   --target x86_64-unknown-linux-gnu`. `rust/` içinden, çünkü
   `rust-toolchain.toml` çalışma dizinine göre seçilir; depo kökünden
   `--manifest-path` ile çağrılan cargo onu görmez.
4. Derlenen binary'ye `--build-info` sorar ve **reddeder**: profil `release`
   değilse, `test_harness` açıksa, hedef ya da build kimliği beklenen değilse.
5. `pcbridge/_native/x86_64-unknown-linux-gnu/pcbridge-native` olarak kurar
   (geçici dosya + `mv`; çalışan eski süreç eski dosyayı kullanmaya devam eder)
   ve yanına `build-info.json` yazar.

Çalışan servisi ve stdio süreçlerini **yeniden başlatmaz**. Yeni binary yalnızca
bir sonraki başlatmada seçilir: varsayılan `[native] capture = "auto"` (Task 4.3)
onu kendiliğinden kullanır, `python` seçiliyse kullanılmaz.

Neden yalnızca release: debug derlemede PNG kodlama monitör başına ~1755 ms,
release'te ~200 ms (ölçüldü 2026-09-13, [capture.md](capture.md)).

Ölçüldü: bağımlılıklar önbellekteyken derleme **16,8 sn**, binary
**5.339.112 bayt**. Temiz (önbelleksiz) derleme süresi ölçülmedi.

`pcbridge/_native/` depoya girmez (`.gitignore`).

## Binary kendini anlatıyor

```bash
pcbridge/_native/x86_64-unknown-linux-gnu/pcbridge-native --version
pcbridge/_native/x86_64-unknown-linux-gnu/pcbridge-native --build-info
```

İkisi de protokol başlatmaz, oturum veriyoluna ya da PipeWire'a dokunmaz.
`build_id` ayrıca `initialize` cevabında gelir; Python istemcisi onu
`NativeHandshake.build_id` olarak saklar. Biçim:
[protocol-v1.md → Komut satırı](protocol-v1.md#komut-satırı).

## Bulma sırası

`pcbridge/native/discovery.py`, PATH'te aramadan:

1. `PCBRIDGE_NATIVE_BIN` ortam değişkeni
2. `config.toml` → `[native] binary_path`
3. paket: `pcbridge/_native/<hedef>/pcbridge-native`

Hiçbiri yoksa `NATIVE_NOT_FOUND`. `capture = "python"` bundan etkilenmez;
`auto` Python'a düşer ve `system_capabilities`'te `degraded` gösterir; `rust`
seçili kalır ve hata verir.

## Tanı: `doctor.sh` → 8. Native yardimci

`python -m pcbridge.native.diagnostics` çalışır. İzin istemez, ekran paylaşımı
açmaz, gerçek izin dosyasına dokunmaz. Söyledikleri:

- `[native] capture` seçimi ve yardımcının nereden bulunduğu — yoksa seviye
  seçime göre: `python` bilgi, `auto` uyarı, `rust` hata
- `--build-info`: sürüm, build, protokol, hedef, profil. Release değilse
  uyarı; test-harness derlemesi, protokol ya da hedef uyuşmazlığı hata
- çalışma zamanı kütüphaneleri: çözülemeyen varsa hata, belgelenmemiş bir
  bağımlılık varsa uyarı
- geçici bir state dizininde handshake + `capabilities`: `capture.monitor`
  durumu ve değilse gerekçesi
- erişilebilirliğin hâlâ `python3-gi` istediği

Ölçüldü (2026-09-13, bu makine, gerçek config): yardımcı paketten bulundu,
`release`, kütüphaneler tamam, `capture.monitor: supported`; `[native] capture
= "python"` olduğu için "hazır ama kullanılmıyor" notu (o gün varsayılan `python`
idi; Task 4.3'ten sonra aynı kurulumda yardımcı kullanılıyor).

## `capabilities` artık çalışma zamanında

Task 4.1'e kadar yardımcı `capture.monitor: supported` diye **sabit** cevap
veriyordu — capture'ın hiç çalışamayacağı bir makinede de. Artık her istekte
oturum veriyolunda `org.gnome.Mutter.ScreenCast` adının sahibine ve PipeWire
soketine bakıyor; oturum açmıyor. Değilse `unavailable`, `reason_code` ve
gerekçe.

## Testler

- `rust/crates/pcbridge-native/tests/build_info.rs` — `--build-info` tek JSON
  nesnesi, `--version` tek satır, başka argüman `2` ile çıkıyor; hazırlık
  kararının dört durumu ve PipeWire soket yolu. Oturum gerektirmiyor.
- `tests/contracts/test_native_diagnostics.py` — tanının seviyeleri: seçime
  göre eksik yardımcı, debug ve test-harness derlemesi, protokol/hedef
  uyuşmazlığı, eski binary, çözülemeyen ve belgelenmemiş kütüphaneler,
  `unavailable` gerekçesi, başarısız handshake.
- `tests/integration/test_native_packaging.py` — paketlenmiş binary ilgisiz
  bir dizine kopyalanıp orada çalıştırılıyor: kendini anlatıyor, yalnızca
  PipeWire ve C çalışma zamanına bağlı, handshake'teki build kimliği
  `--build-info` ile aynı; yardımcı yoksa Python kurulumu çalışmaya devam
  ediyor.

## CI

`.github/workflows/native.yml` — Ubuntu 24.04: derleme bağımlılıkları, fmt,
clippy ve testler (iki özellik kipinde), `scripts/build-native.sh`, paketleme
testi (`PCBRIDGE_TEST_REQUIRE_RELEASE=1`) ve artifact. Canlı capture ve input
testleri CI'da hiç koşmuyor. **Push edilmediği için henüz hiç çalışmadı**
(2026-09-13); YAML yerelde ayrıştırıldı, ilk gerçek koşumun sonucu buraya
yazılacak.

## Yapılmayacak

- Rust araç zincirini çalışma zamanı gereksinimi yapmak.
- İmzasız otomatik güncelleme.
- GUI paketi.

## Geri alma

`[native]` altına `capture = "python"` yazıp süreçleri yeniden başlatmak (Task
4.3'ten beri varsayılan `auto`). Binary'yi kaldırmak gerekmiyor: bulunsa da
`python` seçiliyken kullanılmıyor.
