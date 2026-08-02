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

- **Gemini görüntüyü göremiyor, sen görüyorsun.** Bağlantı senin için. Gemini
  yalnızca görüntünün ekranın neresine denk geldiğini (ofset ve ölçek) okuyor;
  "şuraya tıkla" derken o bilgiyi kullanıyor.
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

## 7. Makine durumu ve bildirim

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
| `screen_info` | Monitör tablosu, koordinat uzayı, hangi ekran birincil |
| `screen_capture` | Ekran görüntüsü alır, 5 dakikalık bağlantı döner |

`desktop_unlock`'tan `screen_capture`'a kadar olanlar `[desktop] enabled = true`
ister; varsayılan kapalı. Tek istisna `screen_info`: yalnızca donanım düzenini
söylediği için hep çalışır.

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
