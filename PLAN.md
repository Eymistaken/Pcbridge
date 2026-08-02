# PLAN — pcbridge'e tam teşekküllü Computer Use eklemek

**Durum:** tasarım onaylandı, Faz 0 ölçümleri tamam, uygulama bekliyor.
**Uygulayıcı için:** bu dosya *gerekçe ve ölçüm kaydı*. Ne inşa edileceği
`UYGULAMA.md`'de anlatılıyor, çalışma kuralları `YAPILACAKLAR.md`'de.
**Hedef sistem:** Zorin OS 18.1 Core (Ubuntu 24.04 LTS tabanlı, GNOME Shell 46, Wayland).
**Tarih:** 2026-08-01

---

## 1. Kısa cevap

**Evet, eklenebilir.** Klavye + fare kontrolü, ekran görüntüsü ve pencere yönetimi
Wayland altında da mümkün. Ama tasarımı belirleyen **üç sert kısıt** var ve bunlar
"Anthropic'in computer use aracını kopyala yapıştır" yaklaşımını doğrudan
elinden alıyor:

| # | Kısıt | Sonuç |
|---|---|---|
| 1 | **Gemini, MCP araç sonucundaki görselleri göremiyor.** Function response'lar yalnızca metin / yapılandırılmış metin destekliyor. | Klasik "ekran görüntüsü gönder → model baksın → tıklasın" döngüsü Spark üzerinden **kurulamaz**. Ekranı modele **metin** olarak anlatmak gerekiyor. |
| 2 | **Wayland, harici süreçlerin girdi enjekte etmesini ve ekran okumasını engelliyor.** GNOME'da `xdotool` yalnızca XWayland pencerelerini görür, `wtype` wlroots ister (Mutter'da çalışmaz). | Girdi için çekirdek seviyesine (`/dev/uinput`) inmek, ekran için portal/kabuk API'si kullanmak şart. |
| 3 | **Spark her yazma işleminde onay soruyor + istek ~110 s'de zaman aşımına uğruyor.** | Tek tek tıklama göndermek kullanılamaz (her tık için telefonda onay). Eylemler **toplu (batch)** çalıştırılmalı. |

### 1.1 Ölçüm günlüğü — 2026-08-01

Makinede fiilen doğrulananlar (tahmin değil):

| Bulgu | Sonuç |
|---|---|
| `gnome-screenshot` 41.0-2build2, GNOME 46 Wayland | ✅ çalışıyor, `3840x1080`, 2,5 MB gerçek yakalama |
| **`claude -p` non-interactive, `Read` ile PNG** | ✅ **görüyor** — ekrandaki 15 haneli rastgele kodu birebir okudu. Planın taşıyıcı varsayımı sağlam |
| **`agy -p "@dosya.png …"`** | ✅ **görüyor** — aynı kodu okudu. `@yol` sözdizimi çalışıyor |
| `agy` çıktısı boruya (`\| cat`) yazıldığında | ✅ **geliyor** — upstream #76 (TTY olmadan boş çıktı) 1.1.9'da düzelmiş görünüyor → `pty = true` artık gerekmeyebilir |
| İki monitör: DP-2 (x=0, sol) + DP-1 (x=1920, sağ, **birincil**) | ⚠️ numaralandırma soldan sağa yapılacak (§2.5) |
| Kesirli ölçekleme | ✅ **yok**, her iki monitör `scale = 1.0` → koordinat matematiği 1:1 |
| İmlecin hangi monitörde olduğunu dışarıdan sormak | ❌ Wayland'de mümkün değil (GNOME `Shell.Eval` kapalı) → varsayılan `monitor="all"` |

**Bunun en büyük sonucu: iki görsel sürücümüz var.** Claude Code (Claude Pro
kotası) ve Antigravity (Google AI Pro kotası) — ikisi de ekran görüntüsü okuyup
GUI sürebilir, kotaları birbirinden bağımsız. Uzun GUI oturumlarında biri
tükenirse diğerine geçilebilir.

Bu üçü birlikte, "doğru" mimariyi şuraya itiyor:

> Ekranı modele **metin olarak** (erişilebilirlik ağacı + OCR) anlat, eylemleri
> **toplu** çalıştır, gerçekten göze ihtiyaç duyan işi **makinedeki Claude Code'a
> devret** (o PNG dosyasını okuyabiliyor, yani gözü var).

Yani pcbridge'in mevcut felsefesi (`agent_run` ile işi yerel ajana devret) burada
da en güçlü çözüm oluyor — computer use, `agent_run`'ın rakibi değil **eli**.

---

## 2. Araştırma bulguları — neyin ne olduğu

### 2.1 Girdi (klavye/fare) enjeksiyonu

| Yöntem | GNOME 46 Wayland'de çalışır mı | Not |
|---|---|---|
| `xdotool` | ❌ (yalnız XWayland) | Native GTK/GNOME pencerelerinde etkisiz |
| `wtype` | ❌ | `virtual-keyboard-unstable-v1` ister; Mutter desteklemiyor |
| **`ydotool` / `dotool` (uinput)** | ✅ | Çekirdek seviyesinde sanal cihaz; kompozitörden bağımsız. **Seçilen yol.** |
| XDG `RemoteDesktop` portalı + libei | ✅ ama | Her oturumda GNOME onay penceresi çıkar. Kalıcı izin (`persist_mode`) portal **1.21+** ile geldi; Ubuntu 24.04'te 1.18 var → **telefondan kullanım için uygun değil**, makine başında birinin "İzin Ver" demesi gerekir |
| `gnome-remote-desktop` (RDP) | ✅ | Ağır; ayrı bir RDP istemcisi + oturum yönetimi gerekir. Yalnızca "sanal ekran" senaryosu için mantıklı |

> ⚠️ **Aşağıdaki iki aday da kullanılmadı.** Ölçüm sonrası `python-evdev` seçildi:
> makinede zaten kurulu, derleme/daemon istemiyor ve ABS eksenini bizim
> tanımlamamıza izin verdiği için çift monitör riskini kapatıyor. Gerekçe:
> **"Faz 1 sonuçları — ölçüldü 2026-08-01 (B bölümü)"** bölümü, 2. madde.

**Karar: uinput.** İki aday var:

- **`ydotool`** — Ubuntu 24.04 deposundaki sürüm **0.1.8** (eski). `mousemove --absolute`
  ve yeni CLI 1.0.x ile geldi → **kaynaktan 1.0.4 derlemek gerekir**.
- **`dotool`** — Go ile yazılmış, stdin'den komut okur, **klavye düzenini bilir**
  (`DOTOOL_XKB_LAYOUT=tr`). Aşağıdaki Türkçe klavye sorununu kökten çözdüğü için
  **birinci tercih**.

> ⚠️ **Türkçe klavye tuzağı.** uinput tabanlı araçlar ham *keycode* gönderir; ne
> yazılacağını sistemin XKB düzeni belirler. Sistem Türkçe Q düzenindeyken
> ydotool'a `type "user@example.com"` dedirtirsen ekrana `user"example.com`
> benzeri bir şey düşer. Üç çözüm var, üçünü de uygulayacağız:
> 1. `dotool` + `DOTOOL_XKB_LAYOUT=tr` (doğru yol),
> 2. metin girişinde **panoya kopyala + Ctrl+V** (`wl-copy`) — düzenden tamamen bağımsız, uzun metinlerde ayrıca çok daha hızlı,
> 3. `key` komutlarında düzenden etkilenmeyen tuşları (Tab, Enter, ok tuşları, F-tuşları) tercih et.

### 2.2 Ekran görüntüsü

| Yöntem | Durum |
|---|---|
| `gnome-screenshot -f x.png` | ✅ **ÖLÇÜLDÜ — çalışıyor.** `41.0-2build2` (noble/universe), Zorin 18.1 + GNOME 46 Wayland: çıkış 0, `3840 x 1080` RGBA, 2,5 MB → gerçek yakalama, siyah kare değil |
| `gdbus … org.gnome.Shell.Screenshot` | GNOME 41+ ile özel API kısıtlandı; **güvenilmez** |
| XDG `Screenshot` portalı | Her çağrıda kullanıcı onayı → otomasyona uygun değil |
| **XDG `ScreenCast` portalı + PipeWire** | ✅ İlk seferde onay, sonra `restore_token` ile sessiz. Ubuntu 24.04'ün portal 1.18'i ScreenCast kalıcılığını **destekliyor**. En sağlam yol, en çok emek isteyen yol |
| GNOME Shell eklentisi + kendi D-Bus'ın | ✅ Kompozitörün içinde çalıştığı için kısıtsız. Bir kez logout gerekir |
| `grim` | ❌ wlroots-only |

**Karar:** birincil yol **`gnome-screenshot`** (ölçüldü, çalışıyor). Kod yine de
backend soyutlamasıyla yazılır — paket GNOME 49'da bozulmuş görünüyor, yani bir
gün dağıtım yükseltmesinde ScreenCast portalı yedeğine geçmek gerekebilir. Ama
**Faz 2 bugün için yarım günlük iş**, PipeWire yazmaya gerek yok.

`gnome-screenshot` notları:

- Tüm masaüstünü yakalar; `-w` odaktaki pencere, `-p` imleci de dahil eder
- `-a` (alan seçimi) **interaktif**, otomasyonda kullanılamaz → kırpma bizde
- Kullanıcının grafik oturumu içinden çalışmalı; systemd birimine oturum
  ortamının aktarılması şart (§3, systemd düzeltmesi)

### 2.5 Çok monitörlü kurulum — koordinat tuzağı

**Ölçüm sonucu: ekran görüntüsü `3840 x 1080`**, yani yan yana iki 1920×1080
monitör tek bir tuval olarak yakalanıyor. Bu, computer use açısından iki ayrı
sorun doğuruyor:

**1. Model için okunamaz.** 3840×1080'i uzun kenardan 1280'e indirince her
monitör ~640×180 kalıyor; buton yazıları okunmaz hâle geliyor. 3,55:1 en-boy
oranı görsel modeller için de kötü.
→ **Çözüm:** `screen_capture(monitor=…)`, tuval her zaman monitör başına
kırpılır. Kırpma Pillow ile, ölçekleme kırpmadan sonra.

**Hangi monitör varsayılan olsun?** İmleç sorulamıyor (Wayland kapalı), odaktaki
pencereye güvenmek de kırılgan: masaüstündeyken odakta pencere yok ve pencereyi
odağa almak için ona tıklamak gerekiyor — otomasyonda tavuk-yumurta.

