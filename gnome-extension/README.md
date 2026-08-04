# pcbridge — Ajan Görünür (GNOME 46 kabuk eklentisi)

pcbridge bir ajana klavye, fare ve ekran erişimi verebiliyor. Bunun tek görünür
işareti bugüne kadar GNOME'un üst çubuktaki küçük turuncu paylaşım simgesiydi.
Bu eklenti aynı durumu **göz kaçırmayacak** biçimde gösteriyor.

- **Ekran kenarlarında yumuşak beyaz çerçeve** — masaüstü izni açıkken belirir,
  kapanınca yumuşakça kaybolur, iki monitörde de görünür.
- **Değişen imleç** — ajan makineyi kullanırken imleç yumuşak köşeli, hafif
  parlayan bir imlece dönüşür; iş bitince eskisine döner.

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

Eklenti kabuğu bozarsa: **Ctrl+Alt+F3** ile bir TTY'ye geçip

```bash
rm ~/.local/share/gnome-shell/extensions/pcbridge-gorunur@eymistaken.local
```

Symlink silindiğinde bir sonraki girişte eklenti hiç yüklenmez ve depodaki
dosyalara dokunulmaz. `install.sh` bu komutu her çalışmasında ekrana basıyor.

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
düşürür. **Ama nested her şeyi ölçemez:** monitörler sanal, ve imleç orada bir
Wayland *istemcisi* olarak çiziliyor — donanım imleç düzlemi yok. İmlecle ilgili
her şey gerçek oturumda ayrıca doğrulanmalı.

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
| `install.sh` / `nested.sh` | kurulum / geliştirme döngüsü |
| `tests/test_state.js` | durum izleyici testi (kabuk gerekmez) |

## Ölçülmüş gerçekler

- GNOME Shell 46.0, Wayland, Zorin OS 18.1.
- `Meta.CursorTracker.set_pointer_visible()` Meta-14 typelib'inde **var**
  (GNOME büyütecinin kullandığı yol).
- `Meta.CursorTracker` sinyalleri: `cursor-changed`, `cursor-updated`,
  `position-invalidated`, `visibility-changed`. **`cursor-moved` yok** — imleç
  konumu `global.get_pointer()` ile alınıyor.
- `Clutter.Canvas` mutter çatalında **yok**; çizim `St.DrawingArea` + Cairo ile.
