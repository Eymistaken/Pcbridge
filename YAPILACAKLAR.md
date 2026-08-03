# YAPILACAKLAR.md — pcbridge çalışma yönergesi

## Başla

Kullanıcı "YAPILACAKLAR.md'dekileri yap" dediyse sırayla şunu yap:

1. **Bu dosyanın tamamını oku.** İhtiyacın olan her şey burada; başka belge
   okumana gerek yok
2. **H0 bölümü** için kendi görev listeni çıkar, kullanıcıya göster
3. Onay gelmeden kod yazma
4. Onaydan sonra adım adım ilerle, **her adımı fiilen test et**
5. Bölüm bitince commit at

> `PLAN.md`, `UYGULAMA.md`, `GELISTIRME.md`, `KULLANIM.md` **geçmiş kayıt**.
> Okuman gerekmiyor, ama silme — neyin neden böyle yapıldığı orada yazıyor.
> `KULLANIM.md` kullanıcıya dönük araç kataloğu, güncel tutulmalı.

---

## Proje

pcbridge, kullanıcının Linux masaüstünü **uzaktan sürülebilir** hale getiren
kişisel bir MCP sunucusu. Bugün çalışan şeyler (33 araç):

| grup | araçlar |
|---|---|
| ajan | `list_agents`, `agent_run`, `computer_task` |
| iş | `job_status`, `job_output`, `job_list`, `job_cancel` |
| terminal | `tmux_list/start/send/keys/capture/kill` |
| kabuk & dosya | `shell_run`, `shell_run_background`, `fs_list/read/write/search` |
| masaüstü izni | `desktop_unlock`, `desktop_lock` |
| girdi | `mouse`, `keyboard`, `computer_batch` |
| ekran | `screen_info`, `screen_capture` |
| erişilebilirlik | `ui_dump`, `ui_click`, `ui_set_text` |
| pencere | `window_list`, `window_focus` |
| sistem | `system_status`, `notify` |

Ayrıca MCP'den **bağımsız** iki CLI kabuğu: `bin/pcb-shot` (ekranı PNG yazar,
global ofsetini söyler) ve `bin/pcb-do` (eylem listesi çalıştırır). Bunlar
`~/.local/bin`'e bağlı ve makinede çalışan herhangi bir ajan Bash'ten
çağırabiliyor. Yönerge dosyası `skills/computer-use/SKILL.md`, oradan
`~/.claude/skills/computer-use`'a symlink.

**Son durum:** A→H bölümleri bitti. `[desktop] enabled = false` (doğru hâli).

Faz H'de eklenenler: `--stdio` taşıması, `screen_capture`'ın görüntüyü araç
sonucunda döndürmesi (`[server] inline_images`), `connect.sh`, `doctor.sh`'e
istemci kayıtları başlığı, `computer_task`'in varsayılan sürücüsünün `claude`
olması ve bir model çözümleme hatasının düzeltilmesi.

---

## Bu bölümün hedefi — H

**pcbridge bugüne kadar Gemini Spark için tasarlandı. Artık AI ajanlarının
bağlandığı bir MCP sunucusu olacak.**

Sebebi tek bir teknik gerçek. Bugüne kadarki bütün mimariyi belirleyen kısıt
şuydu: *Spark'a giden MCP function-response kanalı araç sonucunda yalnızca
metin taşıyor.* Ekranı modele anlatmak için `ui_dump` (metinsel ağaç) yazıldı,
görüntüler `/shot/<token>.png` bağlantısına dönüştürüldü, ve `computer_task`
görsel işi PNG okuyabilen yerel bir ajana devretmek için var edildi.

**Claude Code ve Codex'te bu kısıt yok.** MCP protokolü araç sonucunda görüntü
taşıyabiliyor (`ImageContent`), FastMCP'de `Image` sınıfı hazır. Yani istemci
ekran görüntüsüne **kendisi** bakabilir.

