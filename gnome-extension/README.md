# pcbridge — Ajan Görünür (GNOME 46 kabuk eklentisi)

> Kurallar ve mimari: **[CLAUDE.md](../CLAUDE.md)** · Sıradaki iş: **[WALKTHROUGH.md](../WALKTHROUGH.md)**

pcbridge bir ajana klavye, fare ve ekran erişimi verebiliyor. Bunun tek görünür
işareti bugüne kadar GNOME'un üst çubuktaki küçük turuncu paylaşım simgesiydi.
Bu eklenti aynı durumu **göz kaçırmayacak** biçimde gösteriyor.

- **Ekran kenarlarında yumuşak beyaz çerçeve** — masaüstü izni açıkken belirir,
  kapanınca yumuşakça kaybolur, iki monitörde de görünür. Bant çok yavaş bir
  nefes alıyor: 11 saniyede bir, en fazla çizildiği kalınlıkta kalarak %12
  inceliyor ve geri dönüyor.
- **Ajanın imleci** (VARSAYILAN KAPALI, aşağıda) — izin açıkken gerçek imleç
  gizlenip yerine yöne dönen bir ok çiziliyor.

Görsel katmana ek olarak iki dar D-Bus yöntemi sunar.
`ActivateWindow(hedef) -> bool` yalnızca zaten açık olan tek ve belirsiz
olmayan eşleşmeyi öne alır. `FocusedWindow() -> (bool, wm_class, app_id,
başlık)` klavye odağındaki pencerenin adını söyler (Adım 8.1): AT-SPI'ın
göremediği bir pencere öndeyken — oyun, birçok Java/Electron penceresi —
pcbridge'in toplu eylemleri odağı buradan okuyabiliyor. Pencere listelemez,
taşımaz, kapatmaz veya boyutlandırmaz. Her çağrıda
`~/.local/state/pcbridge/desktop_unlock.json` grant'ini yeniden okur; izin
kapalıysa hiçbir şey yapmadan `false` döner. Eklenti grant dosyasını yazmaz.

## Kurulum

```bash
./gnome-extension/install.sh
```

Symlink kurar (depoda düzenlediğiniz dosya doğrudan çalışan eklentidir) ve
etkinleştirir. Sonra **çıkış yapıp yeniden girin** — GNOME 45+ eklenti kodunu
önbelleğe aldığı için Wayland'de kabuğu yeniden başlatmanın başka yolu yok.

```bash
./gnome-extension/install.sh --durum      # kurulu mu, etkin mi
./gnome-extension/install.sh --kaldir     # etkinliği kaldır + symlink'i sil
```

## Acil geri alma

**Önce bunu çalıştırın — anında etki eder:**

```bash
gnome-extensions disable pcbridge-gorunur@eymistaken.local
```

Sonra kalıcılaştırın:

```bash
./gnome-extension/install.sh --kaldir
```

> **`rm` tek başına yetmez.** Diskteki dosyayı silmek *çalışan* eklentiyi
> durdurmuyor — kabuk onu zaten belleğe almış oluyor, GNOME 45+ ESM modüllerini
> önbellekte tutuyor. Etkisi ancak kabuk yeniden başlayınca görülüyor. Bu bir
> kez yaşandı: kullanıcıya yalnızca `rm` söylendi, hiçbir şey değişmedi ve
> makineyi yeniden başlatmak zorunda kaldı.

Kabuk tamamen kilitliyse **Ctrl+Alt+F3** ile bir TTY'ye geçip yukarıdaki
`gnome-extensions disable` komutunu oradan çalıştırın.

## Geliştirme

GNOME 45+ ESM modüllerini önbelleğe alıyor: `gnome-extensions disable/enable`
JS'i **yeniden okumaz**. Gerçek oturumda her kod değişikliği çıkış/giriş demek.
Bu yüzden geliştirme iç içe (nested) bir kabukta yapılıyor:

```bash
./gnome-extension/nested.sh          # eskisini öldür, yenisini başlat, logu göster
./gnome-extension/nested.sh --log    # logu izle
./gnome-extension/nested.sh --oldur  # kapat
```

Nested kabuk sizin oturumunuza dokunmaz; bozuk bir eklenti yalnızca o pencereyi
düşürür. **Ama nested her şeyi ölçemez:** monitörler sanal ve kompozitleme iki
kat (nested bir pencereye çiziyor, gerçek kabuk onu bir kez daha
kompozitliyor) — yani buradan çıkan maliyet sayıları gerçek oturumu abartıyor
olabilir.

