# Neler yapabilirsin

Telefondan ya da tarayıcıdan Gemini'ye yazdığın cümleler. Gemini hangi aracı
çağıracağına kendi karar veriyor — komut ezberlemene gerek yok, ama ne
istediğini net söylemek sonucu belirgin şekilde iyileştiriyor.

Cümlelerin başına **"pcbridge ile"** eklemek Gemini'nin doğru aracı seçme
olasılığını artırıyor. Bir süre sonra gerek kalmıyor.

---

## 1. Kodlama ajanlarına iş verdirme

En değerli kısım burası. `agent_run` Claude Code'u ya da Antigravity'yi
bilgisayarında başlatır, arka planda çalıştırır, sonucu telefonuna döner.

> pcbridge ile `~/projeler/site` klasöründe claude'a şunu yaptır: login
> formundaki doğrulama hatasını bul ve düzelt

> claude'a `~/kod/api` içinde testleri çalıştırıp kırılanları onarmasını söyle

> antigravity ile `~/projeler/oyun` klasöründeki README'yi güncelle

> az önce başlattığın işin durumu ne

> o işi iptal et

> bugün çalışan işleri listele

**Konuşmayı sürdürme.** Her iş bir oturum kimliği döner. Devam etmek istersen:

> aynı oturumda devam et ve bir de testlerini yaz

Gemini kimliği kendisi hatırlayıp `resume_session` olarak geçirir. Unutursa
"önceki oturum kimliğiyle devam et" demen yeterli.

**Süre.** Ajanlar dakikalar sürebilir. `agent_run` varsayılan olarak 30 saniye
bekler, bitmezse iş kimliğiyle döner. Sonra "durumu ne" diye sorarsın. Bu
tasarım bilinçli — uzun görevlerde bağlantı zaman aşımına uğramasın diye.

### Model ve akıl yürütme seviyesi seçme

Model demezsen **Sonnet 5 + medium** çalışır: hızlı ve ucuz, günlük işlerin
çoğu için yeterli. Ağır iş için bilinçli olarak yükseltirsin:

> claude'a opus ile `~/kod/api` içindeki yarış durumunu bulmasını söyle

> opus, extra efor ile bu mimariyi baştan tasarla

> haiku ile şu klasörde ne var diye bak

Antigravity tarafı ayrı kotada — Claude kotan biterse buraya geçebilirsin:

> antigravity ile `~/projeler/oyun` klasöründeki README'yi güncelle

> antigravity üzerinden 3.1 pro ile, düşük efor: şu dosyayı özetle

Serbest yazabilirsin: "Gemini 3.6 Flash", "gemini-3.6-flash", "3.6 flash" ve
"flash" aynı yere gider. Efor için Türkçe de olur: "yüksek", "orta", "düşük",
"extra" (= xhigh), "maksimum".

Bilmen gereken üç davranış:

- **Fable kapalı.** Pro planında ek kullanım kredisi yaktığı için `fable` ve
  onun takma adı `best` hiçbir koşulda seçilemez; istersen gerekçesiyle reddedilir.
- **Sessiz düşürme yok.** Bir ajanda olmayan bir efor istersen (Antigravity'de
  `xhigh` yok) en yakın alt seviyeye inilir ve **iş özetinde bunu görürsün**.
- **Ne istediğin değil, ne çalıştığı yazar.** İş özetinin başında
  `ajan: claude · model: opus · effort: high` satırı var; istenen ile gerçekleşen
  model farklıysa en üste uyarı basılır.

Neyin var olduğunu unutursan:

> hangi modelleri kullanabiliyorsun

`list_agents` her ajanın model tablosunu, varsayılanını ve efor listesini döner.

---

## 2. Açık terminale müdahale (tmux)

Bilgisayarında gerçekten açık duran bir terminale telefondan yazarsın.
Başına geçtiğinde `tmux attach -t <isim>` ile aynı oturuma girip devam edersin.

> `cc` adında bir terminal aç ve içinde claude başlat

> `cc` oturumuna "bu dosyayı refactor et" yaz

> `cc` oturumunun ekranını göster

> claude bir onay soruyor galiba, `cc` ekranına bak — uygunsa Enter'a bas

> `cc` oturumunda Escape'e bas

> açık terminalleri listele

> `cc` oturumunu kapat

