---
name: computer-use
description: Bu Linux masaüstünü ekrana bakarak sür. Ekran görüntüsü al, PNG'yi oku, tıkla/yaz, sonucu doğrula. GUI görevleri için — pencere, menü, düğme, form. AT-SPI'ın göremediği uygulamalarda (Electron: Vesktop, VS Code, Discord) tek yol budur.
---

# Bilgisayarı ekrana bakarak sürmek

Senin bir gözün var: PNG okuyabiliyorsun. Bu makinede iki komut seni ekrana
bağlıyor.

## Döngü

```bash
pcb-shot --monitor 2                 # 1. bak
```
```
Read /run/user/1000/pcbridge/shots/...png      # 2. gerçekten gör
```
```bash
pcb-do '[{"a":"click","x":2760,"y":312}]'      # 3. eyleme geç
pcb-shot --monitor 2                            # 4. SONUCU DOĞRULA
```

Dördüncü adım isteğe bağlı değil. Bir eylemin işe yaradığını görmeden bir
sonrakine geçme.

## Bu makinenin gerçekleri

- **İki monitör**, ikisi de 1920×1080, yan yana, tek bir 3840×1080 tuval:

  | monitör | konnektör | x aralığı | not |
  |---|---|---|---|
  | 1 | DP-2 | 0 – 1919 | sol |
  | 2 | DP-1 | 1920 – 3839 | sağ, **birincil** |

- **GNOME üst çubuğu ve `Super` menüsü monitör 2'de** (sağda) beliriyor.
  Bunu bilmezsen `Super`'a basıp sol ekranda menü ararsın ve "çalışmadı"
  sanırsın.
- **Koordinatlar her zaman global.** `pcb-shot` her görüntünün ofsetini
  yazıyor; görüntüdeki piksele o ofseti ekle. Varsayılan ölçek 1:1 olduğu için
  hesap sadece toplama: `global_x = ofset_x + görüntü_x`.
- Klavye düzeni Türkçe (`tr+intl`). `type` eylemi bunu kendisi hallediyor
  (pano üzerinden), sen düşünme.
- `Super`'a bastıktan **sonra** pano bloklanıyor; genel bakış açıkken metin
  yazacaksan `{"a":"type","text":"...","raw":true}` kullan.

## Eylemleri GRUPLA

Her `pcb-do` çağrısı ayrı bir süreç ve sanal klavye/fare her seferinde
sıfırdan kuruluyor. Ölçüldü:

| | süre |
|---|---|
| süreç başına cihaz kurulumu | **1,4 s** |
| gerçek tuş basımı | 0,03 s |

On eylemi tek tek göndermek ~14 saniyeyi çöpe atmak demek. Aynı listede
gönder:

```bash
pcb-do '[{"a":"click","x":2760,"y":900},
         {"a":"wait","ms":300},
         {"a":"type","text":"merhaba"},
         {"a":"key","keys":"Return"}]'
```

Ekran görüntüsü de bedava değil: bir görüntü ~40 bin girdi jetonu. Her
eylemden sonra doğrula, ama aynı ekranı iki kez okuma.

## Eylemler

```
{"a":"key",          "keys":"ctrl+s"}          tuş / kombinasyon (kaç tuş olursa)
{"a":"hold",         "keys":"shift"}           BASILI TUT — release'e kadar
{"a":"release",      "keys":"shift"}           bırak
{"a":"type",         "text":"...", "raw":false} metin yaz
{"a":"wait",         "ms":400}                  bekle (en fazla 30000)
{"a":"move",         "x":.., "y":..}            imleci taşı
{"a":"click",        "x":.., "y":..}            sol tık (x/y yoksa yerinde)
{"a":"double_click", "x":.., "y":..}
{"a":"triple_click", "x":.., "y":..}            satırın tamamını seçer
{"a":"right_click",  "x":.., "y":..}
{"a":"middle_click", "x":.., "y":..}
{"a":"mouse_down",   "button":"left", "x":.., "y":..}   BASILI TUT
{"a":"mouse_up",     "button":"left"}                    bırak
{"a":"drag",         "x":.., "y":.., "to_x":.., "to_y":.., "button":"left"}
{"a":"scroll",       "amount":-3, "horizontal":false}   eksi = aşağı / sola
{"a":"launch",       "app":"Vesktop"}           uygulama başlat
{"a":"focus",        "window":"Metin Düzenleyici"}  pencereyi öne al (~6,5 s)
{"a":"ui_click",     "id":"90e6"}               erişilebilirlik düğümü
{"a":"ui_set_text",  "id":"1b72", "text":"..."} metin kutusunu doğrudan doldur
```

**İmleç ışınlanmıyor**, hedefe ara noktalardan geçerek gidiyor (~5000 px/s,
ekranın bir ucundan diğerine 0,4 sn). Bir `move` anlık dönmez; "takıldı" sanıp
çağrıyı tekrarlarsanız iki hareket üst üste biner.

**`hold` / `mouse_down` sonraki eylemlere taşar.** Ara duraklaması olan bir
sürükleme — kaydırıcı, seçim dikdörtgeni, dosyayı klasöre bırakma — böyle
yapılır: `mouse_down`, `move`, `move`, `mouse_up`. `drag` bunun tek atışlık hâli.

