# Native capture session

> Kurallar ve mimari: **[CLAUDE.md](../../CLAUDE.md)** · Sıradaki iş: **[WALKTHROUGH.md](../../WALKTHROUGH.md)**

Bu belge `pcbridge-native`'in Mutter ScreenCast oturumunu anlatır:
`rust/crates/pcbridge-native/src/platform/linux/session.rs`. Karşılığı Python
tarafında `pcbridge/desktop/screencast.py` + `screencast_helper.py`.

Native protokoldeki `capture.frame` bu oturumu ve PipeWire akışını tembel açar.
Ancak `[native] capture` hâlâ `"python"`; mevcut Python shot pipeline'ı bu
metodu henüz çağırmıyor ve kullanıcıya açık capture yolu Python yardımcısı.
Task 3.4 bu native sonucu mevcut shot/koordinat sözleşmesine bağlayacak.

## Neden oturum var

Bu makinede ekran görüntüsü almanın sessiz tek yolu **ekran paylaşımı**:
`gnome-screenshot` ve XDG portal flaş patlatıp ses çıkarıyor,
`org.gnome.Shell.Screenshot` GNOME 46'da "Access denied" diyor. Paylaşım
yolunda flaş yok çünkü sistem bunu fotoğraf değil video sayıyor.

Paylaşım demek oturum demek: `CreateSession` → connector başına
`RecordMonitor` → `Start`, ve her connector'ın PipeWire düğüm numarası
arkasından `PipeWireStreamAdded` sinyaliyle geliyor.

## Durum makinesi

```text
Closed ──open()──► Starting ──► Ready ──stop()──► Stopping ──► Closed
                      │                              ▲
                      └────── hata ──► Stopping ──────┴──► Failed
```

`Failed` ayrı bir durum, `Closed`'ın bir çeşidi değil. İkisi de kompozitörde
kaynak tutmuyor; farkı `Failed`'ın **neden** öyle olduğunu hatırlaması.
`stop()` `Failed`'ı temizleyip `Closed`'a indiriyor.

`Starting` ve `Stopping` gerçek durumlar, çünkü hata yolu "başlarken mi düştü,
kapanırken mi" ayrımına muhtaç: başlarken düşen bir oturumun kompozitörde
**zaten** yarattığı kaynaklar var. Geçiş kaydı (`history()`, son 16 kenar)
teşhis için tutuluyor.

## Başlangıç sırası ve her adımın garantisi

1. **Kapı** (`checkpoint`) — grant hâlâ geçerli mi. Veriyoluna dokunmadan önce.
2. `CreateSession`.
3. Her connector için: **kapı**, sonra `RecordMonitor`. Kapı her monitörden
   önce yeniden soruluyor; revoke ikinci monitörden önce gelirse birinci için
   yaratılmış kaynak kapatılıyor.
4. **Kapı**, sonra `Start`.
5. Düğümler gelene kadar bekle (`STREAM_TIMEOUT`, 10 sn). Eşleme **stream
   nesne yoluna** göre yapılıyor, geliş sırasına göre değil.
6. **Kapı** — düğümler beklenirken gelen bir revoke `Ready` üretmemeli.

Herhangi bir adım başarısız olursa o ana kadar yaratılan her şey kapatılıyor
(`Stop`), oturum `Failed`'a düşüyor. Tek istisna: bağlantının kendisi
kaybolduysa `Stop` denenmiyor — ölü sokete gönderilen `Stop` yalnızca ikinci
bir hata üretir.

### Neden stream yoluna göre eşleme

Ölçüldü 2026-09-12, 11 koşum: PipeWire düğüm numaraları geri dönüşümlü ve
**sırası kararlı değil**. Aynı istek için `DP-4`/`DP-3` bir koşumda
`[83, 82]`, başka bir koşumda `[69, 72]` düğümlerini aldı. Yani "ilk gelen
sinyal ilk kaydettiğim monitördür" varsayımı sessizce yanlış ekrana yakalama
demek olurdu. Her `RecordMonitor` kendi stream nesne yolunu döndürüyor ve
eşleme onun üzerinden yapılıyor.

### Erken sinyal

Sinyal aboneliği `connect()` içinde, yani **herhangi bir oturum var olmadan
önce** kuruluyor ve tek bir eşleşme kuralıyla `Stream` arayüzünün tamamını
dinliyor. Bu yüzden `RecordMonitor` ile ilk bekleme arasındaki boşlukta gelen
bir duyuru kaybolmuyor. Bedeli: kural nesne yoluna göre daraltılmadığı için
**başka istemcilerin** stream duyuruları da bize ulaşıyor; tanımadığımız yol
atlanıyor, hata sayılmıyor ve beklemeye devam ediliyor.

Mutter'ın sinyali `Start` dönmeden önce mi sonra mı yaydığı **ölçülmedi**;
tampon sayesinde fark etmiyor.

## Oturumu kapatan beş şey

