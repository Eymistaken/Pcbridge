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
ayrılmadı ve aynı ölçüm eski backend için yapılmadı. Task 4.3'ten önce
yapılacak.

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

## Kapsanmayanlar (bir kişi gerektiriyor)

- **Ekran kilidi senaryosu.** Kilidi açmak kullanıcının parolasını istiyor;
  kullanıcı yokken ekran kilitli kalırdı.
- **Paylaşım göstergesinin gözle görülmesi.** Gösterge ekran paylaşımı olmadan
  görülemiyor; test kapanışı Mutter'ın D-Bus oturum sayısından doğruluyor.

İkisi `WALKTHROUGH.md` → "Kullanıcıyı bekleyenler" listesinde.