Bunun dürüst sonucu: **F bölümünün (`computer_task`) asıl gerekçesi ortadan
kalkıyor.** Gören bir istemci görüntüye bakar ve `computer_batch` çağırır,
arada ajan olmasına gerek kalmaz. `computer_task` ölmüyor ama gerekçesi
değişiyor: "uzun süren bir GUI işini arka plana at, ana ajan bloke olmasın".

**Metin yolu kalıyor ve gerekçesi güçleniyor.** Ölçüldü: bir ekran görüntüsü
ajana ~40 bin girdi jetonu, `ui_dump` ise ~0,1 saniye ve birkaç yüz jeton.
Üstelik `ui_click` koordinat kullanmadığı için **ıskalayamaz**. Yani GTK
uygulamalarında metin ağacı hem ucuz hem isabetli yol; görüntü, erişilebilirlik
ağacının boş geldiği yerler (Electron, tuval, oyun) için yedek. Gemini bizi
verimli yolu önce inşa etmeye zorlamış — bu bir baypas değil, kazanç.

---

## İstemci gerçekleri (ölçüldü 2026-08-03, tahmin etme)

| istemci | sürüm | MCP istemcisi | nasıl |
|---|---|:---:|---|
| **Claude Code** | 2.1.220 | ✅ | `claude mcp add` — stdio / http / sse |
| **Codex CLI** | 0.144.1 | ✅ (aboneliği yok, bkz. aşağı) | `codex mcp add <ad> -- <komut>` (stdio) · `--url` (streamable HTTP) · `--bearer-token-env-var` · **`codex mcp login` ile OAuth** |
| **Claude Desktop** | kurulu, çalışıyor | ✅ | yapılandırma dosyası, stdio |
| **Antigravity (`agy`)** | 1.1.10 | ❌ | `mcp` alt komutu **yok**. `plugin` var ama o Claude/Gemini eklentisi içe aktarıyor, MCP değil |
| **Gemini Spark** | — | ✅ | bugün çalışan yol; HTTP + OAuth, **görüntü alamaz** |

**Antigravity bu bölümün kapsamı dışında.** Kullanıcı kararı: MCP istemcisi
olmadığı için ona ayrıca uğraşılmayacak. Ama `agent_run` hedefi olarak
`config.toml`'da tanımlı ve çalışıyor — **oraya dokunma, kaldırma.** Yalnızca
yeni iş yapılmayacak.

> ### ⚠️ Codex BU MAKİNEDE ÇALIŞTIRILAMIYOR
>
> `codex` CLI kurulu ve `codex mcp --help` çıktısı yukarıdaki desteğin
> **belgelendiğini** gösteriyor. Ama kullanıcının **Codex aboneliği yok**, yani
> gerçek bir Codex oturumu açılamıyor. Testi kullanıcının kuzeni yapacak.
>
> Bunun sana iki sonucu var:
>
> 1. **Codex tarafını ölçemezsin.** `codex mcp add` yapılandırma dosyasına
>    yazar (bu denenebilir), ama sunucuya gerçekten bağlanıp araç listesi
>    alması ve **görüntü bloğunu işleyip işlemediği** ölçülemez.
> 2. **Belgelere "Codex destekleniyor" YAZMA.** Doğru cümle: *"Codex için
>    yapılandırma hazırlandı, bu makinede denenmedi."* Denenmemiş bir şeyi
>    çalışıyormuş gibi yazmak bu projenin en sevmediği şey — ölçülmemiş her
>    iddia bir sonraki kişiyi yanıltıyor.
>
> Yapabileceğin: kurulum komutlarını üret, yapılandırma dosyasına doğru
> yazıldığını `codex mcp list` / `codex mcp get` ile doğrula, ve kuzenin
> deneyebilmesi için `KULLANIM.md`'ye net bir "Codex ile bağlanma" adımı yaz.
> Sonuç geldiğinde bu dosyaya işlenir.

