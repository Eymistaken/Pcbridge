# AGENTS.md

Bu depoda çalışan **her** kodlama ajanı için giriş noktası — Codex, Claude
Code, Gemini CLI, Antigravity, hangisi olursa.

## Talimatlar tek dosyada: [CLAUDE.md](CLAUDE.md)

Proje kuralları, mimari, ölçülmüş makine gerçekleri, değişmez kurallar ve
"bu makinede test etmenin tehlikesi" bölümü hepsi orada. **Başlamadan önce
baştan sona oku.** Adı `CLAUDE.md` ama içeriği ajandan bağımsız: dosya adı
tarihsel, kural değil.

Bu dosya bilinçli olarak ince tutuluyor. Bir zamanlar `CLAUDE.md`'nin
kopyası olarak üretilmişti ve iki gün içinde 83 satır ayrıştı; üstelik
otomatik değiştirme komut adlarını da bozmuştu (`claude -p` → `Codex -p`,
"Claude Code, Codex, Claude Desktop" → "Codex, Codex, Codex"). İki
gerçeğin olduğu yerde biri eskir, ve eskiyen kopya en kötü anda —
yanlış bir komutu çalıştırırken — fark edilir.

## Hemen bilmen gereken üç şey

1. **Bu depoda çalışırken pcbridge'in kontrol edeceği makinenin
   üzerindesin.** Bir `type` testi senin terminaline yazabilir ve Enter'a
   basabilir. `CLAUDE.md`'nin "⚠️ Bu makinede test etmenin tehlikesi"
   bölümünü atlama.
2. **Ölçmediğin şeyi "çalışıyor" diye yazma.** Bu depoda "hata vermedi"
   kanıt sayılmıyor; ekran görüntüsü, pencere başlığı ya da bir ölçümle
   doğrula.
3. **`config.toml` parola ve statik token içeriyor.** `.gitignore`'da ve
   öyle kalacak — loglama, ekrana basma, commit etme.

## Sıradaki iş

[YAPILACAKLAR.md](YAPILACAKLAR.md) · devam eden işin adım kaydı için
[ADIMLAR.md](ADIMLAR.md) · belge haritasının tamamı `CLAUDE.md` sonunda.

## Native migration durumu

- Son tamamlanan task: **1.1 — `DesktopRuntime` ve Python provider adapter'ını çıkar** (`3a6a36f`)
- Aktif task: **1.2 — Runtime capability ve typed error katmanını ekle**
- Sıradaki task: **1.3 — Structured MCP hata yüzeyini kur**
- Blocker: Yok
- Son doğrulanan gate: **Task 1.1 acceptance geçti; Phase 1 devam ediyor.** Güvenli desktop baseline `578/0`; model suite `106/0`; safety selector `1/1`; contract suite `12/12`; server `--check` exit `0`.
