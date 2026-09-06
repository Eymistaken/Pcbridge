# Görev: masaüstü araçlarının davranış sözleşmesini netleştir

Depo: `~/Belgeler/Pcbridge`. Şartname: **`KURALLAR.md`** (depo kökünde).
Önce onu baştan sona oku — bu istem onun özeti değil, uygulama emri.

`CLAUDE.md`'yi de oku ve oradaki "Değişmez kurallar" bölümüne harfiyen uy.
Aşağıda yalnızca bu göreve özel olanları tekrarlıyorum.

---

## Neyi çözüyoruz

Üç somut şikâyet var, üçünün de kaynak koddaki karşılığı `KURALLAR.md`'de
saptanmış durumda:

1. Ajan `desktop_unlock` sonrası kapatmayı unutuyor; çerçeve dakikalarca
   ekranda kalıyor. Ajanın "işim bitti" olayı olmadığı için bu **yapısal**,
   ajanın dikkatsizliği değil. Çözüm kodda olmalı.
2. Ajan grafik uygulamaları `shell_run` ile açıyor. Doğru yol (`window_focus`
   → GNOME araması) **zaten var** ama docstring'i onu söylemiyor.
3. Fare bazen hiç kıpırdamıyor. Bu bir hata değil — `ui_click` AT-SPI
   kullanıyor. Hiçbir yerde yazmıyor, o yüzden hata gibi görünüyor.

---

## Kapsam — dört faz, sırayla

Her fazdan sonra **dur ve bana göster**. Hepsini tek seferde yapıp sonunda
"bitti" deme.

### Faz 1 — docstring ve INSTRUCTIONS (kod riski yok)

Kod davranışı değişmiyor, yalnızca ajanın okuduğu metinler.

**a)** `tools.py` → `window_focus`: docstring ve `Field(description=...)`
düzelt. Şu an ikisi de aracı "yalnızca açık pencereler için" gibi
gösteriyor; gerçekte `apps.focus()` GNOME aramasını kullandığı için
**kapalı uygulamayı da açıyor**. Yeni metin bunu açıkça söylesin ve
`shell_run` ile grafik uygulama açmanın yasak olduğunu belirtsin.
`Field` açıklamasındaki *"Names from `window_list` work best"* ifadesi de
yanıltıcı — kapalı uygulamalar `window_list`'te yok.

**b)** `tools.py` → `ui_click`: docstring'e imlecin **kıpırdamayacağını** ve
bunun kasıtlı olduğunu ekle. Ajan bunu eksiklik sanıp `mouse` ile
"düzeltmeye" çalışmasın.

**c)** `server.py` → `INSTRUCTIONS`: `KURALLAR.md` bölüm 3b'deki masaüstü
kural bloğunu ekle. Mevcut "Guidelines" listesini bozma, altına ayrı bir
`Desktop rules` başlığı aç.

Docstring ve `Field` metinleri **İngilizce** (istemci araç seçerken yalnızca
onları okuyor). Kullanıcıya dönen çıktı metinleri Türkçe.

### Faz 2 — kayan kira: izin kendiliğinden düşsün

`KURALLAR.md` bölüm 1'deki tasarım. Özet: izin sabit son tarihe değil **son
eyleme** bağlansın.

- `safety.py`: `unlock()` artık iki alan yazsın — `hard_until` (sert tavan,
  `unlock_default_minutes`'tan gelen) ve `until` (kayan).
- Başarılı her `check()` sonrası `_touch()`:
  `until = min(hard_until, now + unlock_idle_seconds)`.
- `is_unlocked()` yalnızca `until`'e bakmaya **devam etsin** — kapının
  mantığı değişmiyor, yalnızca `until` artık kayıyor.

Dikkat edilecekler:

- `_write_state()` dosyayı **komple üzerine yazıyor**. `_touch()` mutlaka
  oku-değiştir-yaz olsun, yoksa `hard_until` ilk eylemde kaybolur.
- **Sadece izin verilen çağrılarda** damgala. Reddedilen bir `check()`
  izni uzatmamalı.
- Süresi dolmuş bir izni `_touch()` **diriltmemeli**. `hard_until` geçmişte
  ya da yoksa hiçbir şey yazma.
- `lock()` her iki alanı da sıfırlasın.
- **Geriye dönük uyumluluk:** diskte `hard_until` içermeyen eski bir dosya
  olabilir. O durumda `hard_until` yokmuş gibi davran ve eski davranışı
  koru — çökme yok, sessiz izin uzaması da yok.

Yeni ayar `unlock_idle_seconds = 90`, **hem** `config.example.toml`'a
yorumuyla **hem** `config.py`'deki `DesktopSpec`'e eklenecek. `0 = kapalı`
(eski davranış) desteklensin. Bu proje bunu bir kere atladı: alanlar
tanımlıydı, config'e yazılan değer hiçbir şey yapmıyordu. Tekrarlanmasın.

**GNOME eklentisine dokunma.** `state.js` zaten yalnızca `until` okuyup
dosya değişimini izliyor ve `_armExpiry` ile zamanlayıcı kuruyor; kayan
`until` yazıldığı anda çerçeve kendiliğinden uyuyor. Bunun gerçekten böyle
olduğunu doğrula, ama eklenti kodunu **değiştirme**.

Ek olarak `KURALLAR.md` bölüm 1'in sonundaki "açan kapatır": `computer_batch`
ve `computer_task` **kendileri unlock ettiyse** çıkışta `desktop_lock`
çeksin. Kullanıcının ya da başka bir çağrının açtığı izne dokunmasınlar —
bunun için state'e `granted_by` alanı gerekiyor.

### Faz 3 — kabuk kapısı (önce bana sor, bölüm "Sorular"a bak)

Masaüstü izni **açıkken** `shell_run` bilinen bir GUI uygulamasını
başlatmaya çalışırsa reddet. `KURALLAR.md` bölüm 3c'deki tasarım.

- Tespit **tahminle değil veriyle**: `apps.entries()` zaten bütün `.desktop`
  girdilerini okuyor. `apps.py`'ye `looks_like_gui_launch(command)` ekle;
  komutun ilk anlamlı simgesini o tabloyla eşleştirsin. Ayrıca açık
  başlatıcılar: `gtk-launch`, `gio launch`, `xdg-open`, `flatpak run`.
- Ret gerekçesi **ne yapılacağını söylesin** — projenin kuralı bu:
  `window_focus("<ad>")` önersin.
- İki valf: `block_gui_launch_in_shell` (bool) ve `gui_launch_allowlist`
  (liste). İkisi de `config.example.toml` + `config.py`.
- Masaüstü izni **kapalıyken** bu kapı hiç devreye girmesin: masaüstü
  kapalıyken kabuk normal kabuktur.

### Faz 4 — parola alanı koruması

`ui_set_text` hedef düğümün AT-SPI rolü parola alanıysa reddetsin.
`uitree` rolü zaten okuyor. `KURALLAR.md` bölüm 4, madde 7.

---

## Kapsam dışı — bunlara girme

- Telefon uygulaması, `pcbridged`, ntfy, Kotlin, PWA. **Hiçbiri bu görevde
  yok.** `JARVIS.md`'yi okursan bunları göreceksin; okuma gereği yok,
  uygulama hiç yok.
- Yerel model, Gemini, yeni `[agents.*]` blokları.
- GNOME eklentisinde `pulse` (`KURALLAR.md` bölüm 2B). Önce nefes
  animasyonunun gerçek oturumdaki CPU maliyeti ölçülecek, o ayrı bir iş.
- `KURALLAR.md` bölüm 4'teki 5. ve 6. maddeler (yıkıcı eylem onayı, döngü
  koruması). Sonraki tur.