Son örnekler `tmux_keys` sayesinde çalışıyor: Enter, Escape, Ctrl-C, yön
tuşları, y/n — hepsini gönderebilirsin. Ajanın onay istemine telefondan cevap
vermenin yolu bu.

---

## 3. Komut çalıştırma

> pcbridge ile `~/İndirilenler` klasöründe kaç dosya var

> git durumunu göster, `~/projeler/site` klasöründe

> `sudo apt update` çalıştır  ← **çalışmaz**, aşağıdaki nota bak

> npm install'ı arka planda başlat, `~/kod/api` içinde

Kısa komutlar `shell_run` ile anında döner (en fazla 60 saniye). Uzun sürecekse
`shell_run_background` kullanılır ve iş kimliği döner.

**Not:** `sudo` gerektiren komutlar çalışmaz — parola soracak yer yok. Root
gerektiren bir şey lazımsa ya parolasız sudo kuralı tanımlaman ya da o işi
bilgisayar başındayken yapman gerekir.

---

## 4. Dosyalar

> `~/Belgeler` klasöründe ne var

> `~/projeler/site/config.json` dosyasını oku

> `~/notlar.md` dosyasına şu satırı ekle: yarın toplantı var

> `~/kod` altında "TODO" geçen yerleri bul

> `~/İndirilenler` içinde 500 MB'tan büyük ne var

Son örnek `shell_run` ile `du`/`find` çalıştırarak hallolur — Gemini genelde
doğru aracı seçiyor.

---

## 5. Bilgisayarı fiilen kullandırma (klavye + fare)

**Varsayılan kapalı.** Açmak için bir kez `sudo ./setup_uinput.sh`, sonra
`config.toml`'da `[desktop] enabled = true` ve `systemctl --user restart pcbridge`.
Neyi kabul ettiğini `README.md`'nin güvenlik bölümünde okuyabilirsin.

Açıkken bile her seferinde **süreli izin** vermen gerekiyor:

> pcbridge ile masaüstü kontrolünü 10 dakikalığına aç

> ekranın ortasına tıkla

> `merhaba dünya` yaz

> Enter'a bas

> Ctrl+S'ye bas

> masaüstü kontrolünü kapat

Süre dolunca izin kendiliğinden kapanır; `desktop_lock` ile erken de kapatırsın.

**Koordinatlar tek bir tuvalde.** İki ekranın birlikte 3840×1080 tek bir yüzey.
Sol ekran 0–1919, sağ ekran 1920–3839. "Sağ ekranın ortasına tıkla" dediğinde
Gemini `monitor` parametresini kullanabilir; numaralandırma **soldan sağa**, yani
`1` sol, `2` sağ. GNOME üst çubuğu ve `Super` menüsü **sağ** ekranda beliriyor.

Dört davranışı bilmen işini kolaylaştırır:

- **Türkçe karakterler doğru çıkar.** Metin klavye tuşu taklidiyle değil,
  **pano üzerinden** giriliyor (`wl-copy` + Ctrl+V). `ış ğü ÖÇ @` sorunsuz.
  Yapıştırmadan sonra panonun eski içeriği geri yüklenir.
- **Makine başındaysan reddeder.** Son 60 saniye içinde klavye/fareye
  dokunduysan yazma eylemleri reddedilir — telefondan gelen fareyle seninki
  kavga etmesin diye. "yine de yap" dersen `force` ile geçer.
- **Ekran kilitliyken hiçbir şey yapmaz.** Kilitli ekranın arkasına parola
  yazdırma yolu yok.
- **Her eylem kaydediliyor.** `~/.local/state/pcbridge/audit.log` — ne zaman,
  hangi araç, hangi parametre.

Acil durdurma, makine başındaysan: `systemctl --user stop pcbridge`.

---

## 6. Ekrana bakma

> ekranımda ne var?

> sağ ekranın görüntüsünü al

Karşılığında **tıklanabilir bir bağlantı** gelir; telefondan açınca ekranını
görürsün. Varsayılan olarak her monitör **ayrı bir görüntü** — hangisine
baktığını tahmin etmen gerekmiyor.

Bilmen gereken üç şey:

