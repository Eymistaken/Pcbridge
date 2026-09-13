# Linux capture doğrulaması (Task 4.2)

> Kurallar ve mimari: **[CLAUDE.md](../../CLAUDE.md)** · Sıradaki iş: **[WALKTHROUGH.md](../../WALKTHROUGH.md)**

Rust capture backend'inin gerçek masaüstünde eski Python/GStreamer yoluyla
karşılaştırılması. Varsayılanı değiştirme kararının (Task 4.3) kanıtı bu belge.

## Nasıl koşulur

```bash
scripts/build-native.sh
PCBRIDGE_TEST_CAPTURE=1 PCBRIDGE_PARITY_REPORT=/tmp/parity.json \
  ./.venv/bin/python -m unittest tests/live/test_capture_parity.py -v
PCBRIDGE_TEST_CAPTURE=1 ./.venv/bin/python -m unittest \
  tests/integration/test_mcp_capture_delivery.py -k Live
```

Test **ekran paylaşımı açar ve monitörleri birkaç dakika statik bir test
deseniyle kaplar.** Girdi göndermez: tıklama, yazma, fare hareketi yok. Bütün
izinler geçici bir state dizininde; kullanıcının gerçek izin dosyası okunmaz,
yazılmaz. Çekilen görüntüler geçici dizinde kalır ve sonunda silinir. Ekran
kilitliyse ya da gerçek bir izinle çalışan Python yardımcısı varsa test kendini
atlar.

Yalnızca **release** binary kabul edilir (`--build-info`: `profile: release`,
`test_harness: false`). `rust/target/debug/pcbridge-native` güvenilir değil:
`tests/integration/test_native_revoke.py` onu test-harness özelliğiyle yeniden
derliyor ve o derleme ekran okumadan sahte kareler veriyor.

## Test deseni

`tests/live/pattern_window.py` (sistem python3, GTK 4.14) her monitöre tam ekran
bir pencere açar: renk çubukları, dama tahtası, monitörü soldan sağa sırasıyla
adlandıran renkli bir işaret karesi ve ikili sayaç tutan 16 hücrelik bir şerit.
İşaret yanlış monitör hatasını, sayaç tazeliği yakalar. Pencere `quit`, stdin
EOF ya da kendi zaman aşımıyla kapanır — ölen bir test ekranı kaplı bırakamaz.

## Sonuçlar (2026-09-13)

Koşullar: Zorin OS 18.1 (Ubuntu 24.04), GNOME Shell 46, Wayland, iki 1920×1080
monitör; PipeWire 1.0.5, WirePlumber 0.4.17; i9-11900K, governor `powersave`,
yük ~0,75–1,2; `pcbridge-native 0.1.0`, build `e8890eccae5a`, release. İki tam
koşum yapıldı; ikincisi aşağıdaki hata düzeltildikten sonra.

| Kanıt (`PLAN.md` 4.2) | Sonuç |
|---|---|
| Monitör kimliği, ofset, boyut, shot→global | `1`, `2`, `all` × 1536 ve tam boyut: 6/6 birebir aynı |
| Statik piksel eşleşmesi (hedef ≥ %99,5) | DP-4 ve DP-3, tam boyut ve 1536: **%100,000** |
| `include_pointer=true` | iki backend birebir (%100,000); native imleçli/imleçsiz farkı yalnızca imlecin durduğu monitörde (%99,989) |
| Her OnDemand kare istekten sonra | sayaç 12 kez değişti: native 12/12 taze (eski yol da 12/12) |
| Hata oranı | 0 — her koşumda 60 sıcak çekim, 60 ham kare, 10 oturum açılışı |
| Sıcak çekim p95 ≤ 1,5 × eski | 1. koşum 179,5 / 126,0 ms = **1,42**; 2. koşum 175,3 / 131,2 ms = **1,34** |
| Revoke ve süre dolumu sonrası kare yok | ikisinde de kare yok; `GRANT_REQUIRED`/`safety` (2. koşum) |
| Kaynak kapanınca paylaşım bitiyor | Python host'u öldürülünce yardımcı 0,11–0,12 sn, Mutter oturumu 0,12–0,13 sn içinde kapandı (oturumlar D-Bus'tan sayıldı) |
| Native çökmesi | çöken yardımcının Mutter oturumu serbest kaldı; sonraki çekim yeni süreçle hatasız geldi |
| Python GI olmadan gerçek capture | canlı stdio sunucusu `gi` engelliyken iki monitörü teslim etti (monitörler arası eşleşme %23,27 — iki ayrı ekran) |
| stdio ve izole HTTP istemcisinde teslim | stdio: 2 görüntü; HTTP (statik token): 2 görüntü, işaretler doğru monitörde |
| Native arızası sırasında kabuk/iş araçları | yardımcı öldürüldüğünde `shell_run` ve `job_list` çalıştı; sonraki iki çekim teslim edildi |