Bu yüzden **varsayılan `monitor="all"`**: her monitör **ayrı bir görüntü** olarak
üretilir (2 × 1920×1080 → her biri 1280×720'e inince hâlâ rahat okunur). Görsel
sürücü ikisine birden bakar, tahmin yok. Diğer seçenekler ek olarak durur:

| `monitor=` | Davranış |
|---|---|
| `"all"` (varsayılan) | Her monitör ayrı görüntü + her birinin global ofseti |
| `1`, `2` | Tek monitör |
| `"focused"` | `Shell.Introspect.GetWindows()`'tan odaktaki pencerenin monitörü; odak yoksa `default_monitor`'a düşer |
| `"window"` | Yalnızca odaktaki pencere (`gnome-screenshot -w`) — dar, hızlı, tek pencerede çalışırken |

`config.toml` → `[desktop] default_monitor = 1` ile "odak yok" durumundaki
davranış sabitlenir.

ℹ️ **Odak, GUI'yi sürmek için gerekli değil.** uinput tıklaması odaktan bağımsız
çalışır; bir pencereye tıklamak zaten onu odağa alır. Odak yalnızca "hangi dar
alanı yakalayayım" optimizasyonu için anlamlı, temel akış için değil.

**2. Sessizce 1920 piksel sola tıklama.** İkinci monitörden kırpılmış bir
görüntüye bakıp "buton (300, 400)'de" denince, gerçek tıklama koordinatı
(300+1920, 400) olmalı. Ofset unutulursa hata **hiç görünmez** — sadece yanlış
yere tıklanır.
→ **Çözüm:** kırpılmış her görüntünün yanında ofseti taşı; `pcb-do` /
`mouse` araçlarının kabul ettiği koordinatlar **her zaman global masaüstü
uzayında** olsun. Model monitör-yerel koordinat verirse `monitor=` parametresiyle
birlikte verir, dönüşümü pcbridge yapar. Tek kaynak:

```bash
gdbus call --session --dest org.gnome.Mutter.DisplayConfig \
  --object-path /org/gnome/Mutter/DisplayConfig \
  --method org.gnome.Mutter.DisplayConfig.GetCurrentState
```

Bu çağrı her mantıksal monitörün `x, y, genişlik, yükseklik, ölçek` değerlerini
veriyor. `screen_info` bu tabloyu döndürür, `capture` da kırpmayı buradan yapar.

**Ölçülen geometri (2026-08-01):**

| Bağlantı | Konum | Boyut | Ölçek | Birincil | Kırpma kutusu |
|---|---|---|---|---|---|
| **DP-2** | x=0, y=0 | 1920×1080 | 1.0 | hayır | `(0, 0, 1920, 1080)` |
| **DP-1** (ASUS VG247Q1A @165 Hz) | x=1920, y=0 | 1920×1080 | 1.0 | **evet** | `(1920, 0, 3840, 1080)` |

✅ **Kesirli ölçekleme yok** — her iki monitör de `scale = 1.0`. Mantıksal ve
fiziksel piksel birebir örtüşüyor, `gnome-screenshot`'ın 3840×1080 tuvali global
koordinat uzayının aynısı. Dönüşüm matematiği 1:1, en kötü tuzak kapandı.

⚠️ **Birincil monitör SOLDA DEĞİL, SAĞDA.** "Monitör 1 = birincil" ya da
"listedeki ilk monitör" gibi doğal görünen bir numaralandırma, kullanıcı "birinci
ekran" derken sağdaki ekranı seçerdi. Bu yüzden:

> **Numaralandırma kuralı: monitörler `x` konumuna göre soldan sağa sıralanır.**
> `monitor=1` → DP-2 (sol), `monitor=2` → DP-1 (sağ).
> Ayrıca `monitor="DP-1"` (bağlantı adı) ve `monitor="primary"` da kabul edilir.
> Sıra `GetCurrentState`'ten her seferinde yeniden hesaplanır — monitör takılıp
> çıkarıldığında ya da yerleri değiştirildiğinde kendiliğinden düzelir.

ℹ️ **GNOME kabuğu birincil monitörde**: üst çubuk, Etkinlikler ve `Super`'a
basınca açılan uygulama ızgarası **sağ ekranda** (DP-1) beliriyor. Görsel sürücüye
bu bilgi `screen_info` üzerinden verilmeli, yoksa `Super`'a basıp sol ekranda
menü arar ve "hiçbir şey olmadı" sanır.

⚠️ **uinput mutlak fare, çift monitörde test edilmeli.** Sanal işaretçi cihazının
ABS ekseni tüm 3840 px'i mi kapsıyor yoksa yalnızca birincil monitörü mü,
denemeden bilinmiyor. İkinci monitörün sağ alt köşesine tıklama testi Faz 1'in
ilk maddesi olacak.

### 2.3 Ekranı modele metin olarak anlatmak

Görsel gönderilemediğine göre iki kaynak kalıyor:

1. **AT-SPI erişilebilirlik ağacı** — GTK/GNOME uygulamaları arayüzlerini D-Bus
   üzerinden bir ağaç olarak yayınlıyor: her düğümün rolü (`push button`,
   `entry`, `menu item`), etiketi, durumu (`enabled`, `focused`, `checked`) var.
   Python'dan `gi.repository.Atspi` ile okunur. **Bu, ekranın metinsel ikizidir**
   ve piksel tahmininden kat kat güvenilirdir.
   - ⚠️ Wayland'de AT-SPI'ın **mutlak ekran koordinatları** güvenilmez olabiliyor.
     Çözüm: koordinat kullanmak yerine düğümün `Action` arayüzünü çağır
     (`do_action("click")`) — koordinat gerekmez, doğrudan tıklanır.
   - ⚠️ Electron uygulamaları (VS Code, Discord…) `--force-renderer-accessibility`
     olmadan ağaç yayınlamaz. Bunlarda OCR + koordinat moduna düşülür.
2. **OCR** — ekran görüntüsü üzerinde `tesseract` (`tesseract-ocr-tur` +
   `tesseract-ocr-eng`), kelime bazlı kutu koordinatlarıyla (`tsv` çıktısı).
   AT-SPI'ın görmediği her şey için (oyunlar, canvas, Electron, uzak masaüstü).

### 2.4 Görüntüyü **insana** göstermek

Model göremiyor ama **sen görebilirsin**. pcbridge zaten HTTPS'ten yayında:
ekran görüntüsünü `~/.local/state/pcbridge/shots/` altına yazıp
`https://<host>/shot/<tek-kullanımlık-token>.png` bağlantısı döndüreceğiz.
Telefonda bağlantıya dokunursun, ekranı görürsün. Token kısa ömürlü (5 dk) ve
tek kullanımlık; OAuth'tan bağımsız olduğu için bağlantıyı paylaşma.

---

## 3. Mimari

```
                    Telefon / Spark  (metin dünyası)
                              │
                    ┌─────────┴──────────┐
                    ▼                    ▼
            ui_dump / ui_click    computer_task
            (metinsel gözler)     (görsel işi yerel ajana devret)
                    │                    │
                    ▼                    ▼
        ┌───────────────────────┐   claude -p  (PNG okuyabilir → gözü var)
        │  pcbridge/desktop.py  │        │
        │  ─────────────────────│◄───────┘  (aynı araçları CLI üzerinden kullanır)
        │  InputBackend  ──► dotool/ydotool ──► /dev/uinput
        │  CaptureBackend ─► gnome-screenshot | portal+PipeWire | shell ext.
        │  UiTree        ──► AT-SPI (D-Bus)
        │  Ocr           ──► tesseract
        │  SafetyGate    ──► kilit ekranı / idle / süreli izin / denetim kaydı
        └───────────────────────┘
```

### Yeni dosyalar

| Dosya | İçerik |
|---|---|
| `pcbridge/desktop/__init__.py` | Ortak arayüzler, backend seçimi (autodetect) |
| `pcbridge/desktop/input.py` | `dotool`/`ydotool` sarmalayıcı: move, click, drag, scroll, key, type, paste |
| `pcbridge/desktop/capture.py` | Ekran görüntüsü backend zinciri + ölçekleme + PNG yazma |
| `pcbridge/desktop/uitree.py` | AT-SPI ağacı → düz metin + kararlı `#id` üretimi + `click_by_id` |
| `pcbridge/desktop/ocr.py` | tesseract TSV → kelime/kutu listesi, `find_text()` |
| `pcbridge/desktop/windows.py` | Pencere listesi/odak (GNOME Introspect D-Bus veya AT-SPI'dan türetilmiş) |
| `pcbridge/desktop/safety.py` | İzin penceresi, kilit/idle kontrolü, hız sınırı, denetim kaydı |
| `pcbridge/shots.py` | `/shot/<token>.png` HTTP rotası, tek kullanımlık token |
| `extensions/pcbridge-frame@local/` | GNOME Shell eklentisi: ekran kenarı gradyan çerçevesi + D-Bus (§7.1) |
| `desktop_doctor.sh` | Faz 0 tanı betiği (aşağıda) |
| `tests/test_desktop.py` | Sanal ekranda (Xvfb/weston-headless) uçtan uca test |

### Değişecek dosyalar

- `pcbridge/tools.py` — yeni araçlar (aşağıdaki tablo)
- `pcbridge/config.py` + `config.example.toml` — `[desktop]` bölümü
- `pcbridge/server.py` — `/shot/...` rotası
- `systemd/pcbridge.service` — **grafik oturum ortamı** (aşağıda, kritik)
- `install.sh` — uinput izinleri, `ydotoold`/`dotoold` kullanıcı servisi, bağımlılıklar
- `doctor.sh`, `README.md`, `KULLANIM.md`, `GELISTIRME.md`

### systemd düzeltmesi (kritik)

> ⚠️ **Ölçüldü: bu makinede gerekmiyordu ve aşağıdaki hâliyle uygulanmadı.**
> Oturum ortamı birime zaten geliyor; `WantedBy`/`PartOf` değişiklikleri ise
> projenin "açılışta otomatik başlama" kararıyla çelişiyor. Uygulanan hâli ve
> gerekçesi: **"Faz 1 sonuçları"** bölümü, 3. madde.

Mevcut birim yalnızca `Environment=DISPLAY=:0` veriyor; Wayland'de bu yetmez.
Servis, grafik oturumun ortam değişkenlerini görmeden ne uinput'a ne D-Bus'a
ne de AT-SPI'a ulaşabilir:

```ini
[Unit]
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Environment=XDG_SESSION_TYPE=wayland
# WAYLAND_DISPLAY, DBUS_SESSION_BUS_ADDRESS, XDG_RUNTIME_DIR oturumdan gelir:
#   ~/.config/autostart içinde ya da oturum açılışında bir kez
#   systemctl --user import-environment WAYLAND_DISPLAY XDG_SESSION_TYPE XDG_CURRENT_DESKTOP
#   dbus-update-activation-environment --systemd --all
```

`WantedBy=default.target` → `WantedBy=graphical-session.target` yapılacak (servis
zaten elle başlatılıyor; bu yalnızca ortamın doğru gelmesi için).

---

## 4. Yeni MCP araçları

Proje kuralı gereği: **docstring'ler İngilizce**, dönüş tipi `str`, çıktı
kırpılmış, 110 s'den uzun bloklama yok, `readOnlyHint`/`destructiveHint` doğru.

| Araç | Hint | Ne yapar |
|---|---|---|
| `screen_info()` | readOnly | Çözünürlük, monitörler, ölçek, odaktaki pencere, imleç konumu, hangi backend'lerin hazır olduğu |
| `screen_capture(region, scale, with_ocr)` | readOnly | Ekran görüntüsü alır; **kısa ömürlü HTTPS bağlantısı** + istenirse OCR metni döner |
| `ui_dump(app, max_nodes, interactive_only)` | readOnly | AT-SPI ağacını metin olarak döker: `#12 push button "Kaydet" [enabled]`. **Modelin gözü budur** |
| `ui_click(id \| label, button, double)` | destructive | `ui_dump`'taki bir düğüme tıklar (önce AT-SPI action, olmazsa koordinat) |
| `ui_set_text(id, text)` | destructive | Bir metin kutusunu doğrudan doldurur (klavye düzeni sorunundan tamamen bağımsız) |
| `screen_find_text(query)` | readOnly | OCR ile ekranda metin arar, kutu merkezlerini döner |
| `mouse(action, x, y, button, clicks)` | destructive | move / click / double / right / drag / scroll — ham koordinat modu |
| `keyboard(action, text \| keys)` | destructive | `type` (pano-yapıştır varsayılan), `key` (`ctrl+shift+t`), `hold`/`release` |
| `computer_batch(actions, final)` | destructive | **En önemli araç.** Eylem listesini sırayla çalıştırır, aralara `wait` koyar, sonunda `ui_dump`/`screen_capture` döner. Spark'ta tek onay = birçok tık |
| `window_list()` / `window_focus(id)` | readOnly / destructive | Açık pencereler, odaklama |
| `desktop_unlock(minutes, reason)` | destructive | GUI kontrolünü süreli açar; makinede masaüstü bildirimi + denetim kaydı |
| `desktop_lock()` | — | Süre dolmadan kapatır |
| `computer_task(goal, app, max_steps)` | destructive | **Görsel işi yerel Claude Code'a devreder**: hedefi verirsin, o ekran görüntülerini kendi gözüyle okuyup adım adım yürütür, `job_id` döner |

### `computer_batch` neden bu kadar önemli

Spark her `destructiveHint` araç çağrısında telefonda onay soruyor. Bir menüden
tek bir öğe seçmek 4-5 çağrı ediyor → 5 onay, 5 × ağ gecikmesi. `computer_batch`
ile:

```json
{"actions": [
  {"a": "key",   "keys": "super"},
  {"a": "wait",  "ms": 400},
  {"a": "type",  "text": "libre"},
  {"a": "wait",  "ms": 600},
  {"a": "key",   "keys": "Return"},
  {"a": "wait",  "ms": 3000}
], "final": "ui_dump"}
```

→ tek onay, tek istek, sonunda ekranın metinsel hâli. Toplam süre 110 s
sınırının altında tutulur; aşarsa iş otomatik olarak arka plan job'ına dönüşür.

---

## 5. Ajan modeli ve effort seçimi

### 5.1 Şu an ne oluyor?

`config.toml`'daki komutta `--model` **yok**:

```toml
command = ["claude", "-p", "{prompt}", "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions"]
```

Yani model seçimi Claude Code'un kendi öncelik sırasına kalıyor:

1. `--model` bayrağı (yok)
2. `ANTHROPIC_MODEL` ortam değişkeni (systemd biriminde yok — temiz)
3. Ayar dosyasındaki `model` alanı → **`~/.claude/settings.json`**

Makinede doğrulandı: **Claude Code v2.1.220, Claude Pro, "Opus 5 with xhigh
effort"**. Picker'daki modeller: Sonnet 5 (varsayılan/önerilen), Fable 5
(*ek kullanım kredisi ister*), Opus 5, Haiku 4.5. Effort: `low / medium / high /
xhigh / max`.

Claude Code v2.1.153'ten beri `/model` ile seçim yapıp **Enter**'a bastığında bu
seçim kullanıcı ayarlarına `model` olarak **yazılıyor**. Effort için de aynısı:
`low/medium/high/xhigh` interaktif oturumda seçilince kalıcı (`effortLevel`).

**Sonuç: evet, telefondan gönderdiğin işler şu an en son seçtiğin modelle
(Opus 5, xhigh effort) çalışıyor** — meğer ki model picker'da `Enter` yerine `s`
("sadece bu oturum") demiş olasın.

**Bunu tahmin etmene gerek yok, ölçebilirsin.** Mevcut parser `system/init`
olayındaki modeli zaten yazdırıyor; `job_status` çıktısındaki şu satır:

```
· oturum baslatildi (model: claude-opus-5, dizin: /home/…)
```

Ayrıca doğrudan bakabilirsin:

```bash
grep -E '"(model|effortLevel)"' ~/.claude/settings.json
```

⚠️ **Dikkat edilecek iki nokta:**

- İşler `os.environ.copy()` ile başlıyor (`jobs.py:112`), yani **systemd
  biriminin ortamı ajanlara aynen geçiyor**. Birime `ANTHROPIC_MODEL` veya
  `CLAUDE_CODE_EFFORT_LEVEL` eklenmemeli — `CLAUDE_CODE_EFFORT_LEVEL` *her şeyin
  üstünde* önceliğe sahip ve `--effort` bayrağını sessizce etkisiz kılar.
- Interaktif oturumda `/model` ile modeli değiştirirsen, **telefondan gelen işler
  de o modele geçer** (ikisi aynı ayar dosyasını okuyor). Bu sürpriz istemiyorsan
  aşağıdaki 5.2'yi uygula: pcbridge her çağrıda modeli açıkça belirtsin.

### 5.2 Değiştirilebiliyor mu? Evet — ve eklemeliyiz

Her ikisi de **oturuma özel** bayraklarla verilebiliyor; kalıcı ayarına dokunmaz:

| Ne | Nasıl | Değerler |
|---|---|---|
| Model | `claude --model <takma ad \| tam isim>` | `opus`, `sonnet`, `haiku`, `fable`, `best`, `opusplan`, `opus[1m]`, `sonnet[1m]` veya `claude-opus-5` gibi tam isim |
| Effort | `claude --effort <seviye>` | `low`, `medium`, `high`, `xhigh`, `max`, `ultracode` |

Opus 5 için effort seviyeleri: `low/medium/high/xhigh/max`. Varsayılan `high`
(yani sen `xhigh`'a **bilerek** çekmişsin). `max` ve `ultracode` yalnızca oturumluk;
`ultracode` = `xhigh` + dinamik workflow orkestrasyonu, Claude Code ≥ v2.1.203 ister.
Tek seferlik derin düşünme için prompt'un içine `ultrathink` yazmak da yeterli —
effort ayarını değiştirmez.

**Tasarım: kod değil, yapılandırma.** Mevcut `{prompt}` / `{session_id}` şablon
mantığını genişletiyoruz, böylece her ajan kendi bayrak sözdizimini, model
listesini, varsayılanlarını ve takma adlarını tanımlar. Kod tarafında tek bir
**çözümleyici** (resolver) var; kurallar `config.toml`'da yaşıyor.

`AgentSpec`'e eklenecek alanlar:

| Alan | Ne işe yarar |
|---|---|
| `model_args` / `effort_args` | Bayrak sözdizimi (`["--model", "{model}"]`) |
| `models` | Serbestçe seçilebilen modeller |
| `restricted_models` | **Yalnızca açıkça istenirse** seçilebilenler (varsayılan seçimde asla) |
| `blocked_models` | Hiçbir koşulda seçilemeyenler |
| `efforts` | O ajanın kabul ettiği effort seviyeleri |
| `default_model` | Model belirtilmezse |
| `model_effort` | **Model başına** varsayılan effort (`sonnet="medium"`, `opus="high"`) |
| `effort_required_with_model` | Model verilince effort de zorunlu mu (agy: evet) |
| `aliases` | Serbest metin → kanonik ad eşlemesi |

### 5.2.1 Senin kuralların

Söylediklerin doğrudan yapılandırmaya çevriliyor:

| Kural | Karşılığı |
|---|---|
| Claude tarafında **asla Fable** | `blocked_models = ["fable", "best"]` — `best` takma adı Fable'a çözülüyor, o yüzden o da kapalı |
| Varsayılan **Sonnet 5 + medium** | `default_model = "sonnet"`, `model_effort.sonnet = "medium"` |
| "opus" dersem → **Opus 5 + high** | `model_effort.opus = "high"` |
| "extra" dersem → **xhigh** | `aliases."extra" = "xhigh"` (ayrıca `"maksimum"/"max"` → `max`) |
| Antigravity'de **yalnızca Gemini** | Gemini'ler `models`'ta; Claude 4.6'lar ve GPT-OSS `restricted_models`'ta |
| Antigravity varsayılanı **Gemini 3.6 Flash + high** | `default_model = "gemini-3.6-flash"`, `model_effort."gemini-3.6-flash" = "high"` |

```toml
[agents.claude]
command = ["claude", "-p", "{prompt}", "--output-format", "stream-json",
           "--verbose", "--dangerously-skip-permissions"]
resume_args = ["--resume", "{session_id}"]
parser = "claude_stream_json"

# YENİ
model_args  = ["--model", "{model}"]
effort_args = ["--effort", "{effort}"]
default_model = "sonnet"
models  = ["sonnet", "opus", "haiku"]
blocked_models = ["fable", "best"]        # ASLA. "best" -> Fable'a cozuluyor
efforts = ["low", "medium", "high", "xhigh", "max"]
effort_required_with_model = false

[agents.claude.model_effort]              # model basina varsayilan effort
sonnet = "medium"
opus   = "high"
haiku  = "medium"

[agents.claude.aliases]
"sonnet 5" = "sonnet"
"sonnet5"  = "sonnet"
"opus 5"   = "opus"
"opus5"    = "opus"
"haiku 4.5" = "haiku"
"dusuk" = "low"
"orta"  = "medium"
"yuksek" = "high"
"extra"  = "xhigh"                        # senin tercihin
"ekstra" = "xhigh"
"cok yuksek" = "xhigh"
"maksimum" = "max"
"en yuksek" = "max"
```

```toml
[agents.antigravity]
command = ["agy", "-p", "{prompt}", "--dangerously-skip-permissions"]
resume_args = ["--conversation", "{session_id}"]
parser = "plain"
pty = true

model_args  = ["--model", "{model}"]
effort_args = ["--effort", "{effort}"]
effort_required_with_model = true         # agy: model tek basina calismiyor (§5.4)
default_model = "gemini-3.6-flash"
efforts = ["low", "medium", "high"]       # agy'de xhigh/max YOK
models  = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-pro"]
# Asagidakiler yalnizca ACIKCA istenirse; varsayilan secimde asla kullanilmaz
restricted_models = ["claude-sonnet-4.6", "claude-opus-4.6", "gpt-oss-120b"]

[agents.antigravity.model_effort]
"gemini-3.6-flash" = "high"
"gemini-3.5-flash" = "high"
"gemini-3.1-pro"   = "high"

[agents.antigravity.aliases]
"gemini 3.6 flash" = "gemini-3.6-flash"
"3.6 flash" = "gemini-3.6-flash"
"flash"     = "gemini-3.6-flash"
"gemini 3.5 flash" = "gemini-3.5-flash"
"gemini 3.1 pro"   = "gemini-3.1-pro"
"pro"              = "gemini-3.1-pro"
"gpt"  = "gpt-oss-120b"
"gpt-oss" = "gpt-oss-120b"
```

> ⚠️ **Yukarıdaki `[agents.antigravity]` bloğu ÖLÇÜMDEN ÖNCEKİ tahmindir, aynen
> uygulanmadı.** `agy models` ile gerçek kimlikler alındı: `claude-sonnet-4.6`
> değil `claude-sonnet-4-6`, `claude-opus-4.6` değil `claude-opus-4-6-thinking`,
> `gpt-oss-120b` değil `gpt-oss-120b-medium`. Ayrıca effort listesi ajan başına
> değil **model başına** (`gemini-3.1-pro`'da medium yok; Claude/GPT-OSS
> modelleri `--effort` hiç kabul etmiyor). Uygulanan hâli için
> **"Faz 0 sonuçları"** bölümüne ve `config.example.toml`'a bak.

### 5.2.2 Çözümleyici — akış

```python
def agent_run(agent=None, prompt=..., workdir=None, resume_session=None,
              model: str | None = None,    # "Model name or alias, e.g. 'opus', 'gemini 3.6 flash'."
              effort: str | None = None,   # "Reasoning effort, e.g. 'high', 'xhigh'."
              wait_seconds=30, timeout=None) -> str
```

1. **Normalize** — `model`/`effort` küçük harfe çevrilir, noktalama/boşluk
   sadeleştirilir, `aliases` uygulanır. "Gemini 3.6 Flash", "gemini-3.6-flash",
   "3.6 flash" hepsi aynı yere gider.
2. **Ajanı çıkar** — `agent` verilmemişse: model hangi ajana aitse o seçilir;
   ait değilse `default_agent` (= `claude`). Bare "opus" → Claude Code Opus 5;
   Antigravity'nin Claude Opus 4.6'sı için `agent="antigravity"` demek gerekir.
3. **Politika kontrolü**
   - `blocked_models` → **reddet**, gerekçeyle ("Fable devre dışı: PLAN §5.2.1")
   - `restricted_models` → yalnızca kullanıcı adı açıkça verdiyse geçer;
     varsayılan/otomatik seçim buraya asla düşemez
   - listede olmayan ad → reddet, geçerli listeyi göster
4. **Varsayılanları doldur** — model boşsa `default_model`; effort boşsa
   `model_effort[model]`, o da yoksa `default_effort`, o da yoksa boş bırak.
5. **Kırp (clamp)** — istenen effort o ajanda yoksa **en yakın alt seviyeye**
   indirilir ve bu **çıktıda söylenir**. `agy` + `xhigh` → `high`, "xhigh
   Antigravity'de yok, high kullanıldı" notu. (Claude Code bunu kendi de yapıyor;
   agy yapmıyor.)
6. **Zorunluluk** — `effort_required_with_model` ve effort hâlâ boşsa çağrı
   reddedilir (agy'nin sessizce varsayılan modele düşmesini engeller, §5.4).
7. **Komutu kur** — `model_args`/`effort_args` yalnızca değer varsa eklenir.
8. **Raporla** — iş özetinin ilk satırı: `ajan: claude · model: opus · effort: high`.
   İş bitince gerçekleşen değer (`modelUsage` / agy başlık satırı) ile karşılaştırılır;
   fark varsa **uyarı** basılır.

Böylece telefondan şunların hepsi çalışır:

| Ne dersen | Ne olur |
|---|---|
| "…yap" (model demezsin) | claude · sonnet · medium |
| "opus ile yap" | claude · opus · high |
| "opus, extra ile yap" | claude · opus · xhigh |
| "antigravity ile yap" | antigravity · gemini-3.6-flash · high |
| "antigravity üzerinden gemini 3.6 flash yüksek efor" | antigravity · gemini-3.6-flash · high |
| "antigravity ile 3.1 pro, düşük efor" | antigravity · gemini-3.1-pro · low |
| "antigravity ile claude opus" | antigravity · claude-opus-4.6 (açıkça istendi → izinli) |
| "fable ile yap" | **reddedilir**, gerekçe döner |

Diğer kurallar:

- `list_agents` çıktısına her ajanın model tablosu, varsayılanları ve effort
  listesi eklenir → Gemini neyin var olduğunu araç açıklamasından değil,
  **veriden** öğrenir
- `parse_claude_stream_json`, `result` olayındaki **`modelUsage`** alanını okuyup
  özete `model: claude-opus-5 · effort: xhigh` satırı ekler
- `--model` yalnızca **başlattığımız oturumu** etkiler; senin interaktif
  `/model` seçimin (Opus 5 · xhigh) bozulmaz
- Araç açıklamaları İngilizce kalır ama **değer listeleri açıklamaya gömülür**
  (`"Model: sonnet | opus | haiku | gemini-3.6-flash | ..."`) — Gemini'nin doğru
  değeri üretme şansı artar; yine de son söz yerel çözümleyicidedir

### 5.3 Neden bu önemli: maliyet

Telefondan atılan "şu klasörde ne var" tarzı bir iş Opus 5 + xhigh ile çalışırsa
gereksiz pahalı — üstelik Claude Pro'dasın, Opus 5 Sonnet'in ~2 katı kullanım
yakıyor. Varsayılanı Sonnet 5 + medium yapmak tam da bunu çözüyor; ağır işi
"opus" veya "opus, extra" diyerek bilinçli olarak seçiyorsun.

`job_status` çıktısı zaten `maliyet: $…` satırını gösteriyor, yani etkisini
ölçebilirsin. Ayrıca Fable 5 Pro planında **ek kredi** istediği için
`blocked_models`'ta — yanlışlıkla seçilip faturaya yansıması mümkün değil.

### 5.4 Antigravity — ölçüldü (agy 1.1.9)

> ⚠️ **Bu bölümün ölçümleri agy 1.1.5'e ait ve 1.1.9'da GEÇERSİZ.** Sessiz geri
> düşüş düzelmiş (artık exit 1), model kimlikleri farklı, effort model başına,
> `pty` gereksiz, açılış başlığı print modunda basılmıyor. Güncel ve uygulanan
> gerçekler: **"Faz 0 sonuçları — ölçüldü 2026-08-01"** bölümü (§6 altında).
> Aşağısı tarihsel kayıt olarak bırakıldı.

Evet, `agy` şu an da kullanılabilir: `agent_run(agent="antigravity", …)`.
`config.toml`'da tanımlı ve `pty = true` ile çıktı sorunu çözülmüş durumda.

**TUI'deki `/model` listesi (agy 1.1.9, Google AI Pro):**

| Görünen ad | Politika |
|---|---|
| Gemini 3.6 Flash | ✅ varsayılan (+ `high`) |
| Gemini 3.5 Flash | ✅ serbest |
| Gemini 3.1 Pro | ✅ serbest |
| Claude Sonnet 4.6 (Thinking) | 🔒 yalnızca açıkça istenirse |
| Claude Opus 4.6 (Thinking) | 🔒 yalnızca açıkça istenirse |
| GPT-OSS 120B (Medium) | 🔒 yalnızca açıkça istenirse |

Effort kaydırıcısı: `low / medium / high` (Claude Code'daki `xhigh` ve `max` yok).
Claude ve GPT-OSS satırlarındaki `(Thinking)` / `(Medium)` etiketleri bu modellerin
kendi sabit akıl yürütme kipleri olabileceğini düşündürüyor — `--effort` kabul
edip etmedikleri ayrıca denenmeli.

**Makinede test edildi (Antigravity CLI 1.1.5, Google AI Pro hesabı):**

```
$ agy --model "gemini-3.6-flash"
⚠ Warning
  --model gemini-3.6-flash requires --effort (available: low, medium, high).
  Using the default model instead.
```

`--effort "high"` eklenince çalışıyor. Yani agy'de **model ve effort ayrılmaz bir
çift**: modeli tek başına vermek işe yaramıyor.

Buradan çıkan üç sonuç plana giriyor:

**1. `agy`'de effort seviyeleri farklı.** `low / medium / high` — Claude Code'daki
`xhigh` ve `max` burada **yok**. İzin listeleri ajan başına ayrı tutulmalı (zaten
öyle tasarlandı, ama artık değerleri biliyoruz).

**2. Sessiz geri düşüş var — ve bu bir tuzak.** Yukarıdaki durum bir **hata
değil, uyarı**: `agy` çıkış kodu 0 ile, ama **istediğin modelden farklı bir
modelle** çalışmaya devam ediyor. Telefondan "gemini-3.6-flash ile yap" dersin,
iş sorunsuz görünür, aslında varsayılan modelle (Gemini 3.5 Flash) yapılmıştır.
Bu yüzden:

- `[agents.*]`'a `effort_required_with_model = true` alanı eklenir. Bu ajanda
  `model` verilip `effort` verilmemişse pcbridge, komutu göndermeden önce
  `default_effort`'ü koyar; o da yoksa **çağrıyı reddeder** ve neden reddettiğini
  söyler. Yanlış modelle sessizce çalışmaktansa açık hata iyidir.
- `plain` ayrıştırıcı çıktıda `Using the default model instead` / `requires
  --effort` kalıplarını arar; bulursa iş özetinin **en üstüne** uyarı basar.

**0. `pty = true` artık gerekmeyebilir.** 1.1.9'da `agy … -p "…" | cat` çıktıyı
sorunsuz veriyor (§1.1), yani projeyi kuran upstream #76 hatası düzelmiş
görünüyor. Faz 1'de dosyaya yönlendirerek de doğrula:

```bash
agy --model gemini-3.6-flash --effort high -p "sadece OK yaz" > /tmp/agy.txt 2>&1; wc -c /tmp/agy.txt
```

Boyut > 0 ise `pty = false` yapılabilir — `script` sarmalayıcısı kalkar, ANSI
gürültüsü azalır, ayrıştırma temizlenir. Şüphedeysen `true` bırakmak zararsız.

**3. Gerçekte hangi modelin çalıştığı çıktıdan okunabiliyor.** `agy` açılış
başlığında `Gemini 3.5 Flash (High)` yazıyor, alt bilgi çubuğunda da
`Gemini 3.5 Flash · high`. `pty = true` olduğu için bu satırlar iş kaydına
düşüyor → ayrıştırıcı bunu yakalayıp `model: … · effort: …` satırı olarak
özete koyar (Claude tarafındaki `modelUsage` ile aynı işlev).

Yapılandırma karşılığı:

```toml
[agents.antigravity]
command = ["agy", "-p", "{prompt}", "--dangerously-skip-permissions"]
resume_args = ["--conversation", "{session_id}"]
parser = "plain"
pty = true

# YENİ
model_args  = ["--model", "{model}"]
effort_args = ["--effort", "{effort}"]
effort_required_with_model = true          # agy 1.1.5: model tek basina calismiyor
default_model  = ""                        # bos = agy'nin kendi varsayilani (Gemini 3.5 Flash)
default_effort = "high"
efforts = ["low", "medium", "high"]        # agy'de xhigh/max YOK
models  = ["gemini-3.6-flash"]             # dogrulanan; asagidaki komutla tamamlanacak
```

Model **kimliklerinin** (CLI'a verilecek tam metin) doğrulanması gerekiyor;
yalnızca `gemini-3.6-flash` kesin biliniyor, diğerleri görünen addan türetilmiş
tahmin. Faz 0'da tek tek:

```bash
for m in gemini-3.5-flash gemini-3.1-pro claude-sonnet-4.6 claude-opus-4.6 gpt-oss-120b; do
  echo "--- $m"; agy --model "$m" --effort high -p "sadece OK yaz" 2>&1 | head -4
done
```

Çıktıda `requires --effort` / `Using the default model instead` görürsen o kimlik
yanlıştır. Doğru kimlikleri `config.toml`'daki `models` / `restricted_models`
listelerine geçir.

⚠️ Antigravity Claude Code'un ayar dosyalarını okumaz — kendi hesabı, kendi
kotası, kendi model listesi vardır. `[agents.*]` blokları birbirinden bağımsız.

---

## 6. Faz planı

### Faz 0 — Ölçüm (kod yazmadan önce, ~30 dk)

Bu plandaki birkaç madde "muhtemelen çalışır" seviyesinde. Önce makinede
ölçülecek. `desktop_doctor.sh` olarak kaydedip çalıştır:

```bash
#!/usr/bin/env bash
echo "== oturum ==";        echo "$XDG_SESSION_TYPE / $XDG_CURRENT_DESKTOP / GNOME $(gnome-shell --version 2>/dev/null)"
echo "== klavye düzeni =="; gsettings get org.gnome.desktop.input-sources sources
echo "== uinput ==";        ls -l /dev/uinput; id -nG | tr ' ' '\n' | grep -x input || echo "input grubunda DEGILSIN"
echo "== ydotool ==";       command -v ydotool && ydotool --version
echo "== claude modeli ==";  claude --version; grep -E '"(model|effortLevel)"' ~/.claude/settings.json 2>/dev/null || echo "settings.json'da model/effort alani yok"
echo "== agy modeli ==";     command -v agy && agy --version
# agy 1.1.5'te dogrulandi: --model tek basina YETMIYOR, --effort sart (low/medium/high)
echo "== agy model+effort =="; agy --model "gemini-3.6-flash" --effort "high" -p "sadece OK yaz" 2>&1 | tail -5
echo "== screenshot 1 ==";  gnome-screenshot -f /tmp/p0.png 2>&1; file /tmp/p0.png 2>/dev/null   # OLCULDU: calisiyor, 3840x1080
echo "== monitorler ==";    gdbus call --session --dest org.gnome.Mutter.DisplayConfig --object-path /org/gnome/Mutter/DisplayConfig --method org.gnome.Mutter.DisplayConfig.GetCurrentState 2>&1 | head -c 600
echo "== portal ==";        dpkg -l xdg-desktop-portal xdg-desktop-portal-gnome 2>/dev/null | awk '/^ii/{print $2, $3}'
echo "== pipewire gst ==";  gst-inspect-1.0 pipewiresrc >/dev/null 2>&1 && echo ok || echo "gstreamer1.0-pipewire yok"
echo "== AT-SPI ==";        python3 -c "import gi;gi.require_version('Atspi','2.0');from gi.repository import Atspi;d=Atspi.get_desktop(0);print('uygulama sayisi:',d.get_child_count());print([d.get_child_at_index(i).get_name() for i in range(min(8,d.get_child_count()))])" 2>&1 | tail -3
echo "== pencere listesi =="; gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell/Introspect --method org.gnome.Shell.Introspect.GetWindows 2>&1 | head -c 300
echo "== idle monitor ==";  gdbus call --session --dest org.gnome.Mutter.IdleMonitor --object-path /org/gnome/Mutter/IdleMonitor/Core --method org.gnome.Mutter.IdleMonitor.GetIdletime 2>&1 | head -c 120
echo "== ekran kilidi ==";  gdbus call --session --dest org.gnome.ScreenSaver --object-path /org/gnome/ScreenSaver --method org.gnome.ScreenSaver.GetActive 2>&1 | head -c 120
echo "== tesseract ==";     command -v tesseract && tesseract --list-langs 2>&1 | head -5
echo "== wl-clipboard ==";  command -v wl-copy || echo "yok: sudo apt install wl-clipboard"
```

**Çıktıya göre kararlar:**

- `gnome-screenshot` çalışıyorsa → Faz 2 bir günden yarım güne iner (PipeWire'a gerek kalmaz)
- `Introspect.GetWindows` boş/hata dönerse → pencere listesini AT-SPI'dan türet
- AT-SPI uygulama sayısı 0 ise → `gsettings set org.gnome.desktop.interface toolkit-accessibility true` + yeniden oturum
- `settings.json`'da `model` alanı yoksa → `/model` picker'da `s` (oturumluk) seçilmiş demektir; §5.2'deki `default_model` daha da gerekli hale gelir
- `agy` (1.1.5) → `--model` + `--effort` birlikte **zorunlu**, ölçüldü (§5.4). Model listesini TUI'deki `/model`'den tamamla

**Çıktı:** `PLAN.md`'ye "Faz 0 sonuçları" bölümü eklenir, belirsizlikler kapanır.

### Faz 0 sonuçları — ölçüldü 2026-08-01 (A bölümü kısmı)

A bölümüne giren ölçümler yapıldı. **Bu planın §5.2.1 ve §5.4'teki üç varsayımı
yanlış çıktı**; ilgili yerler aşağıdaki gerçeklere göre düzeltildi. (Masaüstü
tarafındaki ölçümler — uinput, AT-SPI, monitör — B bölümüne kaldı.)

**1. Claude Code tarafı: sorun doğrulandı.**

```
$ grep -E '"(model|effortLevel)"' ~/.claude/settings.json
  "model": "opus",
  "effortLevel": "xhigh",
```

Yani §5.1'in tahmini doğru: telefondan gönderilen **her iş** şu anda Opus 5 +
xhigh ile çalışıyor. `default_model = "sonnet"` gerçekten gerekli.

**2. `agy models` alt komutu var — model kimliklerini tahmin etmeye gerek yok.**

```
$ agy models
gemini-3.6-flash-high      gemini-3.5-flash-high      gemini-3.1-pro-high
gemini-3.6-flash-medium    gemini-3.5-flash-medium    gemini-3.1-pro-low
gemini-3.6-flash-low       gemini-3.5-flash-low
claude-sonnet-4-6          claude-opus-4-6-thinking   gpt-oss-120b-medium
```

**Effort, model kimliğinin parçası.** `--model X --effort Y`, `X-Y` kompozitine
çözülüyor; iki yazım eşdeğer ve ikisi de çalışıyor (ölçüldü, exit 0):

```
agy --model gemini-3.6-flash --effort high   ≡   agy --model gemini-3.6-flash-high
```

**3. §5.2.1/§5.4'teki model kimlikleri yanlıştı — nokta değil tire, ve son ekler
var.** Doğrusu: `claude-sonnet-4-6` (❌ `claude-sonnet-4.6`),
`claude-opus-4-6-thinking` (❌ `claude-opus-4.6`), `gpt-oss-120b-medium`
(❌ `gpt-oss-120b`).

**4. Effort listesi ajan başına değil, MODEL başına.** §5.4'teki
"`efforts = ["low","medium","high"]`, ajan geneli" yaklaşımı gerçeği anlatmıyor:

| Model | Kabul ettiği effort | Ölçülen davranış |
|---|---|---|
| `gemini-3.6-flash`, `gemini-3.5-flash` | low, medium, high | effort **zorunlu** |
| `gemini-3.1-pro` | **low, high** (medium YOK) | `--effort medium` → exit 1 |
| `claude-sonnet-4-6`, `claude-opus-4-6-thinking`, `gpt-oss-120b-medium` | **hiçbiri** | `--effort` verilirse exit 1 |

```
$ agy --model gemini-3.1-pro --effort medium -p …
Error: invalid model selection (--model "gemini-3.1-pro" --effort "medium"):
  gemini-3.1-pro has no "medium" effort (available: low, high)   → exit 1

$ agy --model claude-sonnet-4-6 --effort high -p …
Error: invalid model selection (--model "claude-sonnet-4-6" --effort "high"):
  --effort is not supported for model "claude-sonnet-4-6"        → exit 1

$ agy --model claude-sonnet-4-6 -p …                             → exit 0, "OK"
```

Sonuç: `effort_required_with_model` tek bir ajan-geneli bayrak olarak yetmiyor.
Yapılandırmaya **model başına izinli effort listesi** giriyor (`model_efforts`);
boş liste = "bu model `--effort` kabul etmiyor, bayrağı hiç ekleme".

**5. Sessiz geri düşüş 1.1.9'da DÜZELTİLMİŞ — §5.4 madde 2 artık geçerli değil.**
1.1.5'teki `⚠ Warning … Using the default model instead` + **exit 0** davranışı
yok. Geçersiz seçim artık `Error: invalid model selection …` + **exit 1**.

Bu iyi haber ama tasarımı değiştiriyor: çözümleyicinin görevi artık "yanlış
modelle sessizce çalışmayı önlemek" değil, **işin baştan hata vermesini
önlemek**. Doğru effort'u yapılandırmadan doldurmak hâlâ şart, gerekçe değişti.

**6. `pty = true` artık gereksiz — kaldırıldı.**

```
$ agy --model gemini-3.6-flash --effort high -p "sadece OK yaz" > /tmp/agy.txt 2>&1
$ wc -c /tmp/agy.txt        →  3   ("OK\n")
```

`script` sarmalayıcısıyla 4 bayt (yalnızca `\r\n` farkı) — ek bilgi yok. Upstream
#76 düzelmiş. `pty = false` yapıldı; ANSI gürültüsü ve `script` bağımlılığı kalktı.

**7. Model başlığı print modunda hiç basılmıyor — §5.4 madde 3 uygulanamaz.**
`Gemini 3.6 Flash (High)` satırı ne pty'li ne pty'siz çıktıda var; o satır yalnızca
interaktif TUI'ye ait. "Gerçekleşen modeli açılış başlığından oku" yolu yok.

**8. Yerine daha iyisi var: `--output-format json`.**

```json
{"conversation_id":"91652d3b-…","status":"SUCCESS","response":"OK\n",
 "duration_seconds":1.74,"num_turns":1,
 "usage":{"input_tokens":19325,"output_tokens":35,"thinking_tokens":26,
          "cache_read_tokens":0,"total_tokens":19360}}
```

Bu, mevcut `plain` ayrıştırıcısının UUID regex avından çok daha sağlam:
`conversation_id` doğrudan geliyor (→ `--conversation` ile devam), `status` ve
token kullanımı da cabası. Model adı JSON'da yok — ama artık gerekmiyor, çünkü
yanlış model sessizce çalışmıyor, exit 1 veriyor (madde 5).

→ `[agents.antigravity]` komutuna `--output-format json` eklendi,
`parser = "agy_json"` yapıldı. Ayrıştırıcı JSON çözemezse düz metne düşer
(hata durumunda stdout boş, stderr'de `Error: …` var).

**9. §5.2.1 ↔ §5.4 çelişkisi çözüldü.** §5.2.1 ve `UYGULAMA.md`
`default_model = "gemini-3.6-flash"` diyor, §5.4 ise `""`. İkiye bir ve gerekçe
net: boş bırakmak "hangi modelle çalıştığını bilmemek" demek, planın çözmeye
çalıştığı sorunun ta kendisi. **`gemini-3.6-flash` uygulandı.**

### Faz 1 sonuçları — ölçüldü 2026-08-01 (B bölümü)

Girdi katmanı yazıldı ve makinede doğrulandı. **Bu planın üç kararı değişti**;
gerekçeleri aşağıda, ilgili bölümlere de not düşüldü.

**1. Mutlak fare TÜM TUVALİ kapsıyor — §2.5 ve §8'deki "yüksek risk" kapandı.**

`ABS_X`/`ABS_Y` aralığı `0..3839` / `0..1079` verilen bir uinput cihazı, global
tuvale **1:1** eşleniyor. Altı noktada ölçüldü (imleç konumu ekran görüntüsü
farkından okunarak):

| Hedef | İstenen | Ölçülen | Sapma |
|---|---|---|---|
| tuvalin başı (sol üst) | (5, 5) | (5, 5) | 0 |
| SOL ekran ortası | (960, 540) | (960, 540) | 0 |
| SOL ekran sağ-alt | (1900, 1050) | (1900, 1050) | 0 |
| SAĞ ekran sol-üst | (1930, 10) | (1930, 10) | 0 |
| SAĞ ekran ortası | (2760, 540) | (2760, 540) | 0 |
| tuvalin sonu (sağ alt) | (3834, 1074) | (3834, 1075) | 1 px* |

\* sprite ekranın alt kenarında kırpıldığı için ölçüm artefaktı, konumlama hatası değil.

Kritik olan **yetenek bileşkesi**: `ABS_X + ABS_Y + BTN_LEFT` → udev
`ID_INPUT_MOUSE=1` ("VMware mutlak faresi" yolu). `BTN_TOUCH` ya da
`BTN_TOOL_PEN` eklenirse cihaz dokunmatik ekran/tablet olur ve kompozitör onu
**tek bir çıkışa** bağlar — ikinci monitör erişilemez hale gelirdi. Göreli
hareket + geri besleme tasarımına gerek kalmadı.

**2. Girdi arka ucu `dotool` değil, `python-evdev` — §2.1 değişti.**

Ölçüm: makinede `dotool`, `ydotool`, `go`, `cmake` yok; depodaki ydotool 0.1.8
(eski). Buna karşılık `python3-evdev` zaten kurulu ve venv'e bir `pip install`
ile giriyor. `dotool`'un tek gerçek üstünlüğü `DOTOOL_XKB_LAYOUT=tr` ile ham tuş
yolunda düzen farkındalığıydı — ama planın kendisi metin girişinin varsayılan
yolunu **pano + Ctrl+V** yapıyor, yani o üstünlük yalnızca `raw=True` kaçış
kapısında işe yarıyor. Bedeli ise Go kurulumu (~400 MB), kaynaktan derleme ve
ayrı bir `dotoold` servisi. evdev ayrıca **ABS aralığını bizim tanımlamamıza**
izin verdiği için 1. maddedeki riski doğrudan hedefledi. Kullanıcı onayıyla
değiştirildi.

Yan etki: acil durdurma komutu değişti. Ayrı daemon yok, sanal cihaz pcbridge
sürecinin içinde yaşıyor → **`systemctl --user stop pcbridge`** (eski plandaki
`pkill -f dotoold` geçersiz).

**3. systemd düzeltmesi büyük ölçüde gereksizmiş — §3 değişti.**

Ölçüm: systemd kullanıcı yöneticisinde `WAYLAND_DISPLAY`,
`DBUS_SESSION_BUS_ADDRESS`, `XDG_RUNTIME_DIR`, `XDG_SESSION_TYPE` **zaten
import edilmiş** durumda ve çalışan pcbridge süreci hepsini görüyor (GNOME
oturumu bunu kendisi yapıyor). Planın "kritik" dediği sorun bu makinede yok.

Ayrıca §3'ün önerdiği iki satır **uygulanmadı**, çünkü projenin açık kararıyla
çelişiyorlar:

- `WantedBy=graphical-session.target` servisi **açılışta otomatik başlatır**;
  oysa `install.sh` servisi bilinçle `disable` ediyor ve `doctor.sh` bunu
  *"açılışta otomatik başlamıyor (istenen davranış)"* diye doğruluyor.
- `PartOf=graphical-session.target` oturum kapanınca pcbridge'i **öldürür**;
  bugün öldürmüyor ve ajan/tmux/kabuk araçlarının masaüstüne ihtiyacı yok.

Uygulanan: yalnızca `After=graphical-session.target` (sıralama, zararsız), artı
`install.sh`'a `import-environment` + `dbus-update-activation-environment`
güvenlik ağı (bunu yapmayan oturumlar için).

**4. Ek ölçümler.**

- Klavye düzeni düz `tr` değil, **`tr+intl`** (`gsettings ... input-sources`).
- `/dev/uinput` için udev kural dosyasının **numarası işlevsel**: ACL'i veren
  satır `73-seat-late.rules` içinde, dolayısıyla kural 73'ten önce gelmeli.
  İlk denemede `80-uinput.rules` yazıldı → `GROUP`/`MODE` uygulandı ama
  `uaccess` ACL'i oluşmadı. `60-pcbridge-uinput.rules`'a alınınca oturum
  kapatmaya gerek kalmadan çalıştı.
- Monitör tablosu `busctl --user --json=short` ile **düz JSON** olarak okunuyor
  → yeni Python bağımlılığı yok. Yedek: `xrandr --listmonitors` (XWayland).
- `wl-copy` `capture_output=True` ile **asılıyor**: pano sahibi olarak arka
  planda yaşadığı için borular EOF vermiyor. Yazma yolunda `DEVNULL` şart.
- Ekran kilidi (`ScreenSaver.GetActive`) ve idle (`IdleMonitor.GetIdletime`)
  D-Bus okumaları servis içinden sorunsuz çalışıyor.

**Uçtan uca doğrulandı:** izin almadan ret → `desktop_unlock(5)` → kullanıcı
makinedeyken `force`suz ret (43 sn idle okundu) → görülen konuma tıklama →
`merhaba @ ış ğü ÖÇ — pcbridge B testi #1` pano yoluyla **birebir** yazıldı,
pano eski içeriğine döndü → `ctrl+a` seçim → `desktop_lock` sonrası tekrar ret.
Testler: `test_desktop.py` 101/101, `test_models.py` 79/79, `test_e2e.py` 111 geçti
+ 4 atlandı (0 hata).

> ⚠️ **Düzeltme.** Bu satır bir ara "`test_e2e.py` 115/115" diyordu. O sayı
> tekrar üretilemiyordu: 12. bölümün dört ayrıştırma kontrolü sabit çıktılı
> sahte bir ajan ister, o da yalnızca geçici olarak PATH'e konmuştu. Sahte ajan
> `tests/fake_agents/claude` olarak depoya alındı; sunucuya PATH ile enjekte
> etmek **mümkün değil** (`jobs.py` `bash -lc` kullanıyor, login kabuğu
> `~/.profile` üzerinden `$HOME/.local/bin`'i PATH'in başına koyuyor — ölçüldü).
> `parse_claude_stream_json`'un kapsamı bu yüzden `test_models.py` 11. bölüme
> taşındı (sunucusuz, 10 kontrol); e2e'deki dördü artık **ATLA** sayılıyor.

### Faz 1 — Girdi katmanı + güvenlik kapısı ✅ tamamlandı 2026-08-01

- [x] ~~`dotool`/`ydotool` derle~~ → **`python-evdev`** kullanıldı; harici derleme
  ve ayrı daemon yok. Gerekçe: "Faz 1 sonuçları", 1. madde
- [x] `/etc/udev/rules.d/`**`60`**`-pcbridge-uinput.rules` → `KERNEL=="uinput", GROUP="input", MODE="0660", TAG+="uaccess"`, `usermod -aG input $USER`
  — **dosya numarası 80 değil 60 olmak zorunda**; ACL'i veren `73-seat-late.rules`
  ondan sonra koşarsa `uaccess` etiketi hiç görülmüyor (ölçüldü). `setup_uinput.sh`
- [x] ~~`dotoold.service` kullanıcı servisi~~ → **gerekmedi**; sanal cihaz pcbridge
  sürecinin içinde yaşıyor, süreç ölünce cihaz da yok oluyor
- [x] **İlk test: ikinci monitörün sağ alt köşesine tıkla.** → 6 noktada ölçüldü,
  tuvalin tamamına 1:1, en büyük sapma 1 px (§2.5 riski kapandı)
- [x] `pcbridge/desktop/input.py`: move/click/drag/scroll/key/type + **pano-yapıştır** metin girişi
- [x] `pcbridge/desktop/safety.py`: `desktop_unlock` süreli izin, ekran kilidi kontrolü, "kullanıcı 60 s içinde klavyeye dokunduysa reddet" (IdleMonitor), saniyede eylem limiti, `audit.log`'a her eylem
- [x] `mouse` / `keyboard` / `desktop_unlock` / `desktop_lock` araçları
- [x] `config.toml`: `[desktop] enabled = false` (**varsayılan kapalı**)
- [x] **Model/effort seçimi (§5.2)** — `AgentSpec` alanları (`model_args`, `effort_args`, `models`, `restricted_models`, `blocked_models`, `efforts`, `default_model`, `model_effort`, `effort_required_with_model`, `aliases`); §5.2.2'deki 8 adımlı çözümleyici; `agent_run`'a `model` + `effort` (+ `agent` artık opsiyonel); `list_agents` çıktısına model tablosu; `parse_claude_stream_json`'a `modelUsage`; `plain` ayrıştırıcıya agy başlık satırı + "default model instead" uyarı taraması (§5.4). Masaüstünden bağımsız, tek başına test edilebilir → **ilk bu yapılabilir**
- [x] §5.2.2'deki "Ne dersen / ne olur" tablosunun her satırı için birim test (çözümleyici saf fonksiyon, sunucu ayakta olmadan test edilebilir)

**Doğrulama:** telefondan `desktop_unlock(10)` → `keyboard(type:"merhaba")` → gedit'e yazıldı mı; Türkçe düzende `@`, `ı`, `ş` doğru çıkıyor mu.

### Faz 2 — Ekran görüntüsü (~1 gün, Faz 0'a göre yarım gün)

- [ ] `capture.py` backend zinciri + otomatik seçim + `screen_info`'da hangisinin seçildiğini göster
- [ ] **Monitör farkındalığı (§2.5)** — `Mutter.DisplayConfig.GetCurrentState`'ten monitör tablosu; `screen_capture(monitor=…)` kırpması; varsayılan tek monitör; her kırpılmış görüntüyle birlikte ofset taşınması; koordinatların **global uzayda** normalleştirilmesi
- [ ] Ölçekleme: kırpma sonrası uzun kenar ≤ 1280 px, PNG optimize (telefon için)
- [ ] `pcbridge/shots.py` + `/shot/<token>.png` rotası, 5 dk TTL, tek kullanım, `state_dir/shots` temizliği
- [ ] `screen_capture` aracı
- [ ] Gerekirse: ScreenCast portalı + `restore_token`'ı `state_dir/screencast.token`'a sakla

### Faz 3 — Metinsel gözler: AT-SPI + OCR (~1.5 gün)

- [ ] `uitree.py`: ağacı gez, gürültüyü ele (görünmez/boş düğümler), kararlı `#id` üret (rol+etiket+yol karması), `ui_dump` çıktısını 4000 karaktere sığdır
- [ ] `ui_click`: önce `Action.do_action`, olmazsa `Component.get_extents` + fare
- [ ] `ui_set_text`: `Text`/`EditableText` arayüzü
- [ ] `ocr.py`: tesseract TSV, `screen_find_text`
- [ ] Elektron uygulamaları için not: `--force-renderer-accessibility` bayrağı

**Bu fazın sonunda Spark, klavye/fare koordinatı bilmeden GUI kullanabiliyor olacak.**

### Faz 4 — Toplu eylem + pencereler (~1 gün)

- [ ] `computer_batch` (eylem şeması, süre bütçesi, 110 s aşımında job'a devir)
- [ ] `window_list` / `window_focus`
- [ ] Hazır makrolar: `open_app(name)`, `switch_window(title)`, `screenshot_of(app)`
- [ ] **Ekran çerçevesi eklentisi (§7.1)** — `pcbridge-frame@local`, Cairo gradyan stroke, `affectsInputRegion: false`, D-Bus `SetActive`/`SetState`, `monitors-changed` bağlantısı, durum renkleri. Bir kez logout gerekiyor

### Faz 5 — Yerel görsel ajan: `computer_task` (~1 gün)

Asıl "tam teşekküllü computer use" burada oluyor. Claude Code makinede çalışıyor
ve **PNG dosyalarını okuyabiliyor** — yani gözü var. `computer_task`:

1. `config.toml`'daki `claude` ajanını, GUI görevleri için hazırlanmış bir sistem
   yönergesiyle başlatır (`~/.claude/skills/computer-use/SKILL.md`)
2. Skill, Claude Code'a şunu öğretir: `pcb-shot` ile ekranı çek → `Read` ile
   PNG'ye bak → `pcb-do '<eylem json>'` ile eyleme geç → tekrar bak
3. Bunlar için iki küçük CLI kabuğu yazılır (`bin/pcb-shot`, `bin/pcb-do`), aynı
   `desktop/` modülünü kullanır — MCP sunucusundan bağımsız çalışır
4. Telefondan gelen çağrı bir `job_id` döner, `job_status` ile izlenir
5. `computer_task` kendi `model`/`effort` parametresini alır (§5.2). Ekran
   görüntüsü okuyup GUI sürmek zor iş — burada varsayılan `opus` + `xhigh`
   mantıklı; rutin metin işleri `sonnet`'te kalır

Sonuç: telefondan *"Ayarlar'ı aç, gece modunu 22:00'ye kur"* diyebilirsin;
Gemini görüntüyü hiç görmez, işi gören yerel ajandır.

### Faz 6 — Belge, test, güvenlik gözden geçirmesi (~0.5 gün)

- [ ] `tests/test_desktop.py`: `weston --backend=headless` (veya Xvfb) içinde gerçek tıklama testleri; CI'da gerçek masaüstüne dokunmadan
- [ ] `tests/test_e2e.py`'ye yeni araçların şema testleri
- [ ] `KULLANIM.md`: telefondan yazılabilecek gerçek cümleler
- [ ] `GELISTIRME.md`: Wayland tuzakları bölümü (düzen, AT-SPI koordinatları, uinput izinleri)
- [ ] `README.md` güvenlik bölümüne GUI kontrolü uyarısı
- [ ] Spark'ta araç listesini yenile (kaldır–ekle)

**Toplam tahmin: 5–6 gün** (Faz 0 sonuçlarına göre ±1 gün).

---

## 7. Güvenlik tasarımı

Bu özellik pcbridge'in risk profilini ciddi biçimde büyütüyor: şu ana kadar
"uzaktan komut çalıştırma" vardı, şimdi **açık oturumundaki her uygulamaya,
oturum açmış tarayıcına, parola yöneticine görsel erişim** ekleniyor. Bu yüzden
güvenlik, sonradan eklenen değil **Faz 1'de yazılan** bir parça:

1. **Varsayılan kapalı.** `[desktop] enabled = false`. Açmak bilinçli bir işlem.
2. **Süreli izin.** GUI araçları yalnızca `desktop_unlock(minutes)` sonrası
   çalışır; süre dolunca kendiliğinden kapanır (varsayılan 15 dk, tavan 120 dk).
3. **Kilit ekranı kontrolü.** `org.gnome.ScreenSaver.GetActive` true ise tüm GUI
   araçları reddeder. Kilitli ekranın arkasında parola yazdırmak yok.
4. **Çakışma koruması.** `IdleMonitor.GetIdletime < 60 s` ise (yani sen
   makinedeysen) yazma eylemleri reddedilir — telefon ile senin faren kavga etmez.
   `force = true` ile bilinçli olarak geçilebilir.
5. **Görünürlük.** Her iki ekranın kenarında, kontrol açıkken duran **canlı
   çerçeve** (§7.1) + `desktop_unlock`'ta masaüstü bildirimi. Bildirim kaçar,
   çerçeve kaçmaz.
6. **Denetim kaydı.** Her eylem `audit.log`'a: zaman, araç, parametre özeti,
   sonuç. Ekran görüntüleri `shots/` altında 24 saat saklanır, sonra silinir.
7. **Hız sınırı.** Saniyede N eylem tavanı; sonsuz döngüye giren bir ajan
   makineyi kilitleyemesin.
8. **Ekran görüntüsü bağlantıları** OAuth'suz erişilebilir olduğu için: 128 bit
   token, tek kullanım, 5 dk TTL, `Cache-Control: no-store`.
9. **Kör noktalar dürüstçe:** parola alanları, banka oturumları, 2FA ekranları
   görüntüye girer. `deny_apps` listesi (varsayılan: parola yöneticisi, banka
   sekmeleri) yardımcı olur ama **tam koruma değildir**. Bu özelliği açmak,
   "telefonumu kaybedersem ne olur" sorusuna cevabın olmasını gerektirir.

### 7.1 Ekran çerçevesi — "kontrol bende" göstergesi

Kontrol açıkken her iki ekranın kenarında ince, mavi-mor gradyanlı, yavaşça
nefes alan bir çerçeve. Hem havalı hem de yukarıdaki 5. maddenin (görünürlük)
en iyi karşılığı: bildirim kaybolur, çerçeve durur.

**Yapılabilir mi: evet, ama tek yolu var — GNOME Shell eklentisi.**

| Yöntem | Durum |
|---|---|
| GNOME Shell eklentisi (Clutter/St aktörü) | ✅ Kompozitörün içinde çizdiği için kısıtsız; tam kontrol |
| `gtk4-layer-shell` ile overlay pencere | ❌ `wlr-layer-shell` ister, Mutter desteklemiyor |
| Şeffaf, hep-üstte GTK penceresi | ❌ Wayland istemcisi kendini hep-üstte yapamaz, konumlandıramaz |

Yani X11'de 20 satırlık iş, Wayland'de küçük bir eklenti. Ama zaten §2.2'de
"ekran görüntüsü yedeği" için eklenti ihtimalini yazmıştık — **aynı eklenti iki
işi de yapar**, o yüzden maliyeti düşük.

**Nasıl çalışacak**

- `~/.local/share/gnome-shell/extensions/pcbridge-frame@local/` — ~100 satır GJS
- Her monitör için bir `St.DrawingArea`; Cairo ile yuvarlatılmış dikdörtgen
  **stroke**, `LinearGradient` mavi → mor, dışa doğru azalan alfa ile 2-3 kat
  yumuşak parıltı
- `Main.layoutManager.addChrome(actor, { affectsInputRegion: false })` +
  `reactive = false` → **tıklamaları asla yemez**, tamamen geçirgen
- `monitors-changed` sinyaline bağlan → monitör takıp çıkarınca kendini düzeltir
- Faz süren bir `Clutter` zamanlayıcısıyla gradyan kayar (nefes efekti)
- D-Bus arayüzü açar: `SetActive(bool)`, `SetState(string)`. pcbridge
  `desktop_unlock`'ta açar, süre dolunca/`desktop_lock`'ta kapatır

**Durum renkleri** (bedava geliyor, çünkü çizim zaten bizde):

| Durum | Görünüm |
|---|---|
| `idle` — izin açık, eylem yok | Yavaş nefes alan mavi-mor, düşük parlaklık |
| `active` — o an tıklama/tuş gidiyor | Daha parlak, hızlı nabız |
| `expiring` — izne < 60 sn kaldı | Amber tona kayar |

⚠️ **Çerçeve ekran görüntüsüne de girer.** Görsel sürücü her karede kenarlarda
mor bir bant görecek. İki seçenek:

- **Önerilen:** çerçeveyi ince tut (4-6 px). Model için gürültü sayılmaz,
  koordinatları etkilemez, ekstra gecikme yok.
- Alternatif: `[desktop] hide_frame_during_capture = true` → eklentiye
  `SetActive(false)` → yakala → geri aç. Temiz kare verir ama her ekran
  görüntüsüne ~200 ms ve gözle görülür bir titreme ekler. Varsayılan **kapalı**.

**Maliyet:** ~yarım gün. **Tuzak:** yeni eklentinin tanınması için Wayland'de bir
kez oturumu kapatıp açmak gerekiyor (kabuk yeniden başlatılamıyor). Eklenti
`metadata.json`'da `shell-version: ["46"]` ile sabitlenir; Zorin 18.1 GNOME 46'da
2029'a kadar sabit olduğu için kırılma riski düşük.

**Faz:** 4'ün sonuna eklendi. Faz 1-3 çalışmadan çerçeveyi yazmanın anlamı yok,
ama Faz 5'teki uzun GUI oturumlarından **önce** hazır olmalı — asıl değeri o
zaman ortaya çıkıyor.

### Alternatif: sanal masaüstü modu (opsiyonel, çok daha güvenli)

`weston --backend=headless` veya `Xvfb :99` ile **ayrı, görünmez bir masaüstü**
açıp otomasyonu orada yapmak. Avantajları: gerçek ekranına dokunulmaz, oturum
açmış hesapların risk altında değil, X11 olduğu için `xdotool`/`scrot` kusursuz
çalışır (Wayland kısıtlarının hiçbiri yok), makine başındayken çakışma olmaz.
Dezavantajı: "benim açık Firefox'umda şu sekmeyi kapat" gibi işler yapılamaz.

`[desktop] mode = "real" | "virtual"` olarak konfigüre edilebilir; `virtual`
modu Faz 1-4'ün büyük kısmını bedavaya getirir ve iyi bir ilk adımdır.

---

## 8. Riskler ve bilinmeyenler

| Risk | Olasılık | Etki | Önlem |
|---|---|---|---|
| Gemini metin ağacını (ui_dump) doğru yorumlayamaz | orta | orta | Çıktı formatını kısa ve tablomsu tut; `computer_task` ile yerel ajana kaç |
| ~~`gnome-screenshot` GNOME 46'da bozuk çıkar~~ | — | — | **Kapandı:** ölçüldü, çalışıyor (§2.2). İleride dağıtım yükseltmesinde bozulursa portal yedeği |
| **Çift monitörde 1920 px kaymış tıklama** | yüksek | yüksek | Global koordinat uzayı + ofset taşıma; dönüşüm tek yerde (`monitors.to_global`), birim testli |
| ~~uinput mutlak fare yalnızca birincil monitörü kapsar~~ | — | — | **Kapandı:** 6 noktada ölçüldü, tuvalin tamamına 1:1, en büyük sapma 1 px (Faz 1 sonuçları) |
| uinput mutlak fare yalnızca birincil monitörü kapsar | orta | orta | Faz 1'de ölçülür; gerekirse göreli hareket + imleç konumu geri beslemesiyle konumlan. **Birincil sağdaki olduğu için testi sol ekranda yap** — kapsama sorunu varsa orada görünür |
| ~~Kesirli ölçekleme koordinatları bozar~~ | — | — | **Kapandı:** her iki monitör `scale = 1.0` (§2.5) |
| AT-SPI ağacı Wayland'de eksik koordinat verir | yüksek | düşük | Koordinat yerine `do_action` kullan |
| Electron/Java uygulamaları ağaç yayınlamaz | yüksek | orta | OCR + koordinat moduna düş |
| uinput izinleri her çekirdek güncellemesinde bozulur | düşük | düşük | `doctor.sh`'a kontrol ekle |
| Türkçe düzende yanlış karakter | **yüksek** | yüksek | Pano-yapıştır varsayılan; `ui_set_text` tercih |
| Spark'ın onay yorgunluğu kullanımı öldürür | yüksek | yüksek | `computer_batch` + `computer_task` |
| Sistem güncellemesi portal/eklenti API'sini değiştirir | orta | orta | Backend soyutlaması + `desktop_doctor.sh` |
| İnteraktif `/model` seçimin telefondaki işlerin modelini de değiştirir | **kesin** | orta | §5.2: her çağrıda `--model` açıkça verilsin |
| Gemini var olmayan bir model adı uydurur | orta | düşük | `models` izin listesi + net hata mesajı |
| **`agy` istenen modeli sessizce yok sayıp varsayılana düşer** (effort verilmezse; çıkış kodu 0, sadece uyarı) | **kesin** | orta | `effort_required_with_model` + çıktıda uyarı kalıbı taraması (§5.4) |
| Telefondan atılan rutin işler Opus 5 xhigh'ta çalışıp pahalıya patlar | yüksek | orta | `default_model = "sonnet"`, `job_status`'taki maliyet satırını izle |

**Faz 0'da kapatılacak bilinmeyenler:** `gnome-screenshot` durumu,
`Introspect.GetWindows` erişimi, AT-SPI ağacının doluluğu, `input` grubu üyeliği,
sistemdeki XKB düzeni.

---

## 9. Bu plan onaylanırsa ilk üç komut

```bash
cd ~/Belgeler/Pcbridge
edit desktop_doctor.sh          # yukarıdaki betiği yapıştır
chmod +x desktop_doctor.sh && ./desktop_doctor.sh | tee /tmp/faz0.txt
```

Çıktıyı paylaş; Faz 0 sonuçlarına göre bu dosyanın 6. bölümünü kesinleştirip
Faz 1'e başlarım. (§5'teki model/effort işi masaüstünden bağımsız — istersen
Faz 0'ı beklemeden ondan başlanabilir.)

---

## 10. Kaynaklar

- [XDG RemoteDesktop portalı](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html) — portal tabanlı girdi enjeksiyonu
- [Peter Hutterer — libei integrations in the XDG RemoteDesktop and InputCapture portals (Temmuz 2026)](http://who-t.blogspot.com/2026/07/libei-integrations-in-xdg-remotedesktop.html) — oturum kalıcılığı portal 1.21+ ile geldi
- [ydotool README](https://github.com/ReimuNotMoe/ydotool) — uinput tabanlı, X11/Wayland/TTY
- [ydotool #43 — non-QWERTY düzenlerde bozuk yazım](https://github.com/ReimuNotMoe/ydotool/issues/43)
- [ydotool paketi, Ubuntu Noble: 0.1.8](https://launchpad.net/ubuntu/noble/amd64/ydotool) — depodaki sürüm eski
- [dotool — düzen farkındalıklı uinput aracı](https://sr.ht/~geb/dotool/)
- [gemini-cli #2136 — MCP araç sonuçlarında görsel desteklenmiyor](https://github.com/google-gemini/gemini-cli/issues/2136)
- [Gemini Spark özel uygulamalar (destek belgesi)](https://support.google.com/gemini/answer/17209137?hl=en&co=GENIE.Platform%3DDesktop)
- [GNOME Discourse — D-Bus ile ekran görüntüsü](https://discourse.gnome.org/t/take-screenshot-in-gnome-environment-via-its-dbus-api/21144)
- [ArchWiki — Screen capture](https://wiki.archlinux.org/title/Screen_capture)
- [AT-SPI2 / pyatspi](https://www.freedesktop.org/wiki/Accessibility/AT-SPI2/) ve [erişilebilirlikle otomasyon örnekleri](https://modehnal.github.io/)
- [Zorin OS 18: Ubuntu 24.04 LTS + GNOME Shell 46](https://ubuntuhandbook.org/index.php/2025/10/zorin-os-18-officially-released-based-on-ubuntu-24-04-lts/), [Zorin OS 18.1 (Nisan 2026)](https://www.omgubuntu.co.uk/2026/04/zorin-os-18-1-released)

Model ve effort (5. bölüm):

- [Claude Code — Model configuration](https://code.claude.com/docs/en/model-config) — öncelik sırası, `/model`'in ayara yazılması, effort seviyeleri, `modelUsage`
- [Claude Code — CLI reference](https://code.claude.com/docs/en/cli-reference) — `--model`, `--effort`
- [Antigravity CLI komut listesi](https://toolsbase.dev/en/reference/antigravity-cli-commands) ve [agy rehberi](https://www.codeagentswarm.com/en/guides/how-to-use-antigravity-cli) — `agy --model`, sürüme bağlı TUI-only davranış
