# WALKTHROUGH.md — nerede kaldık, ne kaldı

> Kurallar, mimari ve ölçülmüş makine gerçekleri: **[CLAUDE.md](CLAUDE.md)**.
> Native migration'ın implementation sözleşmesi: **[PLAN.md](PLAN.md)**.
> Bu dosya tek bir soruyu cevaplar: **sırada ne var ve şimdiye kadar ne yapıldı.**

Depoda tek "yapılacak iş" listesi budur. Başka bir dosyaya durum özeti
kopyalama: `AGENTS.md` bir kez `CLAUDE.md`'nin kopyası olarak üretilmişti ve
iki günde 83 satır ayrıştı. İki gerçeğin olduğu yerde biri eskir.

## Durum özeti

- **Aktif adım:** Yok (`Adım 2 tamamlandı, gerçek oturumda ölçüldü`)
- **Son tamamlanan adım:** Adım 2 — `window_focus` hızlı yolu
- **Sıradaki uygulanabilir adım:** Adım 3 — Native migration Faz 3 (Task 3.1)
- **Blocker:** Yok
- **Son doğrulanan gate:** **Gate 2 geçti** (native migration). Task 2.4 typed
  desktop state, fail-closed policy ve native lock watcher acceptance'ı dahil.
- **Native migration içindeki sıradaki task:** 3.1 — Native display snapshot

Çalışma kuralı (kullanıcı isteği, 2026-09-12): **her adım sonunda ilerleme bu
dosyaya yazılır ve durulur; devam için onay beklenir.**

---

# Yol haritası

Sıra yukarıdan aşağı. Her adım tek başına sınanabilir ve geri alınabilir.

| Adım | Ne | Durum |
|---|---|---|
| 0 | Belge omurgası: tek giriş noktası, ölü referansların onarımı | `tamamlandı` |
| 1 | `KURALLAR.md` §4'teki 5/6/7 kapıları (parola alanı, tekrar tıklama, kapatma onayı) | `tamamlandı` |
| 2 | `window_focus` hızlı yolu (6701,3 ms → **5,2 ms**, gerçek oturum) | `tamamlandı` |
| 3 | Native migration Faz 3: ilk Rust capture subsystem → Gate 3 | `bekliyor` |
| 4 | Native migration Faz 4: paketleme, parity, varsayılan değişikliği → Gate 4 | `bekliyor` |
| 5 | Native migration Faz 5–8: input, accessibility, capture kapsamı, retirement | `bekliyor` |
| 6 | İmleç katmanı (gnome-extension) — yarım kalan iş | `bekliyor` |
| — | Faz W (Windows), Faz M (macOS), Faz G (GUI), `JARVIS.md` | `ertelendi` |

## Adım 0 — Belge omurgası

**Durum:** `tamamlandı`

**Amaç:** kalan her işin tek, güncel bir listesi olsun; var olmayan içeriğe
işaret eden belgeler onarılsın.

**Neden gerekti.** `b4fa5ed` commit'inde `YAPILACAKLAR.md` native migration
günlüğüyle değiştirildi. İçindeki `window_focus` ölçümü ve imleç katmanı
bulguları o anda **silindi**; yalnızca git geçmişinde (`22a5824`) kaldılar. Beş
belge hâlâ o silinmiş içeriğe bağlantı veriyordu. Ayrıca `PLAN.md`
commit'lenmemişti: on commit, yalnızca izlenmeyen bir dosyada bulunan task
kimliklerine atıf yapıyordu.

**Yapılacaklar:**

1. `PLAN.md` commit'lensin (içerik değişmeden).
2. `YAPILACAKLAR.md` → `WALKTHROUGH.md` (`git mv`, geçmiş korunur); durum
   özeti + yol haritası + kurtarılan kayıtlar + ilerleme kaydı tek dosyada.
3. `CLAUDE.md` bu dosyaya işaret etsin; üç ölü satır onarılsın.
4. `AGENTS.md` kopyaladığı durum bloğunu bıraksın, yönlendirmeye dönsün.
5. `UYGULAMA.md`, `KURALLAR.md`, `gnome-extension/README.md`, `KULLANIM.md`,
   `README.md`, `GOREV-kurallar.md` içindeki ölü referanslar onarılsın.
6. `PLAN.md`'nin sözleşme satırlarındaki dosya adı güncellensin.
7. Proje belgelerine tek biçim başlık eklensin.
8. `graphify-out/` `.gitignore`'a eklensin. **`*.md` yazılmayacak** —
   `.gitignore` bunu açıkça yasaklıyor, bütün belgeler izleniyor.

**Ölçüt:** `grep -rn 'YAPILACAKLAR' --include='*.md' .` yalnızca tarihsel
anlatım satırlarını döndürür, bağlantı döndürmez.

### Yapılanlar

- `PLAN.md` içerik değiştirilmeden commit'lendi (`6cddfe4`). Öncesinde sır
  taraması yapıldı: `config.toml`'daki gerçek parola ve statik token dosyada
  geçmiyor, Tailscale hostname'i yok.
- `YAPILACAKLAR.md` → `WALKTHROUGH.md` (`git mv`; on task kaydının tamamı
  korundu). Üstüne durum özeti, yol haritası ve kurtarılan kayıtlar eklendi.
- `22a5824:YAPILACAKLAR.md` içinden kurtarılanlar: `window_focus`'un altı
  satırlık ölçüm tablosu, etkilenen dört çağıran, "bozulmaması gereken faz
  1/3" gerekçesi ve imleç katmanının bütün bulguları + tasarım kararları.
- `CLAUDE.md`: beş düzeltme. Giriş artık `WALKTHROUGH.md`'ye yönlendiriyor;
  imleç katmanı satırı onarıldı; belge haritasında `PLAN.md` "geçmiş kayıt"
  olmaktan çıkıp **yürürlükteki sözleşme** olarak ayrıldı.
- `AGENTS.md`: kopyaladığı "Native migration durumu" bloğu kaldırıldı, yerine
  neden kopyalanmaması gerektiğini söyleyen yönlendirme kondu.
- Ölü referanslar onarıldı: `UYGULAMA.md` (4), `KURALLAR.md` (2),
  `gnome-extension/README.md` (1), `KULLANIM.md` (1), `README.md` (1),
  `GOREV-kurallar.md` (1). İmleç katmanına bakanlar `WALKTHROUGH.md`'ye,
  makine ölçümlerine bakanlar `CLAUDE.md`'ye yönlendirildi.
- `PLAN.md` sözleşme satırları güncellendi ve iki eskimiş iddia düzeltildi:
  "Migration uygulaması henüz başlamadı" (Faz 0–2 bitti) ve `AGENTS.md`'ye
  durum kopyalamayı **isteyen** kural — kopyalamayı **yasaklayan** kurala
  çevrildi, gerekçesiyle.
- `KURALLAR.md`'nin "Hiçbir madde henüz uygulanmadı" ibaresi gerçekle
  değiştirildi: §1, §3 ve §4'ün 1–3'ü uygulandı; 5/6/7 açık.