Süreler (ms, 2. koşum; `capture()` ve ham karede 30 örnek, açılışta 5):

| | eski p50 / p95 | native p50 / p95 |
|---|---|---|
| `capture()` uçtan uca, 1536, desen | 122,9 / 131,2 | 163,7 / 175,3 |
| ham kare | 46,9 / 57,7 | 81,9 / 93,6 |
| oturum açılışı + ilk kare | 152,4 / 189,1 | 92,2 / 104,6 |

Okuma: native **oturum açılışında** hızlı (yardımcıya JSON gidiş-dönüşü ve
GStreamer boru hattı kurulumu yok), **karede** yavaş — kare Rust'ta PNG'ye
kodlanıp IPC'den geçiyor ve Python onu yeniden çözüyor. Desen basit bir
görüntü; gerçek masaüstünde iki yol da Python'un `save(optimize=True)` bedelini
(~1 sn/monitör) ödediği için oranın düşmesi beklenir — **bu ölçülmedi**.

MCP düzeyinde, gerçek masaüstünde, GI engelli stdio sunucusunda iki monitörlük
`screen_capture` release ile **5,1–5,2 sn** sürdü, soğuk ve sıcak aynı. Bunun ne
kadarının Python kodlaması, ne kadarının MCP serileştirmesi olduğu bu koşumda
ayrılmadı ve aynı ölçüm eski backend için yapılmadı. Ayrı bir ölçümle
parçalandı: aşağıda, "Varsayılan değişikliği (Task 4.3)".

## Varsayılan değişikliği (Task 4.3)

### MCP düzeyinde süre, iki backend

Gerçek masaüstü, iki monitör, stdio sunucusu, release yardımcı, yük ~1,0;
ısınmadan sonra 5 çağrı, p50 (ms):

| | stdio `screen_capture(all)` | bellek içi toplam | çekim hattı | görüntü doğrulama | kalan (FastMCP) | stdio borusu |
|---|---|---|---|---|---|---|
| eski | 4.684 | 4.658 | 4.628 | 2 | 28 | 26 |
| native | 5.113 | 5.082 | 5.053 | 2 | 27 | 31 |

Oran **1,09**. Sürenin ~%99'u çekim hattında: iki yolda da Python'un
`save(optimize=True)` kaydı, gerçek içerikte monitör başına ~2,2 sn. MCP katmanı
~30 ms, stdio borusu ~30 ms. Yukarıda ayrılmadığı yazılan fark buydu: daha basit
bir karede ~1 sn ölçülen Python kaydı gerçek içerikte iki katına çıkıyor.

### Gösterge ve varsayılan, taze süreçlerde

`tests/live/test_capture_default.py` (3/3) ve Rust
`session_open_shows_the_share_before_any_frame`:

- stdio ve servis tarzı HTTP süreci, örnek config'le, native yolu seçti; Mutter
  oturumu `desktop_unlock` anında açıldı, çekim o oturumu kullandı,
  `desktop_lock` kapattı;
- yardımcı bulunamayınca süreç Python'a düştü: `system_capabilities` `degraded`
  + gerekçe, `screen_capture` sonucunda not;
- parity gate'i `start()` oturum açtıktan sonra yeniden koştu: 11/11, piksel
  %100, tazelik 12/12, p95 oranı 1,405 (yük 1,38–1,44).

### Yayılım

Değişiklik **yalnızca yeni başlayan süreçlerde** etkili; çalışan hiçbir süreç
kendiliğinden yeni varsayılana geçmez.

