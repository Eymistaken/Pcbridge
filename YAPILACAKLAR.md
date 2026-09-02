# YAPILACAKLAR.md

## Sıradaki iş: `window_focus` açık pencere için aramadan geçmesin

**Belirti.** `window_focus` çağrıldığında masaüstünde Super'a basılıyor,
uygulama adı GNOME aramasına yazılıyor ve Enter'a basılıyor. Uygulama arama
sonuçlarında ilk sırada çıkmazsa Enter **web arama sağlayıcısına** düşüyor ve
tarayıcıda bir DuckDuckGo araması açılıyor. Kullanıcı bunu fark edip sordu:
"super tuşuna basıp pcbridge desktop yazıp duckduckgo aramasına tıklıyorsun,
bilerek mi?"

**Ölçüm — 2026-09-02, `audit.log`.** Süre bunu ele veriyor. Aşağıdaki satırlar
`window_focus` aracının değil, `computer_batch` içindeki **`focus` eyleminin**
kayıtları (`batch_step`) — ikisi de aynı `apps.focus()`'a gidiyor. Bunun ayrıca
yazılması gerekiyor, çünkü **`window_focus` olayları `ms` alanı taşımıyor**:
o olaylara bakarak bu ölçüm tekrarlanamaz. Ham satır:

```
{"ts": "2026-09-02T09:53:19", "event": "batch_step", "i": 0,
 "a": "focus 'PcBridge Desktop'", "ok": true, "ms": 6935}
```

Zaten **açık ve görünür** bir pencere için:

| Saat (2026-09-02) | Hedef | Süre |
|---|---|---|
| 09:53:19 | PcBridge Desktop | 6935 ms |
| 10:19:52 | PcBridge Desktop | 6669 ms |
| 10:27:40 | PcBridge Desktop | 6674 ms |
| 13:23:11 | PcBridge Desktop | 6683 ms |
| 17:11:17 | PcBridge Desktop | 6616 ms |
| 17:15:49 | PcBridge Desktop | 6631 ms |

Altı çağrının altısı da **~6,6 saniye**. Doğrudan pencere etkinleştirme
milisaniye sürer. Bu süre genel görünüm animasyonu + yazma + arama
sonuçlarının gelmesi demek — yani açık pencere için bile arama yolu
kullanılıyor. Tekrarlamak için:

```bash
grep "PcBridge Desktop" ~/.local/state/pcbridge/audit.log
```

### Etkilenen yollar — üçü de aynı fonksiyona gidiyor

`window_focus`'u tek başına düzeltmek **yetmez**. `apps.focus()`'un üç çağıranı
var; yukarıdaki ölçümü fiilen üreten yol `window_focus` değil, `computer_batch`.

| Yer | Ne |
|---|---|
| `tools.py:1677` | `window_focus` MCP aracı |
| `tools.py:1908` | `computer_task(app=…)` — aşağıya bakın, **en kötü durum burada** |
| `ops.py:152` | `DeviceOps.focus` — **hem `computer_batch` hem `bin/pcb-do`** |

Buna bağlı iki ayar daha var; hızlı yol gelirse ikisi de yanlış kalır:

| Yer | Ne |
|---|---|
| `batch.py:63` | `"focus": 7000.0` — sabit bütçe, "olculdu 6.5 sn" yorumuyla; boşuna 7 saniye bekler |
| `ops.py:48` | `_needs()`: listede `focus` varsa **klavye cihazı açılıyor** (arama yolu tuş istediği için); gereksiz kalır |

**`computer_task(app=…)` bu bedeli her seferinde ödüyor.** Kod
(`tools.py:1904-1910`) önce `appslib.launch()` çağırıyor, 1,5 saniye bekliyor,
**sonra** `appslib.focus()` çağırıyor — yani uygulamayı kendi açtığını **bilerek**
arama yoluna giriyor. Bu, "açıksa etkinleştir" dalının tereddütsüz kazandığı
durum: pencerenin açık olduğu zaten belli.

**Sunucu bunu zaten biliyor.** `audit.log`, 2026-08-23:

```
window_focus_error · target: gnome-shell
'gnome-shell' one alinamadi; odakta 'gnome-terminal-server | eymistaken@ZorinOS: ~' var.
GNOME aramasi baska bir sonuc secmis olabilir.
```

Hata metni olasılığı doğru tarif ediyor; eksik olan, o olasılığa hiç
düşmeyen bir yol.