Bıraktığınızdan emin olun. Dizi yarıda kalırsa (hata, bütçe, odak kayması)
basılı kalanlar kendiliğinden bırakılır; düzgün biterse **bırakılmaz** —
"tut, sonraki çağrıda tıkla" meşru bir kullanım. Rapor her iki durumda da ne
olduğunu söyler. Son çare olarak sunucu bir süre sonra (varsayılan 120 sn)
hepsini bırakır, ama o zamana kadar kullanıcı makinesini kullanamaz.

`ui_click` / `ui_set_text` koordinat gerektirmiyor ve **çok daha güvenilir** —
ama yalnızca GTK/GNOME uygulamalarında çalışıyor. Electron uygulamalarında
(Vesktop, VS Code, Discord) erişilebilirlik ağacı **boş**; orada gözünle
çalışmak zorundasın. Zaten bu skill'in var olma sebebi o.

## Başka bir pencereye tıklayacaksan önce söyle

Bir tıklama odağı değiştirdiğinde `pcb-do` **durur** ve kalan tuşları
göndermez. Sebebi aşağıdaki kaza; ama sen bunu bilerek yapıyorsan niyetini
önceden bildir:

```bash
pcb-do --expect-focus "Metin Düzenleyici" \
       '[{"a":"click","x":900,"y":500},
         {"a":"wait","ms":300},
         {"a":"type","text":"merhaba"}]'
```

Odak **beklediğin** pencereye giderse dizi devam eder. **Başka** bir yere
giderse yine durur — koruma kalkmıyor, sadece niyetin ölçüt oluyor.

Pencere adını `pcb-shot` görüntüsündeki başlık çubuğundan okuyabilirsin; bir
parçası yeterli (`"Düzenleyici"` de olur).

Bunu yazmadan tıklarsan kod 2 alırsın ve mesajda hangi pencereye geçildiği
yazar — oradan öğrenip tekrar deneyebilirsin.

## Çıkış kodları

| kod | anlamı | ne yapmalısın |
|---|---|---|
| 0 | hepsi yapıldı | devam |
| 2 | **kısmen** yapıldı | `pcb-shot` ile bak, nerede kaldığını gör |
| 3 | güvenlik kapısı reddetti | dur, kullanıcıya söyle |
| 4 | bozuk JSON | düzelt, `--dry-run` ile doğrula |

`pcb-do --dry-run '<json>'` hiçbir şey çalıştırmadan listeni ayrıştırır.
Emin değilsen önce bunu kullan.

Kod 3 aldıysan tekrar deneme. Kullanıcı izni kapatmış, ekran kilitlenmiş ya
da süre dolmuş olabilir; bunları senin aşman gerekmiyor ve aşamazsın.

## Ekran görüntün bayatlar

Bir görüntüye bakıp koordinat çıkardıktan sonra **hemen** tıkla. Arada kod
yazma, düşünme molası verme, başka iş yapma. Kullanıcı o sırada başka bir
pencereye geçmiş olabilir ve senin koordinatın artık bambaşka bir şeyin
üstündedir.

`pcb-do` en yeni görüntü 60 saniyeden eskiyse koordinatlı eylemi **reddediyor**
(kod 3). Bu bir ağ, kural değil — ağa güvenip beklemek yerine döngüyü sıkı tut:

```
pcb-shot  →  Read  →  pcb-do        ← aralarında başka hiçbir şey yok
```

Bu da bir kazadan geliyor: 3 Ağustos 2026'da 69 saniyelik bir görüntüye göre
tıklandı. Vesktop sanılan yerde başka bir uygulama vardı, tıklama oraya düştü.
Odak koruması **ötmedi**, çünkü odak zaten o uygulamadaydı — değişen bir şey
yoktu. Aşağıdaki koruma bu durumu yakalamıyor; sıkı döngü yakalıyor.

## Kör tıklama yasağı

**Bir noktaya, orada ne olduğunu görmeden tıklama.**

Bu kural bir kazadan geliyor. 2 Ağustos 2026, 21:13: bir ölçüm sırasında
`move(920, 520)` + `click` yapıldı ve oranın metin düzenleyici penceresi
olduğu **varsayıldı, doğrulanmadı**. Tıklama masaüstüne düştü. Odak oraya
kaydı. Ardından temizlik için gönderilen `ctrl+a` + `Delete` masaüstündeki
**23 öğeyi çöpe gönderdi**.

Karşılığı koda girdi: `pcb-do` artık fare tıklamalarından sonra odağın
değişip değişmediğini kontrol ediyor ve kaymışsa **duruyor** (çıkış kodu 2).
Bu ağ seni yakalar, ama ağa güvenerek atlama.

Pratikte: tıklamadan önceki `pcb-shot` çıktısını gerçekten `Read` et. Hedefin
görüntüde nerede olduğunu göster kendine. Sonra tıkla.

## Takılırsan

Tahmin etme. Üç denemede ilerleyemediysen dur ve kullanıcıya ne gördüğünü,
ne denediğini, neyin olmadığını anlat. Yanlış yere tıklamaya devam etmek her
zaman en kötü seçenek.

Aynı şey belirsizlikte de geçerli: "hangi sohbet penceresi", "hangi dosya"
gibi sorularda ekrandan emin olamıyorsan sor.
