# KURALLAR.md — masaüstü araçlarının davranış sözleşmesi

> Kurallar ve mimari: **[CLAUDE.md](CLAUDE.md)** · Sıradaki iş: **[WALKTHROUGH.md](WALKTHROUGH.md)**

> **KISMEN UYGULANDI.** Bu belge kod değişikliği önerisi olarak yazıldı.
> §1 kayan kira (`unlock_idle_seconds`), §3'teki yönerge/kapı üçlüsü ve §4'ün
> 1–3 numaralı maddeleri o gün uygulandı; **§4'ün 5, 6 ve 7 numaralı maddeleri
> hâlâ açık** ve `WALKTHROUGH.md` Adım 1'de sıraya alındı. §4 tablosunun
> "Durum" sütunu güncel kaynaktır. "Ölçüldü" işaretli satırlar 2026-08-21'de bu makinede
> koşturuldu ya da doğrudan kaynak koddan okundu; geri kalanı tasarım.

Üç şikâyet vardı. Kaynağa bakınca ikisinin kökü sanılandan farklı çıktı.

| Şikâyet | Gerçek kök | Sınıf |
|---|---|---|
| Ajan izni kapatmayı unutuyor | Tasarımda "kapatma" olayı yok, yalnızca sabit bir son tarih var | Otomatik |
| Fare bazen yumuşak, bazen hiç kıpırdamıyor | Hata değil: iki ayrı mekanizma. `ui_click` fareye **hiç dokunmuyor** | Görünürlük |
| Terminalden uygulama açıyor | Doğru yol var ama **docstring onu söylemiyor** | Yönerge + kapı |

---

## Kural sınıfları

Her kural üçünden biri olmalı; hangisi olduğu yazılmazsa tartışma sürekli
baştan başlıyor.

| Sınıf | Nerede yaşar | Ajan uymazsa |
|---|---|---|
| **O — Otomatik** | Kod kendi halleder | Ajanın bilmesine gerek yok |
| **K — Kapı** | `SafetyGate` / araç girişi | Reddedilir, gerekçe döner |
| **Y — Yönerge** | `INSTRUCTIONS` + docstring | Hiçbir şey. Umut. |

**Tek başına Y yazmak kural koymak değildir.** Kritik olan her şey O ya da K
olmalı; Y yalnızca *tercih* içindir ("şunu şuna yeğle").

---

## 1. İznin kendiliğinden kapanması — "kayan kira" (sınıf: O)

### Şu an ne oluyor (koddan okundu)

`SafetyGate.unlock()` diske `{"until": <şimdi + dakika>}` yazıyor.
`desktop_lock` çağrılmazsa izin **son tarihe kadar açık kalıyor** —
varsayılan 15 dakika, tavan 120. Ajan iş bitince kapatmayı unutunca çerçeve
dakikalarca ekranda kalıyor.

Ajanın unutması sürpriz değil, **yapısal**: ajanın "işim bitti" diye bir
olayı yok. Son araç çağrısından sonra ne olduğunu bilmiyor. Bu yüzden
çözümün ajanda değil kodda olması gerekiyor.

### Öneri

İzin *son tarihe* değil **son eyleme** bağlansın. Her başarılı
`gate.check()` "yaşıyorum" damgası vursun; damga yenilenmezse izin
kendiliğinden düşsün.

```python
# safety.py — kavram
def check(self, tool, write=True, force=False) -> Decision:
    ...                       # mevcut beş kat aynen
    self._touch()             # <- geçtiyse damgala
    return Decision(True)

def _touch(self) -> None:
    st = self._read_state()
    hard = float(st.get("hard_until", 0) or 0)
    if hard <= time.time():
        return
    # DISKTEKI `until` KAYAN degerdir; sert son tarih ayri alanda durur.
    st["until"] = min(hard, time.time() + self.spec.unlock_idle_seconds)
    st["hard_until"] = hard
    self._write_state(st)
```

`unlock()` artık iki alan yazar: `hard_until` (sert tavan) ve `until`
(kayan). `is_unlocked()` yalnızca `until`'e bakmaya devam eder — yani
kapının mantığı değişmiyor.

### Bunun bedava getirdiği şey