Nested kabuk **sahte** bir durum dosyası okur (`PCBRIDGE_GORUNUR_STATE`).
Gerçek `desktop_unlock.json`'a `{"until": …}` yazmak pcbridge'e **fiilen
masaüstü izni vermek** olurdu — `SafetyGate` aynı dosyayı okuyor. Efekti
denemek için:

```bash
echo "{\"until\": $(( $(date +%s) + 120 ))}" > /tmp/pcbridge-gorunur-test-state.json
echo '{"until": 0}' > /tmp/pcbridge-gorunur-test-state.json
```

Kabuk gerekmeyen testler doğrudan `gjs` ile koşuyor (`-m` şart, dosya bir ESM
modülü):

```bash
gjs -m gnome-extension/tests/test_state.js
gjs -m gnome-extension/tests/test_window_control.js
```

## Dosyalar

| Dosya | Ne |
|---|---|
| `pcbridge-gorunur@eymistaken.local/extension.js` | giriş noktası, durum makinesi |
| `pcbridge-gorunur@eymistaken.local/state.js` | `desktop_unlock.json` izleyici |
| `pcbridge-gorunur@eymistaken.local/windowcontrol.js` | iki yöntemli dar D-Bus pencere yüzü (etkinleştirme, odaktaki pencere) |
| `pcbridge-gorunur@eymistaken.local/frame.js` | kenar çerçevesi |
| `pcbridge-gorunur@eymistaken.local/cursor.js` | ajanın imleci (varsayılan kapalı) |
| `pcbridge-gorunur@eymistaken.local/frameclock.js` | işi kare başına bire indiren yardımcı |
| `pcbridge-gorunur@eymistaken.local/selftest.js` | kabuğun içinden ölçüm (aşağıda) |
| `install.sh` / `nested.sh` | kurulum / geliştirme döngüsü |
| `tests/test_state.js` | durum izleyici testi (kabuk gerekmez) |
| `tests/test_window_control.js` | eşleşme, grant ve yöntem sözleşmesi (kabuk gerekmez) |
| `tests/test_cursor.js` | kare saati mantığı (kabuk gerekmez) |

### Ajanın imleci — varsayılan kapalı

İzin açıkken gerçek imleç gizlenir ve yerine hareket yönüne dönen bir ok
çizilir. **Kapalı geliyor**, çünkü bu katman bir kez çıkarıldı: gerçek
makinede fiziksel fareyle tıklamalar basmıyor ve fare donuyordu (2026-08-04).
Sebep bulunamadı; bütün denemeler sentetik fareyle yapılmıştı ve tek
ölçülmemiş fark olay hızıydı — fiziksel fare ~1000 Hz rapor ediyor.

Geri gelirken iki şey değişti: konum artık her olayda değil **kare başına bir
kez** uygulanıyor ve aktör `addTopChrome` yerine `Main.uiGroup`ta duruyor.
Nested kabukta ölçüldü (2026-09-20, aynı fare fırtınası): eski kod 1992 olayın
1992'sini çiziyordu, yenisi 263'ünü (58 çizim/sn, yani kare hızı).
**Gerçek oturumda fiziksel fareyle DOĞRULANMADI.**

```bash
touch ~/.local/state/pcbridge/gorunur-imlec     # aç
rm    ~/.local/state/pcbridge/gorunur-imlec     # kapat
```

İşaret dosyası her izin açılışında yeniden okunuyor, yani açıp kapatmak için
kabuğu yeniden başlatmak gerekmiyor: bir sonraki `desktop_unlock` yeni durumu
alır. Fare yine tuhaflaşırsa dosyayı silin, izni kapatıp açın; hiçbir şey
kalmaz. Eklentinin tamamını kapatmak için `gnome-extensions disable
pcbridge-gorunur@eymistaken.local`.

### Pencere etkinleştirme yüzü

Oturum veriyolundaki ad, nesne ve arayüz:

```text
io.github.eymistaken.Pcbridge.WindowFocus
/io/github/eymistaken/Pcbridge/WindowFocus
io.github.eymistaken.Pcbridge.WindowFocus.ActivateWindow(s) -> b
io.github.eymistaken.Pcbridge.WindowFocus.FocusedWindow() -> (b, s, s, s)
```

`ActivateWindow`'un `true`'su, `Meta.Window.activate()` sonrasında GNOME
kabuğunun odak penceresinin aynı pencere olduğunu doğruladığı anlamına gelir.
Hedef yoksa, en iyi eşleşme belirsizse, grant kapalıysa veya etkinleştirme
doğrulanmazsa `false` döner; pcbridge bu durumda mevcut GNOME arama yedeğine
düşer.

