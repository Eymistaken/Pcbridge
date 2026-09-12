# UYGULAMA.md — computer use'u pcbridge'e ekleme rehberi

> Kurallar ve mimari: **[CLAUDE.md](CLAUDE.md)** · Sıradaki iş: **[WALKTHROUGH.md](WALKTHROUGH.md)**

> # ⛔ BU DOSYA ARTIK ŞARTNAME DEĞİL — TARİHÎ KAYIT
>
> A–F bölümleri **tamamlandı** (`93e3be7`). Bu dosya o işin *başlangıçtaki
> niyetini* saklıyor; **uygulanacak talimat listesi değil.** Güncel yönerge
> `CLAUDE.md`'de, sıradaki iş `WALKTHROUGH.md`'de.
>
> **Buradaki bazı talimatlar ölçümle ÇÜRÜTÜLDÜ.** Uygulamadan önce bil:
>
> | bu dosya diyor ki | gerçek (ölçüldü) |
> |---|---|
> | "`Shell.Introspect.GetWindows()` deneyeceksin" (F bölümü) | **Kapalı** — GNOME 46'da "Access denied". Pencere listesi AT-SPI'dan geliyor |
> | `pcb-shot` PNG'leri `/tmp/pcb/`'ye yazar | `$XDG_RUNTIME_DIR/pcbridge/shots` (mod 700). `/tmp` 775, ekran görüntüsü en gizlilik-hassas çıktı |
> | `computer_task` varsayılanı `opus` + `high` | Değer koda gömülmedi, `config.toml`'a taşındı (`[desktop] computer_task_*`) |
> | `ocr.py` + tesseract + `screen_find_text` | **Hiç yazılmadı**, bilinçli olarak ertelendi — AT-SPI'ın kör olduğu yerler için `computer_task` daha iyi cevap |
> | `computer_batch` 110 sn'yi aşarsa job'a çevir | Job'a devir **yapılmadı**; `jm.start()` ayrı süreç istiyor. Yerine kısmi çalıştırma + rapor |
> | `Component.grab_focus` ile pencere öne alma | **Ölü** — GTK'da hata, Electron'da `False`. Tek yol GNOME araması (~6,5 sn) |
>
> Ayrıca **temel varsayımı değişti**: bu dosya "Gemini görsel göremiyor, o yüzden
> her şeyi metne çevir" üzerine kurulu. Sınır Gemini'nin *kanalıydı*; Claude Code
> ve Codex'te o kısıt yok, MCP görüntü taşıyabiliyor. Ölçüm `CLAUDE.md`'nin
> "Ölçülmüş makine gerçekleri" bölümünde.
>
> Ölçüm sonuçlarının tamamı `PLAN.md`'de ("Faz N sonuçları" başlıkları).
> Çelişki görürsen **ölçüm kazanır**, bu dosya değil.

---

Bu dosya sana **ne inşa edeceğini ve neden** anlatıyor. Yapılacaklar listesini
kendin çıkar; burada kutucuk yok, çünkü işin şeklini okuduktan sonra kendi
sıralaman benim tahminimden iyi olacak.

Çalışma kuralları, makine gerçekleri ve dokunulmayacak yerler
**`CLAUDE.md`**'de — bu dosyadan önce onu oku; sıradaki iş `WALKTHROUGH.md`'de.
Çelişki görürsen `CLAUDE.md` kazanır, ama önce dur ve kullanıcıya söyle.

**Sıra önemli:** A bağımsız ve tek başına değerli, ilk onu bitir. B olmadan
C-D-E'nin anlamı yok. F, B+C+D'ye dayanıyor. G en sonda ve tamamen opsiyonel.

---

## A · Ajan modeli ve effort seçimi

Bu bölümün masaüstüyle ilgisi yok; tek başına test edilebilir ve bittiğinde
kullanıcı hemen faydasını görür. O yüzden ilk bu.

### Sorun

`config.toml`'daki ajan komutlarında `--model` yok. Claude Code kendi ayar
dosyasındaki modeli kullanıyor — yani kullanıcı interaktif oturumda `/model` ile
modeli değiştirdiğinde telefondan gelen işlerin modeli de sessizce değişiyor.
Antigravity tarafında daha kötüsü var: `agy --model X` derken `--effort`
vermezsen **hata değil uyarı** basıp varsayılan modelle devam ediyor, çıkış kodu
0. Yani yanlış modelle çalışmış olursun ve haberin olmaz.