**GNOME eklentisi tek satır değişmeden uyuyor.** `state.js` zaten yalnızca
`until` okuyor, dosya değişimini izliyor ve `until` anına bir zamanlayıcı
kuruyor (`_armExpiry`). Kayan `until` yazıldığı anda çerçeve de kayıyor:
ajan durunca ~90 saniye sonra nefes sönüyor. Eklenti tarafında yapılacak iş
**sıfır**.

`Gio.FileMonitor`'ün ölçülmüş 800 ms hız sınırı da burada lehimize: saniyede
10 eyleme kadar yazılan damgalar tek olayda birleşiyor, eklenti thrash
etmiyor.

### Yeni ayar

```toml
# Son masaüstü eyleminden bu kadar saniye sonra izin kendiliğinden düşer.
# unlock_default_minutes SERT TAVAN olarak durmaya devam eder; bu ondan
# once devreye giren yumusak sinir. 0 = kapali (eski davranis).
unlock_idle_seconds = 90
```

`config.example.toml` **ve** `config.py`'ye birlikte eklenecek — proje
kuralı, bir kere atlanmış.

### Karar bekleyen

- **90 saniye doğru mu?** Bir `agent_run` ortasında ajan düşünürken 90 sn
  sessiz kalabilir mi? `window_focus` tek başına ~6,5 saniye sürüyor,
  ekran görüntüsü ~1,5 saniye. Düşünme payıyla 90 sn geniş görünüyor ama
  **ölçülmedi**. Öneri: 90 ile başla, `audit.log`'dan eylemler arası en uzun
  boşluğu ölçüp ayarla.
- **`computer_task` istisna olmalı mı?** Yerel görsel ajan uzun süre
  düşünebilir. İki seçenek: (a) `computer_task` koştuğu sürece damgayı
  kendisi yeniler, (b) istisna yok, ajan yavaşsa izin düşer ve yeniden
  açar. **(a) öneriliyor** — (b) görevi ortasından kesiyor.

### İkinci katman: "açan kapatır"

Kayan kira boşluğu 90 saniyeye indiriyor, sıfıra değil. Sıfır için görev
sınırını bilen araçlar kendileri kapatsın:

- `computer_batch` ve `computer_task` **kendileri unlock ettiyse** çıkışta
  `desktop_lock` çeksin.
- Kullanıcının açtığı izne dokunmasınlar — kullanıcı 60 dakika açtıysa onu
  batch'in kapatması yanlış olur. Yani state'e `granted_by` alanı gerekiyor.

---

## 2. Fare neden bazen kıpırdamıyor (sınıf: görünürlük)

### Bu bir hata değil — ölçüldü

Üç ayrı yol var ve üçü farklı davranıyor:

| Araç | Nasıl tıklıyor | İmleç oynar mı |
|---|---|---|
| `ui_click` | AT-SPI `Action.do_action` — uygulamaya "bu düğmeyi çalıştır" der | **Hayır.** `/dev/uinput`'a hiç dokunmuyor (`needs_input=False`) |
| `mouse` | uinput mutlak fare + `move_path` ara noktalar | Evet, `pointer_speed = 5000` ile yumuşak |
| `computer_batch` | eylem türüne göre ikisinden biri | Karışık |

Yani "bazen yumuşak, bazen hiç kıpırdamıyor" tam olarak ajanın `ui_click` ile
`mouse` arasında gidip gelmesi. Üstelik `INSTRUCTIONS` ona **`ui_click`'i
yeğlemesini söylüyor** ve haklı olarak söylüyor: koordinat kullanmadığı için
ıskalayamıyor, ~0,1 saniye sürüyor, ekran görüntüsü gerektirmiyor.

### Asıl sorun teknik değil

Bu bir doğruluk sorunu değil, **okunabilirlik** sorunu. Ekranın kenarındaki
nefes alan çerçeveyi tam da "bilgisayarımı bir AI kullanıyor" hissi için
yazdın. `ui_click` o hissi bozuyor: bir şeyler oluyor ama görünür bir fail
yok, ekran kendi kendine değişiyor.

### Üç seçenek

**A. İmleci `ui_click` öncesi düğümün üstüne taşı — ÖNERİLMİYOR.**
Kulağa doğru geliyor ama `CLAUDE.md`'de ölçülmüş bir engel var:
*"AT-SPI `get_extents` koordinatları yanlış."* Düğümün nerede olduğunu
güvenilir bilmiyoruz. Yanlış yere taşınan bir imleç, hiç taşınmayandan
kötü — üstelik `do_action` doğru düğüme tıkladığı için görüntü ile eylem
birbirini tutmaz. **Yanıltıcı geri bildirim, geri bildirim yokluğundan
kötüdür.**