| Tetik | Yol |
|---|---|
| Revoke | Lease watchdog → `FailClosed` → `SessionHandle::close_fail_closed` |
| Ekran kilidi (ya da kilit durumu bilinmiyor) | Lock watchdog → aynı yol |
| Düğüm gelmedi (timeout) | `MissingStream` → temiz kapatma → `Failed` |
| Host EOF / süreç ölümü | `Drop` → `Stop` |
| Kompozitör bağlantısı koptu | `BusError::Lost` → yerel kapatma, `Stop` denenmez |

**Neden bayrak + kapı ikilisi.** Watchdog oturum kilidini bekleyemez: kesmeye
çalıştığı başlangıç o kilidi stream timeout'u kadar tutabilir ve on saniye
bekleyen bir fail-closed sinyali fail-closed değildir. O yüzden watchdog önce
`FailClosedFlag`'i kaldırıyor, sonra kilidi **deniyor**; kilit meşgulse
uçuştaki çağrı bir sonraki kapı noktasında kendini iptal ediyor.

Bayrak *yetki* değil, **kesme**. `open()` girişte bayrağı temizliyor ve yetkiyi
kapıya soruyor. Aksi halde ekran kilidi — kullanıcı döndüğünde biten bir durum —
oturumu kalıcı olarak mandallardı.

Kilidi kilitlenen bir mutex "kaldırılmış" sayılıyor: oturumda bir yerde panik
olması ekran paylaşımını sürdürmek için gerekçe değil.

`try_lock`'un ikinci ve daha tehlikeli gerekçesi **yeniden girme**: başlangıcın
içinde danışılan kapı `Lifecycle`, ve `Lifecycle` revoke'u fark ettiği anda
kayıtlı kaynaklara kapan diyor. Yani bu kod **kilidi zaten tutan aynı
thread'den** çağrılabiliyor; `lock()` orada kendi kendine kilitlenirdi.

## Python yardımcısından farklar

| Konu | `screencast.py` | Burası |
|---|---|---|
| Farklı monitör kümesi istenirse | `already: True` döndürüp **eski kümeyi tutuyor** | Yeniden kuruyor |
| İmleç kipi değişirse | `ensure_cursor` ayrı metot | Aynı `open()` yolu, `Recreated` döner |
| Sinyal aboneliği | `RecordMonitor` sonrası, stream yoluna göre | `connect()` içinde, arayüze göre |
| Düğüm eşlemesi | stream yoluna göre (aynı) | stream yoluna göre |
| Yetki kapısı | Yok (yardımcı grant bilmiyor) | Her adımda `SessionGuard` |

## D-Bus zaman aşımı

Oturum metotları için **10 sn**, `GetActive`/`GetIdletime`/`GetCurrentState`
için kullanılan 200 ms değil. Gerekçe: o üçü kompozitörün elindeki bir değeri
okuyor, `Start` ise PipeWire akışı pazarlığı yapıyor. Python yardımcısı —
bu sıranın ölçülmüş tek uygulaması — çağrı başına 10 sn veriyor. Sonsuz değil:
takılan bir kompozitör yakalama isteğini süresiz tutamaz.

## Ölçüldü (2026-09-12, gerçek Mutter, iki monitör)

`PCBRIDGE_TEST_CAPTURE=1 cargo test --test capture_session_live`, 11 koşum,
her koşum ayrı süreç (veriyolu bağlantısı ölçümün **dışında**):

| İşlem | Süre |
|---|---|
| `open` (CreateSession + 2×RecordMonitor + Start + iki sinyal) | **2,6 – 4,8 ms**, ortalama 3,6 |
| `open` (aynı istek, yeniden kullanım) | **0,003 – 0,007 ms**, veriyoluna hiç gitmiyor |
| İmleç kipi değişimi (Stop + tam yeniden kurulum) | **3,8 – 6,4 ms** (6 koşumda kaydedildi) |
| `stop` | **0,8 – 1,4 ms** |

Python yardımcısında aynı imleç değişimi **~113 ms** olarak kaydedilmişti. İki
sayı aynı ölçüm noktasından alınmadı (Python'unki yardımcı sürece JSON gidiş
dönüşünü de içeriyor), yani bu bir kıyaslama değil; aynı kullanıcı-görünür
işlemin iki taraftaki maliyeti.

Koşum sonrası `busctl --user tree org.gnome.Mutter.ScreenCast` altında Session
nesnesi kalmadı ve `screencast_helper.py` süreci açılmadı.

## Frame alma ve PNG (Task 3.3)

Oturum connector başına bir PipeWire düğüm numarası üretiyor. Bir düğümden tek
kare alıp PNG'ye çevirmek `capture.rs`'in işi; kuralları
`pcbridge-core::frame`'de.

### Sıra neden bu sırada

PipeWire karesi, üreticinin **hâlâ sahibi olduğu** bellek olarak geliyor. O
tamponu geri vermeden önce ne yaparsak kompozitör onu bekliyor — ve 1920×1080
bir kareyi PNG'ye çevirmek onlarca milisaniyelik saf CPU. Kodlamayı o pencerenin
içinde yapmak bir ekran görüntüsünün bedelini bütün masaüstüne ödetirdi.