### İnşa edeceğin şey

`AgentSpec`'i genişletip, komutu kurmadan önce çalışan bir **çözümleyici**
yazacaksın. Kurallar koda değil `config.toml`'a gömülü olacak; yeni bir CLI
eklendiğinde kod değişmeyecek.

`config.py`'daki `AgentSpec`'e şu alanlar giriyor: `model_args`, `effort_args`
(bayrak sözdizimi şablonları), `models` (serbest seçilebilenler),
`restricted_models` (yalnızca kullanıcı adını açıkça verdiyse), `blocked_models`
(hiçbir koşulda), `efforts`, `default_model`, `model_effort` (model → varsayılan
effort sözlüğü), `effort_required_with_model` (bool), `aliases` (serbest metin →
kanonik ad). `load_config()` bunları okuyacak; hepsi opsiyonel, yokluğunda
bugünkü davranış aynen korunmalı.

Çözümleyiciyi `pcbridge/models.py` diye ayrı bir modüle, **saf fonksiyon** olarak
yaz. Sunucu ayakta olmadan test edilebilmesi bunun tek sebebi:

```python
def resolve(cfg, agent: str | None, model: str | None, effort: str | None) -> Resolution
# Resolution: agent, model, effort, notes: list[str], error: str | None
```

Sırayla şunları yapacak:

1. **Normalize et.** Küçük harfe çevir, noktalama ve fazla boşluğu sadeleştir,
   sonra `aliases`'ı uygula. "Gemini 3.6 Flash", "gemini-3.6-flash", "3.6 flash"
   aynı yere gitmeli. Türkçe effort adları da alias: `yuksek`→`high`,
   `extra`→`xhigh`, `maksimum`→`max`.
2. **Ajanı çıkar.** `agent` verilmemişse modelin hangi ajana ait olduğuna bak;
   bulamazsan `claude`'a düş. Çıplak "opus" → Claude Code'un Opus 5'i;
   Antigravity'nin Claude Opus 4.6'sı için kullanıcının ajanı açıkça söylemesi
   gerekiyor.
3. **Politikayı uygula.** `blocked_models`'taki bir ad gelirse gerekçeli reddet.
   `restricted_models`'taki bir ad **yalnızca kullanıcı onu açıkça istediyse**
   geçer — varsayılan doldurma buraya asla düşemez. Listede olmayan ad gelirse
   reddet ve geçerli listeyi göster (Gemini bazen model adı uyduruyor).
4. **Varsayılanları doldur.** Model boşsa `default_model`. Effort boşsa önce
   `model_effort[model]`, sonra `default_effort`, o da yoksa boş bırak.
5. **Kırp.** İstenen effort o ajanda yoksa en yakın alt seviyeye indir ve bunu
   `notes`'a yaz. `agy` + `xhigh` → `high` olur ve kullanıcı bunu görür. Sessiz
   düşürme yok.
6. **Zorunluluğu denetle.** `effort_required_with_model` açıkken effort hâlâ
   boşsa çağrıyı reddet. Bu madde tam olarak agy'nin sessiz geri düşüşünü
   engellemek için var.

`tools.py`'da `agent_run`'a `model` ve `effort` parametreleri ekleyeceksin ve
`agent` artık opsiyonel olacak (çözümleyici çıkarıyor). Docstring'lerde geçerli
değerleri **açıkça say** — Gemini doğru değeri üretsin diye. `list_agents`
çıktısına da her ajanın model tablosunu, varsayılanlarını ve effort listesini
ekle; model karşı taraftan görünsün.

`jobs.py`'da iki ayrıştırıcı işi var. `parse_claude_stream_json`, `result`
olayındaki `modelUsage` alanını okuyup özete `model: … · effort: …` satırı
eklesin. `plain` ayrıştırıcı ise agy'nin açılış başlığındaki model satırını
(`Gemini 3.5 Flash (High)`) yakalasın ve çıktıda `Using the default model
instead` / `requires --effort` kalıplarını görürse özetin **en üstüne** uyarı
bassın. İstenen ile gerçekleşen farklıysa bu fark görünür olmalı.