**İstenen davranış.** Pencere zaten açıksa arama yoluna hiç girilmesin,
doğrudan etkinleştirilsin. Arama yalnızca uygulama **kapalıyken** (yani
gerçekten başlatmak gerektiğinde) kullanılsın. Bu hâliyle araç üç şeyi
birden yapıyor: bul, başlat, öne al — ayrılması gerekiyor.

### Bozulmaması gereken (faz 1 ve faz 3 buna dayanıyor)

"Bul + başlat + öne al"ın tek araçta olması kaza değil, **bilinçli bir karar** —
2026-08-21:

- `a0c7d0a` (**faz 1**) `window_focus`'un docstring'ini tam da "kapalıysa açar
  da" desin diye yeniden yazdı; ajan bunu bilmediği için `shell_run`'a
  düşüyordu.
- `4eda165` (**faz 3**) kabuktan GUI başlatmaya kapı koydu ve ret gerekçesi
  ajanı doğrudan **`window_focus`'a yönlendiriyor** (`tools.py:585`).

Ayırma yapılırken bu ikisi ayakta kalmalı:

- **Ajana verilen kapı tek kalsın.** İçeride iki yol olsun (etkinleştir /
  başlat), dışarıda tek araç görünsün — yoksa faz 3'ün ret gerekçesi yanlış
  araca işaret eder.
- **Kapalı uygulamayı açma yeteneği kaybolmasın.** Kaybolursa faz 3 kapısı
  ajanı yapamayacağı bir işe yönlendiriyor olur.

**Olası yol (denenmedi, ölçülmedi).** Bu depoda zaten bir GNOME kabuk
eklentisi var (`gnome-extension/`). Kabuğun içinden pencere etkinleştirmek
tek satır (`Meta.Window.activate`); eklentiye bir D-Bus yöntemi eklenip
`window_focus`'un önce onu denemesi, yoksa aramaya düşmesi düşünülebilir.
GNOME 46 + Wayland'de `Shell.Eval` güvenli kip dışında kapalı olduğu için
eklenti yolu muhtemelen tek temiz seçenek. **Bunların hiçbiri denenmedi** —
burada ölçülen yalnızca sorunun kendisi.

Bu yol seçilirse iki bedeli var, ikisi de baştan yazılsın:

- Aşağıdaki **"Kapsam dışı"nın "eklenti yalnızca görsel katman" kuralını
  deliyor.** Kural ya bilerek gevşetilir ya da başka bir yol aranır.
  **Karar verilmedi** — burada yalnızca seçenek olarak duruyor.
- **Eklenti isteğe bağlı.** Kurulu değilse hızlı yol da yok; o yüzden GNOME
  araması yedek olarak **kalmak zorunda**. Silinmiyor, ikinci sıraya düşüyor.

### Devam edilecekse buradan başlanmalı

1. **Tespit yarısı zaten çözülü.** "Pencere açık mı" sorusunu `window_list`
   (→ `tree.windows()`, AT-SPI) bugün cevaplıyor. Eksik olan yalnızca
   **etkinleştirme**; mesai oraya harcansın.
2. **Bir etkinleştirme yolu seç ve ÖLÇ.** İlk aday eklentinin D-Bus yöntemi.
   Ölçüm yine `audit.log`'daki `ms` alanı: bugünkü ~6,6 saniyeye karşı ne
   çıkıyor?
3. Tutarsa dal `apps.focus()` içine yazılsın: **açıksa etkinleştir, yoksa
   aramaya düş.** Üç çağıran da aynı fonksiyondan geçtiği için iş tek yerde
   biter; `window_focus`, `computer_task`, `computer_batch` ve `pcb-do`
   kendiliğinden kazanır.
4. `batch.py:63`'teki 7000 ms bütçesi ve `ops._needs()`'in klavye açması
   gözden geçirilsin (yukarıdaki tablo).
5. Sözleşme metinleri **birlikte** güncellensin: `tools.py` docstring'i,
   `KULLANIM.md` ("`window_focus` … kapalıysa açar da"), `apps.py` modül
   docstring'i ("Calisan tek yol: GNOME'un kendi aramasi") ve `CLAUDE.md`'nin
   "Çalışan tek yol GNOME araması … ~6,5 saniye" ölçülmüş-gerçek satırı.

**Kabul ölçütü.**

- Açık pencere için `focus`: **6,6 s → 1 saniyenin altı**, en az beş çağrının
  `ms` ortalamasıyla.