**B. Görünürlüğü çerçeveye yükle — ÖNERİLİYOR.**
İmleç değil, zaten var olan katman konuşsun. `desktop_unlock.json`'a bir
alan daha:

```json
{"until": ..., "hard_until": ..., "pulse": 1755764400.12, "tool": "ui_click"}
```

Eklenti dosya değişiminde `pulse` damgası yenilenmişse çerçeveyi **bir kez
parlatsın** (nefes ritminden bağımsız, ~250 ms kısa bir yükselme). Böylece
her eylemde — `ui_click` dahil — görünür bir işaret oluyor, üstelik
koordinat bilmeye gerek kalmıyor.

Bedava değil ama ucuz: `_touch()` zaten her eylemde dosyaya yazıyor,
`pulse` aynı yazmaya biniyor. Eklenti tarafında ~20 satır.

**C. Hiçbir şey yapma, sadece belgele.**
`KULLANIM.md`'ye "AT-SPI tıklamalarında imleç oynamaz, bu normaldir" yaz.
En ucuzu, ama istediğin hissi vermiyor.

### Ölçüm boşluğu — ayrı konu ama ilgili

`frame.js`'de nefes animasyonu **açık** (`BREATH_MS = 5500`, tam çevrim 11
saniye). `CLAUDE.md` ise nested kabukta ölçülen **%19 CPU**'yu ve
"gerçek oturumda ÖLÇÜLMEDİ" notunu taşıyor. Yani şu an makinede sürekli
koşan, maliyeti gerçek oturumda hiç ölçülmemiş bir animasyon var.

`pulse` eklemeden önce bu ölçülmeli — yöntem `CLAUDE.md`'de zaten
yazılı: `top` değil, `/proc/<pid>/stat`'tan CPU zamanı farkı. Çıkan sayı
%1'in altındaysa mesele yok; %10 civarıysa nefes yerine `pulse`'a geçmek
hem daha ucuz hem daha bilgilendirici olur (sürekli animasyon "izin açık"
der, parlama "şu an bir şey oldu" der).

---

## 3. Terminalden uygulama açma (sınıf: Y + K)

### Kök neden docstring — ölçüldü

Sandığından iyi durumdasın: **doğru yol zaten var ve zaten GNOME araması
kullanıyor.**

`apps.focus()` şunu yapıyor: `super` → uygulama adını **ham tuşla** yaz →
`Return` → AT-SPI ile odağı doğrula, tutmazsa `Escape` ile toparla ve açıkça
hata ver. Ölçülen süre ~6,5 saniye. Bu **birebir senin elinle yaptığın şey**.

Ama `window_focus`'un docstring'i şunu diyor:

> *"Bring an application's window to the front so the next keystrokes go
> there."*

Ajan bunu okuyunca "bu **açık** pencereler için" diye anlıyor — ki
`Field(description=...)` de öyle diyor: *"Names from `window_list` work
best."* Kapalı bir uygulamayı açması gerektiğinde elinde bu araç yokmuş gibi
davranıp `shell_run`'a düşüyor. **Ajan kuralı çiğnemiyor, aracı bilmiyor.**

Ayrıca `apps.launch()` (`gtk-launch` ile .desktop girdisi) kodda var ama
**hiçbir MCP aracı onu dışarı vermiyor**.

### Neden gerçekten önemli (gerekçe uydurmayalım)

"Arama menüsünden açmak daha doğru" demek yetmez; ölçülebilir farklar:

1. `shell_run` ile açılan uygulama **pcbridge'in çocuğu** olur.
   `systemctl --user restart pcbridge` onu öldürür — `CLAUDE.md`'de zaten
   yazılı: *"restart çalışan işleri de öldürür, `start_new_session` oturum
   grubunu ayırıyor ama cgroup'u değil."*
2. `.desktop` girdisinden açılan uygulama doğru `app_id`'yi alır; kabuktan
   açılan çoğu zaman almaz. `app_id` yoksa `window_list` ve `window_focus`
   onu adıyla bulamaz — yani ajan kendi açtığı pencereyi sonradan
   bulamıyor.