Kütüphane tarafı hazır: `fastmcp 3.4.5` (`fastmcp.utilities.types.Image`),
`mcp.types.ImageContent`, `FastMCP.run(transport=...)`.

---

## Makine gerçekleri (ölçüldü, tahmin etme)

- **Zorin OS 18.1 Core** = Ubuntu 24.04 LTS + **GNOME Shell 46**, **Wayland**
  (X11'e geçmek seçenek değil, önerme)
- **İki monitör**, ikisi de 1920×1080, **ölçek 1.0**:

  | Bağlantı | Konum | Kırpma kutusu | Not |
  |---|---|---|---|
  | DP-2 | x=0 | `(0, 0, 1920, 1080)` | **sol** → `monitor=1` |
  | DP-1 | x=1920 | `(1920, 0, 3840, 1080)` | **sağ**, **birincil** → `monitor=2` |

  GNOME üst çubuğu ve `Super` menüsü **sağdaki** monitörde. Numaralandırma her
  zaman **x konumuna göre soldan sağa**, "birincil önce" değil.
- **Klavye düzeni Türkçe** (`tr+intl`). uinput ham keycode gönderir → ASCII
  bozulur. Metin girişinde varsayılan yol **`wl-copy` + Ctrl+V**
- **Girdi katmanı `python-evdev`**, `dotool`/`ydotool` değil. Mutlak fare
  3840×1080 tuvale 1:1 eşleniyor
- `gnome-screenshot` **kurulu ve çalışıyor**. Görüntü **monitör başına kırpılır,
  ölçekleme kırpmadan sonra**; her görüntü global ofsetini taşır
- **Koordinat gidiş-dönüşü** tam çözünürlükte ~1 px, 1280'e küçültülmüşte ~5 px
- **`Shell.Introspect` kapalı** — `GetWindows` "Access denied" (GNOME 46).
  Pencere listesi ve odak yalnızca AT-SPI'dan okunuyor
- **Erişilebilirlik ağacı dolu ve kullanılabilir**: GTK4'te rol/etiket/durum
  eksiksiz, `Action` ve `EditableText` var. Ama `get_extents` **koordinatları
  yanlış** — tıklama `Action.do_action` ile yapılır. `gi` venv'de yok, AT-SPI
  ayrı bir süreçte (sistem `python3`) çalışır
- **AT-SPI Electron'un PENCERESİNİ görüyor ama İÇİNİ görmüyor.** Vesktop'ta
  `windows()` pencereyi listeliyor (yani odak takibi çalışıyor) ama
  `dump(target='vesktop.bin')` **0 düğüm**
- **uinput olayı `IdleMonitor`'ü SIFIRLIYOR** — 104227 ms → 151 ms. "Kullanıcı
  makinede mi" kontrolü bir eylem dizisinin **içinde** yapılamaz; dizi kendi
  tuşunu kullanıcı sanar. Kontrol yalnızca dizi/görev başında
- **GNOME overview açıkken (`super` sonrası) Wayland panosu bloklanıyor** —
  `wl-paste` 5 sn'de cevap vermedi. Overview'da ham tuş yolu (`raw=true`) şart
- **Pencere öne alma: AT-SPI ve D-Bus yolları KAPALI.** `Component.grab_focus`
  GTK'da hata, Electron'da `False`; `org.freedesktop.Application.Activate`
  `exit=0` dönüp hiçbir şey yapmıyor (sessiz başarısızlık). Çalışan tek yol
  GNOME'un kendi araması (`super` + ad + `Return`), **~6,5 saniye**
- **Taze süreçte uinput maliyeti:** klavye 1,301 s + fare 1,306 s = **2,607 s**;
  ikisi önce yaratılıp **tek bekleme** paylaşılırsa **1,41 s**
  (`InputBackend.ensure`). Gerçek tuş basımı 0,030 s
- **`systemctl --user stop/restart pcbridge` ÇALIŞAN İŞLERİ DE ÖLDÜRÜR.**
  `start_new_session` oturum grubunu ayırıyor ama **cgroup'u değil**; iş
  servisin cgroup'unda kalıyor ve `KillMode=control-group` hepsini alıyor.
  İkisi birden doğru: acil durdurma gerçekten çalışıyor **ve** kod
  değişikliğinden sonraki restart uzun bir ajan işini keser
- **`[desktop] enabled = false` yalnızca masaüstü araçlarını kapatır.**
  `shell_run`, `agent_run`, `fs_*`, `tmux_*` bu kapıdan geçmez — kapalıyken de
  komut çalışır, uygulama açılır, `config.toml` okunabilir. Bu bilinçli; koruma
  engelleme değil, `audit.log`'a **iz bırakma**
- **Ekran görüntüsünün maliyeti sürücüye göre 20–30 kat değişiyor.** `agy`'de
  tek görüntü ~40 bin girdi jetonu; **Claude'da ~1200–1900** (ölçüldü
  2026-08-03: 1920×1080 → ~12976, 1280×720 → ~11712 cache_creation, fark ~1264).
  Yani `computer_task`'i pahalı yapan şey görüntü değil, **ayrı oturum ve
  tekrar eden turlar**
