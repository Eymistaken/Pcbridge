# Pcbridge Native Core ilerleme kaydı

## Durum özeti

- Aktif task: **1.4 — Çok yollu execution sözleşmesini düzelt** (`başlıyor`)
- Son tamamlanan task: **1.3 — Structured MCP hata yüzeyini kur** (`a414d05`)
- Sıradaki uygulanabilir task: **2.1 — Rust workspace ve executable protocol harness**
- Blocker: Yok
- Son gate: **Gate 0 geçti; Gate 1 devam ediyor.** Bütün gerçek test bayrakları kapalı güvenli baseline `578 geçti, 0 kaldı`; model suite `106/0`, safety selector `1/1`, contract suite `25/25`.

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
