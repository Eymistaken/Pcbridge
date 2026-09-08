# Pcbridge Native Core ilerleme kaydı

## Durum özeti

- Aktif task: **1.1 — `DesktopRuntime` ve Python provider adapter'ını çıkar** (`başlıyor`)
- Son tamamlanan task: **0.2 — Public contract ve backend parity fixture'larını oluştur** (`d010462`)
- Sıradaki uygulanabilir task: **1.2 — Capability modelini ve `system_status` çıktısını genişlet**
- Blocker: Yok
- Son gate: **Gate 0 geçti.** Bütün gerçek test bayrakları kapalı güvenli baseline `578 geçti, 0 kaldı`; model suite `106/0`, safety selector `1/1`, contract suite `7/7`.

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

**Durum:** `başlıyor`

**Amaç:** MCP tool registration ile desktop kaynak sahipliğini ayırmak; mevcut Python davranışını provider adapter arkasından korumak.

**Gate:** Phase 1 devam ediyor.

**Rollback:** Runtime wiring task commit'i bağımsız geri alınabilir.