3. Ortam farkı: kabuktan gelen süreç pcbridge'in ortamını miras alıyor;
   `.desktop` yolu oturumun ortamını alıyor.

Bu üçü "tercih" değil, **arıza kaynağı**. O yüzden bu kural K sınıfı olmayı
hak ediyor.

### Öneri — üç parça

**3a. Docstring düzeltmesi (Y, 10 dakika).** `window_focus`:

> *"Bring an application to the front, **launching it if it is not already
> running** — this goes through the desktop's own search, exactly as the user
> would. This is the ONLY correct way to open a graphical application; never
> use `shell_run` for that."*

`Field(description=...)`'daki "Names from `window_list` work best" da
yanıltıcı: kapalı uygulamalar `window_list`'te yok. Yeniden yazılmalı.

**3b. `INSTRUCTIONS`'a masaüstü kural bloğu (Y).** `server.py` içinde zaten
bir `INSTRUCTIONS` metni var ve masaüstü hakkında yalnızca üç satır
söylüyor. Eklenecek:

```
Desktop rules — these are not preferences:
  * To open a graphical application, use `window_focus`. It goes through the
    desktop's own search and launches the app if it is closed. NEVER launch a
    graphical application with `shell_run`: it becomes a child of this server,
    dies when the server restarts, and often has no app id, which means you
    will not find the window again.
  * `shell_run` stays for non-graphical work: builds, file operations, queries.
  * `ui_click` does not move the mouse pointer — it asks the application
    directly. That is intended; do not "fix" it with the `mouse` tool.
  * After `desktop_unlock`, call `desktop_lock` when the graphical work is
    done. The permission also expires on its own, but leaving it open keeps
    the on-screen indicator running and worries the user.
```

**3c. Kapı (K).** Masaüstü izni **açıkken**, `shell_run` bilinen bir GUI
uygulamasını başlatmaya çalışırsa reddedilsin:

```python
# tools.py > shell_run girisi — kavram
if gate.is_unlocked() and cfg.desktop.block_gui_launch_in_shell:
    hit = appslib.looks_like_gui_launch(command)
    if hit:
        gate.audit("shell_run_denied", reason="gui_launch", app=hit)
        return (
            f"⛔ `{hit}` bir masaüstü uygulaması. Kabuktan açılırsa bu "
            f"sunucunun çocuğu olur, servis yeniden başlayınca kapanır ve "
            f"pencere kimliği oluşmadığı için sonradan bulunamaz.\n"
            f"Bunun yerine: window_focus(\"{hit}\")"
        )
```

Tespit **tahminle değil veriyle**: `apps.entries()` zaten bütün `.desktop`
girdilerini okuyor. `looks_like_gui_launch()` komutun ilk anlamlı simgesini
alıp o tabloyla eşleştirir. Ayrıca açık başlatıcılar: `gtk-launch`,
`gio launch`, `xdg-open`, `flatpak run`.

Yanlış pozitiflere karşı iki valf:

```toml
# Masaustu izni ACIKKEN shell_run'un GUI uygulamasi baslatmasi engellensin mi.
# Kapaliyken hic devreye girmez: masaustu kapaliyken kabuk normal kabuktur.
block_gui_launch_in_shell = true
# Bu adlar her zaman serbest (kabuktan calistirmak mesru olanlar).
gui_launch_allowlist = ["code", "gnome-text-editor", "nautilus"]
```

### Karar bekleyen

- **`app_open` diye ayrı bir araç açılsın mı?** `window_focus` hem açıyor
  hem öne alıyor; adı yalnızca ikincisini söylüyor. İki seçenek:
  (a) docstring'i düzelt, araç sayısı 33'te kalsın;
  (b) `app_open` ekle (`apps.launch()` → `gtk-launch`, aramadan hızlı:
  ~6,5 sn yerine ~1 sn), `window_focus` öne almaya odaklansın.
  **(b) öneriliyor** ama araç sayısını artırıyor ve `gtk-launch` yolunun
  GNOME araması kadar güvenilir olduğu **bu makinede ölçülmedi**.
- **Allowlist'te ne olmalı?** Yukarıdaki üçü tahmin. Sen kabuktan hangi GUI
  uygulamalarını meşru olarak açıyorsun?

---

## 4. Sırada olması gereken diğer kurallar