Bu yüzden ayrım hatırlanacak bir kural değil, **yapısal**: `FrameSource`
sahipli bir `RgbaFrame` döndürüyor, yani dönüşüm çoktan olmuş ve tampon çoktan
geri verilmiş. Ayrıca işçi `detach`'i kodlamadan önce çağırıyor.

### Sessizce yanlış cevap üreten üç tuzak

1. **`x` kanalı alfa değildir.** `BGRx`/`RGBx`'te dördüncü bayt tanımsız. Onu
   alfaya kopyalamak, üretici oraya sıfır yazdığında **tamamen saydam** bir PNG
   üretir: teknik olarak geçerli, tamamen boş bir görüntü.
2. **Stride, genişlik çarpı dört değildir.** Satırlar hizalama için dolgulu.
   Tamponu tek blok gibi okumak her satırı biraz daha sağa kaydırır.
3. **Boyut sınırları tahsisten önce gelir.** 60000×60000 iddia eden bir başlık
   daha sayıyken reddedilmeli, 14 GB istendikten sonra değil. Test bunu sırayla
   sabitliyor: dört baytlık bir tamponla 3,6 milyar piksellik istek
   `TooManyPixels` döndürüyor, `BufferTooSmall` değil.

### Tazelik ve frame identity neden ayrı

Kare kimliğinin sequence/timestamp alanı ve kaynağı çağırana rapor edilir. Ama
bu alanlar "kare bu isteğe mi ait" sorusunu cevaplamaz: üretici PTS'sini bizim
saatimizle karşılaştırmak ölçülmemiş bir varsayım olur. Bunun yerine kaynak her
kareyi geldiği anda ayrı bir monotonik `Instant` ile damgalıyor; istekten eski
olan atılıyor ve **sayılıyor** (`stale_frames`).

Gerçek ölçüm bir ikinci varsayımı da eledi: Mutter'ın GNOME 46 akışı
`SPA_META_Header` sağlamıyor ve `pw_stream_get_time_n().now` bu portal düğümünde
`0` dönüyor. Kod metadata varmış ya da sıfır geçerliymiş gibi davranmıyor:

- `SPA_META_Header` varsa sequence ve PTS aynen taşınır;
  `frame_identity_source = "spa_meta_header"`.
- Yoksa PipeWire source ömrü boyunca yerel sıra ve source başlangıcından beri
  monotonic nanosaniye taşınır;
  `frame_identity_source = "source_monotonic_clock"`.

### Sınırlar

| Sınır | Değer | Nerede |
|---|---|---|
| Kare bekleme | 8 sn | Python yardımcısıyla aynı; bir MCP çağrısı 110 sn'yi geçemez |
| En büyük kare | 32 milyon piksel | Tahsisten önce |
| IPC yükü | 128 MiB | `MAX_PIXELS * 4 <= MAX_BINARY_BYTES` **derleme zamanında** doğrulanıyor |

### Gerçek PipeWire kaynağı

`pipewire_source.rs` PipeWire `MainLoop`/`Context`/`Stream` nesnelerinin
tamamını tek adanmış thread'de tutar; thread sınırından yalnızca sahipli RGBA8
kare geçer. Event kanalı iki öğeyle sınırlıdır. Format pazarlığı gerçek SPA
enum'larıyla yalnızca BGRx/RGBx/BGRA/RGBA ilan eder; bilinmeyen format,
CPU-map edilemeyen DMA-BUF, negatif stride ve bozuk/eksik chunk reddedilir.

**`Stream` her çekimde yeniden kuruluyor (2026-09-13 düzeltmesi).**
`MainLoop`, `Context` ve `Core` thread ömrü boyunca yaşıyor; `Stream` ise tek
bir düğüm için yaratılıyor ve bir sonraki çekimde yok ediliyor. İlk uygulama
tek bir `pw_stream`'i tutup her çekimde başka düğüme yeniden bağlıyordu ve bu,
aşağıdaki "Yanlış monitör" bölümünde ölçülen hatayı üretti. Eski GStreamer
yardımcısı bu hataya hiç düşmedi, çünkü her çekimde yeni bir `pipewiresrc`
kuruyor.

Düğümü **kimliğiyle** hedeflemek libpipewire'da eskimiş sayılıyor: başlık
`target.object` = hedefin `object.serial`'ını istiyor. Mutter ise yalnızca
kimliği bildiriyor. Bu makinede (PipeWire 1.0.5, WirePlumber 0.4.17) taze akış
+ kimlik doğru monitörü veriyor ve bunu
`each_connector_gets_its_own_monitors_frame` her canlı koşumda sınıyor. Bir
PipeWire yükseltmesi bunu bozarsa yol belli: registry'den düğümün
`object.serial`'ını okuyup `target.object` ile bağlanmak.