- **Anthropic API görüntünün uzun kenarını 1568'e indiriyor.** Bu yüzden
  `screenshot_scale_long_edge = 1280` korunuyor: o sınırın altında kalınca
  modelin gördüğü piksel ile bizim raporladığımız ölçek aynı kalıyor. 1920
  gönderilseydi "ölçek 1.0" bilgisi **sessizce yalan** olurdu
- **Claude Code araç sonucundaki görüntüyü GERÇEKTEN okuyor.** Ölçüm yolu:
  bilinen içerikli PNG (`KELIME-1234`), model değeri birebir söyledi. "Evet
  görüyorum" demesi kanıt sayılmadı. Uçtan uca da doğrulandı — `--stdio` ile
  bağlanan gerçek bir oturum `screen_capture` çağırıp ekranı doğru tarif etti
- **stdio'da fastmcp auth'u kendisi atlıyor** (`fastmcp/server/server.py:196`:
  "skip_auth=True means auth checks should be skipped (STDIO transport)").
  Yani `build_app()`'in ürettiği aynı örnek stdio'da OAuth'suz çalışıyor
- **stdio'da HİÇBİR `@mcp.custom_route` rotası servis edilmiyor** — HTTP
  sunucusu yok. `/shot/<token>.png`, `/healthz`, `/consent`, `/.well-known/*`
  orada yok; `screen_capture` bu yüzden bağlantı yerine dosya yolu döner
- **FastMCP'de dönüş tipi `-> list` (çıplak) yazılırsa araç patlıyor:**
  outputSchema üretiliyor ve çağrı `"outputSchema defined but no structured
  output returned"` diyor. Görüntü dönen araçlarda tip **`list[ContentBlock]`**
- **venv'deki `pcbridge.pth` repo yolunu `sys.path`'e ekliyor**, bu yüzden
  `python -m pcbridge.server` **herhangi bir dizinden** çalışıyor (cwd=/ ile
  ölçüldü, 33 araç). İstemci kayıtlarında `cwd` vermek gerekmiyor
- **stdio testinde stdin'i erken kapatmak sunucuyu yanıt yazmadan kapatıyor.**
  `communicate()` ile hepsini birden göndermek `tools/list` yanıtını yutuyor ve
  test/tanı sunucuyu bozuk sanıyor. Yanıt **satır satır** okunmalı

---

## ⚠️ Bu makinede test etmenin tehlikesi

**Sen pcbridge'in kontrol edeceği makinenin üzerinde çalışıyorsun.** uinput
tıklaması/tuşu odaktan bağımsız gider — bir `type` testi **senin çalıştığın
terminale** yazabilir ve Enter'a basabilir.