- **Görüntü Spark'a geçmiyor, sen görüyorsun.** Bağlantı senin için. Sebep
  Gemini'nin kör olması değil — Spark'a giden MCP kanalı araç sonucunda
  yalnızca metin taşıyor. (Gemini'nin gözü var; Antigravity içindeki Gemini
  görüntü okuyabiliyor.) Gemini bu kanaldan yalnızca görüntünün ekranın
  neresine denk geldiğini (ofset ve ölçek) okuyor; "şuraya tıkla" derken onu
  kullanıyor. Ekranda ne olduğunu **metin olarak** öğrenmesinin yolu ise
  `ui_dump` — aşağıdaki bölüm.
- **Bağlantı 5 dakika yaşıyor ve OAuth'tan bağımsız.** Yani bağlantıyı alan
  herkes görüntüyü açabilir — **paylaşma.** Süre dolunca kendiliğinden ölür,
  dosyalar da 24 saat sonra silinir.
- **Ekran görüntüsü de izin istiyor.** `desktop_unlock` vermeden çalışmaz;
  ekranda ne varsa (parolalar, mesajlar) hepsini gösterdiği için klavye/fareyle
  aynı kapıdan geçiyor. Tek farkı: makinenin başında olman ekran görüntüsünü
  engellemiyor, yalnızca yazma eylemlerini engelliyor.

> ekrandaki Kaydet düğmesine tıkla

Tipik akış şu: Gemini önce görüntüyü alır, düğmenin görüntüdeki yerini okur,
formülle gerçek koordinata çevirir, sonra tıklar. Küçültülmüş görüntüden okunan
koordinat birkaç piksel şaşabilir (ölçüldü: ~5 px) — düğme için sorun değil.

`ekranımı tam çözünürlükte göster` dersen küçültme yapılmaz, sapma da kalmaz.

---

## 7. Ekranı Gemini'ye okutma (asıl yol)

Ekran görüntüsü senin için. Gemini'nin ekranda ne olduğunu **öğrenmesinin**
yolu ise başka: uygulamalar arayüzlerini zaten metin olarak yayınlıyor.

> açık pencerede ne var?

Karşılığında düğmelerin, menülerin ve metin kutularının listesi gelir — her
birinin kısa bir kimliğiyle:

```
#993a toggle button "Aç"
#90e6 push button "Yeni sekme"
#1b72 text [editable]
```

Sonra doğrudan onlara iş verilir:

> Kaydet düğmesine bas

> arama kutusuna "fatura" yaz

Bu yol **tahmin içermiyor.** Koordinat hesaplanmıyor, piksel okunmuyor;
uygulamanın kendi beyanı kullanılıyor ve tıklama pencerenin nerede olduğundan
bağımsız çalışıyor. Metin kutusuna yazarken de klavye taklit edilmiyor — Türkçe
karakterler doğrudan gidiyor, hiçbir düzen sorunu yok.

İki sınırı bilmen iyi olur:

- **Bazı uygulamalar arayüzünü yayınlamıyor.** Özellikle bazı Electron
  uygulamaları (Claude masaüstü gibi) yalnızca pencere çerçevesini veriyor.
  Böyle bir durumda araç bunu sana açıkça söyler; o zaman `screen_capture` +
  koordinatla tıklama yoluna dönülür.
- **Listede olmayan şey tıklanamaz.** Oyun, harita, çizim tuvali gibi
  yerlerde yayınlanacak bir "düğme" yok. Orada da ekran görüntüsü yolu geçerli.

Bu araçlar da `desktop_unlock` istiyor: ekrandaki yazıları okumak, ekran
görüntüsü almakla aynı gizlilik sınıfında.

---

## 8. Tek onayda çok adım (`computer_batch`)

> menüden "Farklı Kaydet"i seç

Bunu tek tek yaptırırsan Gemini dört ayrı araç çağırır: menüyü aç, bekle,
öğeyi bul, tıkla. **Her çağrı telefonunda ayrı bir onay kutusu demek** — dört
onay, dört tur gecikme. Bir menü seçimi için kabul edilemez.

`computer_batch` hepsini tek çağrıya sığdırır: bir eylem listesi alır, sırayla
çalıştırır, sonunda ekranın son hâlini gösterir.

```json
[{"a": "ui_click", "id": "89f0"},
 {"a": "wait", "ms": 400},
 {"a": "ui_click", "id": "3c1a"}]
```

Eylemler: `key`, `type`, `wait`, `move`, `click`, `double_click`, `right_click`,
`middle_click`, `drag`, `scroll`, `ui_click`, `ui_set_text`, `launch`, `focus`.

