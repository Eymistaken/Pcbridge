# ADIMLAR.md

Ekran görüntüsü koordinatları ve çekim temizliği üzerine üç adımlık işin
kaydı. Sıra korunuyor: **her adım bitince durulur, bildirilir, onay beklenir.**

| Adım | Konu | Durum |
|---|---|---|
| 1 | Koordinat dönüşümünü sunucuya taşı | ✅ bitti — `e2ac8b2` |
| 2 | Ekran görüntüsü temizliği gerçekten uygulansın | ✅ bitti — `e1a2ee7` |
| 3a | `pcb-shot` ölçeği config'ten okusun | ✅ bitti |
| 3b | `screenshot_scale_long_edge` 1280 → 1536 | ✅ bitti |
| 4 | `shot` unutulursa reddet + instructions | ✅ bitti |

Onaylanan tasarım kararları: **çekim kimliği (`shot=`)** yolu · `pcb-shot`
ölçeği **config'ten** okusun · CLI (`pcb-shot` / `pcb-do`) Adım 1 kapsamında.

---

## ADIM 1 — Koordinat dönüşümünü sunucuya taşı ✅

### Sorun

`screen_capture` görüntüyü uzun kenarı 1280 olacak şekilde küçültüyor,
`mouse` ise global tam çözünürlük koordinatı bekliyordu. Aradaki dönüşümü
**model** yapıyordu:

```
global_x = ofset_x + görüntü_x / ölçek
```

Zayıf modeller bu aritmetiği tutturamıyor, sistematik olarak hedefin
kenarına tıklıyor ve bazen ofset/ölçek bilgisini tamamen kaybediyorlardı.
Gereken bütün veri zaten `capture.Shot` üzerindeydi (`offset`, `scale`,
`scaled`) ve dönüşüm fonksiyonu da orada yazılıydı — kullanılmıyor, yalnızca
metne basılıp modele okutuluyordu.

Mevcut `monitor=` parametresi bu işi **görmüyor**: monitör-yerel *tam
çözünürlük* koordinatı bekliyor, ölçeği hesaba katmıyor. 1280'lik bir
görüntüde `monitor=2, x=640` demek 2560'a tıklamak demek; doğrusu 2880.

### Yapılanlar

**1. Dönüşüm tek geçide indi** — `capture.to_global()`:

| girdi | davranış |
|---|---|
| `shot` da `monitor` da yok | koordinat zaten global — **eski davranış aynen** |
| `monitor=2` | monitör-yerel, tam çözünürlük — **eski davranış aynen** |
| `shot="m2-a1b2c3"` | o çekimin ofseti **ve ölçeği** uygulanır |
| ikisi birden | reddedilir — farklı uzaylar, sessizce birini seçmek tam da önlenmek istenen hata |
| `shot` bir pencere çekimi | reddedilir — ekranda nerede olduğu bilinmiyor |

`tools.py` ve `ops.py` yalnızca dizin listesini bağlıyor;
`monitorslib.to_global` çağrıları oralardan tamamen kalktı. İkinci kopya yok
— iki yerde yapılsaydı biri gün gelir güncellenmez ve **sessizce 1920 piksel
sola tıklanırdı.**