Process callback'i tamponu dequeue eder, doğrular ve RGBA8'e kopyalar. Tampon
RAII ile geri verildikten sonra stream **callback'in içinde disconnect edilir**;
ancak bundan sonra sahipli kare işçiye gönderilir ve PNG kodlaması başlar. Bu
sıra yalnızca performans tercihi değil: ilk uygulamada disconnect kontrol
kanalından istendi ve sürekli process callback'leri komutu 2 saniye aç bıraktı.
Gerçek testte wait + encode dışındaki ek yük tam **2001 ms** idi. Stream ilk
kareden sonra kendi callback'inde bırakılınca aynı ek yük **1 ms'nin altına**
indi.

Build için `libpipewire-0.3-dev` ve bindgen'in kullandığı `libclang-dev`
gerekiyor. İkisi de 2026-09-12'de kullanıcı tarafından kuruldu; ölçülen
PipeWire/SPA sürümü 1.0.5. Bunlar build gereksinimleri; paketlenmiş binary'nin
runtime bağımlılık ayrımı Task 4.1'de belgelenecek.

### Gerçek kare ölçümü (2026-09-12)

İki ardışık kare alan release testi beş ayrı session'da çalıştırıldı; toplam
**10 gerçek 1920×1080 kare**. Her PNG decode edildi, ilk karenin siyah olmadığı
ve bütün alfa baytlarının `255` olduğu doğrulandı; her ikilide sequence `1 → 2`
ve timestamp arttı. Sonuçlar:

| Ölçüm | 1. oturum | 2. oturum (bağımsız doğrulama) |
|---|---|---|
| Kare bekleme (ilk kare) | 57,3–63,2 ms | 52,4–83,3 ms |
| PNG encoding (ilk kare) | 32,2–34,0 ms | **49,4–53,0 ms** |
| Toplam, session/source zaten açık | 87,1–97,3 ms | **101,0–107,7 ms** |
| Wait + encode dışı stream bırakma ek yükü | 0,2–0,4 ms | <1 ms |

**İki oturum aynı bandı vermedi ve bu kayıtta durmalı.** İkinci oturum aynı
release binary'siyle, aynı monitörde, 7 koşumda ölçüldü ve PNG kodlaması
tutarlı biçimde ~%50 daha uzun sürdü. Fark gürültü değil: ikinci oturumun
encode örnekleri 49,4 / 49,9 / 50,8 / 51,1 / 51,5 / 53,0 ms ile çok dar.
En olası sebep makine durumu — o sırada `load average` **3,02** ve CPU
governor **powersave** idi — ama bu **ölçülmedi**, yalnızca gözlendi. Sonuç:
tek bir oturumda alınmış dar bir bandı bu makinenin davranışı diye okuma;
sayılar yük altında **%50'ye kadar** kayıyor.

Aynı monitörde Python/GStreamer `screencast_helper.py` ölçümü: **71 ms**,
1920×1080, siyah değil, alfa tamamen opak. Kareler farklı anlarda alındığı için
bu bir piksel-birebir parity iddiası değil; Task 4.2 o gate'i ayrı kuracak.
Bugünkü ölçüm Task 3.3'ün daha dar sorusunu doğruluyor: iki yol da gerçek,
decode edilebilir ve aynı boyutta görüntü üretiyor; Rust yolu debug değil release
derlemede ölçüldü.

Koşumlardan sonra Mutter altında Session nesnesi, `pcbridge-native` process'i,
`screencast_helper.py` process'i ve geçici PNG kalmadı.

**Üretim yolu uçtan uca doğrulandı (2026-09-12, bağımsız).** Gerçek native
binary'ye geçici bir state dizininden grant verilip stdio protokolü üzerinden
sürüldü: `initialize` → `display.snapshot` → `capture.frame`. Sonuç
**421.034 baytlık** bir PNG, `binary_len` ilan edileniyle birebir aynı,
1920×1080, `desktop_rect [0,0,1920,1080]`, `frame_identity_source`
`source_monotonic_clock`. Görüntü istatistikle doğrulandı (5.786 ayrı renk,
kanal ortalamaları ~30, satırlar birbirinden farklı) — yani gerçek masaüstü,
düz dolgu ya da gürültü değil. Yanlış `grant_id` ile aynı istek
`REVOKED`/`safety` döndürdü, stderr boştu, süreç temiz kapandı, arkada Mutter
Session nesnesi kalmadı.

Bu yol artık `capture_frame_ipc_live.rs` ile **otomatik**: aynı diziyi gerçek
binary'ye karşı sürüyor ve her ret tipini ayrı ayrı sınıyor — yanlış
`grant_id`/`revoke_epoch` → `REVOKED`, eski düzen → `DISPLAY_CHANGED`, olmayan
connector → `DISPLAY_MAPPING_UNKNOWN`, şemasız `display_id` ve bilinmeyen
`freshness` → `INVALID_PARAMS` — sonra bir retten sonra helper'ın hâlâ kare
verdiğini doğruluyor. Reddedilen çağrı **hiç** binary taşımıyor. Diğer ikisi
farklı seviyeleri tutuyor: `ipc_protocol.rs` deterministik fake backend'i,
`capture_frame_live.rs` kütüphaneyi doğrudan.

Kapının gerçekten kapı olduğu mutasyonla denendi: `matches_token` her zaman
`true` dönecek şekilde bozulunca test kırmızıya döndü.