- `config.toml`'da `[native]` bölümü yoksa ya da `capture = "auto"` ise yeni
  süreçler native yolu kullanır. `capture = "python"` yazılıysa değişen bir şey
  yok.
- **Servis:** önce `job_list` ile çalışan iş olmadığını doğrulayın (restart
  işleri öldürür), sonra `systemctl --user restart pcbridge`.
- **stdio istemcileri** (Claude Code, Codex, Claude Desktop): istemciyi yeniden
  başlatın. `ps -eo pid,lstart,args | grep 'pcbridge.server --stdio'` eski
  süreçleri gösterir; kapanana kadar eski ayarla çalışırlar.
- **Doğrulama:** `./doctor.sh` → 8. bölüm; `system_capabilities` →
  `capture.monitor` backend `linux.mutter.pipewire`; `desktop_unlock` sonrası
  sağ alttaki görev çubuğunda paylaşım göstergesi var, `desktop_lock` sonrası yok.
- **Geri alma:** `[native]` altında `capture = "python"`, ardından aynı yeniden
  başlatmalar.
- Revoke ve eski yardımcı kayıtlarının kontrolü dahil adım adım sıra:
  [protocol-v1.md → Native yola geçiş runbook'u](protocol-v1.md#native-yola-geçiş-runbooku).

## İlk koşumun bulduğu hata

İlk koşumda revoke ve süre dolumu kare **vermedi**, ama
`BACKEND_UNAVAILABLE`/`capability` olarak sınıflandı. İki kök vardı:

1. `capture.py`, bir backend istisnasının taşıdığı tipli nedeni
   (`desktop_error`) düz bir `CaptureError`'a sarıyordu; provider da onu
   "backend yok" diye raporluyordu. Yani native yardımcının **bütün** tipli
   redleri (REVOKED, DISPLAY_CHANGED, FRAME_TIMEOUT…) provider düzeyinde
   kayboluyordu.
2. `NativeScreenCast._grant()` izin yokken tipsiz bir hata fırlatıyordu.

Düzeltmeden sonra ikisi de `GRANT_REQUIRED`/`safety`. Provider düzeyinde üç tipli
red ve izinsiz çekim sözleşme testine girdi; iki düzeltme ayrı ayrı geri
alınınca testler kırmızıya döndü. Üretimde araçlar önce `SafetyGate`'ten geçtiği
için bu yanlış kategori kullanıcıya ulaşmıyordu; savunma katmanında ise ajanı
`desktop_unlock` yerine yardımcıyı onarmaya yönlendirirdi.

## Kullanıcıyla yapılan kontroller (2026-09-13)

Önceden "kapsanmayanlar" olarak kalan iki senaryo kullanıcı başındayken
yapıldı. Paylaşımı açan süreç iki kontrolde de ayrı bir state dizininde
çalıştı, kullanıcının gerçek iznine dokunmadı.

- **Ekran kilidi.** Native paylaşım açıkken ekran kilitlendi
  (`org.gnome.ScreenSaver.Lock`), kullanıcı 27,5 sn sonra parolasıyla açtı.
  Kilitliyken 3. ve 11. saniyede: Mutter oturumu 0 (native kilit gözcüsü
  kapattı), `SafetyGate.check` → `SCREEN_LOCKED`, kapıyı atlayan doğrudan
  provider çağrısı → `SCREEN_LOCKED`, 0 PNG. Kilit açılınca oturum
  kendiliğinden açılmadı; sonraki çekim normal geldi.
- **Paylaşım göstergesi.** Kendi paylaşımını açmayan `gnome-screenshot` ile üç
  kare: önce simge yok; native paylaşım açıkken sağ monitörün altındaki görev
  çubuğunda turuncu paylaşım simgesi var; süreç `SIGKILL` ile öldürülünce yok.
  Mutter oturumu 0,11 sn'de kapandı.

Kalan küçük tutarsızlık: kilit native oturumu kapatınca Python tarafındaki
`is_open()` `True` kalıyor (`WALKTHROUGH.md` → Adım 4 → "Kullanıcıyla yapılan
kontroller").
