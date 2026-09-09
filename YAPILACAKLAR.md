# Pcbridge Native Core ilerleme kaydı

## Durum özeti

- Aktif task: **Yok** (`Task 2.3 tamamlandı`)
- Son tamamlanan task: **2.3 — Atomik grant ve süreçler arası revoke** (`c4aa76b`)
- Sıradaki uygulanabilir task: **2.4 — Native lock/activity observations ve safety ayrımı**
- Blocker: Yok.
- Son gate: **Gate 2 geçti.** Atomik grant, süreçler arası revoke,
  native watchdog ve PID-reuse güvenli registry acceptance'ı geçti.

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
