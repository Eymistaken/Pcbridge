# WALKTHROUGH.md — nerede kaldık, ne kaldı

> Kurallar, mimari ve ölçülmüş makine gerçekleri: **[CLAUDE.md](CLAUDE.md)**.
> Native migration'ın implementation sözleşmesi: **[PLAN.md](PLAN.md)**.
> Bu dosya tek bir soruyu cevaplar: **sırada ne var ve şimdiye kadar ne yapıldı.**

Depoda tek "yapılacak iş" listesi budur. Başka bir dosyaya durum özeti
kopyalama: `AGENTS.md` bir kez `CLAUDE.md`'nin kopyası olarak üretilmişti ve
iki günde 83 satır ayrıştı. İki gerçeğin olduğu yerde biri eskir.

## Durum özeti

- **Aktif adım:** yok. Kalan tek iş Task 7.2 (XDG ScreenCast portal
  backend'i) ve o bu makinede doğrulanamıyor — aşağıya bakın.
- **Son tamamlanan adım:** Adım 6 — imleç katmanı geri geldi (2026-09-20),
  kare saati düzeltmesiyle ve **varsayılan kapalı**; gerçek fareyle doğrulama
  kullanıcıda (#8). Öncesinde: Task 7.3 ölçüldü ve **uygulanmadı** (2026-09-20):
  buffered'ın kazancı 64,5 ms / 3698 ms. Bunun yerine ölçülen darboğaz
  düzeltildi — `optimize=True` kaldırıldı, çekim 3698 ms'den **850 ms**'ye
  indi. 7.4 ön koşulu düştüğü için uygulanmadı. Task 7.1 aynı gün tamamlandı,
  **Gate 6** geçti. Pencere öne alma tek sırada
  toplandı: eklenti → zaten öndeyse tuş yok → kapalıysa tuşsuz başlatma →
  arama yedeği. Aramaya yalnızca kurulu uygulama adı yazılıyor, sonuç
  uygulamanın kimliğiyle doğrulanıyor. Başlatılan uygulama kendi systemd
  kapsamında. Ayrıntı: Adım 5 → Task 6.4.
- **Sıradaki uygulanabilir adım:** Task 7.2, ama önce bir karar: portal
  backend'i yalnızca GNOME/Mutter DIŞINDAKİ masaüstleri için ve bu makinede
  ne çalıştırılabiliyor ne de ölçülebiliyor (portal penceresine kullanıcının
  tıklaması gerekiyor). Kullanıcı isterse yazılır ve "yazıldı, hiç
  çalıştırılmadı" diye kaydedilir.
- **Blocker:** Yok
- **Son doğrulanan gate:** **Gate 6 geçti** (2026-09-20): erişilebilirlik
  okuma, eylem ve pencere işlemleri gerçek masaüstünde doğrulandı. Gate 5
  2026-09-19'da, Gate 4 2026-09-13'te geçti. stdio istemcileri, uygulama
  kapatılıp açılınca yeni koda geçer (#5).
- **Native migration içindeki sıradaki task:** 7.2 → 7.3 → 7.4, sonra Faz 8 (**Gate 7**)
- **Kullanıcıyla yapılan kontroller (2026-09-13):** #1, #3, #4 yapıldı; #5'in
  servis tarafı yapıldı; #2 (GitHub) kullanıcının kararıyla bekliyor. Ayrıntı:
  Adım 4 → "Kullanıcıyla yapılan kontroller".

Çalışma kuralı (kullanıcı isteği, 2026-09-13, öncekinin yerine): her task
sonunda güvenli testler çalıştırılır, sonuç bu dosyaya yazılır ve değişiklikler
yalnızca yerel commit'lenir. **Push yapılmaz ve cloud GitHub'a dokunulmaz.**
Her task bitince durulur ve sonraki task için kullanıcı onayı beklenir.
Gerçek klavye/fare testi kullanıcı yokken çalıştırılmaz. 2026-09-19'da kullanıcı
canlı testler için "bundan sonra sormadan test yap hepsini kabul ediyorum" dedi:
kullanıcı başındayken canlı testler sorulmadan, ama başlamadan önce haber
verilerek koşulur.

2026-09-19 ekleri, kullanıcının sözleriyle ve sırasıyla:

1. "github yok. tamamen bitirene kadar yok. adım 7'ye kadar olan adımları da
   yapalım. dediğim gibi test için sorma." Push, iş tamamen bitene kadar yok.
   Faz 6 ve Adım 6 arka arkaya yapılmaya başlandı; Task 6.1 bu onayla yapıldı.
2. Task 6.2 sürerken iki geri alma geldi:
   - "tamam bundan sonra testler için sormaya devam edersen sevinirim": gerçek
     masaüstüne dokunan her testten önce yeniden onay istenir (pencere açan,
     gerçek erişilebilirlik ağacını okuyan, girdi gönderen, ekran yakalayan).
     Masaüstüne dokunmayan otomatik testler (birim, sözleşme, `cargo test`,
     test kipindeki yardımcıyla entegrasyon) her task sonunda sorulmadan
     koşar.
   - "ve bu adımı da bitirince dur sonraki adıma geçme hemen": her task
     bitince yine durulur ve sonraki task için onay beklenir.
3. "tamam devam et 6.3 ile. ve artık testler için yine sorma": Task 6.3
   onaylandı ve canlı testler yeniden sorulmadan koşuyor. Başlamadan önce ne
   açılıp ne kıpırdayacağı tek satırla haber veriliyor. Task'lar hâlâ tek tek
   onaylanıyor: 6.3 bitince durulur.
4. Aynı gece, uyumaya giderken (Task 6.4 sürerken): durmadan, onay istemeden
   ilerlenebildiği kadar ilerlenecek. Testler için asla onay sorulmayacak,
   adımlar arasında onay beklenmeyecek. Her adımda yerel commit, GitHub'a
   dokunulmaz. Kullanıcı açıkça "geldim" demedikçe yok sayılır; dönünce durum
   raporu verilir. Kullanıcıyı gerçekten gerektiren işler (oturumdan
   çıkış/giriş, sudo, portal penceresine tıklamak, fiziksel fare, push)
   "Kullanıcıyı bekleyenler"e yazılıp atlanır, beklenmez. Bu madde 3'teki
   "her task bitince dur" kuralının yerine geçer.

## Kullanıcıyı bekleyenler

Kullanıcı yokken yapılamayan ya da gözle doğrulanması gereken işler. Biri
yapılınca silinmez, `yapıldı` diye işaretlenir.

| # | Ne | Neden bekliyor | Durum |
|---|---|---|---|
| 1 | Adım 6 — imleç katmanı: fiziksel farenin olay hızını ölçmek | Fareyi elle oynatmak gerekiyor; eklenti değişikliği oturumdan çıkış/giriş istiyor | ölçüm `yapıldı` (~1000 Hz); eklenti değişikliği Adım 6'da bekliyor |
| 2 | `.github/workflows/native.yml`'ı ilk kez çalıştırmak | Depo public, push kullanıcının kararı; iş akışı yerelde yalnızca ayrıştırıldı, hiç koşmadı | `bekliyor` — 2026-09-13: kullanıcı "şimdilik gönderme" dedi; 2026-09-19: "tamamen bitirene kadar yok". Gönderilecek 42 commit'te sır taraması 2026-09-13'te temizdi; göndermeden önce yeniden taranacak |
| 3 | Task 4.2 ekran kilidi senaryosu: native capture kilitliyken kare vermiyor mu | Kilidi açmak parola istiyor; kullanıcı yokken ekran kilitli kalırdı | `yapıldı` (2026-09-13) |
| 4 | Paylaşım göstergesinin kaynak kapanınca kaybolduğunu gözle görmek | Gösterge ekran paylaşımı olmadan görülemiyor; test yalnızca Mutter oturum sayısını doğruladı | `yapıldı` (2026-09-13) |
| 5 | Task 4.3 yayılımı: servisi ve stdio istemcilerini yeniden başlatıp native yolu gerçek kullanımda görmek. `config.toml`'da `[native]` bölümü yok, yani yeni süreçler native yolu seçecek. Sıra: `bridgekilit` → `job_list` boş mu → `systemctl --user restart pcbridge` → Claude Code/Codex'i yeniden başlat → `./doctor.sh` 8. bölüm → `desktop_unlock` sonrası sağ alttaki görev çubuğunda gösterge var, `desktop_lock` sonrası yok. Geri alma: `[native]` altına `capture = "python"` + aynı yeniden başlatmalar. Task 5.1'in yürütme kilidi ve eylem başına izin kontrolü de aynı yeniden başlatmayla devreye girer | Restart çalışan işleri öldürür; stdio süreçleri istemcinin; göstergeyi gözle görmek gerekiyor | servis `yapıldı` (2026-09-13); Claude Desktop ve Claude Code'un stdio süreçleri uygulama bir kez kapatılıp açılınca geçer |
| 7 | Task 6.3 ve 6.4 yayılımı: native erişilebilirlik (`ui_dump`, `ui_click`, `ui_set_text`, `window_list`) ve yeni pencere sırası (`window_focus`, `launch`, `focus`) stdio istemcilerinde. Servis iki kez yeniden başlatıldı ve yeni kodda; Claude Code ve Claude Desktop'un stdio süreçleri eski kodu çalıştırıyor | stdio süreçleri istemcinin; uygulama kapatılıp açılınca yeni koda geçer | servis `yapıldı` (2026-09-19, 6.3 ve 6.4); stdio uygulama yeniden başlayınca |
| 8 | Adım 6 — imleç katmanı gerçek oturumda: çıkış/giriş sonrası işaret dosyasını açıp (`touch ~/.local/state/pcbridge/gorunur-imlec`) izin verdikten sonra FİZİKSEL fareyle tıklama ve akış normal mi? 2026-08-04'te bozulan buydu; kare saati düzeltmesi nested kabukta ölçüldü ama gerçek farede denenmedi. Bozulursa işaret dosyasını silmek yeter | Eklenti kodu ancak çıkış/girişte yeniden okunuyor; arıza yalnızca fiziksel fareyle görüldü | `bekliyor` |
| 6 | Task 5.4 / 4 — gerçek girdi testi (`yapıldı` 2026-09-19, 8/8): `PCBRIDGE_TEST_INPUT=1 PCBRIDGE_INPUT_REPORT=<yol> ./.venv/bin/python -m unittest tests/live/test_input_parity.py -v`. İki ekranı ~1 dk kaplayan test penceresi; fare kendiliğinden hareket eder, pencereye tıklar, pencerenin içindeki kutuya Türkçe metin yazar, Shift'i kısa süre basılı tutar | Gerçek tuş ve tıklama gönderiyor; kullanıcı başında olmalı ve o sırada klavye/fareye dokunmamalı. Acil durdurma: Super+L (ekran kilidi native aygıtları anında kapatır) | `yapıldı` (2026-09-19) |

---

# Yol haritası

Sıra yukarıdan aşağı. Her adım tek başına sınanabilir ve geri alınabilir.

| Adım | Ne | Durum |
|---|---|---|
| 0 | Belge omurgası: tek giriş noktası, ölü referansların onarımı | `tamamlandı` |
| 1 | `KURALLAR.md` §4'teki 5/6/7 kapıları (parola alanı, tekrar tıklama, kapatma onayı) | `tamamlandı` |
| 2 | `window_focus` hızlı yolu (6701,3 ms → **5,2 ms**, gerçek oturum) | `tamamlandı` |
| 3 | Native migration Faz 3: ilk Rust capture subsystem → Gate 3 | `tamamlandı` (3.1–3.5 ✅, **Gate 3 geçti**) |
| 4 | Native migration Faz 4: paketleme, parity, varsayılan değişikliği → Gate 4 | `tamamlandı` (4.1–4.3 ✅, **Gate 4 geçti**) |
| 5 | Native migration Faz 5–8: input, accessibility, capture kapsamı, retirement | `devam ediyor` (5.1–5.4 ✅ **Gate 5**; 6.1–6.4 ✅ **Gate 6**; 7.1 ✅, 7.3/7.4 ölçülüp uygulanmadı; sırada 7.2) |
| 6 | İmleç katmanı (gnome-extension) — yarım kalan iş | `uygulandı, kapalı geliyor` (gerçek fareyle doğrulama kullanıcıda, #8) |
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

### Task 3.1 — Native display snapshot · `tamamlandı`

**Ne yapıldı.** Monitör tablosunun kuralları tek bir yere yazıldı ve iki dilde
aynı fixture'la sabitlendi: `pcbridge-core::display` (Rust) ile
`monitors.resolve_state()` (Python). Taşıma adaptörleri ayrı kaldı — Python
`busctl --json=short`, Rust zbus — ama hangi mod geçerli, dönüşüm eksenleri ne
zaman takas eder, sıra nasıl kurulur, yuvarlama nasıl yapılır: hepsi ortak.

`_from_mutter` artık kendi ayrıştırıcısını taşımıyor, `resolve_state`e veriyor.
Bu şart: iki kopya kural er geç ayrışır ve ayrışma bir piksel olarak değil
**yanlış ekrana tıklama** olarak görünür.

**Yeni:** `rust/crates/pcbridge-core/src/display.rs`,
`rust/crates/pcbridge-native/src/platform/linux/display.rs`,
`rust/crates/pcbridge-native/tests/display_contract.rs`,
`tests/contracts/test_display_contract.py`,
`tests/fixtures/native/display_state_cases.json` (6 kabul + 5 ret vakası).
`display.snapshot` protokol metodu eklendi; `contracts.py` `topology_id()`
seam'ini kazandı.

**Yol boyunca bulunan üç şey — üçü de ölçüldü, uydurulmadı:**

1. **Connector adları kararlı değil.** `CLAUDE.md` "DP-2 (x=0) / DP-1 (x=1920,
   birincil)" diyordu; makine artık **DP-4 / DP-3** diyor. Geometri, sıra ve
   numaralandırma hiç değişmedi, ama `monitor="DP-1"` gibi ada göre seçim bu
   makinede artık çözülmüyor. Hem Mutter hem `xrandr --listmonitors` aynı şeyi
   söyledi. **Tasarım sonucu:** `topology_id` connector adını **içermiyor** —
   içerseydi her yeniden adlandırmada "düzen değişti" derdi. Kalıcı kimlik için
   monitörün `serial` alanı eklendi.
2. **Python `round()` bankacı yuvarlaması yapıyor, Rust'ınki yapmıyor.**
   960,5 → Python 960, Rust 961. Kesirli ölçekte bir piksel sessizce
   ayrışırdı. Kural iki tarafta da açıkça yazıldı (sıfırdan uzağa) ve
   fixture'a bir yarım-sınır vakası kondu. **Mutter'ın kendi yarım-sınır
   davranışı ÖLÇÜLMEDİ:** bu makinede iki monitör de ölçek 1.0, bölme her zaman
   tam. Kesirli ölçek donanımı olan biri doğrulamalı.
3. **"connect syscall sayısı 0" kaydı eskimiş.** Task 2.1 öyle ölçmüştü ve o
   gün doğruydu; **Task 2.4** desktop-state sağlayıcısını ekleyince açılışta
   oturum veriyoluna bir bağlantı kuruldu ve yeniden ölçülmedi. Bugünkü taban
   çizgisi **1**. `display.rs` olmadan derlenmiş binary'de de 1 çıktı, yani
   artış bu task'tan gelmiyor. Protokol belgesine düzeltme olarak yazıldı.

**Ölçümler (gerçek makine, gerçek Mutter):**

- Rust `display.snapshot` ile Python `list_monitors()` **birebir aynı tabloyu**
  verdi: aynı sıra, aynı boyut, aynı `topology_id`
  (`v1|0,0,1920,1080,1.0000,0,0|1920,0,1920,1080,1.0000,0,1`), aynı canvas
  `[3840, 1080]`.
- İlk `display.snapshot` (D-Bus bağlantısı dahil) **16,7 ms**; ikincisi
  önbellekten **0,1 ms**.
- `strace -e trace=connect`: `capabilities` dizisi **1** bağlantı,
  `display.snapshot` dizisi **2**. Yani oturum veriyolu bağlantısı gerçekten
  tembel ve tam olarak bir tane ekliyor.

**Test sonuçları:**

- `tests/contracts/test_display_contract.py` (yeni) → **10 test**
- Rust `display_contract.rs` (yeni) → **7 test**
- Tam Python contract discovery → **120 tests, OK** (112 → 120)
- `tests/test_desktop.py` live bayrakları kapalı → **583 geçti, 0 kaldı**
- `cargo fmt --check`, `cargo clippy --workspace --all-targets -- -D warnings`,
  `cargo test --workspace --all-targets` → temiz
- `python -m pcbridge.server --check -c config.example.toml` → exit `0`

**Korunanlar:** Birincil sağda olsa da `monitor=2`; `1`, `"primary"`, `"all"`
ve ada göre seçim davranışları; `capture.to_global()` tek koordinat girişi.
Varsayılan capture backend **değişmedi** (`[native] capture = "python"`).

**Rollback:** Tek commit; `git revert`. Rust tarafı hiçbir üretim yolunu
beslemiyor — `display.snapshot` yalnızca istendiğinde çağrılıyor ve Python
capture yolu ona hiç bakmıyor.

**Sonraki somut adım:** Task 3.2 — Mutter capture session lifecycle. Bu
snapshot'ı session'a bağlayacak olan task o.

### Task 3.2 — Mutter capture session lifecycle · `tamamlandı`

**Ne yapıldı.** Ekran paylaşımı oturumunun ömrü Rust'a, kompozitör çağrılarının
**arkasına** yazıldı: `CaptureSession` durum makinesi
(`Closed → Starting → Ready → Stopping → Closed`, `Failed` ayrı durum),
`ScreenCastBus` trait'i ve onun gerçek Mutter uygulaması. Trait sayesinde
gerçek makinede üretmesi zor olan sıralar — düğüm duyurusunun erken gelmesi,
hiç gelmemesi, `RecordMonitor` ile `Start` arasında revoke — sıradan birer test.

Oturumu kapatan beş tetiğin beşi de yazıldı: revoke, ekran kilidi, düğüm
zaman aşımı, host EOF (`Drop`) ve kompozitör bağlantısının kopması. Kayıp
bağlantıda `Stop` **denenmiyor** — ölü sokete gönderilen `Stop` yalnızca ikinci
bir hata üretir.

**Yeni:** `rust/crates/pcbridge-native/src/platform/linux/session.rs`,
`rust/crates/pcbridge-native/tests/capture_session.rs` (27 test, sahte
veriyolu), `rust/crates/pcbridge-native/tests/capture_session_live.rs`
(`PCBRIDGE_TEST_CAPTURE=1` ile), `docs/native/capture.md`.

**Değişen:** `lifecycle.rs` (fail-closed kayıt defteri), `display.rs` (aşağıda),
`platform/linux/mod.rs`, `CLAUDE.md`, `docs/native/protocol-v1.md`.

**Plandan üç bilinçli sapma:**

1. **`lifecycle.rs`'e dokunuldu** — plan dosya listesinde yoktu, ama
   implementation maddesi 6 "revoke, lock … session'ı kapat" diyor ve watchdog
   bugüne kadar yalnızca bir `AtomicBool`'u sıfırlıyordu. Bir boolean ekran
   paylaşımı göstergesini söndürmez. `Lifecycle::register_fail_closed` ile
   kaynaklar kaydoluyor ve watchdog onlara **kapan** diyor; bildirim kenar
   tetiklemeli, yani revoke edilmiş bir grant kayıtlı kaynakları saniyede on
   kez uyandırmıyor.
2. **Oturum D-Bus zaman aşımı 200 ms değil 10 sn.** `display.rs` 3.2 için
   `method_timeout()` diye 200 ms'lik bir sabit bırakmıştı, ama o üçlü
   (`GetActive`/`GetIdletime`/`GetCurrentState`) kompozitörün elindeki bir
   değeri okuyor; `Start` PipeWire akışı pazarlığı yapıyor. Python yardımcısı —
   bu sıranın ölçülmüş tek uygulaması — çağrı başına 10 sn veriyor. 200 ms
   normal bir başlangıcı hataya çevirirdi.
3. **Bayrak listesinde olmayan bir live test eklendi.** Sahte veriyolu kuralları
   doğrular; metot adlarının, argüman imzalarının ve sinyal biçiminin Mutter'ın
   kabul ettiği şeyler olduğunu **yalnızca** gerçek kompozitör gösterebilir.

**Yol boyunca bulunan üç şey:**

1. **PipeWire düğüm numaralarının sırası kararlı değil.** 11 koşumda aynı istek
   için `DP-4`/`DP-3` bir kez `[83, 82]`, bir kez `[69, 72]` aldı — yani hangi
   monitörün düğümü önce duyuruluyor, değişiyor. "İlk gelen sinyal ilk
   kaydettiğim monitördür" varsayımı sessizce yanlış ekranı yakalamak olurdu.
   Eşleme `RecordMonitor`'ün döndürdüğü stream nesne yoluna göre yapılıyor.
2. **`display.rs` uygulamadığı bir zaman aşımı ilan ediyordu.** `METHOD_TIMEOUT`
   tanımlıydı ve yorumu "takılan bir kompozitör korumalı bir isteği açık
   tutamaz" diyordu, ama `connect()` onu bağlantıya hiç vermiyordu. Koruma
   yazılıydı, yoktu. Bu task'ta bağlandı; artıkta kalan `method_timeout()`
   fonksiyonu silindi.
3. **Fail-closed yolu kendi kendine kilitlenebilirdi.** Başlangıcın içinde
   danışılan kapı `Lifecycle`, ve `Lifecycle` revoke'u fark ettiği anda kayıtlı
   kaynaklara "kapan" diyor — yani kapatma çağrısı **oturum kilidini zaten
   tutan aynı thread'den** gelebiliyor. `try_lock` bunu zararsız kılıyor
   (uçuştaki çağrı bir sonraki kapı noktasında kendini kapatıyor); `lock()`
   olsaydı revoke anında kilitlenirdi. `closing_from_inside_the_guard_does_not_deadlock`
   bunu sabitliyor.

**Acceptance — planın adlandırdığı dört senaryo:**

| Plan senaryosu | Test |
|---|---|
| Erken sinyal | `a_stream_announced_before_start_is_not_lost` |
| Missing stream | `a_missing_node_fails_the_start_and_closes_the_session` |
| Double start/stop | `opening_twice_with_the_same_request_touches_nothing`, `stopping_twice_is_harmless` |
| Revoke-during-start | `a_revoke_between_record_monitor_calls_closes_the_created_session`, `a_revoke_after_the_nodes_arrive_still_refuses`, `the_fail_closed_flag_aborts_a_start_in_flight` |
| Capability sorgusu session açmıyor | `read_only_queries_never_touch_the_bus` + `strace` (aşağıda) |

**Testlerin gerçekten tuttuğu mutasyonla denendi.** Üç şey ayrı ayrı bozuldu —
`abort`'un `Stop` çağrısı, düğümler geldikten sonraki son kapı noktası, ve
`Drop` — ve **6 test kırmızıya döndü**. Sonra dosya geri alındı.

**Ölçümler (gerçek Mutter, iki monitör, 11 koşum, her koşum ayrı süreç;
veriyolu bağlantısı ölçümün dışında):**

| İşlem | Süre |
|---|---|
| `open` (CreateSession + 2×RecordMonitor + Start + iki sinyal) | **2,6 – 4,8 ms**, ortalama **3,6** |
| `open` (aynı istek — yeniden kullanım, veriyoluna gitmiyor) | **0,003 – 0,007 ms** |
| İmleç kipi değişimi (Stop + tam yeniden kurulum) | **3,8 – 6,4 ms** |
| `stop` | **0,8 – 1,4 ms** |

Python yardımcısında aynı imleç değişimi **~113 ms** olarak kaydedilmişti; iki
sayı aynı ölçüm noktasından alınmadı (Python'unki yardımcı sürece JSON gidiş
dönüşünü içeriyor), yani kıyaslama değil, aynı işlemin iki taraftaki maliyeti.

Koşumlardan sonra `busctl --user tree org.gnome.Mutter.ScreenCast` altında
Session nesnesi kalmadı; `screencast_helper.py` süreci açılmadı.

`strace -e trace=connect`, release binary: `initialize → capabilities →
shutdown` **1** bağlantı, `display.snapshot`'lı dizi **2** — Task 3.1'deki
sayıların ikisi de değişmedi. Session kodu tek bir bağlantı bile eklemedi,
çünkü onu açan bir üretim yolu yok.

**Test sonuçları:**

- Rust `capture_session.rs` (yeni) → **27 test**
- Rust `capture_session_live.rs` (yeni, bayraksız atlanır) → **1 test**
- `cargo test --workspace --locked` → **54 test** (26 → 54)
- `--features pcbridge-native/test-harness` ile → **60 test**, 0 hata
- `cargo fmt --check` ve `cargo clippy --workspace --all-targets -- -D warnings`
  (iki feature kipinde de) → temiz
- `tests/test_desktop.py` live bayrakları kapalı → **583 geçti, 0 kaldı**
- Python contract discovery → **122 test, OK**
- `tests/test_models.py` → **106 geçti**; `test_test_safety.py` → OK
- `python -m pcbridge.server --check -c config.example.toml` → exit `0`

**Korunanlar:** Varsayılan capture backend `[native] capture = "python"`;
ekran paylaşan tek üretim yolu hâlâ `screencast_helper.py`. Protokolde oturum
açan metot **yok** — `dispatch.rs` bu modüle hiç dokunmuyor.

**Bilinçli olarak yapılmayanlar:** RemoteDesktop pointer izni (imleç kipi
yalnızca 0/1), portal persistence, buffered capture (7.3).

**Rollback:** Tek commit; `git revert`. Üretimde çağıranı olmadığı için geri
alma bugün zaten etkisiz.

**Sonraki somut adım:** Task 3.3 — OnDemand PipeWire frame alma ve güvenli PNG
encoding. Oturumu ilk açacak olan metot orada geliyor.

### Task 3.3 — OnDemand frame + güvenli PNG · `tamamlandı`

**Durum:** Part A'daki saf tampon/PNG kuralları ve `CaptureWorker`, kullanıcı
`libpipewire-0.3-dev libclang-dev` kurduktan sonra gerçek PipeWire source ve
binary IPC ile tamamlandı. `pkg-config`: PipeWire **1.0.5**, SPA **0.2**.

**Ne yapıldı.**

- `pipewire_source.rs` PipeWire `MainLoop`, `Context` ve `Stream` nesnelerini
  tek adanmış thread'de tutuyor. Thread sınırından compositor belleği değil,
  yalnızca sahipli RGBA8 kare geçiyor; event kanalı 2 öğeyle sınırlı.
- Format pazarlığı gerçek SPA enum'larıyla yalnızca
  BGRx/RGBx/BGRA/RGBA'yı kabul ediyor. Bilinmeyen format, CPU-map edilemeyen
  DMA-BUF, negatif stride, bozuk metadata/chunk ve sınırı aşan frame tahmin
  edilmeden typed hataya dönüyor.
- `CaptureWorker` grant/revoke/cancel/timeout kapılarını koruyor; producer
  tamponu dönmeden PNG kodlanamıyor. İlk gerçek uygulamada `detach` kontrol
  kanalında 2 saniye aç kaldı; live kırmızı test bunu yakaladı. Artık tampon
  bırakıldıktan sonra stream process callback'inin içinde disconnect ediliyor,
  sahipli kare ancak sonra işçiye gidiyor.
- `capture.frame`, geçerli `display_id + topology_id + session_id + grant_id +
  revoke_epoch` istiyor; bounded parametreleri doğruluyor ve tek monitör PNG'sini
  base64'siz framed binary payload olarak gönderiyor. Başarı metadata'sı gerçek
  backend'i, boyut/desktop rect'i, sequence/timestamp kaynağını, bekleme ve
  encoding sürelerini taşıyor.
- Production handshake artık `display.snapshot` ve `capture.on_demand`
  feature'larını; capability sorgusu `linux.mutter.pipewire` / desteklenen
  `capture.monitor` sonucunu ilan ediyor. Sorgu session veya PipeWire açmıyor.
  Varsayılan `[native] capture = "python"` **değişmedi**; Task 3.4'e kadar
  Python shot yolu yeni metodu çağırmıyor.

**Ölçümün düzelttiği iki varsayım.** Gerçek Mutter/GNOME 46 akışı üç koşumda da
`SPA_META_Header` vermedi. İlk fallback olarak denenen
`pw_stream_get_time_n().now` da gerçek portal düğümünde `0` döndü. İkisi de
uydurulmadı: header varsa üretici sequence/PTS'si `spa_meta_header` etiketiyle;
yoksa source-yerel sequence ve source monotonic timestamp
`source_monotonic_clock` etiketiyle taşınıyor. Tazelik bunlardan ayrı receipt
`Instant` ile ölçülüyor.

**Gerçek kare ölçümü (`PCBRIDGE_TEST_CAPTURE=1`, release).** Beş session, her
session'da aynı source'tan iki ardışık kare: toplam **10× 1920×1080**. Her
PNG decode edildi; siyah placeholder olmadığı, alfa kanalının tamamen opak
olduğu, sequence'in `1 → 2` ve timestamp'in arttığı doğrulandı.

- ilk kare wait: **57,3–63,2 ms**
- PNG encoding: **32,2–34,0 ms**
- session/source kurulduktan sonraki toplam: **87,1–97,3 ms**
- wait + encode dışı stream kapatma ek yükü: **0,2–0,4 ms**
- Python/GStreamer yardımcısı, ayrı ama aynı monitör ölçümü: **71 ms**,
  1920×1080, siyah değil, alfa opak. Kareler aynı anda alınmadığı için bu henüz
  piksel-birebir parity gate'i değil; o Task 4.2.

Koşumlardan sonra Mutter Session nesnesi, `pcbridge-native` process'i,
`screencast_helper.py` process'i veya geçici PNG kalmadı.

**TDD / hata kayıtları.** Bilinmeyen SPA formatı ve binary IPC önce eksik API
nedeniyle kırmızıydı. Gerçek test önce üç kez `frame metadata is missing`, sonra
geçersiz `timestamp=0`, ardından stream bırakmada **2001 ms** ek yük ile kırmızı
oldu; her kök neden ayrı düzeltilip aynı test yeşile çevrildi. Part A'daki
format/stride/alpha/allocation/tazelik/detach mutasyonlarının tamamı korunuyor.
Son workspace gate'i ayrıca eski test fixture adının floating-point zamana
dayandığı için iki paralel testte çakışabildiğini gerçek `ENOENT` ile yakaladı;
PID + atomik sayaç yapıldı. `native_revoke` **30**, `desktop_state` **20**
ardışık tam dosya koşumunda yeniden kırılmadı.

**Güvenlik ve kalite incelemesi.** Capture isteğinde path yok; native dosya
yazmıyor. Grant token initialize snapshot'ıyla birebir eşleşmeden ve lifecycle
yeniden doğrulanmadan session açılmıyor. Topology/connector tahmin edilmiyor,
request alanları ile frame/payload allocation'ları sınırlı, görüntü ve token
loglanmıyor, crate `unsafe` kodu yasaklıyor. Yeni dependency ağacı yerel
`cargo tree` ile incelendi; `cargo audit --no-fetch` yerel **1243 advisory** ile
**138 dependency** taradı ve bulgu vermedi. Beş eksenli review'da kalan blocker
yok.

**Doğrulama:**

- Rust workspace, default → **89 test**, 0 hata
- Rust workspace, `pcbridge-native/test-harness` → **98 test**, 0 hata
- `cargo fmt --check`; clippy default + test-harness `-D warnings` → temiz
- Python contract discovery → **122 test**, OK
- `tests/test_desktop.py` (live bayrakları kapalı) → **583 geçti**
- `tests/test_models.py` → **106 geçti**; `test_test_safety.py` → OK
- release build → temiz; `ldd` runtime'da `libpipewire-0.3.so.0` buldu
- gerçek Python `NativeClient` → feature/capability doğru, binary payload `0`,
  stderr `0` byte, kapanıştan sonra process yok
- `python -m pcbridge.server --check -c config.example.toml` → exit `0`

**Bilinen sınır:** protokol dispatcher'ı bugün eşzamanlı capture çalıştırmıyor;
bu yüzden ayrı `cancel` request'i aktif capture ile interleave olamaz ve
`canceled: false` döner. Frame işçisinin cancellation yolu testli; production
çağrı kendi 1–8000 ms zaman aşımı ve ayrı lifecycle watchdog'larıyla sınırlı.
Task 3.4 native client deadline'ını bu timeout ile aynı sözleşmeye bağlamalı.

**Bağımsız doğrulama (2026-09-12, ayrı oturum).** Kod okundu ve iddialar
yeniden ölçüldü:

- Bütün gate'ler tekrarlandı: fmt, clippy (iki feature kipinde), Rust
  **89/98**, `test_desktop` 583, contract 122, `test_models` 106,
  `server --check` exit 0. Rapor edilen sayıların hepsi doğru.
- **Üretim yolu uçtan uca sürüldü** — ki ne live test ne harness testi onu
  kapsıyor. Geçici state dizininde grant, gerçek binary, stdio protokolü:
  `capture.frame` **421.034 baytlık** gerçek bir masaüstü PNG'si döndürdü,
  `binary_len` birebir eşleşti, yanlış `grant_id` `REVOKED` aldı, stderr boştu,
  arkada süreç/Session kalmadı. Ayrıntı `docs/native/capture.md`.
- Tembel bağlantı değişmemiş: `capabilities` **1** connect (yalnızca
  `/run/user/1000/bus`), `display.snapshot` **2**. Capabilities hiçbir PipeWire
  soketine dokunmuyor.
- **Zamanlama bandı tekrarlanmadı.** Aynı release binary, aynı monitör, 7
  koşum: PNG kodlaması 49,4–53,0 ms (kayıtta 32,2–34,0), toplam 101,0–107,7 ms
  (kayıtta 87,1–97,3). Fark tutarlı, gürültü değil; o sırada `load average`
  3,02 ve governor `powersave` idi. Kod sorunu değil — ama tek oturumda alınmış
  dar bir bant makinenin davranışı gibi okunuyordu. `capture.md` artık iki
  oturumu da gösteriyor.

**Kalan iki açık nokta (defect değil, kayıt):**

1. `capabilities` `capture.monitor: supported` diyor ve bu **build zamanı**
   iddiası: Mutter ScreenCast çalışma anında yoksa (GNOME dışı oturum) yine
   "supported" der. Runtime kullanılabilirlik raporlaması Task 4.1'in işi.
2. `capture_frame_production` yolunun otomatik testi **yok**; `ipc_protocol.rs`
   fake backend'i, `capture_frame_live.rs` kütüphaneyi sürüyor.

**Rollback:** Bu tamamlayıcı commit yerelde `git revert` edilebilir; Part A
`d31aaf1` ayrı kalır. Varsayılan backend Python olduğu için rollout değişmedi.

**Sonraki somut adım:** Task 3.4 — native PNG payload'ını mevcut Python shot
store, monitor seçimi ve `capture.to_global()` sözleşmesine bağla; varsayılanı
değiştirme.

### Task 3.4 — Rust capture'ı Python shot pipeline'ına bağla · `tamamlandı`

**Ne yapıldı.** Karenin **nereden geldiği** değişti, başka hiçbir şey
değişmedi. Kırpma, ölçekleme, istemciye giden PNG, çekim kimliği, iki arama
dizini, kayıt dosyası ve bütün koordinat dönüşümü `capture.py`'de kaldı.
Sebep tek cümle: çekim kimliği sonraki bir `mouse(shot=…)` çağrısının ofseti
ve ölçeği bulma yolu; o defteri ikinci bir dile taşımak iki kopyanın ayrışıp
**yanlış ekrana tıklanması** demek.

**Yeni:** `pcbridge/desktop/backends/rust.py`
(`NativeScreenCast` + `RustCaptureProvider` + `select_capture_backend`),
`tests/contracts/test_capture_backend_selection.py` (18 test).
**Değişen:** `capture.py` (per-frame `taken_at`, typed hata geçişi),
`runtime.py` (`select_capture_provider`), `backends/python.py`
(`degraded_reason`), `config.example.toml`, `CLAUDE.md`,
`docs/native/capture.md`.

**Seam neydi.** Eski `capture()` zaten bir `screencast` nesnesi alıyordu
(`is_open` / `ensure_cursor` / `capture(connector, path)`). Native tarafa aynı
şekli veren bir adaptör yazmak, pipeline'a hiç dokunmadan backend'i
değiştirmeye yetti — `capture.py` ikisini ayırt edemiyor.

**Backend tablosu tek yerde** ve saf: `select_capture_backend`. Seçim runtime
kurulurken **bir kez** yapılıyor, oturum ortasında değişmiyor (bir `all`
çekimi iki farklı kaynaktan birleştirilemez). Önemli satır `rust`: zorunlu
tutulmuş backend yardımcı yoksa **sessizce Python'a dönmüyor**, seçili kalıp
görünür şekilde hata veriyor — zorunlu tutmanın amacı tam olarak onun çalışıp
çalışmadığını görmek. `auto` ise Python'a düşerken gerekçeyi capability
raporuna `degraded` limitation olarak yazıyor.

**`taken_at` artık karenin kendi saati.** Native yol kareyi ne kadar
beklediğini bildiriyor; bir `all` çekiminde monitörler sırayla okunuyor ve
aradaki fark yüzlerce milisaniye olabiliyor. Damga bayatlık uyarısını sürüyor,
o yüzden fark önemli. İleriye doğru bir saniyeden fazla sapan damga yok
sayılıyor: gelecekten gelen bir damga bayatlık kontrolünü **sessizce**
kapatırdı.

**Paylaşım göstergesi biraz kayıyor — kayıtta, gizli değil.** Python yolunda
paylaşım `desktop_unlock` ile açılıyor, gösterge izinle birlikte beliriyor.
Native oturum istek üzerine: ilk çekimde açılıyor, revoke/kilide kadar açık
kalıyor. Yani izin ile ilk çekim arasında grant'i olan ama göstergesi olmayan
bir pencere var. Tartışılabilir biçimde daha doğru bir sinyal (o pencerede
hiçbir şey ekranı okuyamıyor), ama gösterge kullanıcının kanıtı; Task 4.3'te
varsayılan değişmeden önce yeniden bakılacak.

**Acceptance — aynı çekim iki backend'den.** `test_capture_backend_selection`
aynı PNG baytlarını hem eski tutamaçtan hem native adaptörden `capture.py`'ye
veriyor ve **çekimlerin birebir aynı** çıktığını doğruluyor: kimlik biçimi,
ofset, boyut, ölçek, kayıt dosyası ve `to_global` sonucu. `shot→global`
merkezleri `(960,540)` ve `(2880,540)`.

**Ölçüldü (gerçek makine, uçtan uca).** `[native] capture = "rust"`, gerçek
helper, geçici state dizininde grant:

| Ne | Sonuç |
|---|---|
| `backend_name()` başlamadan / başladıktan sonra | `pcbridge-native` → `linux.mutter.pipewire` |
| `capture.monitor` | `supported` / `linux.mutter.pipewire` |
| Çekim | `m1-e0cf3b`, DP-4, ofset `(0,0)`, 1920×1080 → 1536×864, ölçek 0,8 |
| PNG | 796.883 bayt, 25.504 ayrı renk (gerçek masaüstü) |
| Kayıt | yazıldı, geri okundu, `to_global(10,10)` → `(12,12)` |
| `capture()` toplam | **414 ms** |

Gerçek masaüstü grant'ine dokunulmadı: doğrulama boyunca `desktop_unlock.json`
`until: 0` kaldı.

**Testlerin tuttuğu mutasyonla denendi.** Beş şey ayrı ayrı bozuldu — zorunlu
`rust`'ın sessizce Python'a dönmesi, boyut uyuşmazlığının yutulması,
`taken_at`'in bekleme süresini yok sayması, grant kimliğinin yanlış
gönderilmesi ve `capture.py`'nin per-frame damgayı kullanmaması — hepsi
kırmızıya döndürdü.

**Test sonuçları:**

- `tests/contracts/test_capture_backend_selection.py` (yeni) → **18 test**
- Python contract discovery → **140 test, OK** (122 → 140)
- `tests/test_desktop.py` live bayrakları kapalı → **583 geçti, 0 kaldı**
- `tests/test_models.py` → **106**; `test_test_safety.py` → OK
- `python -m pcbridge.server --check -c config.example.toml` → exit `0`

**Korunanlar:** Varsayılan `[native] capture = "python"` **değişmedi**. Çekim
kimliği biçimi, iki arama dizini, `window` çekiminin legacy davranışı ve
`capture.to_global()`'ın tek koordinat girişi olması aynen duruyor. Native
monitor desteği native **window** desteği iddiası üretmiyor.

**Bilinçli olarak yapılmayanlar:** Çekim kimliğini Rust'a taşımak; Python
capture dosyasını silmek; varsayılan backend'i değiştirmek.

**Rollback:** `[native] capture = "python"` (zaten varsayılan). Çekim
metadata'sı göç istemiyor.

**Sonraki somut adım:** Task 3.5 — Screenshot artifact ve MCP image delivery
bütünlüğü. Gate 3'ün son task'ı.

### Task 3.5 — Screenshot artifact ve MCP image delivery bütünlüğü · `tamamlandı`

**Ne yapıldı.** Çekimin başarılı olması ile görüntünün istemciye bütün olarak
ulaşması ayrıldı ve ikisi ayrı ayrı doğrulandı. Tasarım ve ölçümler:
`docs/native/capture.md` → "Çekim artifact'ı ve teslim (Task 3.5)".

- **Bütün ya da hiç yayım.** Görüntüler gizli bir hazırlık dizininde
  üretiliyor, bütün monitörler hazır olunca hard link ile yayımlanıyor; hata
  durumunda bu çekimden diskte hiçbir şey kalmıyor.
- **Asla üstüne yazma.** Kimlik `shot=` aramasının baktığı iki dizinde de boş
  olmalı; çakışmada yeni son ek. `pcb-shot --out` kayıt kopyası yayımın
  parçası.
- **Teslim kontrolü.** Görüntü gitmeden önce PNG imzası ve kayıttaki boyut;
  tutmazsa `IMAGE_DELIVERY_FAILED` (yeni kod, `PLAN.md` taksonomisine
  gerekçesiyle eklendi). Metin görüntü sırasını söylüyor; `computer_batch`
  kimlik satırlarını kesmiyor.
- **Terk edilmiş hazırlık dizinleri** süpürülüyor, `shot_keep_hours = 0` olsa
  bile.

**Yeni:** `tests/contracts/test_shot_artifacts.py` (15),
`tests/integration/test_mcp_capture_delivery.py` (8, biri canlı),
`tests/integration/delivery_fixture.py`, `tests/integration/delivery_server.py`.
**Değişen:** `capture.py`, `presentation.py`, `shots.py`, `tools.py`,
`cli/shot.py`, `contracts.py`, `backends/python.py`, `backends/rust.py`,
`errors.py`, `test_capabilities.py`, `test_capture_contract.py`, `PLAN.md`,
`KULLANIM.md`, `CLAUDE.md`, `docs/native/capture.md`.

**Acceptance — ölçüldü.**

| Ölçüt (`PLAN.md`) | Kanıt |
|---|---|
| MCP client PNG'yi decode edip fixture içeriğini doğruluyor | bellek içi **ve gerçek stdio boruları**: dört renk çeyreği iki monitörde de doğru |
| Path başka araca aktarılmadan inline görüntü kullanılabiliyor | görüntü `ImageContent` base64'ünden çözülüyor, yol kullanılmıyor |
| HTTP token expire oluyor | `/shot/<token>.png` önce 200 + aynı baytlar, TTL sonrası 404, dosya duruyor |
| stdio için ölü HTTP URL üretilmiyor | metinde `/shot/` yok, kayıtlı token 0 |
| Partial failure'da artifact kalmıyor | ikinci monitör / yazma / kopya hatası → dizin boş |
| ID collision'da overwrite yok | başka dizindeki kimlik, var olan PNG, yayım yarışı → yeni son ek, eski dosya aynen |
| Inline görüntü okunamazsa delivery failure | `isError`, `IMAGE_DELIVERY_FAILED`; ulaşan görüntü yine gidiyor |

**Testlerin tuttuğu mutasyonla denendi: 12/12.** Link yerine üstüne yazan
kopya, kimlik kontrolünü kapatmak, geri almayı kapatmak, hazırlık dizinini
bırakmak, süpürmeyi ters çevirmek, MCP süpürmesini atlamak, teslimde boyut
kontrolünü kapatmak, teslim hatasını yutmak, batch'in kimlik satırlarını da
kesmesi, MCP aracının `pcb-shot` kimliklerini yok sayması, sıra satırını
kaldırmak, `pcb-shot --out` kopyasını düşürmek. İlk koşumda batch mutasyonu
**kaçtı** (11/12): test uzun bir rapor kuruyordu ama `tail_chars` sondan
tuttuğu için kimlik satırları hiç risk altında değildi. Test, çekim metninin
sınırı tek başına aştığı durumu kuracak şekilde düzeltildi; mutasyon
yakalandı.

**Yolda bulunan hata: native yol ikinci monitörde yanlış ekranı veriyordu.**
Gate 3'ün piksel kanıtı için native ile eski Python yardımcısı aynı monitörde
arka arkaya karşılaştırılınca DP-3 yalnızca %26,92 tuttu. Sıra deneyi kök
nedeni ayırdı: tek bir `pw_stream` başka düğüme yeniden bağlandığında ilk
düğümde kalıyordu — hangi monitör önce istenirse bütün kareler ondan geldi.
Düzeltme ayrı commit'te: çekim başına yeni akış. Varsayılan `python` olduğu
için kullanıcıya yansımadı; Task 4.3 varsayılanı değiştirmiş olsaydı
`monitor=2` sol ekranı gösterecekti. Ayrıntı: `docs/native/capture.md` →
"Yanlış monitör"; yeni canlı regresyon testi eski kaynağa karşı kırmızı.

**Süre (ölçüldü).** İki monitörlük `screen_capture` debug binary ile 6–9,6 sn;
release'te `capture.frame` monitör başına 271–294 ms. Kalan asıl maliyet
Python'un `save(optimize=True)`'u (~1 sn/monitör) ve eski backend'de de aynı.
Task 4.1 paketlemesi **release** binary üretmeli; Task 4.2'nin p95 ölçütü bu
farkı görecek.

**Test sonuçları:**

- Python contract discovery → **155 test, OK** (140 → 155)
- `tests/integration/test_mcp_capture_delivery.py` → 7 geçti + 1 canlı
  (`PCBRIDGE_TEST_CAPTURE=1` ile **geçti**)
- `tests/integration` `test_native_*` → OK
- `tests/test_desktop.py` live bayrakları kapalı → **583 geçti, 0 kaldı**
- `tests/test_models.py` → **106**; `test_test_safety.py` → OK
- `python -m pcbridge.server --check -c config.example.toml` → exit `0`
- Rust workspace → **91** (varsayılan) / **100** (`test-harness`); fmt ve
  clippy iki kipte temiz
- Canlı: `capture_frame_live.rs` 2/2, `LiveNativeDelivery` 1/1

**Rollback:** Yayım `capture.py`'de tek fonksiyon ailesi (`_render` +
`_publish`); geri almak commit'i revert etmek. Native capture ayrı ve
varsayılan `python`.

### Gate 3 — Rust capture parity · `geçti` (2026-09-13)

| Gerekli kanıt | Nerede |
|---|---|
| Pixel | Eski yardımcıyla aynı monitörde arka arkaya: DP-4 **%99,993** (native/native tabanı %99,987), DP-3 **%100,000** — düzeltmeden sonra; önce %26,92 |
| Metadata | Task 3.4 parity: iki backend'den birebir aynı çekim yapısı; canlı: kayıttaki ölçekli boyut = çözülen görüntü |
| Coordinate | Task 3.4: `shot→global` `(960,540)` / `(2880,540)` iki backend'de aynı; canlı: ofsetler kayıtta korunuyor |
| Freshness | `after_request` bariyeri, `stale_frames = 0`; canlı: `taken_at` < 60 sn; `capture_worker.rs` bayat kare testleri |
| MCP delivery | bellek içi, gerçek stdio, HTTP + TTL, canlı gerçek sunucu |
| GI olmadan gerçek capture | canlı sunucu, `gi` engelli ve `gnome-screenshot` çalışamazken iki monitörü teslim etti |

Gate 3 ilk denemede **geçmezdi**: piksel kanıtı yanlış monitör hatasını ortaya
çıkardı. Hata düzeltilip regresyon testiyle kilitlendikten sonra geçti.
Varsayılan hâlâ `python`; Rust varsayılanı Gate 4'e (Task 4.2 + 4.3) bağlı.

**Sonraki somut adım:** Task 4.1 — native binary build/package ve tanı.

## Adım 4 — Faz 4: paketleme, parity, varsayılan değişikliği (Gate 4)

Task 4.1 (build/package/`doctor.sh` tanısı) → 4.2 (gerçek Linux capture
parity, `PCBRIDGE_TEST_CAPTURE=1`) → 4.3 (varsayılan `native.capture = "auto"`).

**Gerçek makinede ölçüm ister.** `systemctl --user restart pcbridge` çalışan
işleri öldürür — önce `job_list`. Bu oturumun `--stdio` süreci yeni kodu
**çalıştırmaz**; doğrulama servise HTTP + statik token ile gider
(`tests/test_e2e.py` kalıbı).

### Task 4.1 — Native binary build/package ve tanı · `tamamlandı`

**Ne yapıldı.** Native yardımcı derlenip kurulabilir, kendini anlatabilir ve
izin istemeden teşhis edilebilir hâle geldi. Ayrıntı:
`docs/native/packaging.md`.

- **`scripts/build-native.sh`:** gereksinim kontrolü (`--check`), `rust/`
  içinden release derleme (`x86_64-unknown-linux-gnu`), build kimliği (commit
  + `-dirty`), derlenen binary'yi `--build-info` ile doğrulayıp debug,
  test-harness ya da yanlış hedefse reddetme, `pcbridge/_native/<hedef>/`
  altına atomik kurulum. Servisi yeniden başlatmıyor.
- **Binary kendini anlatıyor:** `--version` ve `--build-info` (protokol
  başlatmıyor, oturum veriyoluna dokunmuyor); `initialize` cevabında
  `build_id`; Python `NativeHandshake.build_id` (isteğe bağlı).
- **`capabilities` artık çalışma zamanında:** `org.gnome.Mutter.ScreenCast`
  adının sahibi + PipeWire soketi; oturum açmıyor. Task 3.3 incelemesinde açık
  kalan "`supported` bir derleme iddiası" noktası kapandı.
- **`doctor.sh` → 8. Native yardimci** (`pcbridge.native.diagnostics`):
  seçim, bulunduğu yer, build bilgisi, kütüphaneler, handshake + capability,
  legacy GI notu. Gerçek izin dosyasına dokunmuyor.
- **`install.sh` 3/8:** paketlenmiş yardımcı varsa bildiriyor; yoksa ve
  araçlar kuruluysa derlemeyi soruyor; değilse Python yolunun kullanılacağını
  söylüyor.
- `.github/workflows/native.yml` (Ubuntu 24.04), `.gitignore` →
  `pcbridge/_native/`, `KURULUM.md` 2b, `docs/native/protocol-v1.md` (komut
  satırı, `build_id`, `capabilities`; girişteki "PipeWire kullanmaz" iddiası
  Task 3.3'ten beri eskimişti, düzeltildi).

**Acceptance — ölçüldü.**

| Ölçüt (`PLAN.md`) | Kanıt |
|---|---|
| Native capture için `python3-gi`, GStreamer, `pipewiresrc` gerekmiyor | release binary'nin bütün NEEDED listesi `libpipewire-0.3.so.0`, `libgcc_s.so.1`, `libc.so.6`, `ld-linux-x86-64.so.2`; paketleme testi GI/GStreamer/GLib/Python bağlanmasını reddediyor. Gate 3'ün canlı testi `gi` engelliyken capture'ı zaten kanıtlamıştı |
| Legacy accessibility GI bağımlılığı ayrıca raporlanıyor | `doctor.sh` 8. bölümün son satırı |
| Build ve runtime bağımlılıkları ayrı | `docs/native/packaging.md`; runtime: `libpipewire-0.3-0t64`, `libc6 ≥ 2.39` (`GLIBC_2.39`), `libgcc-s1` |
| Smoke test repo cwd'sine güvenmiyor | binary ilgisiz bir dizine kopyalanıp orada, süzülmüş ortamla çalıştırılıyor; handshake'teki build kimliği `--build-info` ile aynı |
| `doctor.sh` izin istemeden raporluyor | gerçek koşum: paketten bulundu, `release`, kütüphaneler tamam, `capture.monitor: supported`, çıkış 0 |
| Native yoksa Python kurulumu çalışıyor | `auto` → Python + `degraded` gerekçesi; `rust` → seçili kalıp nedenini söylüyor |
| Kurulum servisi yeniden başlatmıyor | `install.sh` ve `build-native.sh` restart çağırmıyor; betik bunu çıktısında da söylüyor |

Ölçüldü: bağımlılıklar önbellekteyken derleme **16,8 sn**, binary
**5.339.112 bayt**. Temiz derleme süresi ölçülmedi.

**Testlerin tuttuğu mutasyonla denendi: 6/6.** PipeWire soketi yokken
`supported` demek, bilinmeyen argümanla yine de başlamak, test-harness
derlemesini işaretlememek, zorunlu yardımcının yokluğunu yalnızca not etmek,
belgelenmemiş kütüphaneyi söylememek, istemcinin `build_id`'yi düşürmesi.

**Test sonuçları:**

- `tests/contracts/test_native_diagnostics.py` (yeni) → 9; Python contract
  discovery → **164, OK** (155 → 164)
- `tests/integration/test_native_packaging.py` (yeni) → **5/5**, gerçek
  paketlenmiş binary ile; integration discovery → 14 OK + 1 canlı atlandı
- `tests/test_desktop.py` → **583**; `test_models.py` → **106**;
  `test_test_safety.py` → OK; `--check` → 0
- Rust → **96** (varsayılan) / **105** (`test-harness`), fmt ve clippy iki
  kipte temiz; `tests/build_info.rs` 5 yeni test
- `bash -n` üç betikte temiz; CI YAML ayrıştırıldı (10 adım)
- **CI çalıştırılmadı:** push yok — "Kullanıcıyı bekleyenler" #2

**Rollback:** `[native] capture = "python"` (varsayılan); paketlenmiş binary
bulunsa da kullanılmıyor.

**Sonraki somut adım:** Task 4.2 — gerçek Linux capture parity gate.

### Task 4.2 — Gerçek Linux capture parity gate · `tamamlandı`

**Ne yapıldı.** Rust capture gerçek masaüstünde eski Python yoluyla, iki
monitörü kaplayan statik bir test deseni üzerinde karşılaştırıldı. Sonuçlar,
koşullar ve yeniden koşma: `docs/native/verification-linux.md`.

**Yeni:** `tests/live/test_capture_parity.py` (11 canlı test),
`tests/live/pattern_window.py` (GTK4 desen, girdi göndermez, kendi zaman
aşımıyla kapanır), `docs/native/verification-linux.md`.
**Değişen:** `capture.py` (tipli red nedeni korunuyor), `backends/rust.py`
(`_grant` → `GRANT_REQUIRED`/`safety`), `screencast.py` (`close()` stdout'u da
kapatıyor), `test_capture_backend_selection.py` (+2 provider düzeyi test),
`test_mcp_capture_delivery.py` (canlı test yardımcısını `--build-info` ile
doğruluyor), `docs/native/capture.md`, `CLAUDE.md`.

**Acceptance — ölçüldü** (iki tam koşum):

| Ölçüt (`PLAN.md`) | Sonuç |
|---|---|
| Monitör kimliği, ofset, boyut, shot→global | 6/6 birebir |
| Statik kırpmada ≥ %99,5 | **%100,000**, iki monitör, tam boyut ve 1536 |
| Her OnDemand kare istekten sonra | 12/12 |
| Hata oranı 0 | 0 |
| Sıcak p95 ≤ 1,5 × eski | **1,42** ve **1,34** |
| Revoke/süre dolumu sonrası kare yok | yok; `GRANT_REQUIRED`/`safety` |
| Kaynak kapanınca paylaşım bitiyor | Mutter oturumu 0,12–0,13 sn'de kapandı (D-Bus); **gözle doğrulama bekliyor** (#4) |
| Native arızasında kabuk/iş araçları | `shell_run` ve `job_list` çalıştı, sonraki çekimler teslim edildi |
| GI olmadan gerçek capture; stdio + HTTP teslim | stdio `gi` engelliyken 2 görüntü; HTTP statik token ile 2 görüntü |

**Bulunan ve düzeltilen.** İlk koşumda revoke ve süre dolumu kare vermedi ama
`BACKEND_UNAVAILABLE`/`capability` olarak geldi. İki kök: `capture.py` native
yardımcının **bütün** tipli redlerini düz `CaptureError`'a sarıyordu (REVOKED,
DISPLAY_CHANGED, FRAME_TIMEOUT hepsi "backend yok" oluyordu) ve `_grant()` izin
yokken tipsiz hata fırlatıyordu. İkisi düzeltildi; **mutasyon 2/2**. Üretimde
araçlar önce `SafetyGate`'ten geçtiği için kullanıcıya ulaşmamıştı.

**Bir tuzak kayda geçti.** `tests/integration/test_native_revoke.py` her
koşumda `rust/target/debug/pcbridge-native`'i test-harness özelliğiyle yeniden
derliyor; o binary ekran okumadan sahte kare veriyor. Canlı testler artık
yardımcılarını `--build-info` ile doğruluyor ve paketlenmiş release'i
tercih ediyor.

**Kapsanmayan.** Ekran kilidi senaryosu (kilidi açmak parola istiyor) ve
göstergenin gözle görülmesi → "Kullanıcıyı bekleyenler" #3, #4. MCP düzeyinde
iki monitörlük `screen_capture` release ile 5,1 sn sürdü; bunun parçaları ve eski
backend'deki karşılığı ölçülmedi → Task 4.3'ten önce.

**Test sonuçları:**

- Canlı: `tests/live/test_capture_parity.py` → 1. koşum 10/11 (yukarıdaki hata),
  düzeltmeden sonra **11/11**; `LiveNativeDelivery` (GI engelli, release) → 1/1
- `tests/contracts/test_capture_backend_selection.py` → 20 (18 → 20); Python
  contract discovery → **166, OK**; integration discovery → 14 OK + 1 canlı
  atlandı
- `tests/test_desktop.py` → **583**; `test_models.py` → **106**;
  `test_test_safety.py` → OK; `--check` → 0

**Rollback:** revoke → native shutdown → `[native] capture = "python"`
(zaten varsayılan).

**Sonraki somut adım:** Task 4.3 — Rust capture'ı varsayılan yap. Önce MCP
düzeyinde iki backend'in süresi aynı koşulda ölçülecek.

### Task 4.3 — Rust capture'ı varsayılan yap · `tamamlandı`

**Ne yapıldı.** `[native] capture` varsayılanı `python` → `auto`. Paketlenmiş,
uyumlu bir yardımcı bulunursa yeni süreçler native yolu seçiyor; bulunmazsa
Python'a görünür biçimde düşüyor. Yayılım: `docs/native/verification-linux.md`
→ "Varsayılan değişikliği (Task 4.3)"; adım adım sıra `docs/native/protocol-v1.md`
→ "Native yola geçiş runbook'u".

- **Varsayılan:** `config.py` (`NativeSpec` ve yükleyici) + `config.example.toml`
  yorumu.
- **Kullanıcının seçimi korunuyor:** `capture = "python"` aynen; `[desktop]
  capture_backend = "gnome-screenshot"` açıkça seçilmişse `auto` Python yolunda
  kalıyor (`runtime.select_capture_provider`).
- **Kullanılan backend görünüyor:** `screen_capture` denetim kaydında
  `backend=`; düşüşte sonuç metninde "Native yakalama kullanılamadı (…)" notu;
  `pcb-shot --json` çıktısında `backend` ve `degraded`; `system_capabilities`'te
  `degraded` + gerekçe (Task 4.1'den).
- **Paylaşım göstergesi yine izinle birlikte açılıyor.** Python yolu yayını
  `desktop_unlock` anında açıyor; native yol oturumu ilk çekime kadar
  açmıyordu. Varsayılanı öylece değiştirmek "izin açık ama gösterge yok"
  durumunu yaratırdı — gösterge kullanıcıya "ajan ekranını görebiliyor" diyen
  tek işaret. Yeni protokol metodu **`capture.session_open`** (yardımcı
  `features`'ta ilan ediyor) grant'i ve revoke epoch'unu doğrulayıp Mutter
  oturumunu kare almadan açıyor; `NativeScreenCast.start()` onu çağırıyor. Eski
  bir yardımcı `UNKNOWN_METHOD` dönerse ilk çekimde açma davranışına düşülüyor.
  Rust'ta `NativeCapture::open_session`, `capture()`'ın oturum kısmından ayrıldı;
  ikisi aynı topoloji ve eşleme kontrollerinden geçiyor.
- `RustCaptureProvider.start()` native redlerini tipli `DesktopError`'a
  çeviriyor (Task 4.2'deki kaybın `start` yolundaki karşılığı).
- Belgeler: `README.md`, `KURULUM.md` 2b, `KULLANIM.md`, `CLAUDE.md`,
  `docs/native/capture.md`, `packaging.md`, `verification-linux.md`,
  `protocol-v1.md` (üretim `capabilities` iddiası ve "varsayılan `python`"
  satırı eskimişti; opt-in runbook'u geçiş runbook'u oldu).

**Acceptance — ölçüldü.**

| Ölçüt (`PLAN.md`) | Kanıt |
|---|---|
| Temiz kurulum Rust capture seçiyor | `tests/live/test_capture_default.py`: örnek config'le başlayan taze stdio süreci ve servis tarzı HTTP süreci native yolu seçti; contract `test_the_shipped_default_is_auto` |
| Eksik binary → görünür degraded fallback | `PCBRIDGE_NATIVE_BIN` olmayan bir yolu gösterirken çekim teslim edildi, `system_capabilities` `degraded` + gerekçe, sonuçta not |
| Kullanıcının `python` / `gnome-screenshot` seçimi korunuyor | contract testleri, ikisi ayrı |
| Fallback'te sonuç/capability/audit backend'i gösteriyor | audit `backend=`, sonuç notu, `pcb-shot --json`; üçü de mutasyonla sınandı |
| stdio ve servis için ayrı talimat | `verification-linux.md` → Yayılım; `protocol-v1.md` runbook'u |
| Job kontrolü olmadan restart yok | kullanıcının servisi yeniden başlatılmadı; talimat `job_list` ile başlıyor |
| Eski MCP tool contract'ları geçiyor | contract discovery **171, OK**; teslim entegrasyonu geçiyor |

**Ölçümler.** MCP düzeyinde iki monitörlük `screen_capture`, aynı koşulda p50:
eski **4.684 ms**, native **5.113 ms** (oran 1,09). Sürenin ~%99'u çekim
hattında, iki yolda da Python'un `save(optimize=True)` kaydı (gerçek içerikte
~2,2 sn/monitör); MCP katmanı ~30 ms. Task 4.2'de "ayrılmadı" diye bırakılan
5,1 sn buydu. `start()` oturumu açtıktan sonra parity gate yeniden koştu:
**11/11**, piksel %100, tazelik 12/12, sıcak p95 181,4 / 129,1 ms = **1,405**;
oturum açılışı p50 native **94,8 ms**, eski 146,1 ms.

**Testlerin tuttuğu mutasyonla denendi: 9/9.** Varsayılanın `python`'a
dönmesi, `auto`'nun açık `gnome-screenshot` seçimini ezmesi, `start()`'ın
oturum açmaması, eski yardımcının `UNKNOWN_METHOD`'unun hata sayılması,
reddedilen `start`'ın tipsiz kaçması, düşüş notunun gösterilmemesi, audit'in
backend'i unutması, `pcb-shot --json`'ın backend'i unutması, yardımcının
`capture.session_open`'ı yönlendirmemesi. Dosyalar hash ile geri doğrulandı.

**Test sonuçları:**

- Canlı: `tests/live/test_capture_default.py` (yeni) → **3/3**;
  `capture_frame_ipc_live` → **2/2** (yeni
  `session_open_shows_the_share_before_any_frame`: oturum sayısı kareden önce
  artıyor); `test_capture_parity.py` → **11/11**; `LiveNativeDelivery` → 1/1
- Python contract discovery → **171, OK** (166 → 171); integration discovery →
  15 koşum, OK (1 canlı atlandı)
- `tests/test_desktop.py` → **583**; `test_models.py` → **106**;
  `test_test_safety.py` → OK; `--check` → 0
- Rust → **97** (varsayılan) / **107** (`test-harness`); fmt ve clippy iki kipte
  temiz; `ipc_protocol.rs` +1 (`capture.session_open` ikili yük taşımıyor ve
  parametrelerini doğruluyor)

### Gate 4 — Default rollout · `geçti` (2026-09-13)

| Gate ölçütü (`PLAN.md`) | Kanıt |
|---|---|
| Paketleme smoke | `tests/integration/test_native_packaging.py` 5/5 — paketlenmiş release binary ilgisiz bir dizinde |
| Görünür fallback | yardımcı yokken `degraded` + gerekçe ve sonuç notu; `rust` seçiliyse düşmeden hata |
| Yeni stdio/service process doğrulaması | taze stdio ve servis tarzı HTTP süreci (izole state, örnek config) native yolu seçti; Mutter oturumu `desktop_unlock` anında açıldı, `desktop_lock` kapattı (D-Bus'tan sayıldı) |

**Sınır.** Doğrulama taze, izole süreçlerle yapıldı. Kullanıcının gerçek
systemd servisi ve açık stdio istemcileri **yeniden başlatılmadı**: restart
çalışan işleri öldürür ve kullanıcı yokken canlı davranışı değiştirirdi.
Gerçek kullanımdaki geçiş ve göstergenin gözle görülmesi → "Kullanıcıyı
bekleyenler" #5.

**Rollback:** `[native]` altına `capture = "python"`, ardından runbook'taki
revoke → yardımcıları kapat → yeni süreçler sırası.

**Sonraki somut adım:** Task 5.1 — input parity fixture'ları ve batch safety.

### Kullanıcıyla yapılan kontroller (2026-09-13)

Kullanıcı başındayken "Kullanıcıyı bekleyenler" listesinden yapılabilenler.
Paylaşım açan süreçlerin hepsi ayrı bir state dizininde çalıştı; kullanıcının
gerçek iznine dokunulmadı. Kullanılan betikler geçiciydi, depoya girmedi.

- **#5 — yayılım.** İzin zaten kapalıydı, servisin cgroup'unda iş yoktu.
  `systemctl --user restart pcbridge` (08:52) sonrası servis HTTP + statik
  token ile sorgulandı: `capture.monitor: supported / linux.mutter.pipewire`.
  `doctor.sh` 8. bölüm tamamen yeşil (build `0cb5bcb46051`, release,
  kütüphaneler tamam). Açık kalan: Claude Desktop'un iki ve Claude Code'un iki
  stdio süreci dünden beri eski kodla çalışıyor; uygulama yeniden başlatılınca
  geçer.
- **#4 — gösterge gözle.** Ayrı bir süreç native paylaşımı açtı; kendi
  paylaşımını açmayan `gnome-screenshot` ile üç kare alındı: önce simge yok,
  paylaşım açıkken **sağ monitörün altındaki görev çubuğunda turuncu paylaşım
  simgesi** var, süreç `SIGKILL` ile öldürülünce yok. Mutter oturumu
  öldürmeden 0,11 sn sonra kapandı, native yardımcı da çıktı.
- **#3 — ekran kilidi.** Native paylaşım açıkken ekran
  `org.gnome.ScreenSaver.Lock` ile kilitlendi; kullanıcı 27,5 sn sonra
  parolasıyla açtı. Kilitliyken 3. ve 11. saniyede: Mutter oturum sayısı 0
  (native kilit gözcüsü paylaşımı kapattı), `SafetyGate.check` →
  `SCREEN_LOCKED`, kapıyı atlayan doğrudan `capture_provider.capture` →
  yardımcıdan `SCREEN_LOCKED`, **0 PNG**. Kilit açılınca oturum kendiliğinden
  açılmadı; sonraki çekim normal geldi.
- **#1 — farenin olay hızı.** Glorious Model D Wireless, USB 12 Mbit/s, uç
  nokta `bInterval 1` (1 ms). Kullanıcı fareyi gezdirirken `/dev/input/event5`
  cihaz kapılmadan okundu: 3,21 sn'de 1003 hareket raporu, en yoğun 1 sn'de
  686, hareket halinde **medyan aralık 1,00 ms (~998 Hz)**, p10 0,89 ms, p90
  2,87 ms. Adım 6'nın hipotezi güçlendi.

**Bulunan iki küçük şey (düzeltilmedi, kayıtta):**

1. **Kilit native oturumu kapatınca Python tarafı haberdar olmuyor.**
   `NativeScreenCast.is_open()` `True` kalıyor, oysa paylaşım kapalı.
   `runtime.py`'deki süre dolumu zamanlayıcısı için zararsız (kapatmayı yine
   dener), ama `tools.py`'de `is_open()`'a bakan iki durum metni yayını açık
   gösterebilir. Hata güvenli yönde (erişimi olduğundan fazla gösteriyor), yine
   de gösterge gerçeği söylemeli.
2. **Belgeler ve iki çıktı metni "GNOME üst çubuğu" diyor** (`CLAUDE.md`,
   `KULLANIM.md`, `KURULUM.md`, `config.example.toml`,
   `skills/computer-use/SKILL.md`, `monitors.py`, `cli/shot.py`; son ikisi
   testle sabitlenmiş). Bu makinede saat, sistem simgeleri ve paylaşım
   göstergesi sağ monitörün **altındaki** Zorin görev çubuğunda. Monitör
   bilgisi doğru, "üst" değil. Toplu düzeltme ayrı iş; `CLAUDE.md`'deki ölçüm
   satırı düzeltildi.

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

### Task 5.1 — Input parity fixture'ları ve batch safety · `tamamlandı`

**Sorun.** İzin çağrının başında bir kez soruluyordu; kırk eylemlik bir
`computer_batch` o tek cevapla koşuyordu. Telefondan `desktop_lock` bir sonraki
çağrıyı durduruyor, çalışan dizinin kalanını durdurmuyordu. Ayrı süreçteki bir
`pcb-do` ile MCP batch'inin tuşlarını da hiçbir şey sıraya koymuyordu.

**Ne yapıldı.**

- **`pcbridge/desktop/execution.py` (yeni):** `ExecutionLock` —
  `state_dir/desktop_execution.lock` üzerinde `flock`, yazma dizileri süreçler
  arası tek sıra. Sahibi ölünce kilit çekirdekle birlikte bırakılıyor, bayat
  kilit kalmıyor. Bekleyen çağrı 10 sn sonra `BUSY` (retryable) dönüyor ve
  kilidi kimin tuttuğunu söylüyor. `ExecutionSlot.pace()` saniyedeki eylem
  penceresini aynı kilit altında süreçler arası paylaşıyor
  (`desktop_execution.json`). `SequenceGuard` `batch.run`'ın cihaz bilmeyen
  `before_action` kancası.
- **`SafetyGate.verify(token)`:** kabul edilmiş dizinin her eylemi öncesi —
  masaüstü açık mı, ekran kilitli mi, **aynı** izin mi (grant id + revoke
  epoch), süresi doldu mu. Etkinlik ve hız bilinçli olarak yeniden sorulmuyor:
  uinput `IdleMonitor`'ü sıfırlıyor (ölçülmüş tuzak), hız kilit altında
  sayılıyor. Red kodları `REVOKED` (kullanıcı kapattıysa retryable değil, izin
  yenilendiyse retryable), `GRANT_EXPIRED`, `SCREEN_LOCKED`.
- **`DesktopRuntime.write_sequence(tool)`:** kilidi alıyor, bekledikten sonra
  izni yeniden doğruluyor; red gelirse bu sürecin basılı tuttuğu girdiyi
  bırakıyor.
- **`batch.run`:** `before_action` kancası (`stopped="safety"`). **Okunamayan
  odak artık "değişmedi" sayılmıyor:** planda tıklama varken odak baştan
  okunamazsa hiçbir eylem gitmiyor; tıklamadan sonra okunamazsa sonraki
  eylemden önce duruyor; `launch`/`focus` sonrası okunamazsa sonraki tıklama
  gönderilmiyor. Tıklamasız planlar eskisi gibi takipsiz koşuyor.
- **Bağlantılar:** `computer_batch` ve `pcb-do` kancayla koşuyor, kilit
  beklemesi bütçeden düşülüyor. Tek eylemli yazma araçları da (`mouse`,
  `keyboard`, `ui_click`, `ui_set_text`, `window_focus`,
  `computer_task(app=…)`) aynı kilidi alıyor. `pcb-do --json` `error_code`
  veriyor. `desktop_lock` ve `bridgekilit` kilidi **beklemiyor**.
- **Plandan geniş tutulan:** `PLAN.md` "write sequence'leri" diyor; tek
  eylemli araçlar da kilide alındı. Gerekçe: telefondan gelen tek bir `mouse`
  tıklaması yerel bir `pcb-do` dizisinin ortasına düşebilirdi — kilidin
  önlemek için var olduğu şeyin ta kendisi.
- **Golden fixture:** `tests/fixtures/native/input_events.json`, Python
  backend'inden bir kez kaydedilip gözden geçirildi; testler onu yeniden
  üretmiyor. İki sanal cihazın yetenekleri (fare: `BTN_TOUCH`/`BTN_TOOL_PEN`
  yok, ABS 0..3839 × 0..1079) ve 13 durum: kombinasyon, basılı değiştiriciyle
  kombinasyon, hold/release, `release_all` sırası, smoothstep yol (960 px → 24
  nokta), ışınlama, kırpılan ilk hareket, çift tıklama, iki kaydırma yönü,
  sürükleme, hold zamanlayıcısının otomatik bırakması, ham yazma. Task
  5.2/5.3'ün Rust tarafı bununla karşılaştırılacak.
- Belgeler: `CLAUDE.md` (katman tablosu, kapı bölümü), `KULLANIM.md` §8,
  `config.example.toml` (`max_actions_per_second` yorumu).

**Ölçüldü (2026-09-13, bu makine):** eylem başına ek kontrolün maliyeti —
ekran kilidi sorgusu p50 **2,9 ms** (maks 4,7), lease touch p50 **0,03 ms**
(dosyaya yazdığında 12 ms). En ucuz eylem 30 ms; bütçe tahmini değiştirilmedi.

**Acceptance — ölçüldü.**

| Ölçüt (`PLAN.md`) | Kanıt |
|---|---|
| Revoke sonrası tek bir ek tuş/tıklama yok | olay kaydı `["key a", "key b"]`, kalan üç eylem gönderilmedi; MCP telinde `computer_batch` → `REVOKED`/`safety`, üç tıklamanın yalnızca biri |
| Odak hatası sonrası tek bir ek tuş/tıklama yok | odak baştan okunamazsa 0 eylem; tıklamadan sonra okunamazsa `ctrl+a` ve `Delete` gitmiyor; `launch` sonrası okunamazsa tıklama gitmiyor |
| Her eylemden önce grant/lock/revoke/deadline; etkinlik yeniden sorulmuyor | altı eylemde ekran kilidi sorgusu 1 + 1 + 6, etkinlik sorgusu 1 (yalnızca kabulde); süre dolumu `GRANT_EXPIRED`, kilit `SCREEN_LOCKED` |
| Süreçler arası sıra, beklemeden sonra yeniden kontrol | başka süreç tutarken `BUSY` (tutanın adıyla); öldürülen sahip bayat kilit bırakmıyor; bekleyen dizi kilidi alınca revoke'u görüyor, 0 eylem |
| Hız sınırı aynı kilit altında paylaşılıyor, batch aralığı korunuyor | hemen ardından başlayan dizi pencereyi 0,8 sn bekliyor; sınır içindekiler beklemiyor; `min_gap` aynen |
| Stop/budget/error/revoke'ta basılı girdi bırakılıyor | budget, focus, repeat, error, safety beşinde de `release_all`; düzgün biten dizide bilinçli hold korunuyor; kabul reddinde süreç kendi basılı girdisini bırakıyor |
| Batch MCP ve gerçek cihaz import etmiyor | alt süreçte `batch` + `execution` import edilince `fastmcp`, `mcp`, `evdev`, `gi`, `pydantic`, `starlette` yüklenmiyor |
| Korunanlar: `expect_focus`, bütçe, kalan eylemler, `super` sonrası ham yazma, bilinçli hold | `tests/test_desktop.py` 583/583, testler değişmeden |

**Testlerin tuttuğu mutasyonla denendi: 16/16.** Kancanın çağrılmaması,
okunamayan odağın üç ayrı yerde yok sayılması, `verify`'ın izne ya da ekran
kilidine bakmaması, kilidin paylaşımlı olması, hız penceresinin hiç
beklememesi, bekleyen dizinin yeniden kontrol edilmemesi, reddedilen kabulde
basılı girdinin bırakılmaması, `computer_batch` / `pcb-do`'nun kancasız
koşması, tek eylemli aracın pencereye sayılmaması; fixture tarafında
kombinasyonun basma sırasıyla bırakılması, yolun smoothstep'i kaybetmesi ve
farenin `BTN_TOUCH` ilan etmesi. Dosyalar hash ile geri doğrulandı.

**Test sonuçları:**

- `tests/contracts/test_batch_safety.py` (yeni) → 33;
  `tests/contracts/test_input_contract.py` (yeni) → 9;
  `test_mcp_errors.py` → 16 (+5); contract discovery → **218, OK** (171 → 218)
- integration discovery → 15 koşum, OK (1 canlı atlandı); teslim testinin sahte
  kapısına `verify` eklendi
- `tests/test_desktop.py` → **583**; `test_models.py` → **106**;
  `test_test_safety.py` → OK; `--check` → 0
- Rust değişmedi. **Gerçek girdi testi çalıştırılmadı:** Task 5.1 istemiyor;
  gerçek girdi testleri kullanıcı yanındayken (Task 5.4, Gate 5).

**Rollback:** Task 5.1 commit'ini geri almak yeterli. Yeni ayar yok, var olan
bir dosya biçimi değişmedi; `desktop_execution.lock`/`.json` yalnızca yeni
dosyalar.

**Sonraki somut adım:** Task 5.2 — Rust keyboard ve held-key lifecycle.
Kullanıcı isteğiyle burada durduruldu.

### Task 5.2 — Rust keyboard ve held-key lifecycle · `tamamlandı`

**Başlangıç kapsamı (2026-09-13, kullanıcı onaylı):** Yalnızca klavye
injection Rust'a taşınacak. Açıkça `[native] input = "rust"` seçildiğinde
klavye native yardımcıdan, pointer ve clipboard mevcut Python provider'dan
gelecek; varsayılan `python` kalacak. Task 5.3 pointer'ı, Task 5.4 clipboard ve
varsayılan değişikliği kapsıyor.

**Ne yapıldı.**

- `evdev 0.13.2` ile `pcbridge-keyboard` virtual device wrapper'ı eklendi;
  custom ioctl binding yazılmadı. Cihaz yalnızca ilk açık native keyboard
  isteğinde kuruluyor, capability isteği `/dev/uinput` açmıyor.
- Python key tablosunun bütün Linux kodları, alias'ları, kombinasyon basma ve
  ters sırada bırakma davranışı korundu. Önceden tutulmuş modifier bir `key()`
  kombinasyonunca bırakılmıyor.
- `held()` ve tek okumalık `take_auto_released()` native process'e taşındı.
  Ayrı monotonic worker, yeni IPC isteği gelmesini beklemeden
  `hold_max_seconds` sonunda açık release gönderiyor.
- Shutdown, revoke/screen-lock ve event write hatası release'i açıkça deniyor;
  ardından virtual device kapanıyor. İlk device'ın 1,2 saniyelik settle
  aralığından sonra grant yeniden doğrulanıyor; bu aralıkta revoke olmuşsa key
  gönderilmiyor.
- Native IPC'ye `input.keyboard.ensure`, `key`, `key_down`, `key_up`, `held`,
  `release_all` ve `take_auto_released` metotları eklendi. State değiştiren
  istekler helper'ın initialize sırasında bağlandığı grant id/revoke epoch ile
  eşleşiyor. Python adapter her input write'ı tek `NativeClient.request` olarak
  gönderiyor; process restart'ı üzerinden replay yok.
- `[native] input = "python"` shipped ve implicit default olarak eklendi. Açık
  `rust` seçimi yalnızca keyboard'u native helper'a taşıyor; pointer ve
  clipboard Python provider'da kalıyor. Helper yokluğu pointer capability'sini
  gizlemiyor ve keyboard için sessiz fallback yapılmıyor.
- Public config, kullanım notu ve native protocol belgesi güncellendi.

**Kod incelemesinde bulunan ve kapatılan iki hata:** Hybrid provider'ın native
helper eksikliğini Python pointer'a da yansıtması regression testiyle önce
kırmızı yakalandı. Ayrıca device settle aralığındaki revoke için ikinci grant
kontrolü ve revoke sonrası kapalı device yeniden kurma yolu eklendi.

**Test sonuçları (2026-09-13):**

- TDD kırmızı koşum: Rust sözleşmesi eksik `platform::linux::input` modülünde,
  Python sözleşmesi eksik `RustKeyboardInputProvider` import'unda beklenen
  şekilde durdu; uygulama sonrasında yeşile döndü.
- `keyboard_contract.rs` → **9 geçti**: golden event/capability eşliği,
  alias'lar, tutulmuş modifier, sahte monotonic saat, shutdown/revoke/error
  cleanup. `native_revoke.rs` → **3 geçti**; tutulmuş tuş revoke sonrası
  250 ms sınırında başka input isteği olmadan bırakıldı ve eski grant reddedildi.
- Rust workspace bütün target'lar: default → **106 geçti**, test-harness →
  **117 geçti**. `strace -f -e trace=openat` ile default Rust test setinde
  `/dev/uinput` açılmadığı ayrıca doğrulandı.
- Python contract discovery → **227 tests, OK**; integration discovery →
  **15 tests, OK (1 live capture skipped)**; `tests/test_desktop.py` →
  **583 geçti**; `tests/test_models.py` → **106 geçti**;
  `tests/test_test_safety.py` → **1 test, OK**.
- GNOME `test_state.js` → **31 geçti**. `python -m compileall`, örnek config
  ile server `--check`, `git diff --check` ve American English spelling taraması
  → exit `0`.
- `cargo fmt --check`; default ve all-features için `cargo clippy -- -D warnings`;
  locked release build → exit `0`. Default release'in `--test-mode` reddi
  beklendiği gibi exit `2`.
- `cargo audit --no-fetch`, yerel 1243 advisory kaydıyla 146 dependency'yi
  taradı; bilinen vulnerability yok. Yeni `evdev` dependency'si
  `Apache-2.0 OR MIT` lisanslı; lockfile Cargo tarafından üretildi.
- `tests/test_e2e.py` ve gerçek input/capture/AT-SPI seçimleri çalıştırılmadı;
  `PCBRIDGE_TEST_INPUT` ve `PCBRIDGE_TEST_BATCH` dahil bütün live bayraklar
  unset kaldı. Gerçek klavye/fare olayı gönderilmedi.

**Acceptance:** Task 5.1 golden event'leri Rust'ta aynı sırada üretildi. Hold
timer sahte monotonic saatle bağımsız çalıştı ve bildirimi yalnızca bir kez
döndürdü. Revoke, shutdown ve event hatası release + close yaptı; settle sonrası
grant kontrolü revoke edilmiş isteğin key göndermesini engelledi. Default
`python`; `rust` yalnızca açık test seçimi; pointer/clipboard migration dışı.

**Commit:** Bu kayıtla aynı yerel Task 5.2 commit'i; push yapılmadı.

**Rollback:** Önce desktop grant'i revoke edip native helper'ı kapat; sonra
`[native] input = "python"` ile yeni pcbridge process'i başlat. Python keyboard
silinmedi ve default zaten bu yol.

**Sonraki somut adım:** Kullanıcı onayından sonra Task 5.3 — Rust pointer,
motion path ve native koordinat adapter'ı. Burada durduruldu.

### Task 5.3 — Rust pointer, motion path ve native koordinat adapter'ı · `tamamlandı`

**Başlangıç kapsamı (2026-09-13, kullanıcı onaylı):** PLAN.md'deki Task 5.3
sözleşmesi uygulanacak. `capture.to_global()` shot/monitor matematiğinin tek
girişi olarak kalacak; native IPC shot veya monitor kabul etmeyecek, yalnızca
çözülmüş global nokta ile enjeksiyon öncesi topoloji korumasını alacak.
`pointer.json`, canvas clamp'i, smoothstep hareket yolu, drag/scroll olay sırası
ve mevcut absolute-device capability seti korunacak. Gerçek klavye/fare testi
çalıştırılmayacak.

**Onaylanmış test dikişleri:** Saf `pcbridge-core` hareket/koordinat sözleşmesi;
kayıt aygıtlı native pointer event sözleşmesi; deterministic helper IPC sınırı;
sahte `NativeClient` kullanan Python Rust provider seçimi. Beklenen değerlerin
kaynağı Task 5.1'de kaydedilip gözden geçirilen
`tests/fixtures/native/input_events.json` golden fixture'ıdır.

**Ne yapıldı (2026-09-18):**

- Saf Rust core'a Python ile aynı smoothstep `move_path` ve tek
  global→absolute-device clamp'i eklendi. `speed=0`, 60 ms taban, yapılandırılan
  tavan, ties-to-even rounding ve drag'in 10 minimum adımı golden fixture ile
  birebir korunuyor.
- `evdev 0.13.2` üzerinde `pcbridge-pointer` virtual device eklendi. Capability
  seti yalnızca `BTN_LEFT/RIGHT/MIDDLE`, `ABS_X/Y`, `REL_WHEEL/HWHEEL`; bilinçli
  olarak `BTN_TOUCH` ve `BTN_TOOL_PEN` yok. Custom ioctl binding eklenmedi.
- Move, click, double click, drag, dikey/yatay scroll, mouse down/up ve
  held/auto-release sözleşmeleri native oldu. Revoke, shutdown, topology rebuild
  ve event write hatasında tutulmuş/geçici düğmelere explicit release gönderiliyor.
  Uzun motion/scroll boyunca mutex uyku süresince tutulmuyor; revoke kalan yolu
  beklemeden hareketi kesiyor.
- Mevcut `pointer.json` `{x,y,t}` biçimi, 300 saniyelik yaşı ve atomik
  `pointer.tmp` replace yolu korundu. Device açılışı state'i silmiyor. Geçerli
  yeni topology eski aygıtı release+close edip yeni geometry ile kuruyor ve taze
  persisted konumu koruyor.
- Native IPC'ye `input.pointer.ensure/move/click/drag/scroll/mouse_down/mouse_up`,
  `held`, `release_all`, `take_auto_released` ve `position` eklendi. State
  değiştiren her istek grant/revoke epoch, bounded ayarlar ve güncel
  `topology_id` ile iki kez korunuyor; uyuşmazlık injection öncesi typed
  `DISPLAY_CHANGED` oluyor. Allowlist `shot`, `monitor` ve diğer bilinmeyen
  alanları reddediyor.
- Python `RustInputProvider`, `[native] input = "rust"` seçiminde keyboard ve
  pointer'ı aynı helper'a bağlıyor; clipboard Task 5.4'e kadar Python'da.
  Adapter her eylemde Python monitor tablosundan taze topology kimliği alıyor,
  yalnızca çözülmüş global noktayı bir kez gönderiyor ve başarısız write'ı
  process restart'ı üzerinden tekrar oynamıyor. Shipped default `python` kaldı.
- Native protokol belgesi, örnek config ve runtime seçim açıklamaları yeni
  pointer sınırıyla güncellendi. PLAN zaten mimari kararı kaydettiği için ayrı
  ADR oluşturulmadı.

**Kod incelemesinde bulunan ve kapatılan hata:** İlk uygulama smooth move ve
scroll boyunca pointer mutex'ini sleep sürelerinde de tutuyordu; 5 saniyelik bir
yolda lifecycle watchdog revoke sonrası aygıtı hemen kapatamıyordu. Kilit tek event
yazımına daraltıldı, click'in geçici basışı cleanup state'ine alındı ve event
hatası timer worker'ını uyandırıyor. Regresyon testi uzun yolu revoke ile 250 ms
sınırının altında kesiyor.

**Test sonuçları (2026-09-18):**

- `pointer_path.rs` → **4 geçti**; `pointer_contract.rs` → **12 geçti**:
  golden path/event sırası, capability seti, clamp, teleport/drag, click/scroll,
  state yaşı, monotonic timer, revoke ve error cleanup.
- Rust workspace bütün target'lar: default → **122 geçti**; test-harness →
  **136 geçti**. Harness IPC'de monitor 2 global `2500` ikinci offset olmadan
  `2500` kaldı; stale topology injection'dan önce reddedildi; geçerli topology
  rebuild held düğmeyi bıraktı ve state'i korudu.
- Python contract discovery → **230 tests, OK**; integration discovery →
  **15 tests, OK (1 live capture skipped)**; `tests/test_desktop.py` →
  **583 geçti**; `tests/test_models.py` → **106 geçti**;
  `tests/test_test_safety.py` → **1 test, OK**.
- GNOME `test_state.js` → **31 geçti**. `python -m compileall`, örnek config
  ile server `--check`, `git diff --check` ve American English spelling taraması
  → exit `0`.
- `cargo fmt --check`; default ve all-features için `cargo clippy -- -D warnings`;
  locked release build → exit `0`. Default release'in `--test-mode` reddi
  beklendiği gibi exit `2`.
- `cargo audit --no-fetch`, yerel 1243 advisory kaydıyla 146 dependency'yi
  taradı; bilinen vulnerability yok. Yeni dependency eklenmedi.
- `strace -f -e trace=openat` ile default Rust test setinde `/dev/uinput`
  açılmadığı doğrulandı. `tests/test_e2e.py` ve gerçek
  input/capture/AT-SPI seçimleri çalıştırılmadı; bütün live bayraklar unset
  kaldı. Gerçek klavye/fare olayı gönderilmedi.

**Acceptance:** Golden path/event sequence aynı; ikinci monitörde offset yalnızca
Python `capture.to_global()` sınırında bir kez uygulanıyor; native caller shot veya
monitor gönderemiyor. Raw global koordinatlar native canvas'ta bir kez clamp
ediliyor. Topology mismatch event öncesi typed reddediliyor; topology rebuild,
persisted state, revoke ve release fail-closed.

**Commit:** Bu kayıtla aynı yerel Task 5.3 commit'i; push yapılmadı.

**Rollback:** Önce desktop grant'i revoke edip native helper'ı kapat; sonra
`[native] input = "python"` ile yeni pcbridge process'i başlat. Python keyboard,
pointer ve clipboard yolu silinmedi; shipped default zaten bu yol.

**Sonraki somut adım:** Kullanıcı onayından sonra Task 5.4 — text/clipboard
adapter'ı ve input default gate. Burada durduruldu.

### Codex'in 5.2/5.3 işinin kontrolü ve izin değişimi düzeltmesi · `tamamlandı` (2026-09-19)

**Neden.** Task 5.2 ve 5.3'ü Codex yaptı (`f89aaf6`, `a28884d`). Kullanıcı 5.4'e
geçmeden önce kısa bir kontrol istedi.

**Kontrol: kayıtlar doğru.** Bütün güvenli testler yeniden koşuldu ve sayılar
Codex'in kaydıyla aynı çıktı: Rust default **122**, test-harness **136**; fmt ve
iki clippy temiz; Python contract **230**, integration **15** (1 atlandı),
`test_desktop.py` **583**, `test_models.py` **106**. Rust testlerinin ikisi de
`strace -f -P /dev/uinput` altında koştu: **0 open/openat**. Yalnızca 6 `statx`
çıktı, yani varlık kontrolü. Test-harness modu gerçek cihaz yerine null klavye
ve null pointer kullanıyor.

**Bulunan hata — üç belirti, tek kök.** Native helper `initialize`'da okuduğu
izne bağlanıyor ve bir daha bağlanmıyor. Bu tasarım gereği; bkz.
`native_revoke.rs`: `revoke_releases_resource_and_session_never_rebinds`. Öte
yandan her `desktop_unlock` yeni bir `grant_id` yazıyor (`lease.grant()` →
`uuid4`). Python ise helper'ı sağlayıcının ömrü boyunca tutuyordu.

| Senaryo | Düzeltmeden önce | Sonra |
|---|---|---|
| Input (harness, null klavye): izin açıkken ikinci `desktop_unlock` | `REVOKED` | çalıştı |
| Input: iki kez `desktop_lock`, sonra üçüncü izin (`close()` yalnızca **bir kez** çalışıyordu) | `REVOKED` | çalıştı |
| Input: izin süresi doldu, sonra yeni `desktop_unlock` | `REVOKED` | çalıştı |
| **Capture** (paketlenmiş helper, gerçek Mutter): izin açıkken ikinci `desktop_unlock` | paylaşım kapandı, `start` ve kare `REVOKED`, düzelmesi için `desktop_lock` gerekiyordu | paylaşım yeniden açıldı (+1 oturum, sızıntı yok), kare geldi |
| Capture: **başka süreç** `desktop_unlock` yaptı, bu süreç `start` çağırmadı | ölçülmedi, kökü aynı | kare geldi, oturum talep üzerine açıldı |

Input satırları yalnızca açık `[native] input = "rust"` seçimini etkiliyordu.
Capture satırı ise **Task 4.3'ten beri varsayılan yolda**: izin açıkken
`desktop_unlock`'u yeniden çağıran bir ajan ekran görüntüsü alamaz hale
geliyordu. Bu hata Task 3.4'te yazıldı ve Task 4.3'te varsayılan yapıldı.
Oradaki canlı testler süreç başına tek bir unlock yapıyordu, o yüzden
yakalanmadı.

**Düzeltme.** `pcbridge/desktop/backends/rust.py` → `GrantBoundHelper`: her
izin için tek helper. İzin kimliği (grant id + revoke epoch) değişince eski
helper'dan release istenir, helper kapatılır ve yenisi başlatılır. Temizlik
hatası yeni izindeki isteği engellemez, yalnızca loglanır. İstek yeni helper'a
**bir kez** gider; tekrar oynatma yok. `NativeScreenCast` ve `RustInputProvider`
aynı sınıfı kullanıyor. `RustInputProvider.close()` artık tek seferlik değil.
Belgeler: `docs/native/protocol-v1.md` (input bölümü) ve `CLAUDE.md` (ölçülmüş
gerçekler).

**Testler.**

- `tests/contracts/test_native_grant_rebind.py` (yeni) → **15**. Sahte helper
  gerçeğinin tek kuralını taşıyor: ilk sorulduğu izne cevap verir, başkasına
  `REVOKED` der.
- `tests/integration/test_native_revoke.py` → **+1**. Gerçek harness helper
  `--test-mode`'da, null klavyeyle koşuyor: ikinci unlock, lock ve üçüncü
  unlock sonrası yazma çalışıyor, her lock helper'ı hemen durduruyor.
  `strace -P /dev/uinput` → 0 open. Düzeltme kaldırılınca bu test de kırmızıya
  dönüyor.
- **Mutasyon 8/8 yakalandı:** izin değişince eskiyi bırakmamak; capture'ın ya
  da input'un izni yok sayması; eski helper'dan release istememek; tek seferlik
  `close()`; `close()`'un helper'ı durdurmaması (input ve capture); temizlik
  hatasının yeni isteği engellemesi. İlk denemede iki mutasyon kaçtı: test
  helper'ın kilitten *sonra* değil, bir sonraki yazmada kapandığını görüyordu.
  Test sıkılaştırıldı. Dosya hash ile geri doğrulandı.
- Sonuçlar: contract **245** (+15), integration **16** (+1, 1 atlandı),
  `test_desktop.py` **583**, `test_models.py` **106**, `test_test_safety.py`
  OK, `--check` 0, `git diff --check` temiz. Rust değişmedi.
- Gerçek klavye/fare olayı gönderilmedi. Capture ölçümü geçici bir state
  dizininde, kullanıcının iznine dokunmadan birkaç saniyelik paylaşımla yapıldı.

**Kontrolde bulunan, bu commit'te düzeltilmeyen:**

- **Task 5.3'ün 7. maddesi yarım.** PLAN.md bunu istiyor: "Capture'dan gelen
  koordinat için topology uyuşmazlığını injection öncesinde reddet". Uygulanan
  kontrol ise Python'un **şu anki** monitör tablosunu Rust'ın **şu anki**
  tablosuyla karşılaştırıyor. Çekim ile tıklama arasında düzen değişirse bunu
  görmüyor, çünkü çekim kaydında (`<id>.json`) `topology_id` yok. Python yolunda
  da bu kontrol hiç yoktu. **Kapatıldı:** Task 5.4 → 0.
- `is_open()`, gözcü paylaşımı kapattıktan sonra da `True` kalıyor. Bilinen
  kusur, Adım 4'te kayıtlı.

**Rollback:** Bu commit'i geri almak yeter. Yeni ayar yok, dosya biçimi değişmedi.

### Task 5.4 — Text/clipboard adapter'ı ve input default gate · `tamamlandı`

**Başlangıç (2026-09-19, kullanıcı onaylı).** PLAN.md 5.4 küçük commit'lere
bölündü:

| # | Ne | Durum |
|---|---|---|
| 0 | Task 5.3'ün yarım 7. maddesi: çekim kaydına `topology_id`; düzen değişince `shot=` koordinatı `DISPLAY_CHANGED` | `tamamlandı` |
| 1 | Pano işlemleri Python'da ayrı arayüzde (`clipboard.py`), davranış aynı; restore fixture'ı | `tamamlandı` |
| 2 | Rust'ta aynı `wl-copy`/`wl-paste` programlarını yöneten adapter; wl-copy boru tuzağı; tek MIME sınırı capability'de | `tamamlandı` |
| 3 | `[native] input = "rust"` seçilince pano native adapter'dan. `type_text` orkestrasyonu Python'da kalır: pano → native `ctrl+v` → geri yükleme | `tamamlandı` |
| 4 | Gerçek girdi testleri, kullanıcı başındayken: Türkçe metin, değiştirici tuşlar, move→doğrulama→click, drag, süre dolumu/revoke, ≤1 px sapma | `tamamlandı` — 8/8, **Gate 5 geçti** |
| 5 | Gate 5 kararı. Geçerse `[native] input` varsayılanı değişir | `tamamlandı` — varsayılan `auto` |

Kod okurken bulunan: `[native] input = "rust"` seçildiğinde `type_text` zaten
native `ctrl+v` gönderiyor. `RustInputProvider` `key()`'i eziyor,
`InputBackend._type_clipboard` de `self.key("ctrl+v")` çağırıyor. Yani 3'ün
orkestrasyon kısmı hazır; eksik olan panonun kendisi.

#### 0 — Çekim kaydında ekran düzeni · `tamamlandı`

**Sorun.** Çekim kaydı monitörün çekim anındaki ofsetini ve ölçeğini tutuyor.
`load_shot` bunu bilerek canlı tablodan değil kayıttan kuruyor. Arada monitör
takılırsa, çıkarılırsa, yer ya da çözünürlük değişirse aynı ofset başka bir
ekrana düşer ve tıklama ajanın hiç görmediği bir yere gider.

**Ne yapıldı.** `Shot.topology` ve kayıtta `topology_id` alanı eklendi. Değer,
çekimde kullanılan monitör tablosundan geliyor ve üç yakalama yolunun üçünde
de yazılıyor (Python yayını, native, `gnome-screenshot`). `capture.to_global()`
`shot=` ile gelen koordinatı güncel tabloyla karşılaştırıyor ve fark varsa
`ShotLayoutChanged` fırlatıyor. Provider bunu `DISPLAY_CHANGED` / `coordinate`
olarak, retryable ve "yeni görüntü al" önerisiyle döndürüyor. `mouse`,
`computer_batch` ve `pcb-do` aynı provider'dan geçtiği için üçü de kazanıyor.
Alanı olmayan eski kayıt eskisi gibi geçiyor. `monitor=` ve global
koordinatlar çekime bakmıyor. Belgeler: `KULLANIM.md`, `CLAUDE.md`.

**Testler.** `tests/contracts/test_shot_layout.py` (yeni) → **6**: üç yol da
düzeni yazıyor; değişmeyen düzende dönüşüm birebir aynı; yer değiştirmiş ve
çözünürlüğü değişmiş düzen reddediliyor; provider `DISPLAY_CHANGED` ve
retryable; eski kayıt geçiyor; `monitor=`/global etkilenmiyor. `gnome-screenshot`
testte gölgelendi, gerçek çekim yapılmadı. **Mutasyon 8/8 yakalandı**: kontrolün
kaldırılması, kayda/yükleyiciye yazılmaması, iki yakalama yolundan birinin ve
`_Pending.shot()`'un alanı düşürmesi, provider'ın yanlış kod ya da retryable
vermesi. Sonuçlar: contract **251** (+6), integration **16**, `test_desktop.py`
**583** (değişmeden), `test_models.py` **106**, `--check` 0.

**Rollback:** Commit'i geri almak yeter. Yeni kayıtlardaki fazladan alanı eski
kod yok sayar.

#### 1 — Pano arayüzü (`clipboard.py`) · `tamamlandı`

**Ne yapıldı.** `input.py`'deki dört pano fonksiyonu (`_wl_read`, `_wl_copy`,
`_clipboard_save`, `_clipboard_restore`) ve `wl-copy` hata metinleri
`pcbridge/desktop/clipboard.py`'ye taşındı. Yeni `Clipboard` arayüzünün üç
işlemi var: `save`, `put_text`, `restore`. Python uygulaması `WlClipboard`.
Argümanlar, `DEVNULL` kuralı, tek MIME davranışı ve hata metinleri aynı kaldı.
`InputBackend` panoyu `clipboard=` ile alıyor (varsayılan `WlClipboard`).
`_type_clipboard` orkestrasyonu yerinde: yedekle → koy → `self.key("ctrl+v")`
→ geri yükle. Davranış değişmedi.

**Ortak fixture.** `tests/fixtures/native/clipboard_cases.json`, 7 durum:
Türkçe metin bayt bayt geri geliyor; boş pano yeniden temizleniyor; ikili
içerik tipini ve baytlarını koruyor; sondaki satır sonu kalıyor; yalnızca ilk
MIME tipi saklanıyor; restore kapalıyken yazılan metin kalıyor; okunamayan pano
temizleniyor. Son ikisi mevcut davranış. Sonuncusu veri kaybı demek ama
değiştirilmedi, çünkü bu adım davranış değiştirmiyor. Her durum program
çağrılarının sırasını, yapıştırmanın gördüğü içeriği ve sonraki panoyu sabitliyor.
Rust adapter'ı (5.4 / 2) aynı dosyayı okuyacak.

**Kanıt, davranış aynı:** fixture'ın 7 durumu refactor **öncesi** `input.py`
(HEAD) ile de koşuldu. Çağrılar, son pano ve `wl-copy`'nin çıktı yakalamaması
yedisinde de birebir aynı çıktı.

**Testler.** `tests/contracts/test_clipboard_contract.py` (yeni) → **7**. Sahte
bir Wayland panosu `wl-paste`/`wl-copy`'yi gerçek programlar gibi cevaplıyor;
hiçbir program çalışmıyor, tuş gitmiyor. **Mutasyon 8/8 yakalandı:** metin
okumada `--no-newline`'ın düşmesi, boş panonun temizlenmemesi, `wl-copy`'nin
çıktıyı yakalaması (boru tuzağı), yanlış MIME, son tipin saklanması, restore
kapalıyken geri yükleme, yapıştırmanın yazmadan önce gitmesi, yazma hatasının
yutulması. Sonuçlar: contract **258** (+7), integration **16**,
`test_desktop.py` **583**, `test_models.py` **106**, `--check` 0, `compileall`
0.

**Rollback:** Commit'i geri almak yeter.

#### 2 — Rust pano adapter'ı ve IPC · `tamamlandı`

**Ne yapıldı.** `rust/crates/pcbridge-native/src/platform/linux/clipboard.rs`
Python'un hep çalıştırdığı `wl-paste`/`wl-copy` programlarını aynı
argümanlarla çalıştırıyor. Programlar `Programs` trait'inin arkasında;
testler bir pano modeli, üretim ise gerçek süreçler kullanıyor. Kurallar
Python'dakiyle aynı: metin tipleri `--no-newline` ile okunuyor, yalnızca ilk tip
saklanıyor, `wl-copy`'nin stdout/stderr'i `/dev/null`'a gidiyor. Buna ek olarak
program başına 10 sn zaman aşımı var ve süre dolunca program öldürülüyor.
İçerik 128 MiB ile sınırlı; sınır aşılınca okuma zaman aşımını beklemeden
kesiliyor. MIME tipi tek satır ve en fazla 256 bayt olmalı. Pano baytları hiçbir
log ya da hata metnine girmiyor.

IPC: `clipboard.read`, `clipboard.write`, `clipboard.clear`. Üçü de izne bağlı;
izin yanlışsa program hiç çalışmıyor. `read`, içeriği response'un binary
payload'unda döndürüyor ve okuma sürerken izin geri alınırsa içerik
döndürülmüyor. Binary payload taşıyan tek request `write`: içerik hiçbir zaman
header'da değil. Bilinmeyen alanlar (`deny_unknown_fields`) ve bozuk MIME
reddediliyor, reddedilen değer yanıtta tekrar edilmiyor. Test kipi yalnızca
`PCBRIDGE_TEST_WL_PASTE`/`PCBRIDGE_TEST_WL_COPY` ile adı verilen programları
çalıştırıyor, yoksa `UNSUPPORTED` dönüyor. `capabilities`
`clipboard.read`/`clipboard.write` durumunu hiçbir program çalıştırmadan
(`PATH` + Wayland soketi) ve tek MIME sınırını `limitations` alanında
bildiriyor. Özellik adı: `clipboard`. Belge: `docs/native/protocol-v1.md`.

**Testler.**

- `tests/clipboard_contract.rs` (yeni) → **6**. Ortak fixture'ın 7 durumu,
  Python'la aynı program çağrıları, yapıştırmanın gördüğü içerik ve sonraki
  pano. Gerçek süreçle yapılan kontroller: `wl-copy`'nin stdout/stderr'i
  `/dev/null`, arkada 3 sn yaşayan bir sahip kalsa da çağrı hemen dönüyor ve
  stdin bayt bayt geçiyor; takılan program 300 ms'de öldürülüyor; sınırı aşan
  içerik hemen reddediliyor; eksik ve başarısız program raporlanıyor; bozuk
  MIME hiçbir programa ulaşmıyor.
- `tests/clipboard_ipc.rs` (yeni, test-harness) → **7**: yazma → okuma bayt
  bayt geri geliyor (NUL dahil); eski izin program çalışmadan `REVOKED` ve pano
  dokunulmamış; okuma sürerken yapılan revoke içerik döndürmüyor; binary
  yalnızca `write`'ta kabul ediliyor; içerik header'da taşınamıyor ve
  yankılanmıyor; adsız programla test kipi panoya ulaşmıyor; `clipboard`
  özellik listesinde.
- Birim test: MIME doğrulaması; üretim `capabilities` pano girdileri.
- **Mutasyon 9/9 yakalandı**, hepsi test hatası: `wl-copy` stdout'unun boru
  olması, `--no-newline`'ın düşmesi, son tipin saklanması, takılan programın
  öldürülmemesi, program öncesi izin kontrolünün kalkması, okuma sonrası
  yeniden kontrolün kalkması, binary'nin her metoda ya da hiçbirine açılması,
  bilinmeyen alanların kabulü.
- `strace -e execve,openat` altında iki Rust test seti: gerçek
  `/usr/bin/wl-copy`/`wl-paste` **0 kez** çalıştı, yalnızca `/tmp`'deki sahte
  programlar (23 çağrı); `/dev/uinput` açılmadı.
- Sonuçlar: Rust default **129** (+7), test-harness **150** (+14); fmt ve iki
  clippy temiz; release derleme `--test-mode`'u reddediyor (exit 2). Python
  contract **258**, integration **16**, `test_desktop.py` **583**.

**Henüz bağlı değil:** Python bu metotları 5.4 / 3'te kullanmaya başlayacak.
Paketlenmiş yardımcı yeniden derlenmedi; o da canlı testlerden önce (5.4 / 4)
yapılacak.

**Rollback:** Commit'i geri almak yeter. Python tarafı henüz çağırmıyor.

#### 3 — Native panoyu `type_text`'e bağla · `tamamlandı`

**Ne yapıldı.** `backends/rust.py` → `NativeClipboard`, `Clipboard` arayüzünü
yardımcının `clipboard.*` IPC'si üzerinden uyguluyor. `RustInputProvider` onu
kullanıyor. `InputBackend._type_clipboard` orkestrasyonu değişmedi: yedekle →
koy → `ctrl+v` → geri yükle. `ctrl+v` zaten native klavyeye gidiyordu. Böylece
`[native] input = "rust"` seçildiğinde Python hiçbir pano programı
çalıştırmıyor.

- İçerik yalnızca binary payload olarak gidiyor. Header'da `grant_id`,
  `revoke_epoch` ve `mime` dışında alan yok. İstekler aynı `GrantBoundHelper`
  üzerinden gidiyor, yani yeni izinde pano da yeni yardımcıya geçiyor.
- **Geri yükleme hataları Python yolundaki gibi:** `WlClipboard.restore` başarısız
  bir `wl-copy`'yi yok sayıyor. Yapıştırma o noktada olmuş olur ve çağrıyı
  başarısız saymak metnin iki kez yazılmasına yol açabilir. Native yolda da
  yalnızca "program başarısız" (`EXECUTION_UNKNOWN`) loglanıp geçiliyor.
  Revoke, eksik program ve zaman aşımı yükseliyor.
- Pano metotlarını tanımayan eski bir yardımcı `UNSUPPORTED` yerine
  `BACKEND_UNAVAILABLE` ve "`scripts/build-native.sh` ile yeniden derleyin"
  diyor. Hiçbir şey yazılmıyor, yapıştırılmıyor.
- **Tek MIME sınırı artık görünüyor** (PLAN 5.4 madde 4). Bildirildiği yer
  `clipboard.write`, çünkü diğer temsilleri kaybeden şey geri yükleme. Rust
  `capabilities` ve iki Python provider aynı sınırlama metnini veriyor.
  Yardımcı yoksa native pano `unavailable`, Python panosuna düşmüyor.
- Belgeler: `KULLANIM.md` (native seçim), `CLAUDE.md` (katman listesi),
  `config.example.toml` ve `config.py` yorumları.

**Testler.**

- `tests/contracts/test_native_clipboard.py` (yeni) → **10**: fixture'ın 7
  durumu IPC sırasıyla (`read` → `write` → `input.keyboard.key` →
  `write`/`clear`), yapıştırmanın gördüğü içerik ve sonraki pano; içeriğin
  hiçbir header'da geçmemesi; yazma hatasında tipli hata ve yapıştırma yok;
  geri yüklemede program hatası yutulur, revoke yutulmaz; eski yardımcıya
  yeniden derleme mesajı; izin yoksa hiç istek yok; iki provider'ın
  capability'leri.
- `tests/integration/test_native_clipboard.py` (yeni) → **2**, **uçtan uca**:
  Python orkestrasyonu → `NativeClient` → gerçek Rust yardımcısı
  (`--test-mode`, null klavye) → pano adapter'ı → sahte `wl-paste`/`wl-copy`
  (dosyada tutulan model). Fixture'ın 7 durumunda program çağrıları ve son pano
  birebir aynı. Yeni izin yeni yardımcı açıyor. `strace -e execve`: gerçek
  `/usr/bin/wl-*` **0 kez** çalıştı, sahte programlar 32 kez.
- **Mutasyon 7/7 yakalandı:** geri yüklemenin her hatayı ya da program hatasını
  yutması; içeriğin header'a sızması; yeniden derleme mesajının kalkması;
  sınırlamanın iki girdiye de yazılması; eksik yardımcının Python panosu gibi
  raporlanması; `read`'in içeriği atması. Gerçek pano programlarına düşebilecek
  mutasyonlar **bilerek** denenmedi: başarısız bir mutant kullanıcının panosunu
  okuyup yazabilirdi.
- Sonuçlar: contract **268** (+10), integration **18** (+2, 1 atlandı),
  `test_desktop.py` **583**, `test_models.py` **106**, `--check` 0,
  `compileall` 0.

**Rollback:** Commit'i geri almak yeter. Varsayılan `python` olduğu için
kurulu davranış değişmedi.

#### 4 — Gerçek girdi testi · `tamamlandı` (2026-09-19, kullanıcı başındaydı)

`tests/live/test_input_parity.py` + `tests/live/input_window.py`. Yalnızca
`PCBRIDGE_TEST_INPUT=1` ile çalışır. Bayrak yoksa 8 testin 8'i de atlanıyor
(denendi).

**Nasıl sınırlanıyor.**

- `input_window.py` (sistem `python3`, GTK4) **her monitörü** tam ekran bir
  pencereyle kaplıyor ve aldığı her olayı global koordinatla raporluyor. Yanlış
  yere giden bir fare olayı kullanıcının penceresine değil bu pencereye düşer
  ve nereye düştüğü görünür. Pencere `quit`, stdin EOF ya da 300 sn sonunda
  kendiliğinden kapanıyor.
- Her tuştan önce pencereden gelen son rapor okunuyor: test alanı odakta
  olmalı, yoksa test **tuş göndermeden** başarısız oluyor. Düzenleyen ya da
  silen hiçbir kombinasyon gönderilmiyor. Alanı temizlemek pencerenin kendi
  `clear` komutuyla yapılıyor. Fare sıcak köşelere 5 px'ten fazla yaklaşmıyor.
- İzin geçici bir state dizininde. Kullanıcının kendi izni, `pointer.json`'ı
  ve denetim kaydı kullanılmıyor.
- Kullanıcının panosu yazmadan önce okunuyor ve sonra karşılaştırılıyor; test
  edilen geri yükleme bu. Her zamanki gibi yalnızca ilk temsil kalıyor.

**Ne ölçüyor** (PLAN 5.4 acceptance + Gate 5): iki monitörde 8 hedefte ≤1 px
sapma ve uzun hareketin ışınlanmadığı; move → pencereden gelen konum raporu →
click; Türkçe metnin birebir gelmesi ve panonun geri yüklenmesi; ham yazma;
Shift/Ctrl değiştiricileri; drag ve iki yönde scroll; revoke'ta basılı tuş ve
düğmenin başka istek olmadan bırakılması (<1 sn); süre dolumunda bırakılması
(<1,5 sn); hold zamanlayıcısının 5 sn'de bırakması ve bunun tek kez
raporlanması. `PCBRIDGE_INPUT_REPORT=<yol>` ölçümleri JSON olarak yazıyor.

**Hazırlık (girdi göndermeden yapıldı):** paketlenmiş yardımcı
`scripts/build-native.sh` ile yeniden derlendi (build `2395da38f310`, release,
test-harness değil). `capabilities` yanıtında `clipboard` özelliği var,
`clipboard.write` tek MIME sınırlamasını taşıyor. Servis boştu (cgroup'ta tek
süreç), yeniden başlatıldı, `healthz` 200.

**Kullanıcı onayı (2026-09-19):** ilk koşum için açık onay verildi. İkinci
soruda kullanıcı "bundan sonra sormadan test yap hepsini kabul ediyorum" dedi;
sonraki koşumlar haber verilerek yapıldı.

**Koşum 1 — 0/8, hiç tuş gitmedi.** Pencere tek bir olay bile raporlamadı.
Teşhis (yalnızca hareket, tıklama ve tuş yok): pencerenin stderr'i
`Gtk.EventControllerLegacy`'nin `event` sinyalinde PyGObject'in `GdkEvent`'i
**`None`** olarak verdiğini gösterdi (GTK 4.14). İşleyicideki istisna GTK
tarafından yutuluyordu. Pencere olay nesnesi taşımayan denetleyicilere geçirildi
(`EventControllerMotion`, `GestureDrag`, `EventControllerKey`,
`EventControllerScroll`). Artık pencere stderr'i saklanıyor ve test sonunda
gösteriliyor. Aynı teşhiste native ve Python fare aynı noktalara gönderildi:
ikisi de hedefe tam oturdu (son konum `(2400, 300)`, 80 ve 90 ara olay).

**Koşum 2 — 5/8.** Kalan 3 hata testin kendisindeydi. İmleç zaten metin
alanının ortasındayken aynı noktaya `move` hareket olayı üretmiyor ve test bunu
"ulaşmadı" sayıyordu. Düzeltme: kıpırdamayan imlecin son raporu, ardından hiç
hareket gelmemişse, kanıt sayılıyor. Bu üç testte tuş gönderilmedi.

**Koşum 3 — 7/8. İki gerçek bulgu.**

- **Gizlilik:** pano karşılaştırması başarısız olunca assertion mesajı
  kullanıcının panosunu test çıktısına yazdı. İçerik parolaya benziyordu;
  kullanıcıya söylendi, dosyaya yazılmadı. Test artık panoyu yalnızca tip ve
  bayt sayısıyla anıyor, özetini (hash) bile yazmıyor.
- **Eskiden beri var olan pano hatası, Python yolunda da:** geri yüklemeden
  sonra `wl-copy` tipleri `UTF8_STRING, STRING, TEXT, text/plain;charset=utf-8,
  ...` sırasıyla sunuyor (ölçüldü). `save()` yalnızca `text/*` tiplerinde
  `--no-newline` veriyordu. İkinci yazmada `UTF8_STRING` satır sonuyla
  okunuyor ve geri yükleme kullanıcının panosuna fazladan bir satır sonu
  koyuyordu. Parola yapıştırılırken bu bir formu gönderebilir. Kullanıcının
  panosu bu koşumda bozulmadı: `text/plain;charset=utf-8` 28 bayt, sonda satır
  sonu yok (içerik gösterilmeden ölçüldü). Düzeltme: `3f040d2`. İçerik her
  tipte `--no-newline` ile okunuyor. Fixture'a sekizinci durum eklendi (takma
  adlar başta); eski kural bu durumda iki tarafta da kırmızı.
- Paketleme testi: yardımcı Task 5.3'ten beri `libm.so.6`'ya bağlanıyor. Tek
  sembolü `hypot` (fare yolunun mesafesi). `libc6`'nın parçası. Düzeltme:
  `0c2799c`.
- Aralıklı bir Rust test hatası (`put` başlayamadı) ETXTBSY yarışına bağlandı:
  paralel testte yeni yazılmış betik, başka iş parçacığının fork'unda açık
  kalıyor. Betik çalıştıran testler artık sırayla koşuyor (5/5 koşu temiz).

**Koşum 4 — 8/8** (paketlenmiş yardımcı `0c2799c0b003`, release):

| Ölçüt | Sonuç |
|---|---|
| İki monitörde 8 hedef (kenarlar dahil, sıcak köşeden 5 px uzak) | en büyük sapma **0 px** |
| Uzun hareket ışınlanmıyor | 60 ara hareket olayı |
| Move → pencereden konum raporu → click | press/release tam `(2620, 300)`'de, alan odak aldı |
| Türkçe metin `Merhaba dünya — ğüşıöç İĞÜŞÖÇ «pcbridge» 1+2=3` | **birebir**, 1,78 sn (pano yedeği + yazma + geri yükleme) |
| Kullanıcının panosu | kaydedilen tip (`UTF8_STRING`) yeniden sunuluyor, baytlar aynı |
| Ham yazma `abc 123` | birebir |
| Shift ve Ctrl değiştiricileri | uygulamaya ulaştı |
| Drag | başlangıç ve bitiş ≤1 px, 10 ara hareket |
| Scroll yukarı/aşağı | 2 + 2 olay, işaretler doğru |
| Revoke'ta basılı Shift / sol düğme | **67 ms / 33 ms**'de bırakıldı, başka istek olmadan |
| İzin süresi dolunca basılı Shift | süre bitiminden **95 ms** sonra bırakıldı |
| Hold zamanlayıcısı (`hold_max_seconds = 5`) | **5,0 sn**'de bıraktı; `take_auto_released()` bir kez `["shift"]` |
| Pencere hataları | yok |

### Gate 5 — Input parity · `geçti` (2026-09-19)

| Gerekli kanıt (`PLAN.md` E) | Nerede |
|---|---|
| Golden events | Task 5.1 fixture'ı; Rust klavye (5.2) ve pointer (5.3) aynı sırayı üretiyor |
| Batch safety | Task 5.1 (her eylem öncesi izin, süreçler arası kilit, basılı girdinin bırakılması) |
| Gerçek boş-editör/input ölçümleri | Koşum 4, yukarıdaki tablo |
| Release/revoke | Koşum 4: revoke 67/33 ms, süre dolumu 95 ms, hold 5,0 sn |

Gate 5 geçti. Sıradaki karar 5.4 / 5: `[native] input` varsayılanı.

#### 5 — Varsayılan: `[native] input = "auto"` · `tamamlandı`

**Neden şimdi.** PLAN 5.4 madde 8: "Keyboard ve pointer birlikte parity
sağlamadan `native.input=auto` default yapma." Parity Gate 5 ile sağlandı.
Capture'daki gibi (Task 4.3) seçim `auto`: paketlenmiş yardımcı varsa native,
yoksa Python.

**Ne yapıldı.**

- `config.py` / `config.example.toml`: `input` artık `python | rust | auto`,
  varsayılanı `auto`. Örnek config'teki yorum ölçümleri ve geri almayı
  anlatıyor.
- `runtime.select_input_provider`: `auto` + yardımcı var → `RustInputProvider`.
  Yardımcı yoksa `PythonInputProvider(degraded_reason=...)`. Geri düşüş
  gizlenmiyor: `input.keyboard` ve `input.pointer` `degraded` durumuna geçiyor,
  gerekçesi `limitations`'da. Capture'ın geri düşüşü de aynı biçimde. `rust`
  hiç geri düşmüyor. `python` yardımcıya bakmıyor bile.
- `doctor.sh` (native tanısı): artık iki ayarı da yazıyor. Eksik yardımcının
  ciddiyeti ikisinin en katısına göre: biri `rust` ise `fail`, biri `auto` ise
  `warn`.
- Belgeler: `KULLANIM.md`, `CLAUDE.md` (ölçülmüş gerçekler: native girdi
  ölçümleri, `wl-copy` takma ad sırası, GTK4 legacy denetleyici tuzağı),
  `docs/native/protocol-v1.md`, `backends/rust.py` modül notu.

**Güvenlik kontrolü: varsayılan değişince hiçbir test gerçek aygıt açmıyor.**
Örnek config'le kurulan runtime'lar artık bu makinede native'i seçiyor. Bu
yüzden bütün Python takımları `strace -e openat,ioctl,write -P /dev/uinput`
altında koşuldu. Sonuç: 4 `openat`, **0 `ioctl`, 0 `write`**. O 4 açılış
`InputBackend.available()`'ın eskiden beri yaptığı izin yoklaması: `O_WRONLY`
açıp hemen kapatıyor, aygıt oluşturmuyor. Her sistem çağrısı listelendi, yalnızca
aç/kapat var. Varsayılan config'le runtime kuran testler hiçbir girdi aracı
çağırmıyor; çağıranlar sahte sağlayıcı kullanıyor.

**Testler.** Varsayılanı sabitleyen iki test bilerek güncellendi. Sözleşme
değişti, test gizlenmedi: "sevkiyat varsayılanı `auto`"; "`python` seçimi
native'e ve yardımcı aramasına hiç dokunmuyor". Yeni testler: `auto`'nun
yardımcıyı seçmesi ve eksikse görünür şekilde düşmesi, bilinmeyen değerin
yüklemede reddi, tanıda `input` ayarının da sayılması. `test_native_client.py`
örnek config'te `input = "python"` satırını değiştiriyordu. Satır artık
olmadığı için değiştirme sessizce hiçbir şey yapmadı ve test kafa karıştırıcı
bir eşitsizlikle düştü. Artık değiştirilecek her satırın var olduğu
doğrulanıyor. Sonuçlar: contract **271** (+3), integration **18** (1 atlandı),
`test_desktop.py` **583**, `test_models.py` **106**, `test_test_safety.py` OK,
`--check` 0.

**Bilinen fark.** Native yolda klavye ve fare ayrı ayrı 1,2 sn bekliyor, yani
ikisi birden ilk açılırken 2,4 sn. Python ikisini tek beklemede açıyordu
(ölçülmüş 1,41 sn). İzin başına ilk eylemde ~1 sn fazladan bekleme demek.
İleride birleşik bir `input.ensure` IPC'si ile kapatılabilir; planlanmadı.

**Yayılım ve geri alma.** Değişiklik yalnızca yeni başlayan süreçlerde geçerli.
Servis ve stdio istemcileri yeniden başlatılınca native girdiye geçer (Kullanıcıyı
bekleyenler #5 ile aynı yol). `system_capabilities` → `input.keyboard` backend
`linux.uinput.native`. Geri almak için `[native]` altına `input = "python"`
yazılır ve aynı yeniden başlatmalar yapılır. Önce `desktop_lock`, böylece basılı
girdi bırakılır.

**Yayılım, yapıldı (2026-09-19).** Kullanıcının `config.toml`'unda
`[native]` bölümü yok, yani varsayılanlar geçerli. Servis boştu (cgroup'ta tek
süreç), yeniden başlatıldı, `healthz` 200, logda hata yok. HTTP + statik token
üzerinden `system_capabilities` çağrıldı; token ekrana basılmadı ve hiç girdi
gönderilmedi: `input.keyboard` ve `input.pointer` → `linux.uinput.native`,
`clipboard.read` ve `clipboard.write` → `linux.wl-clipboard.native`
(`write`'ta tek MIME sınırlaması), `capture.monitor` → `linux.mutter.pipewire`.
stdio istemcileri (Claude Code, Claude Desktop, Codex) uygulama kapatılıp
açılınca geçer.

**Rollback:** Commit'i geri almak yeter. Kod, `rust` ve `python` seçimlerini
aynen koruyor.

### Task 6.1 — Element target bütünlüğü · `tamamlandı` (2026-09-19)

**Sorun.** `ui_click`/`ui_set_text` hedefi son dökümdeki indeks yolu, rol ve adla
buluyordu. Yol tutmazsa aynı rol+adı uygulamanın tamamında arıyor ve **ilk
eşleşmeye** basıyordu. Uygulama bulunamazsa **odaktaki uygulamaya** düşüyordu.
pcbridge'in GTK4 test penceresinde (`tests/live/a11y_window.py`) üç "Kapat"
düğmesi ölçüldü. Üçüncüsü başlık çubuğunun pencereyi kapatan düğmesi. Yani A
grubunun "Kapat"ı silinince eski kod B'ninkine basardı. İkisi de gidince
pencerenin kendi kapatma düğmesine basardı. Kısa kimlikler de 4 onaltılık
karakterdi (65 536 değer) ve aynı dökümde çakışan kimlik sessizce eziliyordu.
400 düğümlük bir dökümde en az bir çakışmanın olasılığı ~%70. Çakışmada ilk
düğümün kimliği ikinciye tıklatıyordu.

**Ölçüm: AT-SPI'ın kendi kimliği var.** Her düğümün bir D-Bus nesne yolu var
(`node.path`), her uygulamanın da tekil bir veriyolu adı (`node.app.bus_name`).
GTK4 test penceresinde araya düğme eklenince 33 nesnenin 20'sinin indeks yolu
kaydı ama 33'ünün de nesne yolu aynı kaldı. Başlık değişince pencerenin yolu
değişmedi. Yeniden yaratılan düğme yeni yol aldı. gnome-shell ve Chromium
`/org/a11y/atspi/accessible/<sayı>` kullanıyor. Chromium yeniden başlayınca
1'den saydığı için veriyolu adı da şart.

**Ne yapıldı.**

- `atspi_helper.py`:
  - Döküm artık uygulamanın veriyolu adını ve pid'ini, kapsamı
    (`window`/`app`), pencerenin yolunu ve her düğümün nesne yolunu döndürüyor.
  - `_resolve` üç şart arıyor:
    - Aynı uygulama (veriyolu adı). Uygulama kapanmışsa başka uygulamaya
      düşülmüyor.
    - Aynı nesne. İndeks yolu tutmazsa aynı hedefin içinde nesne yolu
      aranıyor. Rol+ad araması **kaldırıldı**.
    - Aynı anlam: rol ve ad değişmemiş olmalı.
  - Hatalar kararlı kodla dönüyor: `ELEMENT_STALE`, `TARGET_MISMATCH`,
    `ELEMENT_AMBIGUOUS`, `ACTION_UNSUPPORTED`.
  - Kısmi ad iki farklı uygulamaya uyarsa döküm reddediliyor. Aynı adlı iki
    süreç varsa odaktaki okunuyor ve dökümde bu yazıyor.
  - Komutlar `_fail` ile istisna fırlatıyor, cevabı `handle()` kuruyor.
    Böylece aynı kod ayrı süreç açmadan test edilebiliyor.
- `uitree.py`:
  - `Dump`'a backend, snapshot, uygulama ve pencere kimliği; `Node`'a nesne
    yolu ve tam özet eklendi.
  - Kısa kimlik en az 4 karakter. Çakışan iki kimlik ayrışana kadar uzuyor.
    Çözüm tam özetin öneki üzerinden; birden fazla öğeye uyan önek
    reddediliyor.
  - `UiTreeError` kararlı kod taşıyor. Yardımcının zaman aşımı
    `EXECUTION_UNKNOWN` ve tekrarlanmıyor.
- `backends/python.py`: yardımcının kodu ortak hata sözleşmesine taşınıyor.
  Yeni döküm gerektiren hatalar `retryable` ve "ui_dump ile yenileyin" diyor.
- `tools.py`:
  - `ui_click`/`ui_set_text` açıklamaları reddi anlatıyor.
  - Taşınmış öğe notu: "ayni oge yeni yerinde bulundu".
  - Denetim kaydı `snapshot` alanı taşıyor.
- Zaten doğru olan iki davranış testle sabitlendi:
  - `focused_window()` ve `windows()` son dökümü değiştirmiyor. `windows()`
    artık veriyolu adı, pid ve pencere yolu da taşıyor (Task 6.4 için).
  - Eylemi olmayan öğede koordinata düşülmüyor.

**Testler.**

- `tests/fixtures/native/accessibility_cases.json` (yeni; Task 6.2'de Rust da
  kullanacak): 16 uygulama, 14 masaüstü, 6 döküm vakası, 19 eylem vakası ve
  kimlik vakaları.
- `tests/contracts/test_accessibility_contract.py` (yeni, 15 test):
  - Gerçek yardımcı kodu sahte bir `Atspi` üzerinde çalışıyor.
  - Aynı vakalar bir kez de `UiTree` ve Python sağlayıcı üzerinden koşuyor.
- Mutasyon denemesi: 12 bozulmanın 12'si yakalandı. Denenen bozulmalar:
  - eski rol+ad çözümü;
  - odaktaki uygulamaya düşme;
  - ad/rol kontrolünü kaldırma;
  - iki öğeye aynı yol verildiğinde ilkini seçme;
  - kısmi adda ilk uygulamayı seçme;
  - aynı adlı süreçlerde odağı yok sayma;
  - kimlikleri 4 karaktere kırpma;
  - önek çözümünü kaldırma;
  - sağlayıcının yardımcı kodunu yok sayması;
  - metin uzunluğunu karakter sayma;
  - pencere kapsamını yok sayma;
  - zaman aşımı kodunu düşürme.

  Dosyalar SHA-256 ile doğrulanarak geri yüklendi. Pencere kapsamı ilk turda
  yakalanmadı. Öğenin başka pencereye taşındığı vaka eklendi, sonra yakalandı.
- `tests/live/test_accessibility_parity.py` (yeni, 7 test): okuma
  `PCBRIDGE_TEST_ATSPI=1`, eylemler ayrıca `PCBRIDGE_TEST_INPUT=1` ister. Test
  gerçek AT-SPI üzerinde, kendi GTK4 penceresinde çalışıyor ve uinput
  açmıyor. **7/7 geçti:**
  - Süreler (yardımcı sürecin açılışı dahil): döküm 77–110 ms (9 ölçüm),
    tıklama 50 ms, metin 46 ms.
  - Taşınan düğmeye kimliğiyle basıldı (`resolved_by: moved`).
  - Yeniden yaratılan, silinen ve uygulaması kapanan hedefler `ELEMENT_STALE`
    döndü; pencere hiçbir tıklama görmedi.
  - Türkçe metin birebir yazıldı.
  - Parola alanı `PASSWORD_FIELD` döndü; pencereye hiçbir karakter gitmedi.
- `test_desktop.py` `PCBRIDGE_TEST_ATSPI=1` ile 587 geçti (gerçek okuma bölümü
  dahil), bayraksız 583 geçti.
- Diğer takımlar: contract 286 (+15), integration 18 (1 atlandı), models 106,
  safety OK, `--check` 0.

**Uyumluluk.** `ui_dump → #id → ui_click/ui_set_text` akışı aynı. İki şey
değişti:
- Yeniden çizilmiş ya da adı değişmiş öğeye artık basılmıyor, yeni döküm
  isteniyor.
- Çakışma varsa kimlik 5 karakter ya da daha uzun oluyor.

`PLAN.md` Task 6.1'e not düşüldü: 4. ve 5. maddeler rol/ad araması yerine nesne
kimliğiyle, daha sıkı uygulandı.

**Rollback.** Commit'i geri almak yeter. Yardımcı ve `uitree` aynı commit'te
değişti, birlikte geri dönerler.

**Sıradaki:** Task 6.2 — Rust AT-SPI read/window/focus provider
(`native.accessibility = "python"` varsayılanıyla).

### Task 6.2 — Rust AT-SPI read/window/focus provider · `tamamlandı` (2026-09-19)

**Önce ölçüm: ham D-Bus, libatspi'nin söylediğini söylemiyor.** GTK4 test
penceresinde aynı düğümler iki yoldan okundu:

- **Rol adı:** GTK4'ün `GetRoleName`'i kendi kelimelerini veriyor. Pencere
  çerçevesine "application", panele "generic"/"group", düğmeye "button" diyor.
  libatspi ise `GetRole` numarasını kendi tablosuyla çeviriyor: "frame",
  "panel", "push button".
- **Eylem adı:** `Action.GetActions` yerelleştirilmiş adı veriyor ("Click").
  libatspi'nin adı `GetName(i)`: "click".
- **Aynı olanlar:** rol numarası ve durum bitleri iki yolda da aynı.

Bütün süzgeçler ve kayıtlı roller libatspi'nin adlarıyla karşılaştırıyor. Bu
yüzden Rust okuyucu numarayı libatspi'nin 131 girdilik tablosuyla adlandırıyor
(`atspi_role_get_name`, at-spi2-core 2.52). Eylem adlarını `GetName` ile alıyor.
`GetRoleName` yalnızca tablonun dışındaki roller için soruluyor; libatspi de
böyle yapıyor. Veriyolunun adresi `org.a11y.Bus.GetAddress`'ten, pid
veriyolunun `GetConnectionUnixProcessID`'sinden geliyor.

**Ne yapıldı.**

- Rust, `platform/linux/accessibility.rs`:
  - Yürüyüş Python yardımcısının `_walk`/`_dedup`/`_find_app`/`windows` koduyla
    adım adım aynı: derinlik önce, en fazla `max_nodes * 25` ziyaret, derinlik
    100, GAction ve kapsayıcı süzgeci.
  - Toplam süre sınırı var. Süre dolarsa `TIMEOUT` döner, yarım liste dönmez.
- `accessibility/bus.rs` (zbus):
  - Çağrı başına 2 sn zaman aşımı.
  - Bir düğümün çocukları birlikte okunuyor (bir kerede en fazla 32 düğüm).
    Bunun için yeni bağımlılık eklenmedi; 20 satırlık bir `join_all` yazıldı.
  - `GetChildren` yanıt vermezse çocuklar tek tek okunuyor.
- `accessibility/fixture.rs`: fixture masaüstünü okuyan ağaç. Rust testleri ve
  test kipindeki yardımcı bunu kullanıyor.
- `dispatch.rs`:
  - Yeni yöntemler `accessibility.dump`, `accessibility.windows` ve
    `accessibility.focused`. Üçü de izne bağlı; bilinmeyen alan reddediliyor.
  - İzin okuma bittikten sonra yeniden doğrulanıyor: okuma sırasında izin geri
    alınırsa ağaç döndürülmüyor.
  - `capabilities`'e `accessibility.read` ve `window.list` eklendi.
    Yalnızca `org.a11y.Bus` adının sahibine bakılıyor; hiçbir uygulama
    okunmuyor.
  - Test kipi yalnızca fixture okuyor (`PCBRIDGE_TEST_A11Y_FIXTURE`,
    `test.accessibility_desktop`).
- Python `RustAccessibilityProvider`:
  - Döküm, pencere listesi ve odaktaki pencere izin altında native'den okunuyor.
  - İzin yokken (`screen_info`, `desktop_unlock`'tan önce) Python yardımcısına
    düşüyor, çünkü native yardımcı izin olmadan yaşamıyor.
  - Eylemler 6.3'e kadar Python'da. Kimlik (veriyolu adı + nesne yolu) iki
    okuyucuda aynı anlamı taşıyor.
  - Hata kodları Python yoluyla aynı öneriyi taşıyor. Eski bir yardımcıda
    derleme ipucu veriliyor.
- `uitree`: `dump_from_response`/`windows_from_response` paylaşıldı. Kısa
  kimlik, snapshot ve son döküm kaydı iki okuyucuda da tek yerde üretiliyor.
- Yeni ayar `[native] accessibility`:
  - Gate 6'ya kadar varsayılan `python`; `auto` geri düşüşü görünür
    (`degraded`), `rust` zorunlu.
  - `config.py` bunu yükleme sırasında doğruluyor. `config.example.toml`'da
    yorumuyla var.
  - `runtime.select_accessibility_provider` seçimi yapıyor.
- `release_resources`: izin kapanınca erişilebilirlik yardımcısı da girdi ve
  yakalamayla birlikte kapanıyor.
- Python yardımcısında eşitlik düzeltmeleri:
  - Bulunamayan uygulama ve odakta pencere olmaması artık iki okuyucuda da
    `TARGET_MISMATCH`.
  - Zaman aşımına uğrayan okuma `TIMEOUT`. Eylem zaman aşımı
    `EXECUTION_UNKNOWN` kalıyor.
- Belgeler: `docs/native/protocol-v1.md` (yöntemler, test kipi, seçim;
  bayatlamış "input varsayılanı python" cümlesi de düzeltildi), `CLAUDE.md`
  (ham D-Bus farkı ve ölçümler), `KULLANIM.md`, `PLAN.md` notu.

**Testler.**

- Fixture büyüdü: 19 uygulama, 17 masaüstü, 12 döküm vakası, 19 eylem vakası
  ve 2 pencere vakası. Yeni vakalar:
  - Electron benzeri boş ağaç: hata değil, uygulama ve pencere adıyla boş
    liste.
  - Bilinmeyen uygulama.
  - Etiketli döküm.
  - `max_nodes` kırpması.
  - GTK4 sarmalayıcısı.
  - Ziyaret tavanının 25. ziyareti.
- Rust `tests/accessibility_read.rs` (9 test): fixture'daki döküm ve pencere
  vakaları, pencere okumasının ağaç gezmemesi, derinlik ve ziyaret sınırları,
  süre sınırı, rol tablosu, durum bitleri, GAction süzgeci.
- Rust mutasyon denemesi: ilk turda 12 bozulmanın 10'u yakalandı. GTK4
  sarmalayıcı eleme ve ziyaret tavanındaki bir kaymanın fixture'da vakası
  yoktu. İkisi eklendi, sonra 12/12. Dosya SHA-256 ile doğrulanarak geri
  yüklendi.
- Python `tests/contracts/test_native_accessibility.py` (18 test) şunları
  kapsıyor:
  - istek parametreleri;
  - izin altında native okuma ve GI yardımcısının hiç çağrılmaması;
  - izinsiz geri düşüş;
  - izin yokken dökümün yardımcı başlamadan reddi;
  - eylemin kimlikle Python'a gitmesi;
  - hata eşlemesi ve eski yardımcıya derleme ipucu;
  - yeni izinde yeni yardımcı;
  - seçim kuralları ve yapılandırma reddi.

  `test_accessibility_contract.py`'ye pencere vakaları eklendi.
- `tests/integration/test_native_accessibility.py` (yeni, 3 test): test
  kipindeki gerçek yardımcı + fixture. Her döküm ve pencere vakasında iki
  okuyucu **aynı sonucu** veriyor:
  - kimlikler, derinlik, durumlar;
  - hata kodları;
  - hata mesajları, birebir.

  İzin geri alınınca ağaç dönmüyor.
- Canlı, yalnızca okuma, 5/5 (kullanıcı onayı geri almadan önce koşuldu;
  testin kendi penceresi ve gnome-shell okundu; izin geçici bir durum
  dizinine yazıldı, ekran paylaşılmadı). İki okuyucu test penceresini
  düğüm düğüm aynı döktü: 3 tur, bir de ağaç değiştikten sonra etiketli döküm.
  Aynı pencereyi ikisi de listeledi. Süreler:

  | Ölçüm | Python | Native |
  |---|---|---|
  | Döküm | 100–112 ms | **14–18 ms** |
  | Pencere listesi | 102 ms | 6,6 ms |
  | gnome-shell (13 düğüm, büyük ağaç) | 1354 ms | 827 ms |

- Takımlar:
  - Python: contract 305 (+19), integration 21 (+3, 1 atlandı), `test_desktop.py`
    583, models 106, safety OK, `--check` 0; canlı testler bayraksız 32/32
    atlanıyor.
  - Rust: `cargo fmt`, iki türde `clippy -D warnings` ve iki türde
    `cargo test --no-fail-fast` temiz.

**Kabul ölçütleri (PLAN 6.2).**

- İki okuyucu aynı fixture takımını geçiyor: ✓ (Rust'ta ve test kipi
  üzerinden uçtan uca).
- Electron'un boş ağacı doğru hedef adıyla dönüyor: ✓ (fixture vakası).
- GUI thread ya da GTK başlatma yok: ✓ (zbus; izin varken GI yardımcısı hiç
  çağrılmıyor, sözleşme testi bunu sınıyor).

Planın 6. maddesi (referansları snapshot bazında saklamak) 6.3'e taşındı.
Okumalar içeri referans taşımıyor; referansı kabul edecek ilk istek eylem.
Gerekçe `PLAN.md` notunda.

**Kurulmadı.** Paketlenmiş yardımcı (`pcbridge/_native`) hâlâ Task 5.4
derlemesi; yeni yöntemler yalnızca `rust/target`'ta. Varsayılan `python`
olduğu için çalışan servis için hiçbir şey değişmedi. `scripts/build-native.sh`
ile kurulum, varsayılan değiştiğinde (6.3) yapılacak.

**Rollback.** Commit'i geri almak yeter. Varsayılan zaten `python`.

**Sıradaki:** Task 6.3 — Rust erişilebilirlik eylemleri ve parity. İşler:
- Eylem anında kimliği doğrulamak ve `do_action` sonucunu denetlemek.
- EditableText ile Türkçe metni yazıp geri okuyarak doğrulamak.
- Zaman aşımından sonra eylemi tekrarlamamak.
- Başarılı olursa ayrı commit'le `accessibility = "auto"` yapmak.

Canlı eylem testleri test penceresinde yapılacak ve önce kullanıcıya
sorulacak.

### Task 6.3 — Rust accessibility actions ve parity · `tamamlandı` (2026-09-19)

**Önce ölçüm: uygulamanın cevabı bir şey kanıtlamıyor.** GTK4 test
penceresine ham D-Bus ile gidildi. Pencereye iki şey eklendi: 5 karakter tutan
bir "Kod" alanı ve "Tamam"ı devre dışı bırakan `disable-ok` komutu.

| Çağrı | Cevap | Gerçekte olan |
|---|---|---|
| Devre dışı düğmede `DoAction(0)` | `false` | hiçbir şey tıklanmadı |
| 5 karakterlik alana `SetTextContents("123456789")` | `true` | alan `12345` tutuyor |
| `SetTextContents` Türkçe metin (23 karakter) | `true`, 0,5 ms | metin birebir |
| `InsertText(0, "ğüş", 2)` | `true` | üç harfin üçü de yazıldı: uzunluk kullanılmıyor |
| `GetText(0, -1)` | `""` | bitiş -1 "sona kadar" demek değil |

Tasarımı bunlar belirledi:
- `false` bir hatadır (`ACTION_UNSUPPORTED`). Önceden "tıklandı" diye
  bildiriliyordu.
- Yazılan metin `CharacterCount` + `GetText(0, sayı)` ile geri okunup
  bütünüyle karşılaştırılıyor. Tutmazsa yeni `TEXT_MISMATCH` dönüyor; mesajda
  yalnızca sayılar var, metnin kendisi yok.
- Native yol metni `SetTextContents` ile yazıyor, yani uzunluk parametresi
  yok. Python yardımcısının bayt uzunluğu (2026-08-02) metin kutusu içindi;
  girdi alanı uzunluğu hiç kullanmıyor.

**Ne yapıldı.**

- Rust `accessibility/action.rs` (yeni) — Python yardımcısının `_resolve`,
  `cmd_act`, `cmd_settext`'i:
  - Kimlik: önce aynı uygulama (veriyolu adı), sonra aynı nesne (önce indeks
    yolu, sonra aynı hedefte veriyolu adı + nesne yolu ile arama), sonra aynı
    anlam (rol ve ad).
  - İstenen eylem adıyla seçiliyor, sırayla değil.
  - `DoAction` ve `SetTextContents` birer kez gönderiliyor ve cevapları
    denetleniyor. Metin en fazla 300 ms bekleyerek geri okunuyor.
  - Hedef 8 sn içinde bulunamazsa `TIMEOUT` dönüyor; o ana kadar hiçbir şey
    gönderilmemiş oluyor.
  - Gönderilip cevaplanmayan çağrı `EXECUTION_UNKNOWN` ve tekrarlanmıyor.
    Eylem çağrıları 5 sn, okumalar 2 sn bekliyor (`bus.rs`, çağrı başına
    zamanlayıcı).
- **Döküm kaydı** (6.2'den taşınan madde):
  - Yardımcı son 8 dökümünü kaydediyor ve her birini kendi `snapshot`
    kimliğiyle döndürüyor.
  - Eylem yalnızca snapshot ve düğümün nesne yolunu taşıyor. Kimliğin geri
    kalanı yardımcının kendi kaydından geliyor, istekten değil.
  - Başka bir yardımcının dökümü `ELEMENT_STALE` ile reddediliyor: başka bir
    süreç ya da yeni bir izinle yeniden başlamış bu yardımcı.
  - D-Bus'ta veriyolu adı + nesne yolu tek bir nesnedir, bu yüzden aramada
    aynı nesneyle ikinci kez karşılaşmak belirsizlik sayılmıyor. Aynı yol aynı
    dökümde iki ayrı veriyolunda görünürse `ELEMENT_AMBIGUOUS` dönüyor.
- `dispatch.rs`:
  - Yeni yöntemler `accessibility.act` ve `accessibility.set_text`.
  - Metin binary payload'da taşınıyor, JSON başlığında değil.
  - İzin, hedef bulunduktan sonra ve çağrıdan hemen önce bir kez daha
    doğrulanıyor.
  - Özellik ve yetenek olarak `accessibility.action` eklendi.
  - Test kipinde `test.accessibility_performed` var.
- Python:
  - `RustAccessibilityProvider._act` tıklamayı ve metni yardımcıya gönderiyor.
    Kısa kimliği çözmek ve parola alanı kuralı Python'da kaldı, yardımcıya hiç
    sorulmuyor.
  - İsteğin kendi zaman aşımı ya da gönderdikten sonra çöken yardımcı
    `EXECUTION_UNKNOWN` sayılıyor. İstek hiç gitmediyse hata olduğu gibi
    iletiliyor.
  - `uitree.dump_from_response` yardımcının snapshot'ını kullanıyor.
  - Yeni hata kodu `TEXT_MISMATCH` eklendi (`errors.py`, `PLAN.md` taksonomi
    düzeltmesi).
- Python yardımcısı da aynı kurallara geçti, iki yol aynı fixture'da aynı
  cevabı versin diye: `false` hata sayılıyor, metin geri okunuyor.
  - Canlı testte yakalandı: PyGObject'te `get_text_iface()` düğümün kendisi.
    Bu yüzden `ti.get_text(0, n)` aslında `Atspi.Accessible.get_text()`
    çağrısı oluyor ve TypeError veriyor. Doğru çağrı
    `Atspi.Text.get_text(düğüm, 0, n)`.
  - Sahte AT-SPI bunu gizlemişti. Artık GI gibi davranıyor; eski çağrıyla 3
    test kırmızıya dönüyor.
- Fixture:
  - 21 uygulama, 19 masaüstü, 13 döküm ve 24 eylem vakası.
  - Yeni "form" uygulaması: 5 karakterlik alan, Text arayüzü olmayan alan,
    salt okunur alan ve devre dışı bırakılabilen düğme.
  - Yeni vaka: istenen eylemin sırayla değil adıyla seçilmesi.
- Belgeler: `docs/native/protocol-v1.md`, `PLAN.md` notu, `CLAUDE.md`
  (ölçülen gerçekler), `KULLANIM.md`, `config.example.toml` yorumu.

**Testler.**

- Rust `tests/accessibility_actions.rs` (10 test):
  - Fixture'daki her eylem vakası.
  - Aynı nesnenin iki kez görülmesi ve aynı yolun iki veriyolunda olması.
  - Taşınan öğenin yolu değil veriyolu + yol ile bulunması.
  - Cevapsız eylemin bir kez gönderilmesi.
  - Süre sınırının hiçbir şey göndermemesi.
  - Geç uygulanan metnin doğrulanması.
  - Kısa kalan metnin yalnızca sayılarla bildirilmesi.
  - Python `repr` tırnaklaması.
- `dispatch.rs` birim testleri: döküm kaydı (başka yardımcının kimliği, en
  fazla 8 döküm).
- Python sözleşme testleri:
  - `test_native_accessibility.py` (26 test, +8): tıklama isteği yalnızca
    snapshot + yol taşıyor; metin binary'de; parola alanı ve eylemsiz öğe
    yardımcıya gitmiyor; cevapsız eylem `EXECUTION_UNKNOWN` ve bir kez
    gönderiliyor; hiç gitmeyen istek "bilinmiyor" sayılmıyor; kodlar
    korunuyor; eski yardımcıya derleme ipucu veriliyor.
  - `test_accessibility_contract.py`: yeni vakalar Python yardımcısında.
- `tests/integration/test_native_accessibility.py` (6 test, +3): her eylem
  vakası iki sağlayıcıdan geçiyor. Kod, mesaj, kategori, sonuç alanları, sahte
  masaüstünde olanlar ve metinler **birebir aynı**. Başka yardımcının dökümü
  reddediliyor, yardımcının aynı düğümü listeleyen kendi dökümü olsa bile.
  Yeni izin eski dökümleri unutuyor.
- Mutasyon denemesi: 18 anlamlı bozulmanın 18'i yakalandı. Bunlar 13 Rust,
  5 Python bozulması; dosyalar SHA-256 ile doğrulanarak geri yüklendi.
  Sınanamayan tek kontrol, izni çağrıdan hemen önce yeniden doğrulamak: o
  aralığa deterministik olarak girilemiyor.
- Canlı, 20/20 (release yardımcı `PCBRIDGE_NATIVE_BIN` ile; izin geçici bir
  durum dizininde; yalnızca testin kendi penceresi; klavye/fare yok):
  - Bütün eylem testleri iki sağlayıcıyla koştu: taşınan düğme, yeniden
    yaratılan düğme, kaldırılan düğme, kapanan uygulama, Türkçe metin + parola
    alanı, devre dışı düğme, 5 karakterlik alan.
  - Native yardımcı eylem sırasında `/dev/uinput` açmıyor (`/proc/<pid>/fd`).
  - İlk koşuda Python yolunda 2 test düştü: yukarıdaki PyGObject tuzağı.
    Düzeltildi, sonra 20/20.

  | Ölçüm | Python | Native |
  |---|---|---|
  | Tıklama (taşınan düğme) | 52 ms | **13 ms** |
  | Metin yazma | 50 ms | **5 ms** |
  | Döküm | 84–137 ms | 17–32 ms |
  | Pencere listesi | 102 ms | 5,6 ms |
  | gnome-shell dökümü | 2231 ms | 1653 ms |

- Takımlar: Python contract 313, integration 24 (1 atlandı), `test_desktop.py`
  583, models 106, safety OK, `--check` 0. Canlı testler bayraksız 42/42
  atlanıyor. Rust `cargo fmt`, iki türde `clippy -D warnings` temiz; `cargo
  test` varsayılan 150, test kipi 171, hepsi geçti.

**Kabul ölçütleri (PLAN 6.3).**
- Native eylem uinput gerektirmiyor: ✓ (yardımcının açık dosyalarıyla
  doğrulandı).
- İmleç hareketi zorunlu değil: ✓ (eylem AT-SPI ile gidiyor, fare aygıtı yok).
- Yanlış ya da bayat hedef reddediliyor: ✓ (fixture ve canlı).
- Metin kırpılmıyor: ✓. Türkçe metin birebir geri okundu. Kırpan bir alan
  artık sessizce geçmiyor.

Eylemler `8431bd1`'de. Varsayılan orada hâlâ `python`; Python yolunda iki
fark var: `false` dönen eylem artık hata, metin geri okunuyor.

**Varsayılan `auto` (planın 7. maddesi, ayrı commit).**
- `NativeSpec.accessibility` ve yükleme varsayılanı `auto`.
  `config.example.toml` bunu yorumuyla anlatıyor: eski bir yardımcı
  `ui_dump`'ta hata verir, geri almak için `python`.
- `doctor.sh` 8. bölüm (`pcbridge.native.diagnostics`):
  - `[native] accessibility` seçimini gösteriyor. Yardımcıya ne kadar
    ihtiyaç olduğunu capture ve input ile birlikte o belirliyor.
  - `auto`/`rust` iken `accessibility.read` ya da `accessibility.action`
    sunmayan eski bir yardımcıyı uyarıyor (`rust`'ta hata). Bu önemli, çünkü
    `auto` her yardımcıyı alır ve eskisi `ui_dump`'ı bozardı.
  - Bir de küçük düzeltme: sebebi olmayan `degraded` satırı artık yardımcının
    bildirdiği sınırlamayı gösteriyor (`window.list`). Önceden "neden
    bildirilmedi" yazıyordu.
- Yardımcı `scripts/build-native.sh` ile kuruldu. Build `0c2799c0b003`
  (Task 5.4) → `8431bd1b707a`. Tanı: `accessibility.read` ve
  `accessibility.action` `supported`.
- Kurulu yardımcıyla canlı test 20/20. Tıklama native 11 ms, Python 53 ms.
  Metin 8 ms'ye karşı 51 ms. Döküm 16–35 ms'ye karşı 83–142 ms.
- **Yayılım, yapıldı.** Kullanıcının `config.toml`'unda `[native]` bölümü yok,
  yani varsayılan geçerli.
  - Servis boştaydı (cgroup'ta tek süreç) ve yeniden başlatıldı. `healthz`
    200, logda hata yok.
  - HTTP + statik token ile `system_capabilities` çağrıldı; token ekrana
    basılmadı, hiçbir girdi gönderilmedi. Önce `linux.atspi`, sonra
    `accessibility.read`, `accessibility.action` ve `window.list` için
    `linux.atspi.native`.
  - stdio istemcileri uygulama kapatılıp açılınca geçer (#7).
- Takımlar: contract 317, integration 24 (1 atlandı), `test_desktop.py` 583,
  models 106, safety OK, `--check` 0.

**Rollback.** `[native]` altına `accessibility = "python"` yazıp servisi ve
stdio istemcilerini yeniden başlatmak yeter. Yardımcıyı geri almak gerekmez:
yeni derleme capture ve input için aynı protokolü konuşuyor.

**Sıradaki:** Task 6.4 — uygulama/pencere işlemlerini (`window_focus`, launch,
pencere listesi) yetenek arkasına almak, sonra **Gate 6**. (Kullanıcı 6.4'ü
"devam et" ile onayladı.)

### Task 6.4 — App/window orchestration'ı capability arkasına al · `tamamlandı` (2026-09-19)

Adım 2 hızlı yolu (eklenti) zaten kurmuştu, yani bu task "çalışan yolu
yetenek arkasına al ve doğrula" işiydi. Ama doğrularken üç şey ortaya çıktı.

**Önce ölçüm.**

| Ne | Sonuç |
|---|---|
| Açık pencerelerin AT-SPI uygulama adı ↔ `.desktop` girdisi | `claude-desktop` ikili adına denk; `gnome-text-editor` (fixture) de öyle; `gnome-terminal-server` ikili adı + ek (audit.log 2026-08-23). `find()` ikili adına bakmıyor |
| `entries()` | 266 girdi, ilk okuma 58,6 ms, sonra 18 ms |
| Bu makinedeki adlar | "Desktop" üç girdiye uyuyor (GitHub Desktop, OpenCode, Pcbridge Desktop); "Firefox" kurulu değil; "PcBridge Desktop" kurulu bir uygulama (2026-09-02 ölçümünün hedefi) |
| GNOME arama sağlayıcıları | Claude sohbetleri, dosyalar (Nautilus), uçbirim sekmeleri, ayarlar, hesap makinesi, kişiler… Varsayılan tarayıcı `google-chrome` |
| Arka plandaki bir süreçten `gtk-launch` | pencere 0,56 sn'de listede, 0,68 sn'de odakta, tuş yok |
| `gtk-launch`'ın başlattığı uygulamanın cgroup'u | **çağıranınki** (ölçümde Claude'un kapsamı; serviste `pcbridge.service` olurdu) |
| `systemd-run --user --scope gtk-launch …` | `app-pcbridge-…scope`, +60 ms (125 ms'ye karşı 64 ms) |

Buradan çıkan üç sorun:
1. **Yanlış arama sonucu başarı sayılıyordu.** Doğrulama "hedef adı
   başlıkta ya da uygulama adında geçiyor mu" idi. GNOME araması web
   aramasına düşünce tarayıcıda başlığı tam da aranan metin olan bir sekme
   açılıyor, yani doğrulama geçiyordu. Kullanıcının 2026-09-02'de gördüğü
   belirti buydu.
2. **Aramaya her ad yazılıyordu.** Pencere başlığı ya da uygulama olmayan bir
   ad Claude sohbeti, dosya ya da web araması açabilir.
3. **`gtk-launch` uygulamayı çağıranın cgroup'unda bırakıyordu.** Servisten
   açılan uygulamayı servisin restart'ı öldürürdü. Bu `window_focus`'un
   vaadinin tam tersi ve toplu `launch` eyleminde ve `computer_task(app=…)`'ta
   zaten vardı.

**Ne yapıldı.**

- `apps.py`: dört iç işlem ve onları birleştiren `bring_to_front`.
  - `resolve_application`: `find()`'in turları, ama belirsizlik açık. Aynı
    turda birden fazla uygulamaya uyan ad `rivals` taşıyor. Aynı görünen adlı
    iki girdi (`google-chrome`, `com.google.Chrome`) tek uygulama sayılıyor.
  - `activate_window`: eklenti (değişmedi).
  - `observe_focus`: odak okunamazsa `BACKEND_UNAVAILABLE`, hiçbir şey
    gönderilmeden. Eskiden arama önce yapılıyor, odak sonra okunuyordu.
  - `launch_application`: `gtk-launch`, sonra pencere görülene kadar izleme
    (0,1 sn aralık, en çok 10 sn). Görülmezse `EXECUTION_UNKNOWN`; çıkış kodu
    başarı sayılmıyor. Uygulama `systemd-run --user --scope` ile kendi
    `app-pcbridge-<kimlik>-<rastgele>.scope`'una giriyor; `systemd-run`
    yoksa eskisi gibi.
  - Sıra: eklenti → hedef zaten öndeyse hiçbir tuş yok → kapalıysa tuşsuz
    başlatma → açık ama eklentinin öne alamadığı pencere için GNOME araması.
  - Aramaya yalnızca kurulu bir uygulamanın adı yazılıyor. Uygulama olmayan
    ad `TARGET_MISMATCH`, belirsiz ad `ELEMENT_AMBIGUOUS` alıyor. İkisinde de
    `execution_state=not_started` ve tuş yok.
  - Kimlik kuralı (`_shows`): kurulu uygulama için AT-SPI uygulama adı
    girdinin kimliğine, kimlik sonuna, ikili adına ya da adına denk olmalı;
    ikili adı 6 karakterden uzunsa öneki de sayılıyor (`gnome-terminal` →
    `gnome-terminal-server`). Başlık tek başına ancak pencerenin süreci başka
    hiçbir kurulu uygulamaya ait değilse yetiyor (LibreOffice `soffice`).
    Yanlış pencere öne gelirse `EXECUTION_UNKNOWN` ve `Escape`: tuşlar gitti,
    tekrarlanmıyor.
  - `apps.focus()` eski imzasıyla duruyor (rollback noktası). `prepare()`
    (`computer_task`) artık aynı sıra: açık uygulamayı yeniden başlatmıyor,
    eskiden ikinci pencere açıyordu.
- `batch.py`:
  - `focus` maliyeti seçilen yoldan geliyor: eklenti varken 200 ms, yokken
    7000 ms.
  - `launch` 300 ms'den 1500 ms'ye çıktı, çünkü artık pencereyi bekliyor.
  - `launch`/`focus` kalan süreyi `budget_left` olarak alıyor. Sığmayan yavaş
    adım (başlatma, arama) `BudgetExceeded` ile hiç başlamıyor ve motor bunu
    `stopped="budget"` sayıyor. İyimser tahmin MCP tavanını aşamaz.
- `ops.py`: `DeviceOps.focus`/`launch` aynı işlemleri kullanıyor (`pcb-do`
  dahil).
- `tools.py`: `window_focus` sonucu hangi yoldan gidildiğini söylüyor. Denetim
  kaydı `path` ve `ms` taşıyor; 2026-09-02 ölçümü yalnızca `batch_step`'ten
  yapılabilmişti. `computer_batch` ve `pcb-do` seçilen yolu motora veriyor.
  Araç açıklaması yeni sırayı anlatıyor.
- `runtime.py`: `window.focus` sınırlama metni yeni sırayı söylüyor.
  `window.move_resize` `unsupported` kaldı.
- Yeni test penceresi `tests/live/window_app.py`: stdin okumuyor, çünkü
  `gtk-launch` uygulamayı stdin kapalı başlatıyor. `a11y_window.py` stdin
  EOF'ta kapanıyordu.
- Belgeler: `PLAN.md` notu, `CLAUDE.md` (üç ölçülmüş gerçek + canlı test
  komutu), `KULLANIM.md` (`window_focus` bölümü Adım 2'den beri eskiydi).
- Yan düzeltme: `tests/test_e2e.py`'de `ui_dump` açıklama kontrolü
  `fe355b2`'den beri kırmızıydı. "structured controls" ifadesi docstring'de
  iki satıra bölünmüştü. Açıklamalar artık boşlukları tek boşluğa
  indirilerek aranıyor.

**Testler.**

- `tests/contracts/test_window_operations.py` (yeni, 46 test): ad çözümü
  (belirsizlik, gizli girdi, `find()` ile aynı tur), kimlik kuralı (web
  araması sekmesi, `soffice`, kısa ikili adı), sıranın her dalı, süre
  sınırları, `gtk-launch` argümanları (systemd kapsamı, boru yok), `DeviceOps`
  ve motorun bütçe davranışı.
- `test_window_focus.py`: arama yedeği testleri kurulu bir uygulamayla. Odak
  iki kez okunuyor ve ikisi de kullanılıyor; eski kodun kullanılmayan
  okumasıyla karıştırılmasın diye yorumlu. `prepare` testleri yeni dosyaya
  taşındı.
- `test_mcp_errors.py` (+3): denetim kaydında `path`/`ms`, retin
  `not_started`'ı, `computer_batch`'in seçilen yolu motora vermesi.
- Mutasyon denemesi: **20 bozulmanın 20'si** yakalandı (`apps` 13, `batch` 3,
  `ops` 1, `tools` 3); dosyalar SHA-256 ile doğrulanarak geri yüklendi.
- Canlı (`tests/live/test_window_operations.py`, 9 geçti, 4 tasarım gereği
  atlandı). Kendi test pencereleri, geçici `.desktop` girdileri; eklenti
  kapalı (`activate_window` → False) ki arkasındaki yollar koşsun:

  | Ölçüm | Python okuyucu | Native okuyucu |
  |---|---|---|
  | Kapalı uygulama → açık ve odakta, tuş yok | 935 ms | **379 ms** |
  | Zaten öndeki hedef, tuş yok | 132 ms | **31 ms** |
  | Toplu `launch` (pencere görülene kadar) | 578 ms | **340 ms** |
  | Arama yedeği (Super + ad + Enter, açık pencere) | 7266 ms | — |

  Açılan pencerenin cgroup'u `app-pcbridge-…scope` (testin kendi kapsamı
  değil). Uygulama olmayan ad hiçbir tuş göndermeden `TARGET_MISMATCH`
  aldı. Arama yedeği ikinci örnek başlatmadan var olan pencereyi öne aldı.
- Takımlar: contract 364, integration 24 (1 atlandı), `test_desktop.py` 583,
  models 106, safety OK, `--check` 0. Uçtan uca (servis, ajan kapalı) **256
  geçti, 0 kaldı, 9 atlandı**. Rust'a dokunulmadı.

**Kabul ölçütleri (PLAN 6.4).**
- Zaten öndeki hedef için `super` gönderilmiyor: ✓ (sözleşme + canlı).
- Soğuk başlatma korunuyor: ✓ (canlı, artık tuşsuz).
- Yanlış arama sonucu başarı sayılmıyor: ✓ (sözleşme, web araması sekmesi
  senaryosu). Canlı üretilmedi, çünkü kullanıcının tarayıcısında sekme açmak
  gerekirdi.

**Yayılım, yapıldı.** Servis boştaydı (cgroup'ta tek süreç) ve yeniden
başlatıldı. HTTP + statik token ile `system_capabilities`: `window.focus`
`supported`/`linux.gnome-shell-extension`, `window.move_resize`
`unsupported`. stdio istemcileri uygulama kapatılıp açılınca geçer (#7).

**Rollback.** `git revert` yeter: yeni ayar yok. Eski davranışın tek girişi
`apps.focus()` ve imzası değişmedi.

**Sıradaki:** Gate 6.

### Gate 6 — Accessibility parity · `geçti` (2026-09-20)

Kanıtların çoğu 6.1–6.3'te üretilmişti, ama 6.4 aynı katmana dokundu. Bu
yüzden kapı **güncel ağaçla** yeniden koşuldu.

| Gerekli kanıt (`PLAN.md` E) | Nerede |
|---|---|
| Target identity | Task 6.1: hedef, uygulamanın veriyolu adı + öğenin nesne yolu. Task 6.3: eylem yalnızca yardımcının kendi dökümündeki düğüme gidiyor, başka yardımcınınki `ELEMENT_STALE` |
| Stable IDs | Task 6.1 ölçümü (GTK4'te araya düğüm eklenince nesne yolları korunuyor, yeniden yaratılan öğe yeni yol alıyor); Task 6.3 döküm kaydı (son 8 döküm, kimliği yardımcı veriyor) |
| Native action | Task 6.3: `DoAction`/`SetTextContents` bir kez gönderiliyor ve cevabı denetleniyor; devre dışı düğme `ACTION_UNSUPPORTED`, kırpılan metin `TEXT_MISMATCH` |
| Unicode text | Task 6.3: Türkçe metin (23 karakter) birebir geri okundu; bu koşumda yeniden |
| Gerçek read/action testi | Bu gece, güncel ağaçla: `test_accessibility_parity.py` **20/20**, `test_window_operations.py` **9** (+4 tasarım gereği atlandı) |

**Bu koşumun ölçümleri** (kendi test penceresi, paketlenmiş yardımcı, izin
geçici durum dizininde):

| Ölçüm | Python | Native |
|---|---|---|
| Döküm (medyan, n=16/12) | 135,8 ms | **25,8 ms** |
| Tıklama | 54,6 ms | **11,7 ms** |
| Metin yazma | 48,0 ms | **7,0 ms** |
| Pencere listesi | 121,1 ms | **6,6 ms** |
| gnome-shell dökümü | 3559 ms | 2241 ms |
| Kapalı uygulamayı açıp öne alma (6.4) | 937 ms | **399 ms** |
| Zaten öndeki hedef (6.4) | 146 ms | **31 ms** |

**Kapı başarısızsa ne olurdu:** "Python accessibility korunur". Bu yol fiilen
denendi: `[native] accessibility` `python` iken sağlayıcı
`PythonAccessibilityProvider` (`linux.atspi`), `auto` ve `rust` iken
`RustAccessibilityProvider` (`linux.atspi.native`). Yani geri alma tek satır.

**Açık kalan, kapıyı engellemeyen iki şey:**
- İzin, hedef bulunduktan sonra eylemden hemen önce bir kez daha
  doğrulanıyor; bu aralığa deterministik girilemediği için mutasyon testiyle
  sınanamadı (Task 6.3).
- Aramanın doğrulamasında başlık kanıtı, süreci hiçbir kurulu uygulamaya
  ait olmayan pencereler için geçerli. AT-SPI adı kendi `.desktop` girdisine
  benzemeyen bir tarayıcı bu boşluktan geçebilir; bu makinenin varsayılan
  tarayıcısı (`google-chrome`) geçmiyor (Task 6.4).

**Sıradaki:** Faz 7 — mixed scale ve koordinat güvenliği (7.1), XDG portal
capture backend'i (7.2), buffered (7.3), adaptive (7.4).

### Task 7.1 — Mixed scale, negatif origin ve topology güvenliği · `tamamlandı` (2026-09-20)

Bu makinede iki monitör de ölçek 1.0 ve (0,0)'dan başlıyor, yani bu task'ın
konusu **ölçülemeyen** bir donanım. O yüzden kural yazıldı, iki dilde aynı
fixture'a bağlandı ve "mevcut sonuç değişmiyor" gerçek makinede doğrulandı.

**Ne değişti.**

- **İki uzay ayrıldı.** Kompozitörün kendi koordinatı `Monitor.platform`'da;
  tuval her zaman (0,0)'dan başlıyor. Öteleme tablo okunurken bir kez
  yapılıyor (Python `_normalize_origin`, Rust `resolve`). Negatif bir tuval
  koordinatı sanal farenin mutlak ekseninde gösterilemez ve kırpma kutusu
  görüntünün dışına düşerdi.
- **Aynı geometri, kaymış origin = aynı düzen.** `topology_id` tuval
  koordinatından üretiliyor, yani bütün ekranlar eşit kayarsa kimlik
  değişmiyor ve hiçbir çekim gereksiz yere geçersizleşmiyor.
- **Çekim kaydı v2:** `source_pixel_size`, `desktop_size`, `scale_xy`,
  `coordinate_space`. Eski alanlar (`size`, `scaled`, `scale`, `offset`)
  aynen duruyor. Görüntü pikselini masaüstü birimine çeviren oran artık
  `desktop_size / scaled`, **her eksen ayrı**: ölçekli monitörde de doğru ve
  tek oranın uzun kenarda bıraktığı bir piksellik kayma yok.
- **Karışık ölçek tanımlı.** Kare ya mantıksal boyutta ya da mantıksal boyut ×
  ölçek gelir; başkası ölçeklenmez, reddedilir (`check_source_size`, iki
  yakalama yolu da aynı kontrolü kullanıyor). Tek görüntü veren
  `gnome-screenshot` yedeğinde oran ancak bütün monitörler aynı ölçekteyse
  çözülüyor; farklı ölçeklerde reddediliyor (`canvas_pixel_ratio`).
- **İki yeni ret:** hiçbir monitörün üstüne düşmeyen koordinat (boşluk, köşe,
  tuval dışı) ve verilen çekimin görüntüsünün dışındaki piksel. Monitör
  tablosu okunamıyorsa kontrol atlanıyor.
- `_ordered` artık `dataclasses.replace` kullanıyor: yeni bir alan eklendiğinde
  sessizce düşmesin (platform koordinatı bir kez öyle düştü, test yakaladı).

**Testler.**

- Yeni fixture `tests/fixtures/native/mixed_scale_cases.json`: beş düzen (bu
  makine, negatif origin, 1,25 + 1,5 kesirli ölçek, 2× HiDPI + 90° döndürülmüş
  dikey ekran, boşluklu ve basamaklı iki monitör), altı koordinat vakası, dört
  gidiş-dönüş vakası, üç ret, üç tuval oranı vakası. Beklenen değerler **elle**
  hesaplandı.
- `tests/contracts/test_coordinate_v2.py` (18 test) ve
  `rust/crates/pcbridge-core/tests/geometry.rs` (6 test) aynı fixture'ı okuyor:
  tablo, tuval, platform origin, topology ve ham piksel boyutu iki dilde de
  aynı.
- Mutasyon denemesi: **15 bozulmanın 15'i** yakalandı (Python 12, Rust 3).
  İlk turda "tuval boyutu origin'i yok sayıyor" sağ kalmıştı, çünkü
  normalleştirmeden sonra iki ifade denk. `canvas_size` genel bir yardımcı
  olduğu için elle kurulmuş, normalleştirilmemiş bir tabloyla sözleşmesi iki
  dilde de teste bağlandı; bozulma artık yakalanıyor.
- Takımlar: contract 382, `test_desktop.py` 583 (capture açıkken 602),
  integration 24 (1 atlandı), models 106, `--check` 0. Rust: fmt ve clippy
  (iki türde) temiz, `cargo test` 156 varsayılan / 177 test kipi.
- Canlı: `test_capture_parity.py` 11 test ve `test_capture_default.py` 3 test
  geçti; ayrıca gerçek bir çekimin kaydı okundu.

**Kabul ölçütleri (PLAN 7.1).**
- Round-trip hatası fixture matrisinde ≤1 masaüstü birimi: ✓ (dört düzende,
  köşeler ve tek sayılı noktalar dahil).
- Eşit ölçekli mevcut iki monitör sonucu değişmiyor: ✓ ölçüldü. Kayıt
  `offset [1920,0]`, `size [1920,1080]`, `scaled [1536,864]`, `scale 0.8` —
  7.1 öncesiyle birebir aynı; görüntünün (0,0)'ı (1920,0), ortası (2880,540),
  son pikseli (3839,1079); tuval 3840x1080, platform origin (0,0).

**Doğrulanmayan.** Kesirli ve 2× ölçekli donanım bu makinede yok: beş düzenin
dördü gerçek bir ekranda hiç çalışmadı. Aynı sebeple Mutter'ın yarım piksel
sınırındaki davranışı hâlâ ölçülmedi. Karışık ölçekli bir kurulum ilk kez
takıldığında `gnome-screenshot` yedeği reddedecek, yayın yolu çalışacak.

**Rollback.** `git revert`; yeni ayar yok. Kayıt biçimi iki yönlü uyumlu: eski
bir süreç yeni kaydı okur (fazladan alanları yok sayar), yeni süreç eski kaydı
okur (v1 testiyle sabit). Yani eski süreçler kapatılmadan da güvenli.

**Sıradaki:** Task 7.2 — XDG ScreenCast portal backend'i.

### Task 7.3 — Buffered capture · `ölçüldü, uygulanmadı` (2026-09-20) · Task 7.4 · `uygulanmadı`

Task'ın amacı "**ölçülmüş ihtiyaç varsa** tekrarlı çekimin gecikmesini
azaltmak" idi. Ölçüm ihtiyacın buffered'da olmadığını gösterdi ve asıl
darboğazın yerini söyledi.

**Bir monitörün çekimi, parça parça** (bu makine, paketlenmiş yardımcı,
gerçek ekran, 8–20 koşum):

| Parça | Süre | Kim |
|---|---|---|
| Kare beklemesi (`wait_ms`) | **64,5 ms** | yardımcı — *buffered'ın kaldıracağı tek şey* |
| PNG kodlama (`encode_ms`) | 445 ms | yardımcı |
| Native çağrı toplamı | ~525 ms | yardımcı + IPC |
| PNG'yi çöz + 1536'ya küçült | 34 + 27 ms | Python |
| **`save(optimize=True)`** | **3106 ms** | Python |
| **Uçtan uca** | **3698 ms** | |

Yani buffered en iyi ihtimalle **%1,7** kazandırırdı; karşılığında sürekli
açık bir PipeWire akışı, monitör başına bir tam kare bellek ve bayat kare
riski. `capture_mode` ayarı, `capture_mode.rs` ve buffered testleri
yazılmadı. Task 7.4 (adaptive) ön koşulu "7.3'ün ölçümleri fayda gösteriyor"
olduğu için kendiliğinden düştü.

**Bunun yerine ölçülen darboğaz düzeltildi.** `optimize=True` kaydı 3106 ms
sürüp dosyayı yalnızca %5 küçültüyordu (906 KiB'a karşı 957 KiB). PNG
kayıpsız olduğu için pikseller aynı; değişen tek şey dosya boyutu ve
bekleme. `_write_crop` artık varsayılan sıkıştırmayla yazıyor.

| Ölçüm | Önce | Sonra |
|---|---|---|
| Python'un PNG payı | 3171 ms | **330 ms** |
| Tek monitör, uçtan uca | 3698 ms | **850 ms** |
| İki monitör, uçtan uca | (ölçülmedi) | **1348 ms** |

Sözleşme testi kaydın `optimize` ile yapılmadığını ve yazılan PNG'nin
pikselinin kaynakla aynı olduğunu sabitliyor; yorumda ölçüm duruyor, geri
koyan önce ölçsün.

**Testler.** `test_capture_contract.py` (+1), `test_desktop.py` 602 (capture
açık), canlı `test_capture_parity.py` + `test_capture_default.py` 14 test
geçti (piksel eşitliği, tazelik, revoke, varsayılan seçim).

**Sıradaki:** Task 7.2 — XDG ScreenCast portal backend'i. Sıra bilinçli
değiştirildi: 7.2 bu makinede doğrulanamıyor (portal penceresine kullanıcının
tıklaması gerekir), 7.3 ölçülebiliyordu.

## Adım 6 — İmleç katmanı · `uygulandı, kapalı geliyor` (2026-09-20)

Bulgular ve tasarım kararları aşağıda, "Kurtarılan kayıtlar" bölümünde. Üç
adımın üçü de yapıldı:

1. Fiziksel farenin olay hızı: **~998 Hz** (medyan aralık 1,00 ms, ölçüldü
   2026-09-13). Hipotez güçlendi.
2. Konum artık **kare başına bir kez** uygulanıyor (`frameclock.js`,
   `Meta.Laters` / `BEFORE_REDRAW`). Fare olayı geldiğinde `global.get_pointer()`
   bile çağrılmıyor; yalnızca bir kare isteniyor.
3. Aktör `Main.layoutManager.addTopChrome` yerine **`Main.uiGroup`**ta. İzlenen
   bir chrome aktörünün her konum değişimi kabuğun girdi bölgesi hesabını
   yeniden kuyruğa sokuyordu; aktör zaten `reactive: false`.

**Ölçüm (nested kabuk, 2026-09-20).** Sanal bir işaretçiyle 2000 hareket
gönderildi (`PCBRIDGE_GORUNUR_BURST=1`, 4 hareket / 4 ms):

| Kod | Fare olayı | Çizim | Çizim/sn | Ana döngü en kötü |
|---|---|---|---|---|
| Eski (her olayda + `addTopChrome`) | 1992 | 1992 | 471 | 23,7 ms |
| Yeni (kare saati + `uiGroup`) | 1992 | **263** | **58** | 36,0 ms |

Çizim sayısı 7,6 kat düştü; gerçek 1000 Hz farede oran ~17 kat olur. Ana döngü
gecikmesi iki koşumda da eşiğin altında kaldı ve aralarındaki fark bu örneklem
için gürültü — **nested kabuk donmayı zaten yeniden üretemiyordu.** Bu yüzden
düzeltmenin gerçek arızayı çözdüğü **kanıtlanmadı**, yalnızca tek ölçülmemiş
farkın (olay hızı) ortadan kalktığı gösterildi.

**Bu yüzden katman VARSAYILAN KAPALI.** Açmak/kapatmak için işaret dosyası:

```bash
touch ~/.local/state/pcbridge/gorunur-imlec     # aç
rm    ~/.local/state/pcbridge/gorunur-imlec     # kapat
```

Dosya her izin açılışında yeniden okunuyor, yani kabuğu yeniden başlatmak
gerekmiyor: bir sonraki `desktop_unlock` yeni durumu alır. Katman kapalıyken
gerçek imlece hiç dokunulmuyor, yani bugünkü davranış birebir korunuyor.

**Testler.** `tests/test_cursor.js` (yeni, 17 test, kabuk gerekmez): bin olay
bir kare, kare içinden gelen istek düşmüyor, kapanışta bekleyen iş iptal
ediliyor, iptal hatası kapanışı durdurmuyor, zamanlayıcı kimlik vermezse
yeniden kuruluyor. `test_state.js` 31, `test_window_control.js` 13 test geçti.
Nested kabukta tıklama geçiş testi 8/8 (çerçeve aktörleri tıklama hedefi
değil).

**Kullanıcıyı bekleyen (#8).** Gerçek oturumda, çıkış/giriş sonrası, fiziksel
fareyle: işaret dosyasını açıp izin verdikten sonra tıklama ve fare akışı
normal mi? Bozulursa dosyayı silmek yeter.

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
