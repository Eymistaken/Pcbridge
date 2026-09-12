# Native migration baseline

> Kurallar ve mimari: **[CLAUDE.md](../../CLAUDE.md)** · Sıradaki iş: **[WALKTHROUGH.md](../../WALKTHROUGH.md)**

Bu belge, native desktop migration başlamadan önce korunacak public sözleşmeleri
ve güvenli test sonuçlarını kaydeder. Son ölçüm 8 Eylül 2026 tarihinde yapıldı.

## Tekrarlanabilir güvenli komutlar

Bütün gerçek desktop izinleri kapalıyken aşağıdaki komutlar çalıştırıldı:

```bash
env -u PCBRIDGE_TEST_CAPTURE \
  -u PCBRIDGE_TEST_INPUT \
  -u PCBRIDGE_TEST_ATSPI \
  -u PCBRIDGE_TEST_BATCH \
  ./.venv/bin/python tests/test_desktop.py

env -u PCBRIDGE_TEST_CAPTURE \
  -u PCBRIDGE_TEST_INPUT \
  -u PCBRIDGE_TEST_ATSPI \
  -u PCBRIDGE_TEST_BATCH \
  ./.venv/bin/python tests/test_models.py

env -u PCBRIDGE_TEST_CAPTURE \
  -u PCBRIDGE_TEST_INPUT \
  -u PCBRIDGE_TEST_ATSPI \
  -u PCBRIDGE_TEST_BATCH \
  ./.venv/bin/python tests/test_test_safety.py

./.venv/bin/python -m unittest discover \
  -s tests/contracts -p 'test_*.py' -v
```

Ölçülen sonuçlar:

- Desktop suite: `578 geçti, 0 kaldı`.
- Model suite: `106 geçti, 0 kaldı`.
- Live-test selector safety suite: `1 test`, `OK`.
- Contract suite: `7 tests`, `OK`.

`tests/test_e2e.py` çalıştırılmadı. Bu baseline canlı server, gerçek agent veya
desktop eylemi gerektirmez.

## Sabitlenen public sözleşmeler

Contract suite şu davranışları provider değişiminden bağımsız olarak doğrular:

- MCP tool adları, annotations, seçili input schema alanları ve varsayılanlar.
- `screen_capture` sonucunda text block'un image block'lardan önce gelmesi.
- Monitörlerin soldan sağa sıralanması; primary monitörün sağda olabilmesi.
- Portrait ve fractional-scale monitor geometrileri ile birleşik canvas boyutu.
- Her monitörün birleşik canvas'tan önce crop edilip sonra resize edilmesi.
- Shot kimliği, metadata lookup ve server-side görüntü koordinatı dönüşümü.
- Taze scaled shot için ambiguity guard; stale veya tam ölçekli shot için
  koordinatın olduğu gibi kalması.
- `pcb-shot --out` ile PNG custom dizine yazılsa da shot metadata'sının default
  arama dizininde bulunabilmesi.

Fixture'lar küçük JSON geometrileri ve çalışma anında oluşturulan düz renkli
PNG'ler kullanır. Repoya gerçek screenshot eklenmez.

## Yapılandırma bağımsızlığı

Model testleri ve yeni contract suite yalnızca `config.example.toml` veya test
içinde kurulan sentetik `Config` nesnelerini kullanır. Private `config.toml`
okunmaz. Baseline öncesindeki dört model hatasının kök nedeni, örnek config'teki
Antigravity varsayılanı `gemini-3.8-flash` iken test beklentisinin eski
`gemini-3.6-flash` değerinde kalmasıydı. Beklentiler ayrı `3d7c31e` commit'inde
örnek config ile hizalandı; başarısız test gizlenmedi.

## Provider parity düzeni

Capture contract, backend'leri `CAPTURE_PROVIDER_FACTORIES` üzerinden çalıştırır.
Başlangıçta yalnızca mevcut Python adapter'ı kayıtlıdır. Native adapter eklendiği
anda aynı sentetik canvas ve aynı assertion'lar ikinci factory için de çalışır.
Production desktop kodunda bu task kapsamında davranış değişikliği yapılmadı.