**2. Çekim kaydı diskte.** Her PNG'nin yanına `<id>.json` yazılıyor: ofset,
boyut, ölçek, monitör, PNG'nin **mutlak** yolu, `taken_at`. Diskte olmasının
sebebi `pointer.json` ile aynı — `pcb-do`'nun her çağrısı yeni bir süreç.
İki dizin de aranıyor (`state_dir/shots` + `pcb-shot`'ınki), yani MCP'den
çekilen görüntüye kabuktan tıklanabiliyor ve tersi.

**3. Kimlik biçimi süzülüyor** (`SHOT_ID_RE = ^(?:m\d{1,2}|win)-[0-9a-f]{6}$`).
Kimlik modelden geliyor ve doğrudan dosya adına dönüşüyor; süzülmeseydi
`shot="../../.ssh/id_rsa"` dizin dışına çıkardı. Hex kısmı PNG adındaki
rastgele son ekle aynı — ikinci bir rastgelelik kaynağı yok, yani dosya adına
bakan insan kimliği okuyabiliyor.

**4. Araç yüzeyi.** `mouse` ve `computer_batch` (ve `pcb-do`) `shot` kabul
ediyor; `screen_capture` her görüntünün yanına `shot: m2-a1b2c3` satırı
basıyor ve dönüşüm talimatı yerine *"koordinatı gördüğün gibi ver"* diyor.
Metin bloğu her zaman **ilk sırada** kalıyor.

### Yol boyunca bulunan iki gerçek açık

- **`pcb-do` bayatlık kontrolü yanlış şeye bakıyordu.** Ölçüt "klasördeki en
  yeni PNG"ydi; arada taze bir çekim varsa **eski bir `shot` kimliği taze
  sayılırdı** — yani korumanın engellemek için var olduğu şeyin ta kendisi.
  Artık `shot` verilmişse o çekimin kendi yaşına bakılıyor. Doğrulama
  sırasında gerçek makinede ateşlendi:
  `` `m2-04b4c5` cekimi 313 saniyelik (sinir 60) ``.
- **`pcb-shot --out /başka/dizin` ile alınan çekimin kimliği bulunamıyordu.**
  `pcb-do` ayrı bir süreç, `--out`u bilemez. Kayıt artık arama dizinine de
  kopyalanıyor; PNG'nin mutlak yolu kayıtta durduğu için görüntü nerede olursa
  olsun bulunuyor.

### Dokunulan dosyalar

| dosya | ne |
|---|---|
| `pcbridge/desktop/capture.py` | `Shot.id` + `taken_at`, `save_meta` / `load_shot` / `to_global` (tek geçit), `SHOT_ID_RE` |
| `pcbridge/config.py` | `shot_search_dirs`, `agent_shot_path` (yol hesabının tek kaynağı) |
| `pcbridge/cli/__init__.py` | `shot_dir()` artık yalnızca yaratıp mod veriyor |
| `pcbridge/desktop/batch.py` | `shot` alanı ayrıştırma + doğrulama, `Ops` imzaları, `describe()` |
| `pcbridge/desktop/ops.py` | `_global()` üzerinden tek geçide bağlandı |
| `pcbridge/tools.py` | `mouse.shot`, `computer_batch` açıklaması, `screen_capture` metni, bayatlık uyarısı |
| `pcbridge/cli/shot.py` | kimlik satırı, `--json`'da `id`, `sweep` JSON'ları da siliyor, `--out` kaydı |
| `pcbridge/cli/do.py` | `shot`'a duyarlı bayatlık kontrolü |
| `tests/test_desktop.py` | bölüm 45 ve 46 yeni; 24, 33, 34, 35 genişledi |
| `tests/test_e2e.py` | `mouse.shot` şeması, açıklama kontrolleri |

### Test durumu

```
tests/test_desktop.py   524 geçti, 0 başarısız   (önce 462)
tests/test_e2e.py       239 başarılı, 0 başarısız, 9 atlandı
```

### Gerçek makine doğrulaması — 2026-09-06, ölçüldü

Görüntüde seçilen bir noktaya `move` gönderildi, sonraki karede imlecin
**hotspot'u** arandı (ağırlık merkezi değil: ok imlecinin sivri ucu sol üst
köşede, kütlesi aşağı sağa uzanıyor, o yüzden ağırlık merkezi sistematik
olarak ~7 px aşağı kayıyor — bu ölçüm yönteminin hatası, dönüşümün değil).

| görüntü noktası | sunucunun ürettiği global | imlecin bulunduğu yer | sapma |
|---|---|---|---|
| (150, 620) | (2145, 930) | (150, 620) | **0 px** |
| (1100, 200) | (3570, 300) | (1100, 200) | **0 px** |

Aritmetik de doğru: `1920 + 150/0,667 = 2145`, `1920 + 1100/0,667 = 3570`.

MCP tarafı gerçek stdio sunucusuyla ayrıca sınandı (bu oturumun kendi MCP
bağlantısı eski kodu çalıştırıyor — `CLAUDE.md`, 2026-08-21 ölçümü):

- `mouse(x=640, y=360, shot=…)` → `(2880, 540)` ✅
- `computer_batch` içinde `{"a":"move","x":200,"y":150,"shot":…}` → `(2220, 225)` ✅
- `shot` + `monitor` birlikte → reddedildi ✅
- `shot="../../etc"` → reddedildi ✅
- Bayat kimlik (`313 saniyelik, sınır 60`) → reddedildi ✅
- `--out` ile alınan çekimin kaydı iki dizine de yazıldı ✅

---

## ADIM 2 — Ekran görüntüsü temizliği ✅

### Sorun

`ShotStore.sweep()` yalnızca `publish()` içinden çağrılıyordu, `publish()` ise
yalnızca `transport != "stdio"` iken. Yani stdio ile bağlanıldığında — asıl
kullanılan yol — `shots/` klasörü **hiç** temizlenmiyordu.
`shot_keep_hours = 24` yazıyor ama hiçbir zaman uygulanmıyordu.

### Yapılanlar