## Python tarafına bağlanma (Task 3.4)

Karenin **nereden geldiği** değişti, başka hiçbir şey değişmedi. Kırpma,
ölçekleme, istemciye giden PNG, çekim kimliği, iki arama dizini, kayıt dosyası
ve bütün koordinat dönüşümü `capture.py`'de kaldı. Sebep tek cümle: çekim
kimliği sonraki bir `mouse(shot=…)` çağrısının ofseti ve ölçeği bulma yolu;
o defteri ikinci bir dile taşımak iki kopyanın ayrışıp **yanlış ekrana
tıklanması** demek.

`NativeScreenCast` eski `ScreenCast` tutamacıyla aynı şekle sahip
(`is_open`, `start`, `ensure_cursor`, `capture(connector, path)`, `close`) ve
`capture.py` ikisini ayırt edemiyor. `RustCaptureProvider` ise Python
sağlayıcısının o tutamaç enjekte edilmiş hâli; yalnızca gerçekten farklı olanı
geçersiz kılıyor: kullanılabilirlik, capability raporu ve backend adı.

### Backend tablosu tek yerde

`select_capture_backend` saf bir fonksiyon ve seçim **runtime kurulurken bir
kez** yapılıyor — açık bir oturumun ortasında backend değişmiyor, yani bir
`all` çekimi iki farklı kaynaktan birleştirilemiyor.

| `[native] capture` | Yardımcı var | Yardımcı yok |
|---|---|---|
| `python` | Python | Python |
| `rust` | Rust | **Rust kalır ve görünür şekilde hata verir** |
| `auto` | Rust | Python + `degraded` (gerekçe capability raporunda) |

`rust` satırı önemli: zorunlu tutulmuş bir backend sessizce Python'a dönmüyor.
Zorunlu tutmanın amacı tam olarak onun çalışıp çalışmadığını görmek.

### `taken_at` artık karenin kendi saati

Eskiden bütün dizi için tek bir damga vardı. Native yol karenin **ne kadar
beklendiğini** bildiriyor, o yüzden her çekim kendi geliş anını taşıyor: bir
`all` çekiminde monitörler sırayla okunuyor ve aradaki fark yüzlerce
milisaniye olabiliyor. Damga bayatlık uyarısını sürdüğü için bu fark önemli.
İleriye doğru bir saniyeden fazla sapan damga yok sayılıyor — gelecekten gelen
bir damga bayatlık kontrolünü **sessizce** kapatırdı.

### Tipli red nedenleri provider'dan geçer (2026-09-13 düzeltmesi)

Native yardımcının tipli redleri (REVOKED, DISPLAY_CHANGED, FRAME_TIMEOUT…)
`NativeCaptureError.desktop_error` olarak geliyor. `capture.py` bu istisnayı düz
bir `CaptureError`'a sarıyordu ve provider onu `BACKEND_UNAVAILABLE` diye
raporluyordu: Task 4.2'nin canlı testi revoke edilmiş bir izni
`BACKEND_UNAVAILABLE`/`capability` olarak gördü. Kural artık `capture.py`'de ve
backend'den bağımsız: tipli bir neden taşıyan istisna o nedenle yeniden
fırlatılır. İzin hiç yokken `_grant()` de `GRANT_REQUIRED`/`safety` üretiyor.
Ayrıntı: [verification-linux.md](verification-linux.md).

### Paylaşım göstergesi biraz kayıyor (kayıtta)

Python yolunda ekran paylaşımı `desktop_unlock` ile açılıyor, yani üst
çubuktaki gösterge izinle birlikte beliriyor. Native oturum **istek üzerine**:
ilk `capture.frame` ile açılıyor ve revoke/kilide kadar açık kalıyor. Yani izin
ile ilk çekim arasında grant'i olan ama göstergesi olmayan bir pencere var.

Bu belgelenmiş davranıştan gerçek bir sapma ve gizlenmiyor. Tartışılabilir
biçimde **daha doğru** bir sinyal — o pencerede hiçbir şey ekranı okuyamıyor,
çünkü oturum yok — ama gösterge kullanıcının kanıtı, o yüzden fark keşfedilmek
yerine yazılıyor. `[native] capture` varsayılan olmadan önce (Task 4.3)
yeniden bakılacak.

### Ölçüldü (2026-09-12, gerçek makine, uçtan uca)

`[native] capture = "rust"`, gerçek helper, geçici state dizininde grant:

| Ne | Sonuç |
|---|---|
| `backend_name()` başlamadan önce | `pcbridge-native` |
| `backend_name()` başladıktan sonra | `linux.mutter.pipewire` (tahmin değil, durum) |
| `capture.monitor` | `supported` / `linux.mutter.pipewire` |
| Çekim | `m1-e0cf3b`, DP-4, ofset `(0,0)`, 1920×1080 → 1536×864, ölçek 0,8 |
| PNG | 796.883 bayt, 25.504 ayrı renk (gerçek masaüstü) |
| Kayıt dosyası | yazıldı, geri okundu, `to_global(10,10)` → `(12,12)` |
| `capture()` toplam | **414 ms** (kare + kırpma + ölçekleme + PNG yazma) |