- Yeni araç eklemek. Araç sayısı 33'te kalıyor — `app_open` konusu
  "Sorular"da, cevabımı almadan ekleme.
- `server.py`'deki `MetadataNormalizer` ve `BasicAuthFormShim`.

---

## Sorular — tahmin etme, sor

Faz 3'e başlamadan önce ikisini de bana sor:

1. **Allowlist'te ne olmalı?** Kabuktan meşru olarak açtığım GUI
   uygulamaları hangileri? Boş listeyle başlayıp benim doldurmam da bir
   seçenek — hangisi daha az sürtünme yaratır, sen öner ama karar benim.
2. **`app_open` ayrı bir araç olsun mu?** `window_focus` hem açıyor hem öne
   alıyor, adı yalnızca ikincisini söylüyor. `apps.launch()` (`gtk-launch`)
   kodda var ama hiçbir araç onu dışarı vermiyor ve aramadan hızlı olması
   bekleniyor — **bu makinede ölçülmedi**. Ölçmeden "daha hızlı" deme.

Başka bir yerde de karar gerekirse dur ve sor. Sessizce varsayma.

---

## Doğrulama — "hata vermedi" çalışıyor demek değil

Bu depoda hiçbir şey istisna atmadığı için çalışmış sayılmıyor. Her faz için
**ölçüm** istiyorum, iddia değil.

- `./.venv/bin/python tests/test_models.py` — sunucu gerekmez
- `./.venv/bin/python tests/test_desktop.py` — 398 kontrol, **girdi
  göndermez**. Kayan kira saf mantık olduğu için testi **buraya** yaz:
  damganın uzatması, `hard_until` tavanı, reddedilen çağrının uzatmaması,
  süresi dolmuş iznin dirilmemesi, eski dosya biçimi.
- `gjs -m gnome-extension/tests/test_state.js` — eklentiye dokunmasan da
  koştur, `until` biçimini bozmadığını bu gösterir.
- `./doctor.sh`

Uçtan uca koşacaksan **`PCBRIDGE_TEST_NO_AGENT=1` ver.** `test_e2e.py`'nin
12. bölümü gerçek bir `claude -p` çalıştırıyor ve bir kere günlük kotamı
bitirdi.

Kayan kira için ayrıca **gerçek gözlem**: izni aç, bir masaüstü aracı
çağır, dur, süre tut. Çerçevenin ne kadar sonra söndüğünü **saniye olarak
yaz**. Beklenen ~90 sn.

Kodu değiştirdikten sonra `systemctl --user restart pcbridge` **şart** —
ama **çalışan işleri öldürür**. Önce `job_list` ile bak, koşan iş varsa bana
söyle.

---

## Çalışma biçimi

1. Önce `KURALLAR.md` + `CLAUDE.md` + ilgili kaynak dosyaları oku.
2. **Kendi görev listeni çıkar ve bana onaylat.** Onay almadan kod yazma.
3. Faz faz ilerle, her fazın sonunda dur ve sonucu göster.
4. Bitince `YAPILACAKLAR.md`'yi güncelle (biten iş + varsa yeni açılan
   konu) ve `KULLANIM.md`'deki araç kataloğunda değişen docstring'leri
   yansıt.
5. Ölçtüğün ve **seni şaşırtan** her sayıyı belgeye yaz. Bu deponun âdeti
   bu: tahmin değil ölçüm, ve şaşırtanı sayısıyla kaydet.

Emin olmadığın bir şeyi çalışıyormuş gibi yazma. Ölçemediysen "ölçülmedi"
de.