`FocusedWindow` `global.display.focus_window`'u okur ve `[bulundu, wm_class,
app_id, başlık]` döner (her alan en fazla 200 karakter). Grant kapalıysa ya da
hiçbir pencere odakta değilse (overview, boş masaüstü) `bulundu = false`.
pcbridge onu yalnızca AT-SPI odağı okuyamadığında sorar; ikisi de okuyamazsa
tıklama içeren toplu eylem eskisi gibi hiç başlamaz. Nested kabukta ölçüldü
(2026-09-22): izin kapalıyken, izin açık ama pencere yokken ve izin kapandıktan
sonra pencere hâlâ odaktayken `false`; zenity odaktayken `[true, "zenity", "",
"Pcbridge Nested A"]`; `busctl` dahil çağrı başına 4,8–11 ms.

```bash
busctl --user --json=short call io.github.eymistaken.Pcbridge.WindowFocus \
  /io/github/eymistaken/Pcbridge/WindowFocus \
  io.github.eymistaken.Pcbridge.WindowFocus FocusedWindow
```

### Kabuğun içinden ölçüm

Eklentinin iddiaları dışarıdan doğrulanamıyor — çerçevenin tıklamayı
engellemediği, ana döngüyü tıkamadığı, nefesin doğru aralıkta kaldığı.
`Shell.Eval` GNOME 41+ ile kapalı olduğu için kabuğa dışarıdan kod sokmak da
mümkün değil. Ölçümü yapabilecek tek yer kabuğun içinde zaten çalışan
eklentinin kendisi:

```bash
PCBRIDGE_GORUNUR_SELFTEST=1 ./gnome-extension/nested.sh
# izni açın, sonra:
./gnome-extension/nested.sh --log | grep SELFTEST
```

Kapalıyken maliyeti tek bir `getenv`.

Fiziksel fareyi taklit eden ölçüm ayrıca isteniyor (imleci gerçekten
oynatıyor): `PCBRIDGE_GORUNUR_BURST=1` ile sanal bir işaretçi 2000 hareket
gönderiyor ve kaçının ekrana yansıdığını, ana döngünün ne kadar geciktiğini
yazıyor. İmleç katmanını da açmak için `PCBRIDGE_GORUNUR_CURSOR=1`.

## Maliyet

Statik çerçeve ölçüm gürültüsünün altında (kapalı %0,55 · açık %0,45–0,50 CPU,
+0,08 MB RSS). **Nefes animasyonu bunu değiştiriyor:** nested kabukta %19 CPU
ölçüldü. Sebep seçilen özellik değil — aynı animasyon saydamlıkla denendiğinde
%23,7 çıktı, yani maliyet büyük saydam şeritlerin 60 fps yeniden
harmanlanmasından geliyor.

Nested bu sayıyı abartıyor olabilir: nested bir pencereye çiziyor ve gerçek
kabuk onu bir kez daha kompozitliyor. **Gerçek oturumda ölçülmedi.** Rahatsız
edici bulursanız `frame.js` içindeki `_startBreathing` çağrılarını kaldırmak
yeterli; çerçeve yine çalışır ve tekrar bedava olur.

## Ölçülmüş gerçekler

- GNOME Shell 46.0, Wayland, Zorin OS 18.1.
- `Clutter.Canvas` mutter çatalında **yok**; çizim `St.DrawingArea` + Cairo ile.
- `Clutter.PropertyTransition`'a aktöre eklenmeden `set_from`/`set_to` verilirse
  geçiş özelliğin tipini bilmiyor, aralık boş kalıyor ve özellik **0'a** düşüyor
  (ölçüldü: 15 ölçek örneğinin hepsi 0.000). `actor.ease()` zincirlemesi
  doğrulanmış yol.
- İmleci değiştirme denendi, geri alındı ve 2026-09-20'de kare saati
  düzeltmesiyle **kapalı olarak** geri geldi — gerekçesi, ölçülmüş bulgular ve
  devam yolu [WALKTHROUGH.md](../WALKTHROUGH.md)'de.
- `Meta.CursorTracker.set_pointer_visible(false)` gerçek oturumda imleci
  gizliyor ve gizli kalıyor (görsel kanıt: 13×21 px fark, başka hiçbir piksel
  değişmedi).
- Fiziksel fare hareket halinde **~1000 Hz** rapor ediyor (medyan aralık
  1,00 ms, ölçüldü 2026-09-13). Ekranda görünebilecek en fazla değişiklik kare
  sayısı kadar; aradaki her şey kabuğa boşuna iş çıkarıyor.