Girdi testlerinde kural:

1. Önce `gnome-text-editor` gibi bir boş pencere aç, testi **oraya** yap
2. Fare testinde önce `mousemove`, sonra ekran görüntüsü alıp konumu
   **doğrula**, ancak ondan sonra `click`
3. Kaçak döngüye karşı acil durdurma: **`systemctl --user stop pcbridge`**
   (sanal cihazlar pcbridge sürecinin içinde yaşıyor; ayrı bir daemon yok)
4. Uzun/tekrarlı girdi denemelerini kullanıcıya haber vermeden başlatma
5. **Tıklamadan sonra odağın kaydığını varsay**
6. **Ekran görüntün BAYATLAR.** Görüntüye bakıp koordinat çıkardıktan sonra
   araya iş sokma

> **Kaza 1 — 2026-08-02.** `move(920, 520)` + `click` yapıldı, oranın metin
> düzenleyici olduğu **varsayıldı, doğrulanmadı**. Tıklama masaüstüne düştü,
> odak oraya kaydı, ardından temizlik için gönderilen `ctrl+a` + `Delete`
> masaüstündeki **23 öğeyi çöpe gönderdi**. (Hepsi geri alındı.) Karşılığı:
> `computer_batch` artık tıklamadan sonra odağı doğruluyor ve kaymışsa duruyor
> (`[desktop] batch_check_focus`), beklenen pencere `expect_focus` ile
> bildirilebiliyor.

> **Kaza 2 — 2026-08-03.** Ekran görüntüsü alındı, hedef pencere öndeydi,
> koordinat okundu. Sonra araya **69 saniye** kod düzenlemesi girdi ve o sırada
> başka bir pencere öne geldi. Tıklama ona düştü. **Odak koruması ötmedi** —
> odak zaten o pencereydeydi, *değişen* bir şey yoktu. Karşılığı: `pcb-shot`
> çıktısında zaman damgası, `pcb-do`'da koordinatlı eylemler için yaş kontrolü
> (`[desktop] agent_shot_max_age_seconds`, varsayılan 60 sn).

İki kaza da aynı kökten: **doğrulanmamış bir varsayıma göre tıklamak.**

---

## Değişmez kurallar

**MCP araçları**

- Docstring ve `Field(description=…)` **İngilizce** — istemci araç seçerken
  bunları okuyor. Kullanıcıya dönen metinler Türkçe
- Docstring "ne zaman kullanılır"ı söylesin, sadece "ne yapar"ı değil
- Çıktıyı `jobslib.tail_chars(metin, 4000)` ile kırp
- `readOnlyHint` / `destructiveHint` doğru işaretlensin
- **110 saniyeden uzun bloklama yok** — uzun işler `jm.start()` ile arka plana
- Yol parametreleri `_resolve_dir` / `_resolve_file` ile çözülsün

**Dokunma**

- `server.py` içindeki `MetadataNormalizer` ve `BasicAuthFormShim` — Google
  OAuth akışının çalışmasının tek sebebi bunlar. Diğer istemcilerde etkisizler,
  **dursunlar**
- `auth.py`'ın OAuth mantığı — görev açıkça istemedikçe
- `config.toml`'daki `[agents.antigravity]` bloğu — `agent_run` hedefi olarak
  çalışıyor

**Güvenlik**

- `config.toml` parola ve statik token içeriyor. `.gitignore`'da, öyle kalsın.
  İçeriğini **loglama, ekrana basma, commit etme**
- Masaüstü yetenekleri `[desktop] enabled = false` ile gelir. **Varsayılanı
  değiştirme**
- Her yeni ayar `config.example.toml`'a **yorumuyla** eklenir
- Denetim kaydı kuralı: **ne yapıldığı yazılır, İÇERİK yazılmaz** — komut evet
  çıktısı hayır, dosya yolu evet içeriği hayır, metin uzunluğu evet metnin
  kendisi hayır
