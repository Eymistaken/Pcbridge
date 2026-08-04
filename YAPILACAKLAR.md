# YAPILACAKLAR.md

## Sıradaki iş

**Şu an sırada bekleyen bir görev yok.** Aşağıdaki "yarım kalan" başlığı
devam etmek isteyene hazır bir zemin bırakıyor.

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

## Kapsam dışı

- Eklentinin ayar arayüzü
- extensions.gnome.org'a yayımlama
- GNOME 46 dışındaki sürümler
- pcbridge'in kendi davranışını değiştirmek — eklenti **yalnızca görsel katman**