24 saat politikası aynen kaldı, artık gerçekten uygulanıyor:

- `ShotStore.__init__` içinde bir kez `sweep()` — uzun süre çekim yapılmayan
  dönemden sonra birikenler için.
- `screen_capture` her çekimden **önce** `sweep()` — taşımadan bağımsız.
  Silinen dosya sayısı `audit.log`'a `swept=` olarak yazılıyor.
- `publish()` içindeki çağrı **duruyor**; HTTP yolunun davranışı değişmedi.
  Docstring artık bunun tek tetikleyici olmadığını söylüyor.
- `sweep()` eşleşen `<id>.json` çekim kayıtlarını da temizliyor ve kaç dosya
  sildiğini döndürüyor. (Tek başına kalan bir kayıt `shot=` ile bulunur ama
  arkasında görüntü olmaz — ajan olmayan bir görüntüye tıklamaya çalışırdı.)

### Testin gerçekten bir şey ölçtüğü doğrulandı

Yeni bölüm 47 aracın **kendisini** çağırıyor (`FastMCP.get_tool(...).fn`),
çünkü "sweep çağrılıyor mu" sorusunun başka türlü cevabı yok:
`ShotStore.sweep()`in doğru çalışması zaten test ediliyordu, eksik olan onu
**kimin** çağırdığıydı.

Düzeltme geçici olarak geri alınıp koşulduğunda test **hatanın tam tarifini**
veriyor:

```
stdio: cekim eski PNG'yi supurdu     FAIL
stdio: cekim eski kaydi supurdu      FAIL
http:  cekim eski PNG'yi supurdu     PASS      <- publish() supuruyordu
```

### Gerçek makine doğrulaması — 2026-09-06

`state_dir/shots` içine 25 saatlik iki PNG + bir `<id>.json` ve bir de taze
PNG kondu, sonra **gerçek `--stdio` sunucusu** el JSON-RPC ile sürüldü
(`desktop_unlock` → `screen_capture`):

| dosya | yaş | sonuç |
|---|---|---|
| `eski-test-1.png`, `eski-test-2.png` | 25 saat | silindi ✅ |
| `m1-000001.json` | 25 saat | silindi ✅ |
| `taze-test.png` | taze | **duruyor** ✅ |

Aynı çağrının metin bloğu Adım 1'i de doğruladı: ilk sırada, `shot: m2-a2f8ef`
kimliğiyle ve "ofseti ve ölçeği pcbridge kendisi uyguluyor" talimatıyla.

---

## ADIM 3 — Ölçek tutarsızlıkları

### (a) `pcb-shot` ile `screen_capture` ölçeğini eşitle ✅

`pcb-shot` varsayılanı `--scale 0` (tam çözünürlük), `screen_capture`
varsayılanı 1280'di. Ajanın gördüğü çözünürlük hangi yoldan bağlandığına göre
değişiyordu: aynı ekran, iki farklı piksel uzayı.

`pcb-shot --scale` varsayılanı artık `None`; verilmezse
`cfg.desktop.screenshot_scale_long_edge` kullanılıyor. `--scale 0` elle hâlâ
verilebilir.

`cli/shot.py` başlığındaki *"TAM ÇÖZÜNÜRLÜK VARSAYILAN"* gerekçesi — *"ölçek
aritmetiği hatası diye bir SINIF ortadan kalkıyor"* — Adım 1 ile
geçersizleşti: o hata sınıfı artık modelde değil, kodda kapandı. Docstring
yeniden yazıldı.

**Doğrulandı** (gerçek makine): `--scale` verilmeden → `1280×720`,
`--scale 1536` → `1536×864`, `--scale 0` → `1920×1080` + uyarı.

### (a2) 1568 sınırının anlamı değişti — uyarı eklendi ✅

Adım 3(b)'yi değerlendirirken çıktı: **1568 artık sadece bir bilgi
tutarlılığı meselesi değil, sessiz bir hesap hatası kaynağı.**

Anthropic API uzun kenarı 1568'i aşan görüntüleri kendisi küçültüyor.
Eskiden bunun sonucu *"raporlanan ölçek modelin gördüğünden farklı olur"*du —
rahatsız edici ama zararsız, çünkü hesabı model yapıyordu ve gördüğü şeye
bakıyordu. Adım 1'den sonra hesabı **sunucu** yapıyor:

- model 1568'e indirilmiş karedeki pikseli söylüyor,
- `to_global()` kayıtlı ölçeği (`scale=1.0`) uyguluyor,
- aradaki **1,22 kat** sessizce koordinata giriyor — ekranın sağ yarısında
  yüzlerce piksel, ve hata hiçbir yerde görünmüyor.

