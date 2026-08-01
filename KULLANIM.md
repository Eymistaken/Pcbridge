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

## 5. Makine durumu ve bildirim

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
| `list_agents` | Tanımlı ajanları ve PATH'te bulunup bulunmadıklarını listeler |
| `agent_run` | Ajana prompt gönderir, iş kimliği döner |
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