Varsayılan **değişmedi**: `config.example.toml` hâlâ `capture = "python"`.

### PNG kodlayıcı: `image` değil `png`

`PLAN.md` "yalnızca png özelliği açık `image`" diyordu. Ölçüldü: `image` 0.25.10
o özellikle bile `moxcms` (renk yönetimi), `pxfm`, `bytemuck`, `num-traits` ve
`byteorder-lite`'ı sürüklüyor — core'un doğrudan bağımlılık ağacı 15 sandık.
`png` sandığı doğrudan kullanılınca 8. Yaptığımız iş RGBA8 tamponu PNG'ye
yazmak ve testte geri okumak; aradaki hiçbir şeye dokunmuyoruz. Planın niyeti
(bütün bir görüntü yığınını çekmemek) bu şekilde daha sıkı karşılanıyor.

## Çekim artifact'ı ve teslim (Task 3.5)

Task 3.5 iki olguyu ayırıyor: **çekim başarılı oldu** ve **görüntü istemciye
bütün olarak ulaştı**. `shots/` altında bir PNG'nin durması ikincisi değil.

### Yayım: bütün ya da hiç

Eskiden `capture.py` her monitörün PNG'sini ve kaydını o monitör bitince
yazıyordu. Üç şey sessizce ters gidebiliyordu:

1. Bir `all` çekiminde ikinci monitör düşünce birincinin görüntüsü ve kaydı
   diskte kalıyordu — kimsenin almadığı ama `shot=` ile hâlâ bulunan bir çekim.
2. Son ek çakışması (24 bit) başka bir çekimin kaydını eziyordu; sonraki bir
   `shot=` tıklaması o zaman başka bir çekimin ofset ve ölçeğiyle çevrilirdi.
3. Yarıda öldürülen bir çekim tam çözünürlükte bir görüntüyü hiçbir
   süpürmenin bakmadığı bir yerde bırakabiliyordu.

Şimdi:

- Görüntüler `out_dir` içindeki gizli, mod 700 bir hazırlık dizininde
  (`.staging-*`) üretiliyor; ham kareler de orada, `/tmp` kullanılmıyor.
- Bütün hedefler hazır olunca yayım **hard link** ile: `os.link` tek adımda ve
  hedef varsa `FileExistsError` veriyor, `os.replace` gibi sessizce ezmiyor.
  Önce PNG'ler, en son kayıtlar — arkasında görüntü olmayan bir kayıt hiçbir
  an görünmüyor. Hard link desteklemeyen dosya sisteminde `x` kipiyle kopya.
- Yeni kimlik `shot=` aramasının baktığı **bütün** dizinlerde boş olmalı
  (`reserved_dirs`); değilse yeni son ek (en fazla 16 deneme). Kontrol ile
  yayım arasında başka bir süreç aynı adı alırsa bu denemenin yayımladıkları
  geri alınıyor: listede yalnızca başarılı link'ler var, yani silinen hiçbir şey
  başkasının dosyası olamıyor.
- `pcb-shot --out` kayıt kopyası (`copy_meta_to`) yayımın parçası: kopya
  yazılamazsa çekim de yayımlanmıyor.
- Terk edilmiş hazırlık dizinleri (10 dakikadan eski) hem `ShotStore.sweep()`
  hem `pcb-shot` süpürmesinde gidiyor, `shot_keep_hours = 0` olsa bile.

### Teslim: istemci çözemiyorsa başarı değil

`screen_capture` her görüntüyü göndermeden önce baytlarına bakıyor: PNG imzası,
IHDR ve **kayıttaki ölçekli boyut**. Tutmazsa sonuç `isError=true` +
`IMAGE_DELIVERY_FAILED`; metin hangi çekimin ulaşmadığını söylüyor, ulaşan
görüntüler yine gidiyor, `structuredContent.shots` bütün kimlikleri taşıyor.
Boyut kontrolünün sebebi: istemci görüntüden okuduğu pikseli `shot=` ile geri
gönderiyor ve sunucu kayıttaki ölçeği uyguluyor — boyutu kaydıyla uyuşmayan
bir görüntü tıklamayı sessizce kaydırırdı. Eskiden okunamayan görüntü başarılı
sonucun içinde tek satırlık bir notla kayboluyordu.

Metin bloğu her zaman ilk; birden fazla görüntü varsa metin eşleşmeyi açıkça
söylüyor ("Goruntuler asagida bu sirayla: …"). `computer_batch` yalnızca kendi
raporunu kırpıyor, son adımın çekim metnini asla. `tail_chars` sondan tuttuğu
için eski kod kimlik satırlarını ancak çekim metni sınırı tek başına aşınca
kesiyordu — nadir, ama kesilince görüntüler kimliksiz kalıyordu.

stdio'da HTTP bağlantısı üretilmiyor (ölü URL yok); HTTP'de
`/shot/<token>.png` token/TTL yolu aynen duruyor.

### Ölçüldü (2026-09-13, gerçek makine)