Şu an yazılı olmayan ama aynı sınıflandırmadan geçmesi gereken maddeler.
Tartışmaya açık:

| # | Kural | Sınıf | Durum |
|---|---|---|---|
| 1 | Ekran görüntüsü bayatlarsa koordinat kullanma | O | **Var** (`agent_shot_max_age_seconds`) |
| 2 | `hold` sonrası `release` unutulursa bırak | O | **Var** (`hold_max_seconds = 120`) |
| 3 | Metin girişi pano yoluyla, ham tuşla değil | O | **Var** (`tr+intl` yüzünden) |
| 4 | Tıklamadan sonra sonucu doğrula | Y | Kısmen — `ui_click` çıktısı söylüyor, `mouse` söylemiyor |
| 5 | Pencere kapatma / kaydetmeden çıkma onay istesin | K | **Var** (2026-09-12). Kapatma kısayolu `confirm_close` ister; `computer_batch`'te ayrıştırmada reddedilir |
| 6 | Aynı düğüme üst üste 3 kez tıklanırsa dur | K | **Var** (2026-09-12). `[desktop] repeat_click_limit = 3`; 3. tıklama hiç gönderilmez |
| 7 | Parola alanına yazma (`role = password text`) | K | **Var** (2026-09-12). Rol ölçüldü; `force` ile aşılamıyor |

**Üçü de kapatıldı.** Karar tablosu `pcbridge/desktop/policy.py` içinde, saf ve
I/O'suz; sözleşmesi `tests/contracts/test_desktop_gates.py`.

Madde 5 bilerek **dar**: yalnızca çağıranın gönderdiği kısayol dizesine bakıyor.
Pencerenin kapatma **düğmesi** kapsam dışı — AT-SPI'da "close" diye bir rol yok
ve ada bakmak dile bağlı olurdu ("Close"/"Kapat"/"Fermer"); bir ipucunu kapatan
zararsız düğmeyi de yakalardı.

Madde 6 **tek dizi içinde** sayıyor. Ayrı ayrı `ui_click` çağrılarıyla dönen
bir ajan hâlâ yakalanmıyor: süreçler arası sayaç, kiranın (`desktop_unlock.json`)
ihtiyaç duyduğu dosya + kilit düzeneğini gerektirirdi. Bilinen sınır.

---

## Önerilen sıra

Her adım tek başına sınanabilir ve geri alınabilir.

1. **3a + 3b** — docstring ve `INSTRUCTIONS`. Kod riski sıfır, en büyük
   davranış değişikliğini muhtemelen bu tek başına yapar. Yaptıktan sonra
   birkaç gün gözle: ajan hâlâ terminale düşüyor mu?
2. **1 — kayan kira.** `safety.py` + iki ayar. Eklenti değişmiyor.
   Doğrulama: izni aç, bir şey yap, dur, kronometre tut — çerçeve ~90 sn
   sonra sönmeli.
3. **Nefes animasyonunun gerçek oturumdaki CPU maliyetini ölç.** `pulse`
   kararı buna bağlı.
4. **2B — `pulse`.** Ölçüm 3'ü geçerse.
5. **3c — kabuk kapısı.** En son, çünkü yanlış pozitif riski taşıyan tek
   madde bu ve 1. adım işe yararsa belki hiç gerekmez.
6. **Bölüm 4'teki 5, 6, 7.**

---

## Bu belgenin dayanağı

Koddan okunanlar (2026-08-21): `pcbridge/desktop/safety.py`,
`pcbridge/desktop/apps.py`, `pcbridge/desktop/ops.py`, `pcbridge/tools.py`
(`ui_click`, `window_focus`, `_guard`), `pcbridge/server.py`
(`INSTRUCTIONS`), `pcbridge/config.py` (`DesktopSpec`),
`gnome-extension/.../state.js`, `.../frame.js`, ve çalışan `config.toml`'un
`[desktop]` bölümü.

Makinede koşturulanlar: `busctl` ile ekran kilidi ve idle sorgusu,
`gsettings` güç/kilit ayarları, `claude mcp list`.

Ölçülmeyen ve bu belgede **iddia edilmeyen** şeyler: `gtk-launch` yolunun
güvenilirliği, nefes animasyonunun gerçek oturumdaki maliyeti, 90 saniyenin
doğru boşluk payı olup olmadığı.