- Sudo isteyen kurulum adımları **kullanıcıya söylenerek** yapılır, sessizce
  çalıştırılmaz

**Etrafından dolaşma**

X11'e geçmek, `--dangerously-skip-permissions`'ı kaldırmak, `config.toml`
sırlarını loga basmak, `[desktop] enabled`'ı varsayılan açık yapmak — bunlar
çözüm değil. Planla çelişen bir gerçekle karşılaşırsan **uydurma**: kullanıcıya
söyle, gerekçesiyle düzelt, sonra devam et.

---

## Görev listesi

> **H0–H6 BİTTİ (2026-08-03).** Aşağıdaki maddeler geçmiş kayıt olarak duruyor;
> ne istendiğini ve neyin neden yapıldığını gösteriyorlar. Sonuçlar ve plandan
> sapmalar `PLAN.md` → "9b. Faz H sonuçları" bölümünde.
>
> **Açık kalan tek iş:** Codex'in gerçekten bağlanıp bağlanmadığı ve görüntü
> bloğunu işleyip işlemediği. Bu makinede ölçülemedi (abonelik yok); kullanıcının
> kuzeni deneyecek. Sonuç gelince buraya ve `KULLANIM.md`'ye işlenecek —
> **o güne kadar hiçbir belgede "Codex destekleniyor" yazmayın.**
>
> Sırada `G` var (ekran çerçevesi), opsiyonel.

### H0 · Ölçüm (kod yazmadan) — ✅ bitti

Hepsi "yanlışsa tasarım değişir" sorusu. Bu projede A–F boyunca ölçüm üç kez
plan varsayımını çürüttü; atlama.

1. **Claude Code MCP araç sonucunda GÖRÜNTÜ alabiliyor mu?** H2'nin tamamı buna
   dayanıyor. Ölçme yolu: pcbridge'e geçici bir araç ekle (ya da küçük bir test
   sunucusu yaz), bilinen içerikli bir PNG döndür, `claude mcp add` ile bağla ve
   `claude -p "o aracı çağır ve gördüğünü söyle"` ile içeriği tarif ettir.
   **Bilinen içerik şart** — "evet görüyorum" demesi kanıt değil.
2. ~~**Codex için aynı soru.**~~ **YAPILAMIYOR** — abonelik yok (yukarıdaki
   uyarı). Onun yerine: `codex mcp add` ile yapılandırmanın doğru yazıldığını
   `codex mcp get pcbridge` ile doğrula, orada bırak. Tasarımı **Claude'un
   ölçümüne** göre kur; Codex'i sonradan gelen bir doğrulama say.
3. **Görüntünün jeton maliyeti.** 1920×1080 tam çözünürlük kaç jeton, 1280'e
   küçültülmüş kaç? `agy`'de 40 bin ölçülmüştü. H2'deki ölçek kararı buna bağlı.
4. **stdio taşıması mevcut `server.py` yapısıyla çalışıyor mu?**
   `mcp.run(transport="stdio")` denenecek. Dikkat: `/shot/<token>.png`,
   `/healthz` ve OAuth rotaları Starlette uygulamasında; stdio'da HTTP sunucusu
   **yok**, yani o rotalar da yok. Ne bozuluyor, çıkar.
5. **Claude Desktop** yapılandırma dosyasının yeri ve biçimi (bu makinede
   kurulu ve çalışıyor).

### H1 · stdio taşıması — ✅ bitti

- `python -m pcbridge.server --stdio` çalışsın. HTTP yolu **aynen kalsın** —
  Spark ve uzaktan erişim onun üstünde
- stdio'da **OAuth yok**: yetki süreç sınırının kendisi. Bu bir güvenlik
  gerilemesi ve belgede açıkça yazmalı (aşağıda)
