# pcbridge — Ajan Görünür (GNOME 46 kabuk eklentisi)

> Kurallar ve mimari: **[CLAUDE.md](../CLAUDE.md)** · Sıradaki iş: **[WALKTHROUGH.md](../WALKTHROUGH.md)**

pcbridge bir ajana klavye, fare ve ekran erişimi verebiliyor. Bunun tek görünür
işareti bugüne kadar GNOME'un üst çubuktaki küçük turuncu paylaşım simgesiydi.
Bu eklenti aynı durumu **göz kaçırmayacak** biçimde gösteriyor.

- **Ekran kenarlarında yumuşak beyaz çerçeve** — masaüstü izni açıkken belirir,
  kapanınca yumuşakça kaybolur, iki monitörde de görünür. Bant çok yavaş bir
  nefes alıyor: 11 saniyede bir, en fazla çizildiği kalınlıkta kalarak %12
  inceliyor ve geri dönüyor.

**Tamamen görsel.** Eklenti hiçbir şeye tıklamaz, hiçbir şey yazmaz, pcbridge'in
davranışını değiştirmez. Yaptığı tek şey `~/.local/state/pcbridge/desktop_unlock.json`
dosyasını **okumak**.

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
```

## Dosyalar

| Dosya | Ne |
|---|---|
| `pcbridge-gorunur@eymistaken.local/extension.js` | giriş noktası, durum makinesi |
| `pcbridge-gorunur@eymistaken.local/state.js` | `desktop_unlock.json` izleyici |
| `pcbridge-gorunur@eymistaken.local/frame.js` | kenar çerçevesi |
| `pcbridge-gorunur@eymistaken.local/selftest.js` | kabuğun içinden ölçüm (aşağıda) |
| `install.sh` / `nested.sh` | kurulum / geliştirme döngüsü |
| `tests/test_state.js` | durum izleyici testi (kabuk gerekmez) |

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
- İmleci değiştirme denendi ve geri alındı — gerekçesi, ölçülmüş bulgular ve
  devam yolu [WALKTHROUGH.md](../WALKTHROUGH.md)'de.
