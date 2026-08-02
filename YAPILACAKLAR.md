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