- systemd birimi değişmiyor; stdio'yu istemci başlatır, servis değil
- `screen_capture`'ın `/shot` bağlantısı stdio'da üretilemez → H2'ye bağlı;
  H2 bitmeden stdio'da o araç ne dönecek, karar ver ve **sessizce boş dönme**

**Güvenlik kararı — kullanıcıya doğrulat:** bugün kapı üç katlı (Tailscale ağı +
OAuth + `desktop_unlock`). stdio ilk ikisini kaldırıyor; sunucuyu başlatabilen
her yerel süreç masaüstüne erişir, önünde yalnızca `desktop_unlock` kalır.
Bunu omuz silkerek geçme, kullanıcıya sor.

### H2 · Inline görüntü — ✅ bitti

- `screen_capture` görüntüyü **`Image` bloğu olarak** döndürsün
- **Metin kısmı KALSIN.** Monitör numarası, global ofset ve dönüşüm kuralı
  görüntüyle birlikte gitmeli; yoksa istemci koordinat hesabını yapamaz ve
  ikinci monitöre yapılan her tıklama 1920 piksel şaşar
- Ayar: `[server] inline_images`. **Spark görüntü bloğu gelince bozulur**, o
  yüzden tek sunucuda iki istemciyi barındırmanın yolu bu bayrak
- Ölçek kararı H0.3'e göre: tam çözünürlük doğruluk verir (ölçek 1:1 → koordinat
  hesabı sadece toplama), küçültme jeton kazandırır
- `/shot/<token>.png` yolu **kalsın** — insan için hâlâ değerli, telefondan
  bakmanın tek yolu

### H3 · İstemci kurulumu ve tanı — ✅ bitti (`connect.sh`)

- Kurulum komutlarını üreten bir yol: `install.sh`'e ekle ya da `connect.sh`
  yaz. Kullanıcı kopyalayıp yapıştırabilsin:
  - `claude mcp add pcbridge -- <DIR>/.venv/bin/python -m pcbridge.server --stdio`
  - `codex mcp add pcbridge -- <DIR>/.venv/bin/python -m pcbridge.server --stdio`
  - uzaktan: `codex mcp add pcbridge --url https://<host>/mcp` + `codex mcp login pcbridge`
- `doctor.sh`'e başlık: hangi istemcilerde kayıtlı (`claude mcp list`,
  `codex mcp list`), stdio başlatılabiliyor mu
- **Codex satırları "kayıtlı mı" der, "çalışıyor mu" DEMEZ.** Burada yalnızca
  yapılandırma doğrulanabiliyor; gerçek bağlantı denenmedi

### H4 · `computer_task`'in yeni yeri — ✅ bitti

- Docstring **dürüstleşsin**: gören bir istemci için gerekli değil. "Ekranı
  kendin görebiliyorsan `screen_capture` + `computer_batch` kullan; bunu uzun
  süren bir GUI işini arka plana atmak için kullan"
- Varsayılan sürücü bugün `antigravity` (`[desktop] computer_task_agent`).
  Antigravity artık kapsam dışı **ve** F canlı denemesinde görevi iki kez
  tamamlayamadı. **Öneri: `claude`.** Bu bir davranış değişikliği, kullanıcıya
  doğrulat
- `computer_task_model` / `computer_task_effort` de ajana göre; `claude`
  seçilirse agy'ye özel model adı (`gemini-3.6-flash`) geçersiz kalır.
  **Bu bugün bir hata:** `computer_task(agent="claude")` çağrısı, yapılandırılan
  model agy'ye ait olduğu için çözümlemede patlar. Düzelt

### H5 · Testler — ✅ bitti (206 + 97 + 312)

- `tests/test_e2e.py`: `inline_images` açık/kapalı şema farkı, stdio ile
  başlayan sunucudan `tools/list`
