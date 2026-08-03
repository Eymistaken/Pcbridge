# YAPILACAKLAR.md — pcbridge çalışma yönergesi

## Başla

Kullanıcı "YAPILACAKLAR.md'dekileri yap" dediyse sırayla şunu yap:

1. Bu dosyanın tamamını oku — makine gerçekleri ve kurallar burada
2. `UYGULAMA.md`'yi oku — ne inşa edileceği orada anlatılıyor
3. **A bölümü** için kendi görev listeni çıkar, kullanıcıya göster
4. Onay gelmeden kod yazma
5. Onaydan sonra adım adım ilerle, **her adımı fiilen test et**

Bölüm sırası A → B → C → D → E → F → G. Bir bölüm bitmeden diğerine geçme.

---

Kısa tutuldu; ayrıntı için:

| Dosya | Ne için |
|---|---|
| **`UYGULAMA.md`** | **Ne inşa edeceğin.** Bölüm bölüm anlatım — işe buradan başla |
| `PLAN.md` | Neden böyle tasarlandı, ölçüm sonuçları, somut yapılandırma örnekleri |
| `GELISTIRME.md` | Yeni MCP aracı ekleme kalıbı, protokol tuzakları |
| `KULLANIM.md` | Kullanıcıya dönük araç kataloğu |

---

## Proje

pcbridge, kullanıcının Linux masaüstünü telefondan (Gemini Spark → MCP) sürmesini
sağlayan kişisel bir MCP sunucusu. Şu an yaptığı: terminal ajanlarına
(Claude Code, Antigravity) prompt gönderiyor, tmux oturumlarını sürüyor, kabuk
komutu çalıştırıyor, dosya okuyup yazıyor. **Eklenmekte olan:** klavye/fare
kontrolü — gerçek anlamda computer use.

---

## Makine gerçekleri (ölçüldü, tahmin etme)