### Kullanıcının istediği politika

Bunlar tercih değil, şart:

- Claude tarafında **Fable asla**. `blocked_models = ["fable", "best"]` —
  `best` takma adı Fable'a çözülüyor, o yüzden o da kapalı (Pro planında ek
  kredi yakar).
- Varsayılan **Sonnet 5 + medium**. "opus" denince **Opus 5 + high**.
  "extra" → **xhigh**.
- Antigravity'de **yalnızca Gemini modelleri** varsayılan olarak seçilebilir.
  Claude Sonnet 4.6, Claude Opus 4.6 ve GPT-OSS 120B `restricted_models`'ta.
- Antigravity varsayılanı **gemini-3.6-flash + high**.

Somut TOML `PLAN.md` §5.2'de duruyor, oradan al.

### Doğrulama

Çözümleyici saf fonksiyon olduğu için `tests/test_models.py` içinde tablo
sürücülü test yaz. `PLAN.md` §5.2.2'deki "Ne dersen / ne olur" tablosunun her
satırı bir test vakası:

| Girdi | Beklenen |
|---|---|
| model/effort yok | claude · sonnet · medium |
| model="opus" | claude · opus · high |
| model="opus", effort="extra" | claude · opus · xhigh |
| agent="antigravity" | antigravity · gemini-3.6-flash · high |
| agent="antigravity", effort="xhigh" | high'a kırpılır + not |
| model="fable" | hata |
| agent="antigravity", model="claude-opus-4.6" | geçer (açıkça istendi) |

Sonra gerçek çalıştırma: `agent_run(model="opus", effort="high", prompt="1+1")`
ile `job_status` çıktısında `claude-opus-5` görmelisin.

### Ölçmen gereken iki şey

agy'nin model kimliklerinin tam yazımı bilinmiyor; yalnızca `gemini-3.6-flash`
uyarı mesajından kesin. Diğerlerini tek tek dene ve `config.toml`'a doğrusunu
yaz:

```bash
for m in gemini-3.5-flash gemini-3.1-pro claude-sonnet-4.6 claude-opus-4.6 gpt-oss-120b; do
  echo "--- $m"; agy --model "$m" --effort high -p "sadece OK yaz" 2>&1 | head -4
done
```

`requires --effort` ya da `Using the default model instead` görürsen o kimlik
yanlıştır. Claude ve GPT-OSS satırlarının `--effort` kabul edip etmediği de
buradan çıkacak.

