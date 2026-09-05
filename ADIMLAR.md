# ADIMLAR.md

Ekran görüntüsü koordinatları ve çekim temizliği üzerine üç adımlık işin
kaydı. Sıra korunuyor: **her adım bitince durulur, bildirilir, onay beklenir.**

| Adım | Konu | Durum |
|---|---|---|
| 1 | Koordinat dönüşümünü sunucuya taşı | ✅ bitti — `e2ac8b2` |
| 2 | Ekran görüntüsü temizliği gerçekten uygulansın | ⬜ bekliyor |
| 3 | Ölçek tutarsızlıkları | ⬜ bekliyor |

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

**Gerçek makine doğrulaması yapılmadı** — kullanıcı iş ortasında durdurdu,
üç adım bitince topluca yapılacak. O ana kadar bilinenler: `pcb-do` görüntü
koordinatı (640, 360) için global (2880, 540) raporladı (beklenen değer),
`--out` kaydı iki dizine de yazıldı, bayat kimlik reddedildi.

---

## ADIM 2 — Ekran görüntüsü temizliği ⬜

### Sorun

`ShotStore.sweep()` kod tabanında yalnızca `publish()` içinden çağrılıyor,
`publish()` ise sadece `transport != "stdio"` iken çalışıyor. Yani stdio ile
bağlanıldığında — asıl kullanılan yol — `shots/` klasörü **hiç**
temizlenmiyor. `shot_keep_hours = 24` yazıyor ama hiçbir zaman uygulanmıyor.

### Yapılacaklar

24 saat politikası aynen kalacak, ama gerçekten uygulanacak:

- `ShotStore.__init__` içinde bir kez `sweep()` — uzun süre çekim yapılmayan
  dönemden sonra birikenler için.
- `screen_capture` her çekimden **önce** `sweep()` — taşımadan bağımsız.
- `publish()` içindeki mevcut çağrı **duracak**; HTTP yolunun davranışı
  değişmesin.
- `sweep()` eşleşen `<id>.json` çekim kayıtlarını da temizleyecek.
  (`cli/shot.py` tarafındaki `sweep()` bunu Adım 1'de zaten yapar hale geldi.)

### Test

`ShotStore(cfg)` kurulumu eski PNG'yi siliyor mu · JSON da siliniyor mu ·
`shot_keep_hours = 0` iken hiçbir şey silinmiyor (mevcut test korunur) ·
stdio yolunda çekim öncesi sweep tetikleniyor mu.

---

## ADIM 3 — Ölçek tutarsızlıkları ⬜

### (a) `pcb-shot` ile `screen_capture` ölçeğini eşitle

`pcb-shot` varsayılanı `--scale 0` (tam çözünürlük), `screen_capture`
varsayılanı 1280. Ajanın gördüğü çözünürlük hangi yoldan bağlandığına göre
değişiyor.

**Karar:** `pcb-shot --scale` varsayılanı `None` olacak; verilmezse
`cfg.desktop.screenshot_scale_long_edge` kullanılacak. Tek kaynak;
`--scale 0` elle hâlâ verilebilir.

`cli/shot.py` başlığındaki *"TAM ÇÖZÜNÜRLÜK VARSAYILAN"* gerekçesi — *"ölçek
aritmetiği hatası diye bir SINIF ortadan kalkıyor"* — Adım 1 ile
geçersizleşti: o hata sınıfı artık modelde değil, sunucuda kapandı. Docstring
buna göre yeniden yazılacak.

### (b) `screenshot_scale_long_edge` 1280 → 1536 önerisi

**Karar kullanıcının**, adım sonunda sorulacak.

| | 1280 | 1536 |
|---|---|---|
| 1920×1080 → | 1280×720 (ölçek 0,667) | 1536×864 (ölçek 0,8) |
| Anthropic 1568 sınırı | altında ✅ | altında ✅ (32 px pay) |
| görüntü jetonu (≈ w·h / 750) | ~1230 | ~1770 (+%44) |
| 1 görüntü pikseli = kaç ekran pikseli | 1,5 | 1,25 |

1568 gerekçesi (`PLAN.md` §9b/2) **geçersizleşmiyor**, gevşiyor: 1536 de
sınırın altında, yani "raporlanan ölçek modelin gördüğüyle aynı" güvencesi
duruyor. Kazanç okunabilirlik ve hedefleme keskinliği, bedel jeton. 1568'e
tam oturmak yerine 1536: 64'ün katı, 1920×1080'i tam sayıya bölüyor, sınıra
pay bırakıyor.

### Belge güncellemeleri

`config.example.toml` (`pcb-shot`'ın da bu ayarı kullandığı) ·
`skills/computer-use/SKILL.md` ölçek notu · `KULLANIM.md`.

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