- `tests/test_desktop.py`: mevcut 312 kontrol bozulmasın
- Gerçek istemci testi (H0'ın kurduğu düzenek) elle, kullanıcıya haber vererek

### H6 · Belgeler ve commit — ✅ bitti

- `README.md`: konumlandırma değişiyor — "Gemini Spark için MCP" değil,
  "ajanların bağlandığı MCP". Güvenlik bölümüne **stdio'nun ağ katmanını
  kaldırdığı** açıkça yazılsın
- `KULLANIM.md`: istemci kurulum bölümü, `computer_task`'in yeni yeri.
  Codex adımları **"denenmedi"** notuyla yazılsın — kullanıcının kuzeni
  deneyecek, sonucu geri gelecek
- `config.example.toml`: yeni ayarlar yorumuyla
- `PLAN.md`'ye "Faz 7 sonuçları" — ölçümler ve sapmalar
- Bu dosyaya H0'dan çıkan makine gerçekleri
- Commit `faz H: ajan-bagimsiz MCP`, staged diff sırlara karşı taranır, push

### G · Ekran çerçevesi — opsiyonel, ertelendi

Kontrol açıkken ekran kenarında ince mavi-mor çerçeve (GNOME Shell eklentisi).
Görsel geri bildirim ve güvenlik göstergesi. **Yapılmadı ve acil değil**;
H bittikten sonra kullanıcı isterse.

---

## Komutlar

```bash
cd ~/Belgeler/Pcbridge

systemctl --user restart pcbridge          # kod degistiyse sart (CALISAN ISLERI OLDURUR)
journalctl --user -u pcbridge -f           # canli log
./doctor.sh                                # tani, 35 kontrol

./.venv/bin/python tests/test_models.py     # cozumleyici (sunucu gerekmez)
./.venv/bin/python tests/test_desktop.py    # masaustu (girdi GONDERMEZ, 312 kontrol)

# gercek cihazlarla (uinput'a yazar, AT-SPI okur):
PCBRIDGE_TEST_CAPTURE=1 PCBRIDGE_TEST_ATSPI=1 PCBRIDGE_TEST_BATCH=1 \
  ./.venv/bin/python tests/test_desktop.py

# e2e sunucu ayakta olmali VE parolayi ortamdan ister; vermezsen OAuth
# adimlari 401 doner ve testin bozuldugunu sanirsin.
export PCBRIDGE_TEST_PASSWORD="$(./.venv/bin/python -c 'import sys; sys.path.insert(0,"."); from pcbridge.config import load_config; print(load_config().password)')"
export PCBRIDGE_TEST_STATIC="$(./.venv/bin/python -c 'import sys; sys.path.insert(0,"."); from pcbridge.config import load_config; print(load_config().static_token or "")')"
./.venv/bin/python tests/test_e2e.py        # 189 gecer + 4 ATLA
```

Masaüstünü elle sürmek (ajan olmadan, sen bakarak):

```bash
./bin/pcb-shot --monitor 2          # PNG yazar, ofseti soyler -> Read ile BAK
./bin/pcb-do --dry-run '<json>'     # ayristirir, calistirmaz
./bin/pcb-do --force '[{"a":"click","x":2081,"y":357}]'
./.venv/bin/python -m pcbridge.cli.lock   # masaustu iznini kapat
```

Dosya düzenleme komutu önerirken `nano` **kullanma**; kullanıcının `edit` takma
adı var (`gnome-text-editor`), root için `edit admin:///yol`.

---

## Çalışma tarzı

- Kendi görev listeni çıkar, kullanıcıya onaylat, sonra başla
- **Her adımdan sonra fiilen test et.** Çalıştığını görmeden sonrakine geçme
- Ölçmediğin şeyi "çalışıyor" diye yazma. Bu projede "hata vermedi" kanıt
  sayılmıyor — `IdleMonitor`, ekran görüntüsü ya da pencere başlığıyla doğrula
- Bölüm bitince commit at
- Kullanıcı Türkçe konuşuyor; sen de Türkçe yanıtla