- **Zorin OS 18.1 Core** = Ubuntu 24.04 LTS + **GNOME Shell 46**, **Wayland**
  (X11'e geçmek seçenek değil, önerme)
- **İki monitör**, ikisi de 1920×1080, **ölçek 1.0** (kesirli ölçekleme yok):

  | Bağlantı | Konum | Kırpma kutusu | Not |
  |---|---|---|---|
  | DP-2 | x=0 | `(0, 0, 1920, 1080)` | **sol** → `monitor=1` |
  | DP-1 | x=1920 | `(1920, 0, 3840, 1080)` | **sağ**, **birincil** → `monitor=2` |

  Birincil monitör sağdaki. GNOME üst çubuğu ve `Super` menüsü orada beliriyor.
  Monitör numaralandırması **her zaman x konumuna göre soldan sağa**, "birincil
  önce" değil. Sıra `Mutter.DisplayConfig.GetCurrentState`'ten okunur.
- **Klavye düzeni Türkçe** (tam olarak **`tr+intl`**). uinput ham keycode
  gönderir → ASCII metin bozulur. Metin girişinde varsayılan yol
  **`wl-copy` + Ctrl+V** (`desktop/input.py` bunu uyguluyor).
- **Girdi katmanı `python-evdev` ile**, `dotool`/`ydotool` ile değil (ölçüm
  gerekçesi `PLAN.md` → "Faz 1 sonuçları"). Mutlak fare 3840×1080 tuvalin
  tamamına 1:1 eşleniyor, ölçüldü.
- `gnome-screenshot` 41.0-2build2 **kurulu ve çalışıyor** (3840×1080 birleşik
  tuval). Ekran görüntüsü **monitör başına kırpılır, ölçekleme kırpmadan
  sonra** gelir; her görüntü global ofsetini taşır (`desktop/capture.py`)
- **`Shell.Introspect` kapalı** — `GetWindows` "Access denied" veriyor (GNOME 46).
  Yani odaktaki pencerenin hangi monitörde olduğu dışarıdan okunamıyor;
  `monitor="focused"` yok. `monitor="window"` var ama koordinat üretmiyor
- Claude Code **v2.1.220**, Claude **Pro** planı · Antigravity CLI **1.1.9**,
  Google AI Pro
- İkisi de PNG okuyabiliyor (görsel işleme doğrulandı)
- **"Gemini görsel göremiyor" derken kastedilen tam olarak şu:** Spark'a giden
  **MCP function-response kanalı** araç sonucunda yalnızca metin taşıyor.
  Sınır kanalın, modelin değil — Gemini'nin görme yeteneği var ve Antigravity
  içindeki Gemini görüntü okuyabiliyor (yukarıdaki satır). Belgelerde bu ayrımı
  koru; kısa kesilirse F bölümünün (`computer_task`, görsel işi PNG okuyabilen
  yerel bir ajana devreder) mantığı anlaşılmaz hale geliyor
- **Erişilebilirlik ağacı dolu ve kullanılabilir** (ölçüldü): GTK4
  uygulamalarında rol/etiket/durum eksiksiz, `Action` ve `EditableText` var.
  Ama `get_extents` **koordinatları yanlış** — tıklama `Action.do_action` ile
  yapılır, koordinatla değil. `gi` venv'de yok, AT-SPI ayrı bir süreçte
  (sistem `python3`) çalışır
- **Spark ekranı `agent_run` üzerinden okuyabiliyor** — ölçüldü 2026-08-02,
  `~/.local/state/pcbridge/jobs/20260802-135923-87d0ea` (`kind:
  agent:antigravity`). Masaüstü kontrolü **kapalıyken** oldu: `screen_capture`
  reddedildi, Spark `agy`'ye ekran görüntüsü aldırıp okuttu. Yani F bölümünün
  mimarisi elle bir kez çalıştırılmış durumda
- **`[desktop] enabled = false` yalnızca masaüstü araçlarını kapatır.**
  `shell_run`, `agent_run`, `fs_*`, `tmux_*` bu kapıdan geçmez — kapalıyken de
  komut çalışır, uygulama açılır, `config.toml` okunabilir. Bu bilinçli
  (projenin amacı bu) ama belgede açıkça yazmalı; koruma engelleme değil,
  `audit.log`'a **iz bırakma**
- **uinput olayı `IdleMonitor`'ü SIFIRLIYOR** — ölçüldü: 104227 ms → 151 ms.
  Yani "kullanıcı makinede mi" kontrolü bir eylem dizisinin **içinde**
  yapılamaz; dizi kendi tuşunu kullanıcı sanar. Kontrol yalnızca dizi başında
- **GNOME overview açıkken (`super` sonrası) Wayland panosu bloklanıyor** —
  `wl-paste` 5 sn'de cevap vermedi, yani varsayılan `type` yolu orada **asılır**.
  Overview'da ham tuş yolu (`raw=true`) şart
- **Pencere öne alma: AT-SPI ve D-Bus yolları KAPALI.** `Component.grab_focus`
  GTK'da `atspi_error`, Electron'da `False`; `org.freedesktop.Application.Activate`
  `exit=0` dönüp hiçbir şey yapmıyor (sessiz başarısızlık). Çalışan tek yol
  GNOME'un kendi araması (`super` + ad + `Return`), **~6,5 saniye**
- **`systemctl --user stop/restart pcbridge` çalışan işleri de ÖLDÜRÜR.**
  `jobs.py` uzun süre tersini yazıyordu; ölçüldü 2026-08-03 ve yanlış çıktı.
  `start_new_session` oturum grubunu ayırıyor ama **cgroup'u değil**; iş
  servisin cgroup'unda kalıyor, `KillMode=control-group` hepsini alıyor.
  İkisi birden doğru: acil durdurma gerçekten çalışıyor **ve** kod
  değişikliğinden sonraki restart uzun bir ajan işini keser
- **Taze süreçte uinput maliyeti:** klavye 1,301 s + fare 1,306 s = **2,607 s**;
  ikisi önce yaratılıp **tek bekleme** paylaşılırsa **1,41 s** (fare olayının
  gerçekten geçtiği `IdleMonitor` ile doğrulandı: 57694 → 404 ms). Gerçek tuş
  basımı 0,030 s. `pcb-do` bu yüzden liste alıyor, tek eylem değil
- **AT-SPI Vesktop'un PENCERESİNİ görüyor ama İÇİNİ görmüyor.** `windows()`
  `'vesktop.bin' | '(41) Discord | Arkadaşlar'` döndürüyor (yani odak takibi ve
  `batch_check_focus` orada çalışıyor), ama `dump(target='vesktop.bin')`
  **0 düğüm**. Electron'un durumu bu; `computer_task`'in varlık sebebi
- **`agy` görebiliyor ve iş bağlamında çalışıyor:** sentetik bir PNG'deki kodu
  4,2 saniyede doğru okudu (`-p --output-format json`, TTY yok, `TERM=dumb`).
  Ama **`--print-timeout` varsayılanı 5 dakika** ve dolduğunda yaptığı işi atıp
  `status: ERROR` dönüyor — bir GUI görevi 239 saniyede buna tosladı. Komuta
  `--print-timeout 30m` eklendi
- **Ekran görüntüsü ajana pahalı:** tek görüntü ~40 bin girdi jetonu. Dört
  görüntülük bir tur 198 bin jetona çıktı

---

## ⚠️ Bu makinede test etmenin tehlikesi

**Sen pcbridge'in kontrol edeceği makinenin üzerinde çalışıyorsun.** uinput
tıklaması/tuşu odaktan bağımsız gider — yani bir `type` testi **senin çalıştığın
terminale** yazabilir ve Enter'a basabilir.

Girdi testlerinde kural:

1. Önce `gnome-text-editor` gibi bir boş pencere aç, testi **oraya** yap
2. Fare testinde önce `mousemove`, sonra ekran görüntüsü alıp konumu **doğrula**,
   ancak ondan sonra `click`
3. Kaçak döngü ihtimaline karşı acil durdurma: **`systemctl --user stop pcbridge`**
   (sanal klavye/fare pcbridge sürecinin içinde yaşıyor, süreç ölünce cihaz da
   yok oluyor — ayrı bir `dotoold`/`ydotoold` daemon'ı yok)
4. Uzun/tekrarlı girdi denemelerini kullanıcıya haber vermeden başlatma
5. **Tıklamadan sonra odağın kaydığını varsay.** Sonraki tuşlar artık başka bir
   pencereye gider

6. **Ekran görüntüsü BAYATLAR.** Görüntüye bakıp koordinat çıkardıktan sonra
   araya iş sokma; kullanıcı o sırada pencere değiştirmiş olabilir

> **Bu gerçekten oldu — 2026-08-02, E bölümü ölçümleri.** `move(920, 520)` +
> `click` yapıldı, oranın metin düzenleyici penceresi olduğu **varsayıldı,
> doğrulanmadı**. Tıklama masaüstüne düştü, odak oraya kaydı, ardından temizlik
> için gönderilen `ctrl+a` + `Delete` masaüstündeki **23 öğeyi çöpe gönderdi**.
> (Hepsi çöpten geri alındı, kalıcı kayıp yok.) İhlal edilen kural 2'ydi.
> Karşılığı koda girdi: `computer_batch` artık fare tıklamalarından sonra odağı
> doğruluyor ve kaymışsa **duruyor** (`[desktop] batch_check_focus`).

> **İkincisi — 2026-08-03, F bölümü canlı doğrulaması.** Ekran görüntüsü alındı,
> Vesktop öndeydi, `oneaura` sohbetinin koordinatı okundu. Sonra araya **69
> saniye** kod düzenlemesi girdi ve o sırada başka bir pencere öne geldi.
> Tıklama Vesktop'a değil ona düştü. **Odak koruması ötmedi** — çünkü odak
> zaten o pencereydeydi, *değişen* bir şey yoktu. Kural 2'nin daha sinsi hali:
> görüntü alınmıştı ama artık geçerli değildi. Karşılığı: `pcb-shot` çıktısına
> zaman damgası, `pcb-do`'ya koordinatlı eylemler için yaş kontrolü
> (`[desktop] agent_shot_max_age_seconds`, varsayılan 60 sn).

---

## Değişmez kurallar

**MCP araçları** (ayrıntı: `GELISTIRME.md`)

- Docstring ve `Field(description=…)` **İngilizce** — Gemini araç seçerken bunları
  okuyor. Kullanıcıya dönen metinler Türkçe
- Docstring "ne zaman kullanılır"ı söylesin, sadece "ne yapar"ı değil
- Dönüş tipi **`str`**. Çıktıyı `jobslib.tail_chars(metin, 4000)` ile kırp
- `readOnlyHint` / `destructiveHint` doğru işaretlensin
- **110 saniyeden uzun bloklama yok** — uzun işler `jm.start()` ile arka plana
- Yol parametreleri `_resolve_dir` / `_resolve_file` ile çözülsün

**Dokunma**

- `server.py` içindeki `MetadataNormalizer` ve `BasicAuthFormShim` — Google OAuth
  akışının çalışmasının tek sebebi bunlar. Gerekçesi `GELISTIRME.md`'de
- `auth.py`'ın OAuth mantığı — görev açıkça istemedikçe

**Güvenlik**

- `config.toml` parola ve statik token içeriyor. `.gitignore`'da, öyle kalsın.
  İçeriğini **loglama, ekrana basma, commit etme**
- Yeni masaüstü yetenekleri `[desktop] enabled = false` ile gelir. Varsayılanı
  değiştirme
- Her yeni ayar `config.example.toml`'a **yorumuyla** eklenir

---

## Komutlar

```bash
cd ~/Belgeler/Pcbridge

systemctl --user restart pcbridge          # kod degistiyse sart
journalctl --user -u pcbridge -f           # canli log
./doctor.sh                                # tani
./.venv/bin/python tests/test_models.py     # cozumleyici + ajan ayristirici (sunucu gerekmez)
./.venv/bin/python tests/test_desktop.py    # masaustu (sunucu gerekmez, girdi gondermez)

# e2e sunucu ayakta olmali VE parolayi ortamdan ister; vermezsen OAuth
# adimlari 401 doner ve testin bozuldugunu sanirsin (bir kez yasandi).
export PCBRIDGE_TEST_PASSWORD="$(./.venv/bin/python -c 'import sys; sys.path.insert(0,"."); from pcbridge.config import load_config; print(load_config().password)')"
export PCBRIDGE_TEST_STATIC="$(./.venv/bin/python -c 'import sys; sys.path.insert(0,"."); from pcbridge.config import load_config; print(load_config().static_token or "")')"
./.venv/bin/python tests/test_e2e.py        # 111 gecer + 4 ATLA (gerekcesi testin icinde)

sudo ./setup_uinput.sh                      # masaustu kontrolu icin, BIR KEZ
```

Dosya düzenleme komutu önerirken `nano` **kullanma**; kullanıcının `edit` takma
adı var (`gnome-text-editor`), root için `edit admin:///yol`.

---

## Çalışma tarzı

- `UYGULAMA.md`'yi oku, kendi görev listeni çıkar, kullanıcıya onaylat, sonra başla
- Bölüm sırası **A → B → C → D → E → F → G**. A bağımsız ve tek başına değerli;
  B olmadan C-D-E'nin anlamı yok; G tamamen opsiyonel
- **Her adımdan sonra fiilen test et.** Çalıştığını görmeden sonrakine geçme
- Bir bölüm bitince commit at (`faz A: model/effort secimi` gibi)
- Planla çelişen bir gerçekle karşılaşırsan: **uydurma, etrafından dolaşma.**
  Kullanıcıya söyle, `PLAN.md`'de ilgili bölümü gerekçesiyle düzelt, sonra devam et
- Kullanıcı Türkçe konuşuyor; sen de Türkçe yanıtla