**Üç durumda kendiliğinden durur** ve nerede kaldığını söyler:

- **Bir eylem başarısız olursa.** Kalanlar çalıştırılmaz — yanlış duruma kör
  devam etmek en kötü sonuç.
- **Süre bütçesi dolarsa** (varsayılan 90 sn). Sıradaki eyleme *hiç başlamaz*,
  yarım tıklama olmaz. Yapılmayanları listeler, yeni bir çağrıyla devam
  edebilirsin.
- **Bir tıklama odağı başka pencereye kaydırırsa.** Bu koruma gerçek bir
  kazadan doğdu: geliştirme sırasında bir ölçüm tıklaması masaüstüne düştü,
  ardından gönderilen `ctrl+a` + `Delete` masaüstündeki 23 öğeyi çöpe gönderdi.
  Artık batch o noktada durur ve tuşlar hiç gitmez.

Bu yüzden batch içinde de **koordinatla tıklamak yerine `ui_click` tercih
edilir**: düğümün kendisine gider, odağın nerede olduğu fark etmez.

> not defterini aç ve içine alışveriş listemi yaz

`launch` uygulamayı açar (Türkçe adıyla da bulur: "metin düzenleyici"),
`focus` açık bir pencereyi öne alır.

**`window_list`** açık pencereleri gösterir, odaktaki `▸` ile işaretli.
**`window_focus`** bir pencereyi öne getirir — ama birkaç saniye sürer, çünkü
masaüstünün kendi aramasından geçmek zorunda (AT-SPI'nin pencere öne alma
çağrıları bu sistemde çalışmıyor, ölçüldü). Sadece bir düğmeye basacaksan
`ui_click` daha hızlı: pencerenin önde olmasını gerektirmiyor.

---

## 9. Makine durumu ve bildirim

> bilgisayarımın durumunu göster

Çalışma süresi, yük, bellek, iki diskin doluluğu, RTX'in sıcaklık ve kullanımı,
çalışan işler ve açık terminaller tek seferde gelir.

> bilgisayarıma "akşam yedekleme yapmayı unutma" diye bildirim gönder

`notify` masaüstünde bildirim çıkarır. Evde olmadığında kendine not bırakmak
ya da bir işin bittiğini fark etmek için.

---

## Araçların tamamı

| Araç | Ne yapar |
|---|---|
| `list_agents` | Tanımlı ajanlar, PATH durumu, model tablosu ve efor listeleri |
| `agent_run` | Ajana prompt gönderir, iş kimliği döner (`model` / `effort` seçilebilir) |
| `job_status` | İşin durumu, adımları, sonucu, oturum kimliği |
| `job_output` | İşin ham terminal çıktısı (ajan çöktüyse buraya bak) |
| `job_list` | Son işler, yeniden başlatmadan sonra da görünür |
| `job_cancel` | Çalışan işi durdurur |
| `tmux_list` | Açık terminal oturumları |
| `tmux_start` | Yeni kalıcı terminal açar, istersen içinde bir CLI başlatır |
| `tmux_send` | Terminale metin yazar ve Enter'a basar |
| `tmux_keys` | Ham tuş gönderir (Enter, Escape, C-c, yön tuşları, y/n) |
| `tmux_capture` | Terminal ekranını okur |
| `tmux_kill` | Oturumu kapatır |
| `shell_run` | Kısa komut çalıştırır, çıktıyı döner |
| `shell_run_background` | Uzun komutu arka plana atar |
| `fs_list` | Dizin içeriği, boyut ve tarihle |
| `fs_read` | Metin dosyası okur |
| `fs_write` | Dosya yazar veya sonuna ekler |
| `fs_search` | Dosya içinde arama (ripgrep varsa onu kullanır) |
| `system_status` | Makine durumu, diskler, GPU, işler, terminaller |
| `notify` | Masaüstünde bildirim çıkarır |
| `desktop_unlock` | Klavye/fare kontrolüne süreli izin verir (varsayılan 15 dk) |
| `desktop_lock` | İzni erken kapatır, sanal cihazları yok eder |
| `mouse` | Fareyi hareket ettirir, tıklar, sürükler, kaydırır |
| `keyboard` | Metin yazar (pano yoluyla) veya tuş kombinasyonu gönderir |
| `screen_info` | Monitör tablosu, koordinat uzayı, odaktaki pencere |
| `screen_capture` | Ekran görüntüsü alır, 5 dakikalık bağlantı döner |
| `ui_dump` | Ekrandaki düğme/menü/kutuları metin olarak listeler |
| `ui_click` | Listedeki bir öğeye tıklar (koordinat kullanmadan) |
| `ui_set_text` | Metin kutusunu doğrudan doldurur (klavye taklidi yok) |
| `computer_batch` | Bir eylem listesini tek onayda sırayla çalıştırır |
| `window_list` | Açık pencereler, odaktaki işaretli |
| `window_focus` | Bir pencereyi öne getirir |