- On bir proje belgesine tek biçim yönlendirme başlığı eklendi.
  `README.md` (İngilizce vitrin) ve `skills/computer-use/SKILL.md`
  (frontmatter'lı skill dosyası) kapsam dışı bırakıldı.
- `graphify-out/` `.gitignore`'a eklendi. `*.md` **yazılmadı**.

### Test sonuçları

Hepsi live bayrakları kapalı olarak:

- `tests/test_desktop.py` → **582 geçti, 0 kaldı**
- `tests/test_models.py` → **106 geçti, 0 kaldı**
- `tests/test_test_safety.py` → **1 test, OK**
- `tests/contracts` discovery → **72 tests, OK**
- `python -m pcbridge.server --check -c config.example.toml` → exit `0`
- Bağlantı denetimi (bütün izlenen `.md`'lerdeki göreli bağlantılar) →
  **0 kırık bağlantı**
- Kabul ölçütü → `YAPILACAKLAR` artık hiçbir belgede **bağlantı** olarak
  geçmiyor; kalan dört satır tarihsel anlatı.

`tests/test_e2e.py` **çalıştırılmadı** (gerçek `claude -p` kotası yakıyor,
bu adım kod değiştirmedi). Rust suite **çalıştırılmadı** (Rust'a dokunulmadı).

### Karar kaydı

**D2 karara bağlandı** (kullanıcı "sen hangisini öneriyorsan onu yap" dedi):
GNOME eklentisinin "yalnızca görsel katman" kuralı **dar kapsamda** gevşetildi.
Dört koşul Adım 2'de yazılı. Karar `PLAN.md` OPEN DECISIONS ve Task 6.4'e de
işlendi.

**Kullanıcının "bütün markdown'lar CLAUDE.md'ye işaret etsin" isteği kısmen
uygulandı.** Kelimesi kelimesine uygulanırsa `KULLANIM.md` (araç kataloğu),
`KURULUM.md` (elkitabı), `README.md` (İngilizce vitrin) ve
`docs/native/protocol-v1.md` (protokol sözleşmesi) silinmiş olurdu — hepsi
taşıyıcı içerik. Uygulanan ayrım: **yönlendirmeye dönen tek dosya `AGENTS.md`**;
diğerleri tek satırlık başlık aldı ve içeriklerini korudu; silinen tek şey
kopyalanmış durum blokları ve ölü referanslar oldu.

**Rollback:** Adım 0 tek commit; `git revert` yeter. `PLAN.md` commit'i
(`6cddfe4`) ayrı ve geri alınmamalı.

**Sonraki somut adım:** Adım 1 — `KURALLAR.md` §4'ün 7 numaralı maddesiyle
(parola alanı kapısı) başla. Önce gerçek AT-SPI rol dizesini **ölç**.

## Adım 1 — `KURALLAR.md` 5/6/7 kapıları

**Durum:** `tamamlandı`

`PLAN.md` bu üçünü **hiç kapsamıyor** (grep ile doğrulandı). Migration'dan
bağımsız, ucuz ve gerçek bir kazayı karşılıyorlar.

Sıra: 7 → 6 → 5 (ucuzdan pahalıya).

**7 — parola alanı kapısı.** `uitree.Node.role` zaten AT-SPI'dan geliyor.
`set_text` yolunda rol parola alanıysa reddedilsin. Kapı **provider sınırında**
olsun ki `ui_set_text` MCP aracı, `ops.ui_set_text` ve `pcb-do` üçü birden
kazansın. Gerçek rol dizesi **ölçülecek**, `"password text"` diye
varsayılmayacak. `force=true` bu kapıyı **açmaz**.

**6 — tekrar tıklama durdurma.** `batch.py`'deki `stopped` alanı zaten
`"" | "budget" | "error" | "focus"` taşıyor; `"repeat"` eklenecek. Aynı hedefe
üst üste 3 kez tıklama batch'i durdurur; tamamlanan adımlar ve kalan eylemler
bugünkü gibi raporlanır. `batch.py` gerçek cihaz tanımadığı için test sahte
`Ops` ile yazılır.

**5 — kaydetmeden kapatma onayı.** En riskli madde, yanlış pozitifi en yüksek
olan da bu. Kapsam dar: kapatma kombinasyonları (`alt+F4`, `ctrl+w`, `ctrl+q`)
ve AT-SPI'da kapatma rolü taşıyan düğmeler, çağıran açıkça onay vermediğinde
reddedilir.

**Ölçüt:** üç kapı da sahte provider'la kırmızı→yeşil gösterilir; gerçek girdi
gönderilmez; `tests/test_desktop.py` bütün live bayrakları kapalıyken tam geçer.

### Yapılanlar

Karar tablosu yeni bir modülde: **`pcbridge/desktop/policy.py`** — saf, I/O
yok, `models.py` gibi. Bilinçli: kapılar masaüstü olmadan sınanabilmeli ve
ileride Python olmayan bir accessibility provider aynı tablodan aynı hükmü
çıkarabilmeli. `SafetyGate` "bu çağıran eyleyebilir mi" sorusuna bakıyor;
bu modül "bu hedefe dokunulur mu" sorusuna.

**Madde 7 — parola alanı.** Rol **ölçüldü**, varsayılmadı:
`Atspi.role_get_name(Atspi.Role.PASSWORD_TEXT)` → `'password text'`; adında
"password" geçen tek rol bu ve parolaya özel bir `StateType` yok. Alan
`ui_dump`'ta görünüyor (liste `editable` düğümleri alıyor), yani ajan kimliği
alabiliyor — kapı gerçekten gerekli. Kapı
`PythonAccessibilityProvider.set_text` içinde, düğüm çözüldükten hemen sonra:
`ui_set_text` (MCP), `DeviceOps.ui_set_text` (`computer_batch`) ve `pcb-do`
üçü birden aynı noktadan geçiyor. `force` bu kapıyı **açmıyor** ve açacak bir
parametre de yok.

**Madde 6 — tekrar tıklama.** `batch.py`'de, eylemden **önce**: 3. tıklama hiç
gönderilmiyor, `stopped="repeat"` dönüyor, kalan eylemler bugünkü gibi
raporlanıyor. "Aynı hedef" = düğüm kimliği ya da koordinat + uzay
(`monitor`/`shot`). `move` seriyi kırıyor, `wait` **kırmıyor** — "tıkla-bekle-
tıkla" tam da döngüye giren ajanın deseni. Sınır
`[desktop] repeat_click_limit = 3`; 0 kapatıyor, 1 reddediliyor (ilk tıklama
bile gönderilmezdi).

**Madde 5 — kapatma onayı.** `expect_focus` deseni tekrarlandı: **niyeti
söylet.** Ayrı bir `confirm_close`, `force` değil — `force` etkinlik
kontrolünü atlayan bir bayrak ve onu burada da geçerli kılmak "reddi görünce
force dene" refleksini ödüllendirirdi. `computer_batch`'te kapı
**ayrıştırmada**: onaylanmamış kapatma listenin ortasında olsa bile hiçbir
eylem çalışmıyor. Akor sırası ve büyük/küçük harf önemsiz (`F4+alt` = `alt+f4`).

### Bilinçli olarak yapılmayanlar

- **Kapatma düğmesi kapsam dışı.** AT-SPI'da "close" diye bir rol yok; ada
  bakmak dile bağlı olurdu ("Close"/"Kapat"/"Fermer") ve bir ipucunu kapatan
  zararsız düğmeyi de yakalardı. Bu maddede yanlış pozitif en pahalı şey.
- **Madde 6 yalnızca tek dizi içinde sayıyor.** Ayrı ayrı `ui_click`
  çağrılarıyla dönen bir ajan hâlâ yakalanmıyor: süreçler arası sayaç,
  kiranın ihtiyaç duyduğu dosya + kilit düzeneğini gerektirirdi. Bilinen sınır,
  `KURALLAR.md` §4'e de yazıldı.

### Test sonuçları

- `tests/contracts/test_desktop_gates.py` (yeni) → **25 test**, kırmızı→yeşil
  her madde için ayrı ayrı gösterildi.
- `tests/contracts/test_mcp_errors.py`'ye üç wire testi eklendi: onaysız
  `keyboard` çağrısı `isError` + `CONFIRMATION_REQUIRED` dönüyor **ve sahte
  input provider'a tek bir tuş gitmiyor**; onaylı çağrı geçiyor; onaysız
  `computer_batch` listenin zararsız ilk eylemini bile çalıştırmıyor.
- Tam contract discovery → **100 tests, OK** (72 → 100).
- `tests/test_desktop.py` live bayrakları kapalı → **582 geçti, 0 kaldı**
- `tests/test_models.py` → **106 geçti, 0 kaldı**;
  `tests/test_test_safety.py` → **1 test, OK**
- `gjs -m gnome-extension/tests/test_state.js` → **31 geçti, 0 kaldı**
- `python -m pcbridge.server --check -c config.example.toml` → exit `0`
- Gerçek süreçte (test ikilisi değil) `pcb-do --dry-run`: onaysız `alt+F4`
  exit `4` ile reddedildi, `confirm_close` ile exit `0`.

**Yol boyunca yakalanan bir şey:** `test_capabilities.py`'deki hata taksonomisi
sözleşmesi iki yeni kodu (`PASSWORD_FIELD`, `CONFIRMATION_REQUIRED`) reddetti.
Test doğru davrandı — taksonomi bilinçli genişletildi ve sözleşme gerekçesiyle
güncellendi.

`tests/test_e2e.py` **çalıştırılmadı** (gerçek `claude -p` kotası yakıyor).
Rust suite **çalıştırılmadı** (Rust'a dokunulmadı). Hiçbir live masaüstü
bayrağı açılmadı; gerçek klavye/fare girdisi gönderilmedi.

**Rollback:** Tek commit; `git revert` yeter. `[desktop] repeat_click_limit = 0`
yalnızca madde 6'yı kapatır, diğer ikisinin anahtarı yok (bilinçli).

**Sonraki somut adım:** Adım 2 — `window_focus`. Önce eklentiye
`ActivateWindow` ekleyip `audit.log`'un `ms` alanıyla ÖLÇ; koda dal yazmadan
önce sayıyı gör.

## Adım 2 — `window_focus` hızlı yolu

**Durum:** `tamamlandı` — gerçek oturumda ölçüldü ve doğrulandı (2026-09-12).

Ölçüm ve bozulmaması gerekenler aşağıda, "Kurtarılan kayıtlar" bölümünde.

**Karar (D2, 2026-09-12): GNOME eklentisinin "yalnızca görsel katman" kuralı
dar kapsamda gevşetildi.** Gerekçe: GNOME 46 + Wayland'de `Shell.Introspect` ve
`Shell.Eval` kapalı (ölçüldü, "Access denied"); kabuğun içinden
`Meta.Window.activate` bilinen tek temiz yol. Dört koşul:

1. Eklenti **tek** D-Bus yöntemi sunar: `ActivateWindow(hedef) -> bool`.
   Taşıma, kapatma, boyutlandırma, pencere listesi **yok**.
2. Yöntem `desktop_unlock.json`'daki grant'i kontrol eder ve izin kapalıyken
   reddeder. Eklenti o dosyayı bugün zaten **okuyor**; izin kapalıyken pencere
   etkinleştiren bir yöntem güvenlik modelinde delik açardı.
3. GNOME araması **silinmez**, `degraded` yedek olarak ikinci sıraya düşer.
   Eklenti kurulu değilken davranış bugünküyle birebir aynı kalır.
4. "Hızlı" iddiası ölçümle kanıtlanır: `audit.log`'un `ms` alanı, en az beş
   çağrının ortalaması.

Geri alma: eklentiden yöntemi kaldırmak yeter; `apps.focus()` yedeğe düşer.

**Adımlar:** ölç → tutarsa dal `apps.focus()` içine yazılsın (üç çağıran da
aynı fonksiyondan geçtiği için `window_focus`, `computer_task`,
`computer_batch` ve `pcb-do` kendiliğinden kazanır) → `computer_task(app=…)`
kendi açtığı uygulama için aramaya girmeyi bıraksın → `batch.py`'deki 7000 ms
bütçesi ve `ops.devices_needed()`'in klavye açması gözden geçirilsin →
sözleşme metinleri birlikte güncellensin.

**Ölçüt:** açık pencere için `focus` **6,6 sn → 1 sn altı** (en az beş çağrının
`ms` ortalaması) · kapalı uygulamayı açma soğuk başlatmayla hâlâ çalışıyor ·
eklenti kurulu **değilken** davranış bugünküyle aynı · aynı ölçüm
`computer_batch` içindeki `focus` eylemiyle de tekrarlanıyor.

### Yapılanlar ve ölçüm

- Eklentiye tek özel yöntem eklendi:
  `io.github.eymistaken.Pcbridge.WindowFocus.ActivateWindow(s) -> b`.
  D-Bus introspection yalnızca bu özel yöntemi gösterdi; pencere listesi,
  taşıma, kapatma ve boyutlandırma yok.
- Yöntem her çağrıda `UnlockState.refresh()` ile sahte nested grant dosyasını
  yeniden okuyor. Grant kapalıyken nested çağrı `b false` döndü ve pencere
  etkinleştirmedi. `true`, kabuğun `global.display.focus_window` değeriyle
  yöntemin içinde doğrulanıyor.
- `apps.focus()` önce bu yolu deniyor; servis yoksa, grant reddederse veya
  hedef açık değilse eski GNOME araması aynen çalışıyor. Eklenti kurulu değil
  testi eski `super` → ham yazı → `Return` sırasını ve beklemeleri birebir
  doğruluyor.
- `computer_task(app=…)` uygulamayı açtıktan sonra zaten odaktaysa ikinci bir
  arama yapmıyor. Nested kabukta kapalı Text Editor soğuk başlatıldı;
  `org.gnome.TextEditor` açıldı ve kabuk içi doğrulama
  `New Document (Draft) - Text Editor` için `PASS` verdi. Birim sözleşmesi de
  bu durumda GNOME aramasının çağrılmadığını doğruluyor.
- `ops.devices_needed()` artık `focus_uses_keyboard` parametresi alıyor.
  **Varsayılan `True`** — yani sormayan çağıran eski, muhafazakâr cevabı
  alıyor. Modül saf kalsın diye probe'u çağıran yapıyor: `window_focus`,
  `computer_batch` ve `pcb-do` üçü de `extension_focus_available()` sonucunu
  geçiriyor. Eklenti yoksa eski klavye ön kontrolü aynen korunuyor; varsa
  `/dev/uinput` hiç açılmıyor.
  `batch.py` içindeki `7000 ms` bütçe tahmini **korundu**, çünkü hedef kapalı
  olduğunda arama yedeğinin en kötü durum maliyeti hâlâ yaklaşık 6,7 saniye.
- Capability, eklenti sahibi varken `supported` / `linux.gnome-shell-extension`;
  eklenti yokken eski `degraded` / `linux.gnome-search` olarak raporlanıyor.

**Önce — gerçek oturum, 2026-09-02:** aynı hedefe altı `batch_step`:
6935, 6669, 6674, 6683, 6616, 6631 ms; ortalama **6701,3 ms**.

**Sonra — nested GNOME kabuğu, 2026-09-12:** aynı yol: 11, 9, 6, 4, 6, 5 ms;
ortalama **6,8 ms**. Gerçek oturum ölçümü aşağıda ve daha hızlı çıktı (5,2 ms),
yani nested burada abartmış — bu, `frame.js` animasyon ölçümündeki nested
şüphesiyle aynı yönde bir gözlem.

### Gerçek oturum doğrulaması — 2026-09-12, çıkış/giriş sonrası

Eklenti **symlink** ile kurulu olduğu için (diskteki eklenti doğrudan bu depo)
yeni kod, ayrı bir kurulum kararı beklemeden, ilk çıkış/girişte yüklendi.
`busctl NameHasOwner` → `b true`.

**Zamanlama.** Ölçüm noktası taban çizgisiyle aynı: `computer_batch` →
`DeviceOps.focus` → `audit.log`'daki `batch_step.ms`.

| Koşul | N | Değerler (ms) | Ortalama |
|---|---|---|---|
| Taban çizgisi, GNOME araması (2026-09-02) | 6 | 6935, 6669, 6674, 6683, 6616, 6631 | **6701,3 ms** |
| Eklenti, fiilen odak değiştiren çağrılar | 4 | 6, 5, 5, 5 | **5,2 ms** |
| Eklenti, günün bütün focus adımları | 10 | 3–6 | **4,4 ms** |

Kabul ölçütü "1 saniyenin altı" idi; sonuç **~1500 kat** hızlanma.
Nested kabuk 6,8 ms göstermişti — yani nested bu sayıyı **abartmış**, gerçek
oturum daha hızlı çıktı.

**Davranış doğrulamaları** (hepsi gerçek oturum, doğrudan `busctl` ile, yani
arama yedeğine düşme riski olmadan):

- **Grant kapalıyken** `ActivateWindow` üç farklı hedef için de `b false`
  döndü ve hiçbir pencere etkinleşmedi. Boş ve 300 karakterlik hedefler de
  `false`.
- **Belirsiz ad reddediliyor:** iki pencereye birden uyan `Desktop Icons` →
  `false`. Tek eşleşen `Claude` → `true`.
- **`skip-taskbar` pencereler hedef sayılmıyor:** `Desktop Icons 1` → `false`.
- **Olmayan hedef** → `false`. Üçünde de pcbridge arama yedeğine düşer.
- **Soğuk başlatma korundu:** kapalı Text Editor `launch` ile açıldı ve
  hemen ardından eklenti yoluyla etkinleştirilebildi (`b true`).
- **`desktop_lock` sonrası** eklenti yine `b false` döndü (`revoke_epoch`
  7 → 8) ve gerçek yayın yardımcısı süreci kalmadı.

**Yan kazanç — Adım 1'in kapıları da gerçek oturumda sınandı.** Boş bir Text
Editor taslağında: `ctrl+w` onaysız **reddedildi** (`force=true` verilmiş
olmasına rağmen — `force` bu kapıyı açmıyor), `confirm_close` ile geçti ve
belge kapandı. Etkinlik koruması da beklendiği gibi ateşledi: kullanıcı
makinenin başında olduğu için ilk `launch` çağrısı reddedildi.

Acil geri alma — kabuk açılmazsa Ctrl+Alt+F3 ile TTY'den de çalışır:

```bash
gnome-extensions disable pcbridge-gorunur@eymistaken.local
```

Sonuç olarak `PLAN.md` Task 6.4 artık "hızlı yolu tasarla" işi değil; gerçek
oturumda capability ve native orchestration parity'sini doğrulama işidir.

### Devralma sonrası tamamlanan üç şey

Adım 2 kullanım limiti nedeniyle yarıda kaldı ve devralındı. Kalanlar:

1. **Üç sözleşme testi kırmızıydı** (`test_window_focus.py`, yedek yol).
   Sebep implementation değil fixture'dı: testler `focused()`'ın iki kez
   çağrıldığını varsayıyordu. Eski `focus()` gerçekten baştan bir kez daha
   çağırıyordu ama sonucu (`before`) **hiçbir yerde kullanılmıyordu** — ölü
   kod, `HEAD`'de doğrulandı. Silinmesi doğru; testlerin iddiası değil
   **kurulumu** düzeltildi ve odağın tek kez okunduğu ayrıca sabitlendi.
2. **`focus` cihaz ihtiyacı iki yere kopyalanmıştı** ve `pcb-do`'ya hiç
   uygulanmamıştı. `devices_needed()` parametreli hale getirildi (varsayılan
   muhafazakâr), satır içi kopya kaldırıldı, `pcb-do` hem kapı hem ön açma
   hem `--dry-run` çıktısı için aynı tek sorgudan besleniyor. Gerçek süreçte
   doğrulandı: `pcb-do --dry-run` artık `klavye=evet` ve
   `focus yolu: GNOME aramasi (eklenti yok)` yazıyor.
3. **Gerçek oturum kurulum durumu ölçüldü** (yukarıda). Eklentinin zaten
   kurulu ve etkin olduğu, yeni kodun sonraki girişte yükleneceği bulundu.

## Adım 3 — Faz 3: ilk Rust capture subsystem (Gate 3)

`PLAN.md` Task 3.1 → 3.5, her biri ayrı commit. Plan dosya listesini,
acceptance'ı ve rollback'i zaten taşıyor; burada tekrarlanmıyor. Kritik
sınırlar:

- **3.1** `tests/fixtures/native/display_cases.json` hazır; Python ve Rust
  **aynı fixture'dan aynı tabloyu** üretmeli. Public index `(x,y)` sırasına
  göre 1'den başlar — primary sağda olsa da monitor 2. Doğrulanmayan geometry
  `DISPLAY_MAPPING_UNKNOWN` ile reddedilir, ilk monitöre düşülmez.
- **3.2** Capability sorgusu session **açmamalı**. Revoke/lock/timeout/EOF'ta
  session kapanır.
- **3.3** Bilinmeyen piksel formatı tahmin edilmez, reddedilir. Frame
  sequence/timestamp olmadan "en yeni frame" döndürülmez.
- **3.4** `capture.to_global()` **tek** koordinat girişi olarak kalır; shot ID
  Rust'a taşınmaz; varsayılan backend değişmez.
- **3.5** Capture başarısı ile görüntünün istemciye ulaşması ayrı doğrulanır;
  dosyanın diskte oluşması uçtan uca başarı sayılmaz.

## Adım 4 — Faz 4: paketleme, parity, varsayılan değişikliği (Gate 4)

Task 4.1 (build/package/`doctor.sh` tanısı) → 4.2 (gerçek Linux capture
parity, `PCBRIDGE_TEST_CAPTURE=1`) → 4.3 (varsayılan `native.capture = "rust"`).

**Gerçek makinede ölçüm ister.** `systemctl --user restart pcbridge` çalışan
işleri öldürür — önce `job_list`. Bu oturumun `--stdio` süreci yeni kodu
**çalıştırmaz**; doğrulama servise HTTP + statik token ile gider
(`tests/test_e2e.py` kalıbı).

## Adım 5 — Faz 5–8

| Faz | Ne | Gate |
|---|---|---|
| 5 | Input parity fixture'ları + batch safety, Rust klavye/pointer/clipboard | Gate 5 |
| 6 | Accessibility read/action + window orchestration (Adım 2'den beslenir) | Gate 6 |
| 7 | Mixed scale, XDG portal backend'i, buffered, adaptive | — |
| 8 | Python screencast helper'ının emekliye ayrılması, iki sürüm gate'i | Gate 7 |

Faz 5.1 (batch safety) Faz 3 ile **paralel** geliştirilebilir; rollout'u 4.3
sonrasına bağlı. Adım 1'deki kapılar 5.1'in yerine geçmez: 5.1
grant/lock/revoke/deadline kontrolü, Adım 1 ise eylem içeriği kapısı.

## Adım 6 — İmleç katmanı

Bulgular ve tasarım kararları aşağıda, "Kurtarılan kayıtlar" bölümünde.
**Önce ölçüm:**

1. Fiziksel farenin gerçek olay hızını ölç (`/dev/input/eventN`'den saniyedeki
   olay sayısı). 1000 Hz çıkarsa hipotez güçlenir, 125 Hz çıkarsa çürür ve
   başka yere bakmak gerekir.
2. Hipotez tutarsa konum **kare saatinde bir kez** uygulanır, her olayda değil.
3. `Main.layoutManager.addTopChrome` yerine `Main.uiGroup` denenir.

Acil geri alma **`gnome-extensions disable <uuid>`** — dizini silmek çalışan
eklentiyi durdurmuyor.

## Ertelenen (bilinçli)

- **Faz W (Windows) / Faz M (macOS)** — `PLAN.md`'nin D1 kararı (hangisi önce)
  verilmedi; Faz 5 tamamlanmadan başlamaz.
- **Faz G** — Tauri + React control plane.
- **`JARVIS.md`** — pcbridge'i kişisel asistana çevirme **teklifi**; ölçüm
  değil tasarım.

---

# Kurtarılan kayıtlar

Bu bölüm `22a5824:YAPILACAKLAR.md` içinden kurtarıldı. İçerik `b4fa5ed`
commit'inde silinmişti ve yalnızca git geçmişinde kalmıştı; beş belge hâlâ
buraya bağlantı veriyordu.

## `window_focus` — ölçüm ve sınırlar

**Belirti.** `window_focus` çağrıldığında masaüstünde Super'a basılıyor,
uygulama adı GNOME aramasına yazılıyor ve Enter'a basılıyor. Uygulama arama
sonuçlarında ilk sırada çıkmazsa Enter **web arama sağlayıcısına** düşüyor ve
tarayıcıda bir DuckDuckGo araması açılıyor. Kullanıcı bunu fark edip sordu.

**Ölçüm — 2026-09-02, `audit.log`.** Aşağıdaki satırlar `window_focus`
aracının değil, `computer_batch` içindeki **`focus` eyleminin** kayıtları
(`batch_step`) — ikisi de aynı `apps.focus()`'a gidiyor. Bunun ayrıca
yazılması gerekiyor, çünkü **`window_focus` olayları `ms` alanı taşımıyor**:
o olaylara bakarak bu ölçüm tekrarlanamaz. Ham satır:

```
{"ts": "2026-09-02T09:53:19", "event": "batch_step", "i": 0,
 "a": "focus 'PcBridge Desktop'", "ok": true, "ms": 6935}
```

Zaten **açık ve görünür** bir pencere için:

| Saat (2026-09-02) | Hedef | Süre |
|---|---|---|
| 09:53:19 | PcBridge Desktop | 6935 ms |
| 10:19:52 | PcBridge Desktop | 6669 ms |
| 10:27:40 | PcBridge Desktop | 6674 ms |
| 13:23:11 | PcBridge Desktop | 6683 ms |
| 17:11:17 | PcBridge Desktop | 6616 ms |
| 17:15:49 | PcBridge Desktop | 6631 ms |

Altı çağrının ortalaması **6701,3 ms** (en az 6616, en çok 6935 ms).
Doğrudan pencere etkinleştirme
milisaniye sürer. Tekrarlamak için:

```bash
grep "PcBridge Desktop" ~/.local/state/pcbridge/audit.log
```

**Etkilenen yollar — üçü de aynı fonksiyona gidiyor.** `window_focus`'u tek
başına düzeltmek yetmez:

| Yer | Ne |
|---|---|
| `pcbridge/tools.py` | `window_focus` MCP aracı |
| `pcbridge/tools.py` | `computer_task(app=…)` — **en kötü durum burada** |
| `pcbridge/desktop/ops.py` | `DeviceOps.focus` — hem `computer_batch` hem `bin/pcb-do` |
| `pcbridge/desktop/batch.py` | `"focus": 7000.0` sabit bütçe; hızlı yol gelirse yanlış kalır |
| `pcbridge/desktop/ops.py` | `devices_needed()`: listede `focus` varsa klavye açılıyor; gereksiz kalır |

`computer_task(app=…)` bu bedeli her seferinde ödüyor: önce `launch()`, 1,5
saniye bekleme, **sonra** `focus()` — yani uygulamayı kendi açtığını **bilerek**
arama yoluna giriyor.

**Sunucu bunu zaten biliyor.** `audit.log`, 2026-08-23:

```
window_focus_error · target: gnome-shell
'gnome-shell' one alinamadi; odakta 'gnome-terminal-server | eymistaken@ZorinOS: ~' var.
GNOME aramasi baska bir sonuc secmis olabilir.
```

**Bozulmaması gereken (faz 1 ve faz 3 buna dayanıyor).** "Bul + başlat + öne
al"ın tek araçta olması kaza değil, bilinçli bir karar — 2026-08-21:

- `a0c7d0a` (**faz 1**) `window_focus`'un docstring'ini tam da "kapalıysa açar
  da" desin diye yeniden yazdı; ajan bunu bilmediği için `shell_run`'a
  düşüyordu.
- `4eda165` (**faz 3**) kabuktan GUI başlatmaya kapı koydu ve ret gerekçesi
  ajanı doğrudan `window_focus`'a yönlendiriyor.

Ayırma yapılırken ikisi de ayakta kalmalı: **ajana verilen kapı tek kalsın**
(içeride iki yol, dışarıda tek araç) ve **kapalı uygulamayı açma yeteneği
kaybolmasın.**

**Tespit yarısı zaten çözülü.** "Pencere açık mı" sorusunu `window_list`
(→ `tree.windows()`, AT-SPI) bugün cevaplıyor. Eksik olan yalnızca
**etkinleştirme**; mesai oraya harcansın.

**Yan etki.** Bu araç masaüstü izni açıkken kullanıcının tarayıcısında
istenmeyen sekme açabiliyor ve pencere düzenini bozabiliyor.

## İmleç katmanı — neden geri alındı, nereden devam edilir

**Durum: çalışıyordu, ama gerçek kullanımda bozdu ve geri alındı.**
Kod git geçmişinde: `2cac1b3` (ilk hâli) ve `3b15559` (son tasarım).

**Zor kısım çözüldü.**
`Meta.CursorTracker.get_for_display(global.display).set_pointer_visible(false)`
gerçek oturumda, gerçek donanımda imleci gizliyor ve **gizli kalıyor** —
çağrıdan 6 saniye sonra hâlâ `false`, kompozitörden tek bir geri açma gelmedi.
Görsel kanıt: imleç durağan bir monitöre konup gizli/görünür kareleri
karşılaştırıldı; fark tam olarak imlecin bulunduğu noktada, **13×21 px**,
ekranda başka hiçbir piksel değişmedi. Yani tema değiştirme yedeğine düşmeye
gerek yok.

**Neden geri alındı.** Gerçek makinede, **fiziksel fareyle**: tıklamalar
basmıyor ve fare donuyor. Ajanın sentetik faresiyle hiç görülmedi. Üç hipotez
test edildi, üçü de **yanlış** çıktı:

1. *"`addTopChrome` ile izlenen aktör her harekette girdi bölgesini yeniden
   hesaplatıyor"* → kabuğun içine konan ana döngü gözcüsü yoğun harekette 1600
   tıkta **0 gecikme** gösterdi.
2. *"Düğme basılıyken imleç takibi duruyor"* → tut + 3 hareket + kare: imleç
   son konumda (891 parlak piksel), basma noktasında 0. **Takip ediyor.**
3. *"Tıklamalar yutuluyor"* → `BUTTON_PRESS=2 · BUTTON_RELEASE=2` görüldü ve
   gerçek oturumda Chrome sekmesi tıklamayla değişti.

**Bütün testlerin ortak kusuru: hepsi sentetik fare ile yapıldı.** Fiziksel
fareyle ölçülmemiş tek fark **olay hızı**. Kod aktörü *her* fare olayında
yeniden konumlandırıyordu; 1000 Hz'lik bir fare saniyede 1000 yeniden çizim
demek, sentetik testte ise ~50 ölçüldü.

**Tasarım kararları (tekrar sorulmasın diye).**

- Şekil: uç + geriye süpürülmüş iki kanat + arka çentik (kâğıt uçak / gönder
  oku). Dört aday arasından kullanıcı seçti.
- Renk: **içi koyu gri→siyah gradyan, etrafı ince beyaz şerit** — kullanıcının
  kendi imleci de böyle. Düz beyaz gövde beyaz zeminde kayboluyor.
- Parıltı: **şeklin çevresini** sarıyor, ucun etrafını değil. Cairo'da
  bulanıklık yok; giderek genişleyen eş saydamlıkta halkalarla yapılıyor ve
  katman başına saydamlık **birikime göre** hesaplanmalı (sabit alfa verilince
  şeklin dibinde beyaz bir yığın oluşuyor). Seçilen değerler: yayılım 12 px,
  tepe saydamlık 0,26.
- Basma hissi (tıklamada küçülüp büyüme) istendi, sonra iptal edildi. Ama
  **yapılabilir olduğu ölçüldü**: `global.stage` `captured-event` düğme
  olaylarını görüyor. Kural: asla `Clutter.EVENT_STOP` dönme, olayı tüketmek
  masaüstünü kilitler.

**Eklenti kapsamı dışında kalanlar:** ayar arayüzü, extensions.gnome.org'a
yayımlama, GNOME 46 dışındaki sürümler.

---

# İlerleme kaydı — native migration (Faz 0–2)

Aşağısı `b4fa5ed`–`a4dd107` arasında tutulan task kaydıdır; olduğu gibi
korunuyor.

## Task 0.1 — Gerçek capture ve input test izinlerini ayır

**Durum:** `tamamlandı`

**Amaç:** `PCBRIDGE_TEST_CAPTURE=1` seçiminin klavye veya fare aygıtı açmamasını garanti etmek; gerçek input ve batch testlerini ayrı, açık izinlere bağlamak.

**Değişen dosyalar:** `tests/test_desktop.py`, `tests/test_test_safety.py`, `CLAUDE.md`

**Yapılanlar:**

- Task başlamadan önce bütün live-test bayrakları kaldırılarak güvenli desktop baseline çalıştırıldı.
- `test_real_hold` yalnızca `PCBRIDGE_TEST_INPUT=1` ile seçilecek hale getirildi.
- Gerçek batch seçimi hem `PCBRIDGE_TEST_INPUT=1` hem `PCBRIDGE_TEST_BATCH=1` gerektiriyor.
- Capture-only seçimin iki capture testini seçip uinput ve batch testlerini atladığı ayrı subprocess testiyle sabitlendi. Alt süreçte klavye ve pointer constructor'ları fail sentinel'iyle değiştirildi.
- `CLAUDE.md` komutları dört live-test iznini ayrı ayrı açıklayacak şekilde güncellendi; ölçülmemiş eski assertion sayıları kaldırıldı.

**Test sonuçları:**

- `env -u PCBRIDGE_TEST_CAPTURE -u PCBRIDGE_TEST_INPUT -u PCBRIDGE_TEST_ATSPI -u PCBRIDGE_TEST_BATCH ./.venv/bin/python tests/test_desktop.py`
  - Kısıtlı ortamda ilk deneme session D-Bus erişimi engellendiği için `test_ambiguous_guard` sırasında durdu; bu bir kod assertion başarısızlığı değildi.
  - Gerçek session erişimiyle tekrar: `578 geçti, 0 kaldı`.
- `./.venv/bin/python tests/test_test_safety.py` → `1 test`, `OK`.
- Regression test red/green kontrolü: hold selector geçici olarak eski capture bayrağına döndürüldüğünde test `FAILED`; doğru input selector'ı geri getirildiğinde `OK`.
- Ek baseline `./.venv/bin/python tests/test_models.py` → `102 geçti, 4 kaldı`. Dört başarısız assertion aynı eski beklentiden geliyor: testler Antigravity varsayılanını `gemini-3.6-flash` bekliyor, mevcut private config `gemini-3.8-flash` çözüyor. Task 0.1 agent politikasını değiştirmedi; bu sonuç Task 0.2'de private config bağımlılığı kaldırılırken ele alınacak.

**Gate:** Task 0.1 acceptance geçti. Gate 0, Task 0.2 tamamlanana kadar açık.

**Commit:** `c1a5c4b` (`test: separate capture and input opt-ins`)

**Rollback:** Task commit'i tek başına geri alınabilir. Güvenli opt-in sınırı olmadan capture live suite çalıştırılmayacak.

**Sonraki somut adım:** Task 0.2'de sentetik baseline fixture'larını ve contract suite'i oluştur.

## Task 0.2 — Public contract ve backend parity fixture'larını oluştur

**Durum:** `tamamlandı`

**Amaç:** Native migration boyunca Python ve Rust provider'ların karşılaştırılacağı, private config veya canlı masaüstü gerektirmeyen sözleşme baseline'ını kurmak.

**Değişen dosyalar:** `tests/contracts/`, `tests/fixtures/native/`, `tests/test_models.py`, `docs/native/baseline.md`

**Yapılanlar:**

- MCP tool adları, annotations, seçili input schema/default değerleri ve content block sırası snapshot olarak sabitlendi.
- İki monitör, primary sağda, portrait, fractional scale, crop-before-resize, shot lookup, `--out`, stale ve ambiguity senaryoları sentetik fixture'lara çıkarıldı.
- Capture sözleşmesi provider factory düzeninde kuruldu; mevcut Python adapter'ı aynı fixture'ları çalıştırıyor.
- Model testlerinin private `config.toml` bağımlılığı kaldırıldı; `config.example.toml` kullanılıyor.
- Önceden ölçülen dört model hatasının kök nedeni eski `gemini-3.6-flash` beklentisiydi. Örnek config ile `gemini-3.8-flash` hizası ayrı `3d7c31e` commit'inde düzeltildi.
- Ölçülen komutlar, sonuçlar ve live-test sınırı `docs/native/baseline.md` içinde kaydedildi.

**Test sonuçları:**

- `tests/test_desktop.py` bütün live bayrakları unset → `578 geçti, 0 kaldı`.
- `tests/test_models.py` bütün live bayrakları unset → `106 geçti, 0 kaldı`.
- `tests/test_test_safety.py` bütün live bayrakları unset → `1 test`, `OK`.
- Contract discovery → `7 tests`, `OK`.
- `tests/test_e2e.py` plan gereği çalıştırılmadı.

**Gate:** Gate 0 geçti. Baseline private config, çalışan server, gerçek agent ve desktop olmadan tekrar üretildi.

**Commit:** `d010462` (`test: establish desktop provider contracts`)

**Rollback:** Task 0.2 yalnızca test fixture'ları ve baseline belgesi ekleyecek; production davranışını değiştirmeyecek.

**Sonraki somut adım:** Task 1.1 için tool/CLI kaynak sahipliğini `DesktopRuntime` sınırına çıkar.

## Task 1.1 — `DesktopRuntime` ve Python provider adapter'ını çıkar

**Durum:** `tamamlandı`

**Amaç:** MCP tool registration ile desktop kaynak sahipliğini ayırmak; mevcut Python davranışını provider adapter arkasından korumak.

**Değişen dosyalar:** `pcbridge/desktop/contracts.py`, `pcbridge/desktop/runtime.py`, `pcbridge/desktop/backends/`, `pcbridge/tools.py`, `pcbridge/server.py`, desktop CLI modülleri, `pcbridge/desktop/ops.py`, contract testleri.

**Yapılanlar:**

- Capture, input, accessibility ve grant protokolleri açık provider sınırlarına çıkarıldı.
- Mevcut Python capture, uinput ve AT-SPI yolları Python provider adapter'larına bağlandı.
- `DesktopRuntime` provider kaynaklarını, screencast grant zamanlayıcısını ve idempotent lifecycle kapanışını sahipleniyor.
- MCP registration ve `pcb-shot`/`pcb-do`/`pcb-lock` aynı runtime factory'yi kullanıyor.
- `computer_task` heartbeat orchestration'ı Python'da kaldı; grant yenilemesi runtime arayüzünden geçiyor.
- `DeviceOps` capture bağımlılığını constructor üzerinden alıyor.
- Server çıkışı ve CLI yürütme yolları runtime'ı `finally` içinde kapatıyor.

**Test sonuçları:**

- Contract discovery → `12 tests`, `OK`.
- `tests/test_desktop.py` bütün live bayrakları unset → `578 geçti, 0 kaldı`.
- `tests/test_models.py` bütün live bayrakları unset → `106 geçti, 0 kaldı`.
- `tests/test_test_safety.py` bütün live bayrakları unset → `1 test`, `OK`.
- `python -m pcbridge.server --check -c config.example.toml` → exit `0`.
- Runtime contract, lazy construction, iki runtime izolasyonu, timer sahipliği, idempotent close ve MCP/CLI/server lifecycle kapanışını doğruluyor.

**Gate:** Task 1.1 acceptance geçti; Phase 1 devam ediyor.

**Commit:** `3a6a36f` (`refactor: centralize desktop runtime ownership`)

**Rollback:** Runtime wiring task commit'i bağımsız geri alınabilir.

**Sonraki somut adım:** Task 1.2 capability snapshot ve typed provider hata eşlemesini ekle.

## Task 1.2 — Runtime capability ve typed error katmanını ekle

**Durum:** `tamamlandı`

**Amaç:** Bir desktop özelliğinin implementasyonu ile o anda kullanılabilir olmasını ayrı, typed durumlar olarak raporlamak.

**Değişen dosyalar:** `pcbridge/desktop/errors.py`, `capabilities.py`, `contracts.py`, `runtime.py`, `backends/python.py`, desktop MCP/CLI hata yakalama yolları ve `tests/contracts/test_capabilities.py`.

**Yapılanlar:**

- Planın sekiz kategorili hata taxonomy'si ve bütün başlangıç hata kodları `DesktopError` üzerinde kararlı alanlarla tanımlandı.
- On üç zorunlu capability anahtarı probe evidence'i, son operation evidence'i ve ayrı authorization durumu ile modellenip thread-safe cache'e bağlandı.
- Capture monitor/window, pointer/keyboard, clipboard read/write, accessibility read/action ve window list/focus/move-resize durumları birbirinden bağımsız probe ediliyor.
- Capture capability token'ı dependency, açık screencast session ve monitor topolojisini; input token'ı uinput izin/aygıtı ile Wayland clipboard socket'ini izliyor.
- Legacy `CaptureError`, `InputError`, `UiTreeError` ve monitor hataları provider sınırında mesaj substring'i kullanılmadan typed hatalara çevriliyor. MCP ve CLI mevcut Türkçe hata metinlerini yakalamaya devam ediyor.
- Capability probe uinput aygıtı oluşturmuyor, klavye/fare olayı yazmıyor, screencast veya portal oturumu açmıyor.
- Gerçek makinede salt okunur probe 13 anahtarın tamamını `143,5 ms` içinde döndürdü. Grant kapalıyken backend capability'leri ayrıca raporlandı; authorization `grant_remaining_seconds=0` olarak ayrı kaldı.

**Test sonuçları:**

- Contract discovery → `20 tests`, `OK`.
- `tests/test_desktop.py` bütün live bayrakları unset → `578 geçti, 0 kaldı`.
- `tests/test_models.py` bütün live bayrakları unset → `106 geçti, 0 kaldı`.
- `tests/test_test_safety.py` bütün live bayrakları unset → `1 test`, `OK`.
- `python -m pcbridge.server --check -c config.example.toml` → exit `0`.
- `tests/test_e2e.py` plan gereği çalıştırılmadı.

**Acceptance:** Capture supported + pointer permission-required ile AT-SPI list degraded + move/resize unsupported sentetik sözleşmeleri geçti. Provider exception metni değiştirilse de hata kodunun değişmediği capture/input/accessibility testleri geçti.

**Gate:** Phase 1 devam ediyor.

**Commit:** `165e336` (`feat: add typed desktop capabilities`)

**Rollback:** Capability/error commit'i bağımsız geri alınabilir; provider sözleşmeleri Task 1.1 halinde kalır.

**Sonraki somut adım:** Task 1.3'te `system_capabilities` aracını ve FastMCP structured error sunumunu ekle.

## Task 1.3 — Structured MCP hata yüzeyini kur

**Durum:** `tamamlandı`

**Amaç:** Agent'ın Pcbridge grant'i ile işletim sistemi backend/izin hatalarını
ayırt edip doğru sonraki eylemi seçebilmesini sağlamak.

**Değişen dosyalar:** `pcbridge/desktop/presentation.py`, `safety.py`,
`batch.py`, `pcbridge/tools.py`, `pcbridge/server.py`, `requirements.txt`, MCP
contract testleri ve kullanıcı/geliştirici belgeleri.

**Yapılanlar:**

- Read-only ve grant gerektirmeyen `system_capabilities` MCP aracı eklendi;
  on üç capability ile authorization durumunu ayrı structured alanlarda sunuyor.
- SafetyGate retleri kararlı hata kodu, retry bilgisi, önerilen eylem ve
  `pcbridge.desktop` scope'u taşıyor.
- Desktop execution hata dalları eski Türkçe metni `content` içinde koruyup
  `structuredContent.error` ve `isError=true` döndüren FastMCP `ToolResult`
  sunumuna geçirildi.
- Capture, pointer, keyboard, accessibility ve window kapsamları birbirinden
  ayrıldı; kullanıcı mesajından substring ile sınıflandırma yapılmıyor.
- Dinamik masaüstü araçlarında otomatik output schema çıkarımı açıkça kapatıldı.
- `computer_batch` final capture/UI okuması başarısız olduğunda tamamlanan adım
  raporunu ve sayısını koruyor; eylemleri yeniden çalıştırmıyor.
- FastMCP, başlangıçta doğrulanan `3.4.5` sürümüne sabitlendi. OAuth ve
  shell/job sonuç sözleşmeleri değiştirilmedi.

**Test sonuçları:**

- Contract discovery → `25 tests`, `OK`; in-memory FastMCP istemcisi content,
  `isError`, structured scope, output schema ve batch partial sonucunu doğruladı.
- `tests/test_desktop.py` bütün live bayrakları unset → `578 geçti, 0 kaldı`.
- `tests/test_models.py` → `106 geçti, 0 kaldı`.
- `tests/test_test_safety.py` → `1 test`, `OK`.
- `python -m pcbridge.server --check -c config.example.toml` → exit `0`.
- `pip check` → bozuk bağımlılık yok; kurulu FastMCP sürümü `3.4.5`.
- Gerçek Python provider'larıyla read-only MCP ölçümü 13 capability ve 7 scope'u
  `183,8 ms` içinde, `is_error=False` ile döndürdü.
- `tests/test_e2e.py` plan gereği çalıştırılmadı.

**Acceptance:** Pcbridge grant, capture izni ve pointer izni sentetik wire
sözleşmesinde sırasıyla `pcbridge.desktop`, `os.capture` ve `os.pointer` olarak
ayırt edildi. Dinamik desktop tool listesinde output schema üretilmedi.

**Gate:** Task 1.3 acceptance geçti; Gate 1, Task 1.4 tamamlanana kadar açık.

**Commit:** `a414d05` (`feat: expose structured desktop errors`)

**Rollback:** Presentation/tool commit'i bağımsız geri alınabilir; Task 1.2 typed
provider hata ve capability katmanı yerinde kalır.

**Sonraki somut adım:** Task 1.4'te shell/filesystem/accessibility execution
yollarının varsayılan ve tool açıklamalarını çok yollu modele hizala.

## Task 1.4 — Çok yollu execution sözleşmesini düzelt

**Durum:** `tamamlandı`

**Amaç:** Shell, filesystem, job ve accessibility yollarını desktop grant'inden
bağımsız ürün davranışı olarak korumak; araç açıklamalarındaki tek-yol
varsayımlarını kaldırmak.

**Değişen dosyalar:** `pcbridge/config.py`, `config.example.toml`,
`pcbridge/tools.py`, `pcbridge/server.py`, `skills/computer-use/SKILL.md`,
`KULLANIM.md`, `CLAUDE.md`, desktop/E2E kaynak testleri ve yeni
`tests/contracts/test_execution_paths.py`.

**Yapılanlar:**

- `block_gui_launch_in_shell` dataclass ve TOML loader varsayılanı `false`
  yapıldı; örnek config aynı değeri açıkça belgeliyor.
- Kullanıcının açıkça verdiği `true` ve `gui_launch_blocklist` hâlâ okunuyor
  ve masaüstü grant'i açıkken eşleşen komutu engelliyor.
- Shell tool açıklamalarındaki mutlak GUI yasağı kaldırıldı. Yeni sürecin
  pcbridge service ömrünü paylaşması ile çalışan Chrome'a URL devretmenin
  farklı davranışları tool, server ve kullanıcı belgelerinde açıklandı.
- `ui_dump` açıklamasındaki eski “istemci görüntü okuyamaz” varsayımı kaldırıldı.
- `desktop_unlock` yalnızca Pcbridge'in süreli grant'ini açtığını, işletim
  sistemi izinlerinin `system_capabilities` içinde ayrı olduğunu söylüyor.
- Permission/backend hatasından sonra execution yolu değiştirilirken kullanıcı
  görevinin ve mevcut izin kapsamının korunması server/skill yönergesine eklendi.

**Test sonuçları:**

- Contract discovery → `31 tests`, `OK`.
- Mock Chrome URL komutu varsayılan policy ile geçti; hiçbir gerçek Chrome veya
  GUI süreci çalıştırılmadı.
- Explicit `block_gui_launch_in_shell=true` + Chrome blocklist hem TOML'dan
  okundu hem mock shell çağrısını yürütmeden engelledi.
- Desktop disabled sentetik MCP'de `shell_run`, `fs_write` ve `job_list`
  başarıyla çalıştı.
- `tests/test_desktop.py` bütün live bayrakları unset → `578 geçti, 0 kaldı`.
- `tests/test_models.py` → `106 geçti, 0 kaldı`.
- `tests/test_test_safety.py` → `1 test`, `OK`.
- `python -m pcbridge.server --check -c config.example.toml` → exit `0`.
- `tests/test_e2e.py` plan gereği çalıştırılmadı.

**Acceptance:** Varsayılan config deterministik shell yolunu açık tutuyor;
explicit blocklist davranışı korunuyor; desktop kapısı shell/filesystem/job
araçlarını kapatmıyor.

**Gate:** Gate 1 geçti. Python provider/runtime/capability/error ve çok yollu
execution sözleşmeleri native entegrasyondan önce sabitlendi.

**Commit:** `fe355b2` (`fix: preserve independent execution paths`)

**Rollback:** Task commit'i bağımsız geri alınabilir; explicit `true` kullanan
config'lerin davranışı rollback gerektirmeden korunur.

**Sonraki somut adım:** Task 2.1'de desktop'a bağlanmayan Rust workspace ve
framed protocol test harness'ını kur.

## Task 2.1 — Rust workspace ve executable protocol harness

**Durum:** `tamamlandı`

**Amaç:** Native desktop API'lerine dokunmadan versioned, framed stdio transport
sınırını gerçek executable üzerinde doğrulamak.

**Değişen dosyalar:** `rust/` altındaki iki crate, exact toolchain ve lockfile;
`.gitignore`, `docs/native/protocol-v1.md` ve `CLAUDE.md` belge haritası.

**Yapılanlar:**

- `pcbridge-core` içine unsafe kodu yasaklayan frame parser/writer ve typed
  protokol hataları eklendi. Dört byte unsigned big-endian header boyu,
  64 KiB JSON ve 128 MiB binary sınırı allocation öncesinde uygulanıyor.
- Kısmi read/write işlemleri tamamlanıyor; eksik header/payload, malformed JSON,
  eksik `binary_len` ve boyut ihlalleri stdout'u kirletmeden temiz hata çıkışı
  üretiyor. Geçersiz request değerleri stderr tanılarına yansıtılmıyor.
- `pcbridge-native` ilk request'te `initialize` zorunluluğunu ve major/minor
  negotiation'ı uyguluyor. Yalnızca `initialize`, `ping`, `capabilities`,
  `cancel` ve `shutdown` dispatch ediliyor.
- Unknown method structured error döndürüp bağlantıyı açık tutuyor; unknown
  major framed error'dan sonra bağlantıyı kapatıyor. Pipelined request ID'leri
  ayrı response'larda korunuyor.
- Deterministik fake backend default dışı `test-harness` Cargo feature'ı ve
  ayrıca `--test-mode` argümanı gerektiriyor. Default release binary bu
  argümanı exit `2` ile reddediyor ve yalnızca `protocol-only` backend ilan
  ediyor.
- `Cargo.lock` çözümlenen sürümleri sabitliyor. Bağımlılık ağında desktop,
  D-Bus, PipeWire, socket veya async runtime kütüphanesi yok.
- Wire contract, lifecycle, error envelope, stdout/log sınırı ve test kipi
  `docs/native/protocol-v1.md` içinde belgelendi.

**Test sonuçları:**

- Test-first kırmızı koşum: boş executable ile integration suite `1 geçti,
  9 kaldı`; framing/dispatch uygulamasından sonra yeşile döndü.
- `cargo fmt --all -- --check` → exit `0`.
- `cargo clippy --workspace --all-targets --all-features -- -D warnings` →
  exit `0`.
- `cargo test --workspace --all-targets` → `16 geçti, 0 kaldı`.
- `cargo test --workspace --all-targets --features
  pcbridge-native/test-harness` → `17 geçti, 0 kaldı`.
- `cargo build --release --locked -p pcbridge-native` → exit `0`; default
  release üzerinde `--test-mode` → exit `2`.
- Release binary initialize → capabilities → shutdown ölçümü: response ID'leri
  `measure:1`, `measure:2`, `measure:3`; backend `protocol-only`; stdout `391`
  byte framed veri; stderr `0` byte; `strace` connect syscall sayısı `0`.
- `ldd` yalnızca `libgcc_s`, `libc` ve dynamic loader gösterdi.
- Python contract discovery → `31 tests`, `OK`; örnek config server check →
  exit `0`.
- `cargo audit --no-fetch`, yerel RustSec advisory veritabanı bulunmadığı için
  çalışamadı. Kullanıcının cloud GitHub'a dokunmama talebi nedeniyle online
  advisory güncellemesi tamamlanmadı. Lockfile ve küçük dependency tree elle
  incelendi.
- `tests/test_e2e.py` plan gereği çalıştırılmadı; hiçbir live desktop test
  bayrağı açılmadı.

**Acceptance:** Partial frame, oversized header/binary, unknown method/version,
zorunlu handshake, EOF/malformed frame, stdout framing ve pipelined ID testleri
geçti. Gerçek executable ölçümünde desktop bağlantısı kurulmadı.

**Gate:** Task 2.1 acceptance geçti. Gate 2, Python supervisor ve revoke
lifecycle task'ları tamamlanana kadar açık.

**Commit:** `b53ea60` (`feat: add native protocol harness`)

**Rollback:** `b53ea60` bağımsız olarak geri alınabilir; Python runtime ve
desktop backend seçimi bu task'ta değiştirilmedi.

**Sonraki somut adım:** Kullanıcı devam istediğinde Task 2.2'de Python
`NativeClient` supervisor'ını fake helper contract'larıyla uygula.

## Task 2.2 — Python `NativeClient` supervisor

**Durum:** `tamamlandı`

**Amaç:** Native child process'in framing, concurrency, timeout, cancellation,
crash ve shutdown lifecycle'ını MCP stdio taşımasından tamamen ayrı yönetmek;
helper arızasında Python server ve non-desktop execution yollarını ayakta tutmak.

**Değişen dosyalar:** `pcbridge/native/`, `pcbridge/config.py`,
`config.example.toml`, `docs/native/protocol-v1.md`,
`tests/contracts/test_native_client.py` ve executable fake helper fixture'ı.

**Yapılanlar:**

- Binary discovery sırası `PCBRIDGE_NATIVE_BIN` → `[native].binary_path` →
  paketlenmiş target yolu olarak sabitlendi. Explicit ama geçersiz yol daha
  düşük önceliğe sessizce düşmeden `NATIVE_NOT_FOUND` döndürüyor.
- Framed protokol parser/writer'ı Python sınırında aynı 64 KiB JSON ve 128 MiB
  binary limitlerini, kısa read/write döngülerini ve major/minor doğrulamasını
  uyguluyor. Yerel olarak encode edilemeyen request sağlıklı helper'ı düşürmeden
  `INVALID_FRAME` oluyor.
- `NativeClient` lazy child başlatma, ayrı reader/writer/stderr thread'leri,
  process nesliyle bağlı request ID'leri, out-of-order response korelasyonu,
  16 pending sınırı ve bounded outgoing queue kullanıyor.
- Deadline beklemesi pipe yazımından bağımsız hale getirildi. Timeout request'i
  pending tablodan atomik çıkarıyor ve best-effort `cancel` kuyruğa alıyor;
  henüz yazılmamış stale request'ler atlanıyor.
- EOF/crash/protocol ihlalinde aynı process neslinin bütün pending request'leri
  typed hatayla tamamlanıyor. Sonraki yeni request temiz process başlatabiliyor;
  eski request hiçbir durumda replay edilmiyor.
- `initialize` sonucundaki `instance_id`, `native_version`, `platform` ve
  `features` alanları kullanılabilirlikten önce doğrulanıyor ve immutable
  handshake olarak saklanıyor.
- Helper yalnızca allowlist grafik oturum ortamını alıyor; parola/token/secret
  adları eleniyor. IPC descriptor'ları inheritable değil; job child
  process'lerine taşınmadığı `/proc` ölçümüyle doğrulandı.
- stderr stdout'a veya MCP yanıtına yazılmadan sürekli boşaltılıyor ve yalnızca
  son 64 KiB bellekte tutuluyor. Kapanış framed `shutdown` → terminate → kill
  sırasını izliyor ve child'ı topluyor.
- `[native] capture="python"` ve isteğe bağlı `binary_path` gerçekten parse
  ediliyor. Python capture varsayılan ve tek runtime yolu olarak kaldı; native
  capture entegrasyonu açılmadı.

**Test sonuçları:**

- TDD kırmızı koşumları önce eksik `NativeSpec`, ardından eksik protocol ve
  client modüllerinde beklenen import hatalarını verdi; her dilim uygulandıktan
  sonra yeşile döndü.
- Native supervisor contract'ları → `20 tests`, `OK`. Stderr flood,
  out-of-order response, timeout/cancel, bloke pipe deadline'ı, process crash,
  no-replay restart, invalid frame/version/handshake, missing binary, pending
  limit, environment/descriptor izolasyonu ve shutdown escalation kapsandı.
- Tam contract discovery → `51 tests`, `OK`.
- `tests/test_desktop.py`, bütün live bayrakları unset → `578 geçti, 0 kaldı`.
- `tests/test_models.py` → `106 geçti, 0 kaldı`.
- `tests/test_test_safety.py` → `1 test`, `OK`.
- `python -m compileall` ve örnek config ile server `--check` → exit `0`.
- Task 2.1 release binary'siyle Python supervisor ölçümü: handshake version
  `0.1.0`, platform `linux`, backend `protocol-only`, capability listesi boş,
  ping nonce korundu, stderr `0` byte, kapanış sonrası process çalışmıyor,
  toplam süre `2.1 ms`.
- `tests/test_e2e.py` plan gereği çalıştırılmadı; hiçbir live desktop test
  bayrağı açılmadı.

**Acceptance:** Helper yokluğu typed hata veriyor ve lazy sınır nedeniyle server
ile non-desktop execution yollarını engellemiyor. Native stdout yalnızca private
pipe'a gidiyor ve MCP/stdout ölçümünü bozmuyor. Normal, timeout ve zorla kapatma
yollarının tümünde child process toplandı.

**Gate:** Task 2.2 acceptance geçti. Gate 2, atomik grant ve süreçler arası
revoke Task 2.3 tamamlanana kadar açık.

**Commit:** `824fe1b` (`feat: add native client supervisor`)

**Rollback:** `native.capture=python` varsayılanı runtime'ı Python yolunda
tutar; `824fe1b` bağımsız geri alınabilir.

**Sonraki somut adım:** Kullanıcı devam istediğinde Task 2.3'te grant state'ini
atomik yaz, native process'e senkronize et ve süreçler arası revoke/expiry
contract'larını uygula.

## Task 2.3 — Atomik grant ve süreçler arası revoke

**Durum:** `tamamlandı`

**Amaç:** Bir süreçte kapatılan veya süresi dolan masaüstü grant'inin diğer
Python süreçlerinde ve açık native helper oturumlarında yeniden kullanılamamasını;
geç kalan heartbeat'in grant'i diriltememesini sağlamak.

**Başlangıç kararı:** `desktop_unlock.json` ayrı ve sabit bir Unix lockfile
altında read-modify-write edilecek, aynı dizindeki geçici dosyadan atomik replace
ile yayımlanacak. Her yeni grant benzersiz `grant_id` alacak; revoke monoton
`revoke_epoch` artıracak. Native helper yalnızca bu kimliğe bağlanacak ve grant
oluşturma veya uzatma yetkisi taşımayacak.

**TDD sınırları:** Python lease store iki bağımsız `SafetyGate` örneğiyle;
native registry PID, process başlangıç kimliği ve instance ID ile; Rust watchdog
ise framed test-harness kaynağı ve gerçek state dosyasıyla sözleşme testine
alınacak. GNOME okuyucusunun yeni alanları yok sayması mevcut JavaScript testiyle
korunacak.

**Değişen dosyalar:** `pcbridge/desktop/lease.py`, `safety.py`, `runtime.py`,
`contracts.py`, `tools.py`, `pcbridge/native/registry.py`, `client.py`, Rust
core lease ve native lifecycle modülleri, Python/Rust contract ve integration
testleri, GNOME state testi ve `docs/native/protocol-v1.md`.

**Yapılanlar:**

- `desktop_unlock.json` ayrı ve sabit `desktop_unlock.lock` altında Unix
  `flock` ile korunuyor; aynı dizindeki `0600` geçici dosyadan `fsync` + atomik
  replace ile yayımlanıyor. State dizini `0700`, state ve lock dosyaları `0600`.
- Mevcut `until`, `hard_until`, `reason`, `granted`, `granted_by` alanları
  korunurken `schema_version`, benzersiz `grant_id` ve monoton `revoke_epoch`
  eklendi. Legacy grant Python için okunabilir kaldı; native session'a uygun
  sayılmıyor.
- `desktop_lock` hem MCP hem CLI yolunda önce epoch'u artırıp grant'i kapatıyor,
  sonra input/capture cleanup ve geçiş dönemi `kill_helpers()` çağrısını yapıyor.
- Heartbeat job başında gördüğü grant kimliğine bağlandı. Revoke ve sonraki yeni
  unlock sonrasında eski heartbeat eşleşemiyor, grant'i diriltemiyor veya yeni
  grant'i uzatamıyor.
- Native registry, `0700` session alt dizininde `0600` kayıtlarla PID, Linux
  process başlangıç kimliği ve instance ID tutuyor. Sinyal yolu kimliği yeniden
  doğrulayıp process'i `pidfd` ile sabitliyor; PID-reuse yarışında geniş ad/PID
  kill yapılmıyor.
- Rust core state'i read-only ve fail-closed okuyor. Native helper initialize
  sırasında yalnızca fresh grant'e bağlanıyor; korumalı dispatch'te yeniden
  doğruluyor ve 100 ms watchdog revoke, expiry, eksik veya bozuk state halinde
  kaynağı kapatıyor. Aynı helper sonraki grant'e yeniden bağlanmıyor ve grant
  oluşturma/uzatma metodu sunmuyor.
- İlk native opt-in ve rollback sırası protokol belgesine runbook olarak eklendi:
  revoke, eski stdio/service process'lerini kapat, helper kimliklerini doğrula,
  sonra yeni process başlat; eski aktif grant'i geri yükleme.

**Test sonuçları:**

- TDD kırmızı koşumları önce eksik Python lease/registry import'larında, sonra
  eksik Rust lease API'sinde ve native test resource metodunda beklenen şekilde
  başarısız oldu; uygulama dilimleri sonrasında yeşile döndü.
- Tam Python contract discovery → `64 tests`, `OK`.
- İki Python process + iki native helper integration → `1 test`, `OK`; revoke
  sonrası iki kaynak da ≤1 saniyede kapandı, yeni işlem reddedildi ve geç
  heartbeat başarısız oldu.
- Rust workspace, bütün target'lar ve test-harness → `22 geçti, 0 kaldı`.
  Revoke watchdog testi ayrıca `250 ms` üst sınırı, expiry testi `1 saniye`
  üst sınırı uyguluyor.
- `cargo fmt --check`; default ve test-harness için
  `cargo clippy --all-targets -- -D warnings`; locked release build → exit `0`.
- `tests/test_desktop.py`, bütün live bayrakları unset → `578 geçti, 0 kaldı`.
- `tests/test_models.py` → `106 geçti, 0 kaldı`;
  `tests/test_test_safety.py` → `1 test`, `OK`.
- GNOME `test_state.js` → `31 geçti, 0 kaldı`; yeni lease kimliği alanları
  okuyucuyu bozmadı.
- `python -m compileall` ve örnek config ile server `--check` → exit `0`.
- `tests/test_e2e.py` plan gereği çalıştırılmadı; capture, input, batch ve AT-SPI
  live bayraklarının hiçbiri açılmadı.

**Acceptance:** Atomik raw reader testi paralel revoke yazımları boyunca bozuk
JSON görmedi ve sabit lockfile inode'u değişmedi. Global epoch iki bağımsız
Python process'te kayıpsız arttı. İki native helper aynı revoke'u gördü, açık
kaynaklarını süre sınırında bıraktı, revoke sonrası ve replacement grant
sonrasında yeni korumalı iş başlatmadı. Late heartbeat grant'i diriltmedi;
GNOME görsel okuyucusu yeni alanlarla aynı davranışı korudu.

**Gate:** Gate 2 geçti. Gerçek native capture hâlâ açılmadı.

**Commitler:** `f9d9cc5` (`feat: make desktop grants process-safe`) ve
`c4aa76b` (`feat: enforce native grant revocation`).

**Rollback:** Önce `pcbridge.cli.lock` ile revoke et; registry kimliği eşleşen
native helper'ları ve eski stdio/service process'lerini kapat; sonra
`native.capture=python` ile yeni process başlat. Eski aktif grant'i geri yükleme.

**Sonraki somut adım:** Task 2.4'te screen lock ve user activity okumalarını
typed observation modeline al; unknown durumlarını fail-closed yap ve native
kaynakları lock signal'ına bağla.

## Task 2.4 — Native lock/activity observations ve safety ayrımı

**Durum:** `tamamlandı`

**Amaç:** Screen lock ve user activity durumlarını bool/`None` kaybı olmadan
typed observation olarak taşımak; belirsizlikte desktop erişimini fail-closed
reddetmek ve native kaynakları lock bağlantısı yaşam döngüsüne bağlamak.

**Başlangıç kararı:** Mevcut GNOME ScreenSaver ve Mutter IdleMonitor sınırları
korunacak, fakat policy yalnızca `known_locked`, `known_unlocked` ve `unknown`
lock durumlarını; activity için de known/unknown observation'ı tüketecek.
`force=true` yalnızca activity reddini aşacak. Unlock sonucu grant ile işletim
sistemi capability'lerini ayrı raporlayacak; uinput grant oluşturmanın ön koşulu
olmayacak.

**TDD sınırları:** Python public observation + `SafetyGate.check()` sözleşmesi,
MCP `desktop_unlock` structured sonucu ve Rust `DesktopStateProvider` + lifecycle
watchdog'u. Batch içinde ikinci activity ölçümü yapılmadığı mevcut execution
yüzeyinden çağrı sayısıyla doğrulanacak; hiçbir canlı capture/input/AT-SPI testi
çalıştırılmayacak.

**Yapılanlar:**

- Python ve Rust tarafında screen lock `known_locked`, `known_unlocked`,
  `unknown`; activity ise `known`/`unknown` ve geçerli `idle_ms` taşıyan typed
  observation modellerine alındı. Boolean IdleMonitor cevabı fail-closed
  reddediliyor.
- `SafetyGate` bilinmeyen lock durumunda read/write işlemlerini, bilinmeyen
  activity durumunda write işlemlerini typed hata kodlarıyla reddediyor.
  `force=true` yalnızca activity kontrolünü atlıyor; grant, revoke, expiry ve
  lock kontrollerini atlamıyor.
- `DesktopStateProvider` runtime sınırına eklendi. Capability ve authorization
  snapshot'ları aynı typed provider'dan besleniyor; lock durumu boolean'a
  indirgenmeden public sonuçta taşınıyor.
- Native GNOME provider, `org.gnome.ScreenSaver.ActiveChanged` sinyalini ve
  Mutter IdleMonitor'u zbus ile okuyor. Bağlantı kaybı veya bozuk cevap unknown
  oluyor ve açık native kaynak kapanıyor.
- Lease ve desktop-state watchdog'ları ayrıldı. Yavaş veya takılan D-Bus
  gözlemi revoke'un 200 ms denetim sözleşmesini geciktiremiyor; method çağrıları
  ayrıca 200 ms timeout kullanıyor.
- `desktop_unlock` uinput uygunluğunu grant ön koşulu olmaktan çıkardı. Capture
  kullanılabilirken pointer/keyboard unavailable olsa da grant açılıyor;
  structured sonuç grant, authorization ve `capability_limitations` alanlarını
  ayrı bildiriyor.
- Batch ve computer task activity'yi yalnızca başlangıçta bir kez okuyor;
  agent'ın kendi girdisini kullanıcı etkinliği sayacak mid-batch kontrol
  eklenmedi.
- Public davranış ve watchdog ayrımının gerekçesi
  `docs/native/protocol-v1.md` içinde kaydedildi.

**Test sonuçları:**

- TDD kırmızı/yeşil: yavaş desktop provider altında revoke önce 250 ms sınırını
  aştı; watchdog'lar ayrılınca aynı test geçti. Boolean activity parser testi
  önce `True -> 1 ms` hatasını üretti, parser sıkılaştırılınca geçti.
- Tam Python contract discovery → `72 tests`, `OK`.
- `tests/test_desktop.py`, bütün live bayrakları unset → `582 geçti, 0 kaldı`.
- `tests/test_models.py` → `106 geçti, 0 kaldı`;
  `tests/test_test_safety.py` → `1 test`, `OK`.
- GNOME `test_state.js` → `31 geçti, 0 kaldı`.
- Rust workspace default set → `19 geçti, 0 kaldı`; test-harness set →
  `25 geçti, 0 kaldı`. Desktop-state contract'larında lock/connection-loss
  kapanışı ve D-Bus'tan bağımsız revoke sınırı ölçüldü.
- `cargo fmt --check`; bütün target'lar ve test-harness için
  `cargo clippy -- -D warnings`; locked release build → exit `0`.
- Güncel RustSec advisory veritabanıyla `cargo audit`, 92 bağımlılık → bilinen
  vulnerability yok.
- `python -m compileall`, `git diff --check`, American English spelling taraması
  ve örnek config ile server `--check` → exit `0`.
- `tests/test_e2e.py` plan gereği çalıştırılmadı; capture, input, batch ve AT-SPI
  live bayraklarının hiçbiri açılmadı.

**Acceptance:** Capture-only fixture grant açtı ve pointer için
`DEVICE_NOT_GRANTED` limitation'ını structured sonuçta korudu. Locked ve unknown
session grant oluşturmadan reddedildi. Unknown lock read/forced write'ı, unknown
activity force'suz write'ı durdurdu. Native lock/unknown açık kaynağı 250 ms
sınırında kapattı; yavaş desktop observation aynı sınırdaki revoke'u geciktirmedi.

**Gate:** Gate 2 geçerli. Phase 2 tamamlandı; gerçek native capture hâlâ açılmadı.

**Commit:** `9d0fe92` (`feat: fail closed on unknown desktop state`)

**Rollback:** Önce desktop grant'i revoke et ve native helper'ları kapat; sonra
native desktop-state provider yerine Python provider ile yeni process başlat.
Yeni fail-closed Python policy korunmalı.

**Sonraki somut adım:** Task 3.1'de Mutter display snapshot'ını zbus ile oku,
monitor sırası/topology kimliğini contract'larla sabitle ve native capture
session'ın aynı snapshot'ı kullanacağı sınırı kur.