Canlı teslim testi gerçek sunucuyu gerçek stdio üzerinden sürdü:
`[native] capture = "rust"`, Python GI içe aktarılamaz (`PYTHONPATH` engeli;
aynı ortamda `python3 -c "import gi"` başarısız) ve `gnome-screenshot`
çalışamaz hâlde.

| Ne | Sonuç |
|---|---|
| `capture.monitor` | `supported` / `linux.mutter.pipewire` |
| `screen_capture(all)` | 2 görüntü, ikisi de 1536×864, istemcide çözüldü |
| DP-4 / DP-3 | 690.509 B · 10.606 renk / 912.058 B · 26.328 renk |
| Monitörler arası piksel eşleşmesi | %26,24 (aynı ekran olsaydı ~%100) |
| Kayıt | ölçekli boyut = çözülen boyut, ofset korunuyor, `taken_at` taze |
| `desktop_lock` sonrası `screen_capture` | `safety` hatası, görüntü yok |
| Kapanıştan sonra | native süreç yok, Python yardımcısı yok, Mutter oturumu artmadı, `.staging-*` yok |

Süre: iki monitörlük çağrı **debug** binary ile 9,6 sn, soğuk ve sıcak aynı.
Monitör başına parçalandığında (release, yük ~1,0, governor `powersave`):
`capture.frame` 271–294 ms (bekleme 60–68, kodlama ~200; daha karmaşık
içerikte 518–527 ms), Python tarafı çözme ~20 + küçültme ~40 +
`save(optimize=True)` **~1000 ms**. Debug binary'de kodlama tek başına
~1755–1811 ms. Python PNG maliyeti eski backend'de de aynen ödeniyor.

Eski yardımcıyla aynı monitörde arka arkaya (native, eski, native), düzeltmeden
sonra: DP-4 **%99,993** (native/native tabanı %99,987), DP-3 **%100,000**.

## Yanlış monitör: bulundu ve düzeltildi (2026-09-13)

Task 3.5'in canlı doğrulamasında, native yol ile eski Python yardımcısı aynı
monitörde arka arkaya karşılaştırıldı (sıra: native, eski, native — iki native
kare gürültü tabanı). Sol monitör (DP-4) birebir tuttu: %99,993, taban
%99,989. Sağ monitör (DP-3) yalnızca **%26,92** tuttu, oysa native'in iki
karesi kendi arasında %99,916'ydı — içerik değişmemişti, backend'ler farklı
şey görüyordu.

İçeriğe bakınca soru netleşti: native DP-4, native DP-3 ve eski DP-4 **aynı
ekranı** gösteriyordu (sohbet penceresi açık sol monitör); yalnızca eski
yardımcının DP-3 karesi birincil monitörün kendisiydi (masaüstü simgeleri,
görev çubuğu). Kanal takası (%0,17) ve bir piksellik kayma (%4–6) sayısal
olarak elendi.

Kök neden bir sıra deneyiyle ayrıldı: taze bir native yardımcıya istekler iki
farklı sırada gönderildi ve her kare eski yardımcının iki monitör karesiyle
karşılaştırıldı.

| Sıra | Önce (tek akış yeniden kullanılıyor) | Sonra (çekim başına akış) |
|---|---|---|
| DP-3, DP-4, DP-3, DP-4 | DP-4 istekleri **DP-3** gösterdi | 4/4 doğru |
| DP-4, DP-3, DP-4, DP-3 | DP-3 istekleri **DP-4** gösterdi (ilk ölçüm) | 4/4 doğru |

Yani yeniden bağlanan akış **ilk bağlandığı düğümde kalıyordu**. Oturum
tarafındaki connector → düğüm eşlemesi (stream nesne yoluna göre) doğruydu;
yanlış olan, doğru düğüm numarasının akışı başka düğüme taşımamasıydı.

Neden hiçbir test yakalamadı: bütün canlı testler **ilk** monitörü okuyordu
(`capture_frame_live.rs` `monitors.first()`, `capture_frame_ipc_live.rs`
`monitors[0]`); MCP canlı testi ise iki monitörü alıp yalnızca boyut, PNG ve
renk sayısına bakıyordu — iki görüntünün **aynı ekran** olduğunu göremezdi
(o koşumda iki PNG 649.475 ve 650.358 bayttı). Varsayılan backend hâlâ
`python` olduğu için kullanıcıya yansımadı; Task 4.3 varsayılanı değiştirmiş
olsaydı `monitor=2` ekran görüntüsü sol monitörü gösterecek ve `shot=`
tıklaması onun koordinatlarıyla sağ monitöre düşecekti.

Artık bunu canlı testler tutuyor:

- `capture_frame_live.rs` → `each_connector_gets_its_own_monitors_frame`:
  ikinci monitör önce, sonra birinci, sonra ikisi tekrar. Aynı monitörün iki
  karesi ≥%90, farklı monitörlerinki <%99 tutmalı. Düzeltmeyle ölçüldü:
  DP-3/DP-3 **%100,00**, DP-4/DP-4 **%99,93**, DP-3/DP-4 **%26,92**.
  **Mutasyonla denendi:** HEAD'deki tek akışlı `pipewire_source.rs` geri
  konunca bu test kırmızıya döndü.