- Kapalı uygulamayı açma **hâlâ çalışıyor** (soğuk başlatmayla denensin).
- Eklenti kurulu **değilken** davranış bugünküyle aynı — yavaş, ama hatasız.
- Ölçüm `computer_batch` içindeki `focus` eylemiyle de tekrarlansın: aynı
  fonksiyondan geçtikleri için ikisi birden düzelmeli.

**Yan etki.** Bu araç masaüstü izni açıkken kullanıcının tarayıcısında
istenmeyen sekme açabiliyor ve pencere düzenini bozabiliyor. Kullanıcı
başındayken rahatsız edici.

---

## Biten: faz 1–3 (2026-08-21)

Üçü de bitti ve hiçbirinin burada kaydı yoktu. Ayrıntı commit mesajlarında.

- **Faz 1 — sözleşme** (`a0c7d0a`): `window_focus` ve `ui_click` docstring'leri
  aracın gerçekte ne yaptığını söyler hâle geldi (kapalı uygulamayı da açıyor;
  `ui_click` imleci bilerek oynatmıyor). Kod davranışı değişmedi.
- **Faz 2 — kayan kira** (`9cde9a9`): masaüstü izni sabit son tarihe değil
  **son eyleme** bağlandı (`unlock_idle_seconds = 90`, `hard_until` sert
  tavan). Ölçüldü: izin son eylemden 90,6 sn sonra düştü, ekran yayını 91,6 sn
  sonra kapandı.
- **Faz 3 — kabuktan GUI başlatma kapısı** (`4eda165`): `shell_run` ve
  `shell_run_background`, engel listesindeki bir GUI uygulamasını başlatan
  komutu reddedip `window_focus`'u öneriyor. **Liste varsayılan olarak boş**,
  yani öntanımlı davranışta hiçbir şey engellenmiyor.

---

## Biten: GNOME 46 eklentisi — ajan görünür olsun

`gnome-extension/pcbridge-gorunur@eymistaken.local`

Masaüstü izni açıkken her monitörün kenarlarında yumuşak beyaz bir çerçeve
beliriyor, izin kapanınca yumuşakça kayboluyor. Tamamen görsel; eklenti
hiçbir şeye tıklamıyor, hiçbir şey yazmıyor, pcbridge'in davranışını
değiştirmiyor — yalnızca `desktop_unlock.json`'ı okuyor.

Kurulum ve geliştirme döngüsü: [gnome-extension/README.md](gnome-extension/README.md).

### Ölçülenler (tahmin değil)

