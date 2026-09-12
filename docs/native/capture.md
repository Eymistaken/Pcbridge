# Native capture session

> Kurallar ve mimari: **[CLAUDE.md](../../CLAUDE.md)** · Sıradaki iş: **[WALKTHROUGH.md](../../WALKTHROUGH.md)**

Bu belge `pcbridge-native`'in Mutter ScreenCast oturumunu anlatır:
`rust/crates/pcbridge-native/src/platform/linux/session.rs`. Karşılığı Python
tarafında `pcbridge/desktop/screencast.py` + `screencast_helper.py`.

**Bugün hiçbir üretim yolu bu oturumu açmıyor.** Protokolde oturum açan bir
metot yok (`dispatch.rs` bu modüle hiç dokunmuyor), `[native] capture` hâlâ
`"python"` ve ekran paylaşan tek yol Python yardımcısı. Task 3.3 kare isteğini,
Task 3.4 Python shot pipeline'ına bağlamayı ekleyecek.

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

## Testler

- `rust/crates/pcbridge-native/tests/capture_session.rs` — 27 test, sahte
  veriyolu. Erken sinyal, yabancı sinyal, eksik stream, kısmi başlangıç
  hatası, çift start/stop, revoke-during-start, kayıp bağlantı, `Drop`,
  watchdog bayrağı, kapının içinden yeniden girme, ve `Lifecycle` kaydının
  gerçekten bağlı olduğu. D-Bus'a dokunmuyor, ekran paylaşmıyor, ekran
  gerektirmiyor.
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

`[native] capture = "python"`. Bu task'ın kodunun üretimde çağıranı olmadığı
için geri alma bugün zaten etkisiz; 3.4'ten sonra anlamlı hale gelecek.