- `test_mcp_capture_delivery.py` → `LiveNativeDelivery`: MCP üzerinden gelen
  monitör görüntüleri çiftler hâlinde karşılaştırılıyor, <%99 olmalı.

Testin ön koşulu kayıtta: iki monitör gerçekten farklı içerik göstermeli. Bir
panel, görev çubuğu ya da tek bir pencere bunu sağlıyor; iki monitörü
tamamen aynı gösteren bir kurulumda test yanlış alarm verir ve mesajı bunu
söylüyor.

## Testler

- `tests/contracts/test_shot_artifacts.py` — 15 test (Task 3.5). Yayımın
  bütün-ya-da-hiç olması (sonraki monitörde hata, yazma hatası, pencere
  çekimi), hiçbir dosyanın üstüne yazılmaması (arama dizinlerindeki kimlik,
  var olan PNG, kontrol ile yayım arasındaki yarış, kayıt kopyası), terk
  edilmiş hazırlık dizinlerinin süpürülmesi ve teslim edilen bloğun
  yayımlanan PNG ile bayt bayt aynı olması.
- `tests/integration/test_mcp_capture_delivery.py` — 8 test (Task 3.5). Gerçek
  bir MCP istemcisi görüntüyü base64'ten çözüp fixture pikselleriyle
  karşılaştırıyor: bellek içi taşıma, **gerçek stdio boruları**, HTTP
  bağlantısının süresi dolunca 404, teslim edilemeyen görüntünün hata olması,
  uzun batch raporunun kimlik satırlarını kesmemesi. Canlı sınıf
  `PCBRIDGE_TEST_CAPTURE=1` ister.
- `rust/crates/pcbridge-native/tests/capture_session.rs` — 27 test, sahte
  veriyolu. Erken sinyal, yabancı sinyal, eksik stream, kısmi başlangıç
  hatası, çift start/stop, revoke-during-start, kayıp bağlantı, `Drop`,
  watchdog bayrağı, kapının içinden yeniden girme, ve `Lifecycle` kaydının
  gerçekten bağlı olduğu. D-Bus'a dokunmuyor, ekran paylaşmıyor, ekran
  gerektirmiyor.
- `rust/crates/pcbridge-core/tests/frame_conversion.rs` — 19 test, elle
  kurulmuş tamponlar. Dolgulu stride, sıfır olmayan chunk offset, kesik tampon,
  kanal sırası, alfa, taşma, sıfır boyut ve PNG gidiş-dönüşü. Beklenen baytlar
  elle yazıldı; hiçbiri dönüştürücüyü çalıştırıp çıktısını kaydederek
  üretilmedi.
- `rust/crates/pcbridge-native/tests/capture_worker.rs` — 15 test, sahte kare
  kaynağı. Bayat kare, zaman aşımı, iptal, revoke, bozuk tampon, başarısız
  attach, gerçek SPA format eşlemesi, frame identity kaynağı ve her hata yolunda
  tam bir `detach`.
- `rust/crates/pcbridge-native/tests/ipc_protocol.rs` — `test-harness` kipinde
  PNG'nin base64 olmadan tam `binary_len` ile taşındığını, decode edilen kesin
  pikselleri ve sınırlandırılmış capture parametrelerini doğrular.
- `rust/crates/pcbridge-native/tests/capture_frame_live.rs` —
  `PCBRIDGE_TEST_CAPTURE=1` yoksa kare okumadan atlanır. Aynı gerçek session ve
  source ile iki frame alır; boyut, PNG imzası, siyah olmayan pikseller, opak
  alfa, artan sequence/timestamp ve her frame sonrası stream kapanışını sınar.
- `rust/crates/pcbridge-native/tests/capture_session_live.rs` —
  `PCBRIDGE_TEST_CAPTURE=1` yoksa atlanır. Gerçek Mutter'a bağlanır; metot
  adlarının, argüman imzalarının ve sinyal biçiminin doğru olduğunu yalnızca bu
  gösterebilir. **Çalışırken üst çubukta paylaşım göstergesi belirir**; kare
  alınmaz, dosya yazılmaz, girdi gönderilmez.

Sahte veriyolunun ne kadar tuttuğu mutasyonla denendi: `abort`'un `Stop`'u,
son kapı noktası ve `Drop` ayrı ayrı bozulunca 6 test kırmızıya döndü.

## Yapılmayacak (bilinçli)

- **RemoteDesktop pointer izni istemek.** `cursor-mode` yalnızca 0 (gizli) ve
  1 (kareye gömülü); Mutter'ın 2. kipi imleci ayrı metadata olarak veriyor ve o
  kapı bu migration'ın açmadığı bir kapı.
- **Portal persistence.**
- **Buffered capture.** Task 7.3'ün işi.

## Geri alma

`[native] capture = "python"`. Varsayılan zaten budur; Task 3.4'e kadar mevcut
Python shot pipeline'ı native protokol metodunu çağırmaz.