---

## Hangi araç neyin iznini istiyor

Burası bir yanlış anlamayı önlemek için: **`[desktop] enabled = false`
bilgisayarını kapatmaz.** Yalnızca pcbridge'in kendi sanal klavye/faresini ve
ekran okumasını kapatır. Komut çalıştırma ve dosya erişimi ayrı bir yoldan
gider ve o yol açıktır — projenin amacı zaten bu.

| Araç grubu | `[desktop] enabled` | `desktop_unlock` | "kullanıcı makinede" koruması |
|---|:---:|:---:|:---:|
| `mouse`, `keyboard`, `ui_click`, `ui_set_text`, `computer_batch`, `window_focus` | gerekli | gerekli | var (`force` ile geçilir) |
| `screen_capture`, `ui_dump`, `window_list` | gerekli | gerekli | yok (okuma) |
| `screen_info` | — | — | — |
| `shell_run`, `shell_run_background`, `agent_run`, `fs_*`, `tmux_*`, `job_*`, `notify` | — | — | — |

Son satır önemli: **`shell_run` masaüstü kapısından geçmez.** Masaüstü kontrolü
kapalıyken bile Gemini komut çalıştırabilir, uygulama açabilir, dosya
okuyabilir. Nitekim ekranı da okuyabiliyor — `agent_run` ile makinedeki bir
ajanı çalıştırıp ona ekran görüntüsü aldırarak (ölçüldü, 2026-08-02).

Bunun bir sonucu var: **`config.toml` okunabilir**, yani parolan ve statik
token'ın. `fs_read` ile de olur, `shell_run` ile de. Engellemedik, çünkü
`shell_run` keyfi komut çalıştırdığı sürece engel gerçek değil — sadece
gerçek olmayan bir güvenlik hissi verirdi. Bunun yerine **iz bırakılıyor**:
her `shell_run`, `fs_read`, `agent_run` çağrısı `audit.log`'a düşüyor.

```bash
tail -f ~/.local/state/pcbridge/audit.log
```

Kayda **ne yapıldığı** yazılır, **içerik** yazılmaz: komut evet çıktısı hayır,
dosya yolu evet içeriği hayır, metin uzunluğu evet metnin kendisi hayır.
Denetim kaydını okuyabilen birinin parolaları da okuyabilmesi anlamsız olurdu.

Asıl sınır başka yerde: sunucu **Tailscale ağında** ve **OAuth** arkasında.
Yani soru "Gemini ne yapabilir" değil, "kim Gemini'ye ulaşabilir".

---

## Bilmen gereken sınırlar

**Yazma işlemlerinde onay çıkar.** Google, özel MCP uygulamalarında dosya
yazma ve komut çalıştırma gibi işlemler için her seferinde onay soruyor.
Telefonda bir onay kutusu göreceksin. Can sıkıcı ama iyi bir güvenlik ağı.

**Sunucu açık olmalı.** `sparkac` demeyi unutursan Gemini "bağlanamadım" der.
`sparkdurum` ile bakabilirsin.

**Uzun çıktılar kırpılır.** Telefonda okunabilirlik için yanıtlar
~12.000 karakterle sınırlı (`config.toml` → `max_output_chars`). Daha fazlası
lazımsa `job_output` ile parça parça iste.

**Gemini bazen yanlış aracı seçer.** "Terminalde şunu çalıştır" dediğinde
`shell_run` yerine `agent_run` seçebiliyor. Aracın adını doğrudan söylemek
(`shell_run ile ...`) bunu çözüyor.

**İşler sunucudan bağımsız yaşar.** `sparkkapat` desen bile arka plandaki
Claude Code görevi devam eder. Bir dahaki açılışta `job_list` ile sonucunu
görürsün.