| Ne | Sonuç |
|---|---|
| Tıklamayı engelliyor mu | **Hayır.** Bant içindeki Chrome sekmesine tıklandı, sekme değişti (gerçek oturum). Ayrıca `get_actor_at_pos(REACTIVE)` 8/8 noktada bizim aktörümüzü döndürmedi |
| Boşta CPU maliyeti | Ölçüm gürültüsünün altında: kapalı %0,55 · açık %0,45–0,50 (`/proc`'tan CPU zamanı farkı) |
| Bellek | +0,08 MB (370,0 → 370,1 MB RSS) |
| Kabuğun ana döngüsü | 5350 tıkta 2 gecikme, en kötü 0 ms |
| İki monitörde | Evet — GNOME'un monitör sırası pcbridge'inkiyle aynı DEĞİL (GNOME #0 = birincil), `frame.js` indeks değil geometri kullandığı için etkilenmiyor |

---

## Yarım kalan: değişen fare imleci

**Durum: çalışıyordu, ama gerçek kullanımda bozdu ve geri alındı.**
Kod git geçmişinde: `2cac1b3` (ilk hâli) ve `3b15559` (son tasarım).

### Zor kısım çözüldü — bu bilgi saklansın

YAPILACAKLAR'ın eski hâli "imleç işi tek bir soruya bağlı: gerçek imleci
gizleyebiliyor muyuz?" diyordu. **Cevap: evet.**

`Meta.CursorTracker.get_for_display(global.display).set_pointer_visible(false)`
gerçek oturumda, gerçek donanımda imleci gizliyor ve **gizli kalıyor** —
çağrıdan 6 saniye sonra hâlâ `false`, kompozitörden tek bir geri açma gelmedi.
Görsel kanıt: imleç durağan bir monitöre konup gizli/görünür kareleri
karşılaştırıldı; fark tam olarak imlecin bulunduğu noktada, **13×21 px**,
ekranda başka hiçbir piksel değişmedi.

Yani tema değiştirme yedeğine (statik dosyalar, yön dönmesi yok) düşmeye
**gerek yok**. Kendi imlecimizi çizip yöne döndürmek yapıldı ve çalıştı.

### Neden geri alındı

Gerçek makinede, **fiziksel fareyle**: tıklamalar basmıyor ve fare donuyor.
Ajanın sentetik faresiyle hiç görülmedi.

Üç hipotez test edildi, üçü de **yanlış** çıktı:

1. *"`addTopChrome` ile izlenen aktör her harekette girdi bölgesini yeniden
   hesaplatıyor, kabuk tıkanıyor"* → kabuğun içine konan ana döngü gözcüsü
   yoğun harekette 1600 tıkta **0 gecikme** gösterdi.
2. *"Düğme basılıyken imleç takibi duruyor"* → tut + 3 hareket + kare: imleç
   son konumda (891 parlak piksel), basma noktasında 0. **Takip ediyor.**
3. *"Tıklamalar yutuluyor"* → `BUTTON_PRESS=2 · BUTTON_RELEASE=2` görüldü ve
   gerçek oturumda Chrome sekmesi tıklamayla değişti.

### Devam edilecekse buradan başlanmalı

Bütün testlerin ortak kusuru: hepsi **sentetik** fare ile yapıldı. Fiziksel
fareyle ölçülmemiş tek fark **olay hızı**. Kod aktörü *her* fare olayında
yeniden konumlandırıyordu; 1000 Hz'lik bir fare saniyede 1000 yeniden çizim
demek, sentetik testte ise ~50 ölçüldü.

Yapılacak ilk şey — sırayla:

1. **Farenin gerçek olay hızını ölç** (`/dev/input/eventN`'den saniyedeki olay
   sayısı). 1000 Hz çıkarsa hipotez güçlenir, 125 Hz çıkarsa çürür ve başka
   yere bakmak gerekir.
2. Hipotez tutarsa **konumu kare saatinde bir kez uygula** — her olayda değil.
   Yaklaşık 15 satır; en son konumu sakla, kare başına bir kez `set_position`.
3. Bir de `Main.layoutManager.addTopChrome` yerine doğrudan `Main.uiGroup`
   denenmeli: saniyede yüz kez yer değiştiren bir aktörü LayoutManager'a
   izletmek her hâlükârda yanlış (GNOME'un kendi büyüteci de imlecini
   `uiGroup`'a koyuyor). Ölçüm bunu suçlamadı, ama tasarım olarak doğrusu bu.

### Tasarım kararları (tekrar sorulmasın diye)

- Şekil: uç + geriye süpürülmüş iki kanat + arka çentik (kâğıt uçak / gönder
  oku). Dört aday arasından kullanıcı seçti.
- Renk: **içi koyu gri→siyah gradyan, etrafı ince beyaz şerit** — kullanıcının
  kendi imleci de böyle. Düz beyaz gövde beyaz zeminde kayboluyor.
- Parıltı: **şeklin çevresini** sarıyor, ucun etrafını değil. Cairo'da
  bulanıklık yok; giderek genişleyen eş saydamlıkta halkalarla yapılıyor ve
  katman başına saydamlık **birikime göre** hesaplanmalı (sabit alfa verilince
  şeklin dibinde beyaz bir yığın oluşuyor). Seçilen değerler: yayılım 12 px,
  tepe saydamlık 0,26.
- Basma hissi (tıklamada küçülüp büyüme) istendi, sonra iptal edildi. Ama
  **yapılabilir olduğu ölçüldü**: `global.stage` `captured-event` düğme
  olaylarını görüyor (`BUTTON_PRESS=2 · BUTTON_RELEASE=2`). Kural: asla
  `Clutter.EVENT_STOP` dönme, olayı tüketmek masaüstünü kilitler.

---

## Kapsam dışı — GNOME kabuk eklentisi

Bu liste **yalnızca `gnome-extension/` içindir**; dosyanın başındaki
`window_focus` işi pcbridge tarafında ve bu listeyle sınırlanmıyor.

- Eklentinin ayar arayüzü
- extensions.gnome.org'a yayımlama
- GNOME 46 dışındaki sürümler
- pcbridge'in kendi davranışını değiştirmek — eklenti **yalnızca görsel katman**