İkincisi: `pty = true` hâlâ gerekli mi? Bu ayar, agy'nin çıktısını yalnızca
gerçek terminale yazan eski bir hatası (upstream #76) yüzünden konmuştu. 1.1.9'da
boruya yazma çalışıyor görünüyor:

```bash
agy --model gemini-3.6-flash --effort high -p "sadece OK yaz" > /tmp/agy.txt 2>&1; wc -c /tmp/agy.txt
```

Boyut sıfırdan büyükse `pty = false` yapabilirsin — `script` sarmalayıcısı
kalkar, ANSI gürültüsü azalır, ayrıştırma temizlenir. Şüphedeysen `true` bırak,
zararı yok.

---

## B · Girdi katmanı — klavye ve fare

### Neden uinput

Wayland'de harici bir süreç girdi enjekte edemiyor. `xdotool` yalnızca XWayland
pencerelerini görür (yani GNOME uygulamalarında hiçbir işe yaramaz), `wtype`
Mutter'ın desteklemediği bir protokol ister, XDG RemoteDesktop portalı ise her
oturumda kullanıcı onayı istiyor — telefondan kullanım için işe yaramaz.

Geriye çekirdek kalıyor: `/dev/uinput` üzerinden sanal bir klavye/fare cihazı
yaratıyorsun, Mutter onu gerçek donanım sanıyor. Kompozitörden bağımsız, onay
penceresi yok, hatta `WAYLAND_DISPLAY` bile gerekmiyor.

### Araç seçimi

> ⚠️ **Uygulandı, ama başka türlü — 2026-08-01.** Aşağıdaki `dotool`/`ydotool`
> yolu **kullanılmadı**; girdi katmanı **`python-evdev`** ile yazıldı
> (`pcbridge/desktop/input.py`). Harici derleme yok, ayrı daemon yok, ABS
> aralığını kendimiz tanımladığımız için mutlak fare tuvalin tamamına eşleniyor.
> `dotool`'un düzen bilme avantajı da gerekmedi: metin girişi zaten
> **pano + Ctrl+V** ile yapılıyor, ham keycode gönderilmiyor.
>
> Kurulumdan da yalnızca udev kuralı + `input` grubu kaldı, **daemon servisi
> yok**; ve kural dosyasının adı **`60-`** olmak zorunda (`80-` çok geç koşuyor,
> `uaccess` ACL'i hiç oluşmuyor — ölçüldü). Uygulaması: `setup_uinput.sh`.
> Gerekçelerin tamamı `PLAN.md` → **"Faz 1 sonuçları"**.

**`dotool`** birinci tercih, çünkü klavye düzenini biliyor
(`DOTOOL_XKB_LAYOUT=tr`) ve kullanıcının klavyesi Türkçe. Go ile yazılmış,
kaynaktan derlenecek. `ydotool` ikinci tercih ama Ubuntu 24.04 deposundaki sürüm
**0.1.8** (eski, `mousemove --absolute` yok) — kullanacaksan 1.0.4'ü kaynaktan
derle.

Kurulum tarafında üç şey gerekiyor: `/etc/udev/rules.d/80-uinput.rules` içine
`KERNEL=="uinput", GROUP="input", MODE="0660", TAG+="uaccess"`, kullanıcının
`input` grubuna eklenmesi (`usermod -aG input $USER`, **oturum kapatıp açmak
gerekiyor**), ve daemon için bir kullanıcı servisi
(`~/.config/systemd/user/dotoold.service`). Bunları `install.sh`'a ekle ama
`sudo` gerektiren kısımları **kullanıcıya söyleyerek** yaptır, sessizce çalıştırma.

### İlk iş: çift monitör testi

Kod yazmadan önce şunu ölç. Sanal işaretçi cihazının mutlak ekseni 3840 pikselin
tamamını mı kapsıyor, yoksa yalnızca birincil monitörü mü? Birincil monitör
**sağdaki** (DP-1, x=1920), o yüzden testi **sol** ekranda yap — kapsama sorunu
varsa orada ortaya çıkar.

İmleci sol ekranın sağ alt köşesine yakın bir yere gönder (örneğin 1900, 1050),
sonra ekran görüntüsü alıp imlecin gerçekten orada olduğunu doğrula
(`gnome-screenshot -p` imleci de yakalar). Sol ekrana gitmiyorsa mutlak
konumlandırma tek monitörle sınırlı demektir; o zaman göreli hareket + ekran
görüntüsünden geri besleme ile konumlanman gerekir ve bunu `PLAN.md`'ye yaz.

### `pcbridge/desktop/input.py`

Şu yetenekleri veren ince bir sarmalayıcı: `move(x, y)` (mutlak, global
koordinat), `click(button, count)`, `mouse_down/up`, `drag(x1,y1,x2,y2)`,
`scroll(amount)`, `key(combo)` (`"ctrl+shift+t"` gibi), `key_down/up`,
`type_text(text)`.

`type_text`'in varsayılan yolu **pano** olmalı: `wl-copy` ile metni panoya koy,
`ctrl+v` gönder. Türkçe düzende ham keycode göndermek `@` yerine `"` yazdırır ve
bu hatayı sonradan bulmak çok can sıkıcı. Ham tuş yolu `raw=True` ile
erişilebilir kalsın ama varsayılan olmasın. Panoyu kullandıktan sonra kullanıcının
eski pano içeriğini geri yükle — sessizce pano çalmak kaba olur.

Koordinatlar **her zaman global masaüstü uzayında** (0–3839 × 0–1079). Kırpılmış
bir monitör görüntüsüne bakıp koordinat üreten çağrılar için `monitor=` parametresi
kabul et ve ofseti sen ekle. Bu dönüşümü tek bir yerde yap; iki yerde yapılırsa
er geç biri unutulur ve sessizce 1920 piksel sola tıklanır.

### `pcbridge/desktop/safety.py`

Bu modül olmadan girdi araçlarını yayına alma. İçinde olması gerekenler:

**Süreli izin.** GUI araçları yalnızca `desktop_unlock(minutes)` sonrası çalışır,
süre dolunca kendiliğinden kapanır. Varsayılan 15 dakika, tavan 120.

**Ekran kilidi kontrolü.** `org.gnome.ScreenSaver.GetActive` true ise her şeyi
reddet. Kilitli ekranın arkasına parola yazdırmak yok.

**Çakışma koruması.** `org.gnome.Mutter.IdleMonitor.GetIdletime` 60 saniyenin
altındaysa kullanıcı makine başında demektir; yazma eylemlerini reddet.
`force=true` ile bilinçli geçilebilsin.

**Hız sınırı** (saniyede N eylem) ve **denetim kaydı** — her eylem `audit.log`'a
zaman, araç, parametre özeti ve sonucuyla.

### systemd birimi

Mevcut birim `Environment=DISPLAY=:0` veriyor, bu Wayland'de yanlış. Ekran
görüntüsü ve AT-SPI oturum ortamına ihtiyaç duyuyor (uinput duymuyor). Birime
`After=graphical-session.target` ve `PartOf=graphical-session.target` ekle,
`WantedBy`'ı `graphical-session.target` yap. `WAYLAND_DISPLAY`,
`DBUS_SESSION_BUS_ADDRESS` ve `XDG_RUNTIME_DIR` oturumdan gelmeli — kullanıcının
oturum açılışında `systemctl --user import-environment` ve
`dbus-update-activation-environment --systemd --all` çalışması gerekiyor,
bunu `install.sh`'a ekle.

`CLAUDE_CODE_EFFORT_LEVEL` veya `ANTHROPIC_MODEL`'i birime **ekleme**. İşler
`os.environ.copy()` ile başlıyor, yani birimin ortamı ajanlara aynen geçiyor ve
`CLAUDE_CODE_EFFORT_LEVEL` her şeyin üstünde önceliğe sahip; A bölümünde yazdığın
`--effort` bayrağını sessizce etkisiz kılar.

### Araçlar

`mouse`, `keyboard`, `desktop_unlock`, `desktop_lock`. `[desktop] enabled = false`
varsayılanıyla gel; kullanıcı bilinçli olarak açsın.

---

## C · Ekran görüntüsü ve monitörler

> ✅ **Uygulandı 2026-08-02.** Aşağıdaki tarif büyük ölçüde olduğu gibi
> gerçekleşti. Üç sapma var, hepsi ölçümle:
>
> 1. **`monitor="focused"` yapılamadı.** `Shell.Introspect.GetWindows` bu
>    makinede `Access denied` veriyor (GNOME 46 arayüzü kısıtlamış), yani
>    odaktaki pencerenin monitörünü dışarıdan okumanın yolu yok. Kalan
>    değerler: `"all"`, `1`/`2`, `"DP-1"`, `"primary"`, `"window"`. Odak
>    bilgisi D bölümünde AT-SPI'dan gelebilir.
> 2. **`"window"` koordinat üretmiyor.** `gnome-screenshot -w` pencerenin
>    ekranda nerede olduğunu bildirmiyor; o görüntünün ofseti `None` ve araç
>    çıktısı "buradan koordinat türetmeyin" diye uyarıyor.
> 3. **`/shot` bağlantısı tek kullanımlık değil**, 5 dakika boyunca sınırsız
>    açılabiliyor. Tek kullanım telefonda yenileme/geri tuşuyla görüntüyü daha
>    kullanıcı bakmadan yakıyordu.
>
> Ayrıca `screen_capture` de `desktop_unlock` istiyor (ekranda ne varsa hepsini
> gösterdiği için), ama "yakında klavye kullanıldı" koruması ona uygulanmıyor.
> Ölçüm ayrıntıları: `PLAN.md` → **"Faz 2 sonuçları"**.

`gnome-screenshot` ölçüldü, çalışıyor — birincil yol o. Yine de `capture.py`'yi
backend soyutlamasıyla yaz; paket GNOME 49'da bozulmuş görünüyor, bir gün
ScreenCast portalı + PipeWire yedeğine geçmek gerekebilir.

Önce `pcbridge/desktop/monitors.py`: `Mutter.DisplayConfig.GetCurrentState`'i
D-Bus'tan oku, mantıksal monitörleri **x konumuna göre soldan sağa** sırala,
1'den numaralandır. Şu an bu DP-2'yi 1, DP-1'i 2 yapıyor — birincil sağdaki
olduğu için "birincil önce" gibi doğal görünen bir sıralama kullanıcının
"birinci ekran" beklentisiyle çelişirdi. Sırayı her çağrıda yeniden hesapla ki
monitör takılıp çıkarıldığında kendiliğinden düzelsin. `monitor=` parametresi
sayı, bağlantı adı (`"DP-1"`) ve `"primary"` kabul etsin.

`capture.py` tam tuvali yakalayıp Pillow ile kırpar. **Varsayılan
`monitor="all"`** ve her monitör **ayrı görüntü** olarak döner. Sebep: imlecin
hangi monitörde olduğunu Wayland'de dışarıdan sormak mümkün değil, odaktaki
pencereye güvenmek de kırılgan (masaüstündeyken odakta pencere yok). İki ayrı
1920×1080 görüntü, 1280×720'e inince hâlâ rahat okunur — tahmin etmeye gerek
kalmıyor. Ölçeklemeyi **kırpmadan sonra** yap.

Her kırpılmış görüntünün yanında **global ofsetini** taşı. Bu bilgi kaybolursa
ikinci monitöre yapılan her tıklama 1920 piksel şaşar ve hata hiçbir yerde
görünmez.

`pcbridge/shots.py` ekran görüntülerini `state_dir/shots/` altına yazar ve
`/shot/<token>.png` rotasından servis eder: 128 bit token, tek kullanım, 5 dakika
TTL, `Cache-Control: no-store`. Bu bağlantı **model için değil kullanıcı için** —
Spark'a giden MCP function-response kanalı yalnızca metin taşıdığı için araç
sonucuna görsel konulamıyor, ama kullanıcı telefondan dokunup ekrana bakabiliyor.
Dosyaları 24 saat sonra temizle.

> Not: sınır **kanalın**, modelin değil. Gemini'nin görme yeteneği var;
> Antigravity içindeki Gemini ve Claude Code PNG okuyabiliyor (ölçüldü —
> `CLAUDE.md` "Ölçülmüş makine gerçekleri"). F bölümündeki `computer_task` tam da
> buna dayanıyor: görsel işi PNG okuyabilen **yerel** bir ajana devrediyor.

Araçlar: `screen_info` (monitör tablosu, hangi backend seçildi, birincil hangisi,
GNOME üst çubuğunun hangi ekranda olduğu) ve `screen_capture`.

`screen_info`'ya GNOME kabuğunun birincil monitörde olduğunu **açıkça** yaz.
Görsel sürücü bunu bilmezse `Super`'a basıp sol ekranda menü arar ve
"çalışmadı" sanır.

---

## D · Metinsel gözler — AT-SPI ve OCR

Bu bölüm Gemini/Spark için var. Gemini MCP araç sonucundaki görselleri göremiyor,
o yüzden ekranı ona **metin** olarak anlatmak gerekiyor.

`pcbridge/desktop/uitree.py`, `gi.repository.Atspi` ile erişilebilirlik ağacını
geziyor. GTK/GNOME uygulamaları arayüzlerini D-Bus üzerinden bir ağaç olarak
yayınlıyor: her düğümün rolü, etiketi, durumu var. Bunu düz metne çevir —
`#12 push button "Kaydet" [enabled]` gibi. Görünmez ve boş düğümleri ele, çıktıyı
4000 karaktere sığdır, `interactive_only` seçeneği sun.

`#id`'ler **kararlı** olmalı: rol + etiket + ağaçtaki yoldan bir karma üret. Model
`ui_dump` ile listeyi alıp `ui_click(12)` diyecek; arada ağaç biraz değişse bile
aynı düğüm aynı id'yi almalı.

Tıklamayı **koordinatla yapma**. Wayland'de AT-SPI'ın mutlak koordinatları
güvenilmez. Düğümün `Action` arayüzünü çağır (`do_action("click")`) — koordinat
gerekmez. Ancak `Action` yoksa `Component.get_extents` + fare yoluna düş, o zaman
da koordinatı bir ekran görüntüsüyle doğrula.

`ui_set_text` metin kutularını `EditableText` arayüzüyle doğrudan doldursun.
Klavye simülasyonundan tamamen kaçındığı için Türkçe düzen sorunundan da bağımsız
— metin girişinde tercih edilen yol bu olmalı.

Başlamadan `gsettings get org.gnome.desktop.interface toolkit-accessibility`
kontrol et. Ağaç boş geliyorsa bu ayar kapalıdır; açıp oturumu yeniden başlatmak
gerekir.

`ocr.py` ise tesseract'ın TSV çıktısını (kelime + kutu koordinatı) kullanır.
`tesseract-ocr-tur` ve `tesseract-ocr-eng` gerekli. Bu, AT-SPI'ın göremediği her
şey için: oyunlar, canvas, ve `--force-renderer-accessibility` olmadan çalışan
Electron uygulamaları (VS Code, Discord). `screen_find_text(query)` aracı metni
arayıp kutu merkezlerini döndürsün.

---

## E · Toplu eylem ve pencereler

`computer_batch` bu projedeki en kritik araç. Spark her `destructiveHint` çağrısında
telefonda onay soruyor; menüden tek öğe seçmek 4-5 çağrı ediyor, yani 5 onay ve
5 tur ağ gecikmesi. Kullanılamaz hale gelir.

`computer_batch` bir eylem listesi alır, sırayla çalıştırır, aralara `wait`
koyabilir ve sonunda `ui_dump` veya `screen_capture` döndürür:

```json
{"actions": [
  {"a": "key",  "keys": "super"},
  {"a": "wait", "ms": 400},
  {"a": "type", "text": "libre"},
  {"a": "wait", "ms": 600},
  {"a": "key",  "keys": "Return"}
], "final": "ui_dump"}
```

Toplam süreyi 110 saniyenin altında tut; aşacaksa işi arka plan job'ına çevir ve
`job_id` döndür. Her eylemi denetim kaydına yaz.

Pencere yönetimi için `org.gnome.Shell.Introspect.GetWindows()` deneyeceksin.
Erişilebilir değilse (bazı sürümlerde kısıtlı) pencere listesini AT-SPI'dan türet.
`window_list` ve `window_focus` bunun üstüne oturur. `open_app(name)`,
`switch_window(title)` gibi hazır makrolar da buraya.

---

## F · Yerel görsel ajan — asıl computer use

Buraya kadar yaptıkların Spark'ın metin dünyasında çalışıyor. Gerçek anlamda
"ekrana bak, karar ver, tıkla" döngüsü ise **makinedeki ajanla** kuruluyor:
Claude Code PNG okuyabiliyor (doğrulandı), yani gözü var. Antigravity de
okuyabiliyor ve kotası ayrı — uzun oturumlarda ikinci sürücü olarak kullanılabilir.

İki ince CLI kabuğu yazacaksın, `bin/pcb-shot` ve `bin/pcb-do`. Aynı `desktop/`
modülünü kullanırlar ama MCP sunucusundan bağımsız çalışırlar — ajan bunları
Bash'ten çağırıyor:

```
Bash: pcb-shot --monitor all      → /tmp/pcb/1.png, /tmp/pcb/2.png + ofsetler
Read: /tmp/pcb/2.png              → ajan gerçekten görüyor
Bash: pcb-do '{"a":"click","x":2760,"y":312}'
Bash: pcb-shot --monitor 2        → sonucu doğrula
```

`pcb-shot` çıktısında monitör numarasını, global ofseti ve boyutu **metin olarak**
da yazsın; ajan koordinat hesabını ona göre yapsın.

Sonra `~/.claude/skills/computer-use/SKILL.md` — ajana bu döngüyü öğreten skill.
İçinde olması gerekenler: her eylemden sonra doğrula, kör tıklama yapma, GNOME
üst çubuğunun sağ ekranda olduğu, koordinatların global olduğu, ve takılırsa
kullanıcıya sorması.

`computer_task(goal, app, model, effort, max_steps)` aracı bunları bağlar:
`agent_run`'ı GUI yönergesiyle çağırır, `job_id` döndürür. Varsayılan model burada
`opus` + `high` olsun — ekran okuyup GUI sürmek zor iş, rutin metin işlerinden
farklı. Kullanıcı isterse Antigravity'ye de yönlendirebilmeli.

---

## G · Ekran çerçevesi (opsiyonel, en son)

Kontrol açıkken her iki ekranın kenarında mavi-mor gradyanlı ince bir çerçeve.
Hem görsel geri bildirim hem de güvenlik göstergesi: bildirim kaybolur, çerçeve
durur.

Wayland'de bir istemci kendini "hep üstte, tüm ekranı kaplayan, tıklama geçiren"
pencere yapamıyor, o yüzden tek yol **GNOME Shell eklentisi**:
`~/.local/share/gnome-shell/extensions/pcbridge-frame@local/` altında
`metadata.json` + `extension.js`.

Her monitör için bir `St.DrawingArea`, Cairo ile yuvarlatılmış dikdörtgen stroke,
mavi→mor `LinearGradient`, dışa doğru azalan alfayla birkaç kat yumuşak parıltı.
`Main.layoutManager.addChrome(actor, { affectsInputRegion: false })` ve
`reactive = false` — **tıklamaları asla yememeli**. `monitors-changed` sinyaline
bağlan. D-Bus'tan `SetActive(bool)` ve `SetState(string)` aç; pcbridge
`desktop_unlock`'ta açar, süre dolunca kapatır. Durumlar: izin açık ama eylem
yokken sakin nefes, girdi giderken parlak nabız, izne 60 saniye kalınca amber.

Çerçeveyi ince tut (4-6 piksel) — ekran görüntülerine girecek ve görsel sürücü
her karede kenarda mor bir bant görecek. İnce olduğu sürece gürültü sayılmaz.
`hide_frame_during_capture` seçeneğini ekle ama **varsayılan kapalı**; her
görüntüye 200 ms ve görünür titreme ekliyor.

`metadata.json`'da `shell-version: ["46"]`. Yeni eklentinin tanınması için
Wayland'de bir kez oturum kapatıp açmak gerekiyor — kullanıcıya bunu söyle.

---

## H · Kapanış

Testler: `tests/test_desktop.py`'yi `weston --backend=headless` ya da Xvfb içinde
çalıştır ki CI gerçek masaüstüne dokunmasın. `tests/test_e2e.py`'ye yeni araçların
şema testlerini ekle.

Belgeler: `KULLANIM.md`'ye telefondan yazılabilecek gerçek cümleler,
`GELISTIRME.md`'ye Wayland tuzakları (klavye düzeni, AT-SPI koordinatları, uinput
izinleri, monitör ofsetleri), `README.md`'nin güvenlik bölümüne GUI kontrolünün ne
anlama geldiğine dair dürüst bir uyarı.

Son adım kolay unutuluyor: **Gemini araç listesini önbelleğe alıyor.** Servisi
yeniden başlatmak yetmez. gemini.google.com → Connected Apps → pcbridge →
senkronizasyonu yenile; görünmezse uygulamayı kaldırıp yeniden ekle.

---

## Takıldığında

Bu plan ölçüme dayanıyor ama her şey ölçülmedi. Plandan sapman gereken bir gerçekle
karşılaşırsan — uinput ikinci monitöre ulaşmıyor, AT-SPI ağacı boş geliyor, agy
model kimlikleri tutmuyor — **uydurma ve etrafından dolaşma.** Durumu kullanıcıya
anlat, `PLAN.md`'de ilgili bölümü gerekçesiyle düzelt, sonra devam et.

Özellikle şunları sessizce çözmeye çalışma: X11'e geçmek (kullanıcı istemiyor),
`--dangerously-skip-permissions`'ı kaldırmak (arka plan işleri asılı kalır),
`config.toml`'daki sırları loga basmak, `[desktop] enabled`'ı varsayılan açık
yapmak.