`--scale 0` tavsiyesi bu yüzden tehlikeli hale gelmişti (`SKILL.md` bunu
öneriyordu). Eklenen koruma bir **kapı değil uyarı**, çünkü kullanıcı tam
çözünürlüğü bakmak için isteyebilir:

```
⚠️ Bu goruntunun uzun kenari 1920 px ve 1568 px'i asiyor. … `shot` ile
verdiginiz koordinat sistematik olarak sasar (yaklasik 1.22 kat). …
bu goruntu yalnizca BAKMAK icin.
```

Uyarı `screen_capture` ve `pcb-shot` çıktılarında, kullanım talimatının
**sonunda** duruyor (üstünde dursaydı son okunan şey talimat olurdu).
`SKILL.md` de düzeltildi.

> Bu, ölçüm değil **çıkarım** olarak işaretli: 1568 sınırı Anthropic'in
> belgelenmiş davranışı (`PLAN.md` §9b/2), buradaki sonuç ondan türüyor.
> Kaymanın kendisi ölçülmedi — ölçmek için modelin ham piksel tahminini
> yalıtmak gerekir ve o tahminin kendi hata payı bu etkiyle karışır.

### (b) `screenshot_scale_long_edge` 1280 → 1536 ✅

**Onaylandı ve uygulandı.** `config.py` varsayılanı ve `config.example.toml`
1536 oldu; `config.toml` bu satırı taşımıyor, yani varsayılandan geliyor.
Doğrulandı: `pcb-shot` ve `screen_capture` artık `1536×864`, ölçek 0,800.

Aynı tam çözünürlük çekimi üç ölçeğe indirilip karşılaştırıldı (ard arda
çekimlerde ekran değiştiği için tek kareden üretmek daha adil):

| uzun kenar | boyut | ölçek | dosya | jeton (≈ w·h/750) |
|---|---|---|---|---|
| 1280 | 1280×720 | 0,667 | 514 KB | ~1230 |
| **1536** | 1536×864 | 0,800 | 668 KB | ~1770 |
| tam | 1920×1080 | 1,000 | 431 KB | ~2765 ⚠️ 1568 üstü |

**Okunabilirlik ölçüldü** — yoğun küçük yazılı bir ekran (Claude Code
oturumu) iki ölçekte de okundu. Ayırt edici ölçüt **Türkçe diakritikler**:

| 1280'de görünen | 1536'da görünen |
|---|---|
| "YAPILACAKLAR.md **degisikliklerini** ve göre…" | "YAPILACAKLAR.md **değişiklikleri** ve görev" |

1280'de `ğ` ve `ş` bulanıklaşıp kayboluyor ve kelime tahmin edilerek
okunuyor; 1536'da doğrudan okunuyor. Bu makinede arayüzün tamamı Türkçe.

**Önerim: evet, 1536.** Gerekçe: 1568 garantisi korunuyor (32 px pay),
diakritikler okunur hale geliyor, hedefleme keskinliği 1,5 px/piksel'den
1,25'e iniyor. Bedel görüntü başına ~540 jeton (%44 göreli, ama mutlak
olarak küçük: Claude'da bir görüntü zaten ~1200–1900 jeton). 1536 ayrıca
64'ün katı ve 1920×1080'i tam sayıya bölüyor.

Kabul ederseniz değişecek tek şey `config.example.toml` ve `config.toml`
içindeki bir satır; kod zaten ayarı okuyor.

### Belge güncellemeleri ✅

`config.example.toml` (iki yolun da bu ayarı kullandığı + 1568 gerekçesi) ·
`skills/computer-use/SKILL.md` (ölçek notu ve `--scale 0` uyarısı).

---

---

## ADIM 4 — `shot` unutulursa ne olacak ✅

### Sorun

Adım 1'in bıraktığı kalıntı risk: model `shot` vermeyi **unutursa** koordinat
global sayılır ve eylem sessizce yanlış yere gider. 1536'lık bir görüntüden
okunan (640, 360) sağ ekrandaki düğmeyi değil **sol ekranın ortasını**
gösterir — ve hiçbir yerde hata görünmez.

### İki katman

**1. Söyleme.** MCP `instructions` (istemcinin sistem promptuna gidiyor)
artık koordinat kuralını açıkça yazıyor: ekran görüntüsünden okunan koordinat
`shot` ile gönderilmeli, aritmetik yapılmamalı, çıplak koordinat global
sayılıp tıklama başka yere düşer.

**2. Reddetme.** Söylemek zorlamak değil. Dört koşul birden doğruysa çağrı
durur:

- `shot` yok, `monitor` yok
- Son 60 saniyede **küçültülmüş** bir çekim var (`scale < 1.0`)
- Koordinat o çekimin ölçekli kutusunun içinde

Belirsizlik **çözülemez** — (640, 360) gerçekten de geçerli bir global
koordinat. O yüzden tahmin edilmiyor, soruluyor. Bu, `batch.py`'nin
`expect_focus` kararının aynısı: *"çözüm korumayı kapatmak değil, niyeti
söyletmek; kaza tam da beyan edilmemiş bir niyetten çıkmıştı."*

Red mesajı iki çıkış yolu veriyor, ikisi de **zaten var olan** parametreler:

```
⛔ Koordinat (640, 360) BELIRSIZ. 2 saniye once kucultulmus bir ekran
   goruntusu aldiniz (`m2-bb424f`, 1536x864, olcek 0.800) ve bu koordinat o
   goruntunun icinde kaliyor -- ama ne `shot` ne `monitor` verdiniz, yani
   GLOBAL tuval koordinati sayilacak ve eylem monitor 1 (DP-4) uzerine duserdi.
     · Koordinati o goruntuden okuduysaniz:  shot="m2-bb424f"
     · Gercekten global/monitor koordinatiysa: monitor=<numara>
   Ikisinden birini secin; hangisini kastettiginizi tahmin etmiyoruz.
```

Ölçüt üçünün kesişimi olduğu için dar: sağ ekrana yapılan global çağrılar
(x ≥ 1536) hiç etkilenmiyor, ölçeksiz çekimler belirsizlik yaratmıyor, bayat
çekimler sayılmıyor. `[desktop] ambiguous_coord_guard = false` ile kapanır.

**Yanlış pozitif kabul edildi:** gerçekten global koordinat veren bir çağrı
bir tur kaybeder ve `monitor=` ile tekrar dener. Sessiz yanlış tıklamadan iyi
bir takas — 2026-08-02'de masaüstündeki 23 öğe tam da böyle gitti.

### Gerçek makine doğrulaması — 2026-09-06

| senaryo | sonuç |
|---|---|
| Varsayılan ölçek | `1536×864`, ölçek 0,800 ✅ |
| `move (640,360)` — **`shot` unutulmuş** | **reddedildi**, gerekçe çekimi ve iki yolu söylüyor ✅ |
| `move (640,360)` + `shot` | `(2720, 450)` ✅ |
| `move (640,360)` + `monitor=1` | `(640, 360)` — niyet beyan edilmiş, geçti ✅ |
| `move (2880,540)` — kutu dışı global | dokunulmadı ✅ |
| MCP `instructions`'ta kural | var ✅ |
| `mouse` ve `computer_batch`, `shot` unutulmuş | ikisi de reddetti ✅ |

---

## Doğrulama

Her adım sonunda:

```bash
./.venv/bin/python tests/test_desktop.py && ./.venv/bin/python tests/test_models.py
```

Servis tarafı — kod değişikliği **kendi MCP çağrılarıyla doğrulanmaz**
(`CLAUDE.md`: bu oturumun stdio süreci eski kodu çalıştırıyor):

```bash
systemctl --user restart pcbridge
```

```bash
PCBRIDGE_TEST_NO_AGENT=1 ./.venv/bin/python tests/test_e2e.py
```

Gerçek makinede uçtan uca (üç adım bitince), `CLAUDE.md`'nin fare kuralına
uyarak — **önce `move`, doğrula, ancak ondan sonra tıklama**:

```bash
./bin/pcb-shot --json
```

Kimliği ve ölçeği oku, PNG'yi `Read` ile aç, seçilen noktaya
`pcb-do '{"a":"move","x":…,"y":…,"shot":"m2-…"}'` gönder, yeni bir
`pcb-shot` ile imlecin hedefte olduğunu gör. Adım 2 için
`ls ~/.local/state/pcbridge/shots` ile eski dosyaların gerçekten gittiğini
göster.

---

## Bu işle ilgisi olmayan iki gözlem

- **`tests/test_models.py`'de 4 kontrol başarısız ve bu işten önce de
  öyleydi** (`git stash` ile doğrulandı). `config.toml`'daki `antigravity`
  varsayılanı `gemini-3.8-flash`, test `gemini-3.6-flash` bekliyor — son
  commit'ten (`config ornegi: gemini 3.8 ve 3.7 eklendi`) kalma. Dokunulmadı.
- **`AGENTS.md`** izlenmeyen bir dosya olarak duruyor (`CLAUDE.md`'nin Codex
  için kopyası). Bu işin ürünü değil, commit'e dahil edilmedi.
