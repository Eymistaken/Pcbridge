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

**[WALKTHROUGH.md](WALKTHROUGH.md)** — depodaki tek yapılacak-iş listesi: yol
haritası, her adımın kabul ölçütü, biten işlerin kaydı ve son doğrulanan gate.
Native migration'ın implementation sözleşmesi [PLAN.md](PLAN.md); belge
haritasının tamamı `CLAUDE.md` sonunda.

**Durum özetini buraya kopyalama.** Bu dosyada bir zamanlar "Native migration
durumu" bloğu vardı ve `WALKTHROUGH.md` ile aynı bilgiyi iki yerde tutuyordu —
tıpkı bu dosyanın `CLAUDE.md` kopyası olduğu dönemdeki gibi. İki gerçeğin
olduğu yerde biri eskir, ve eskiyen kopya en kötü anda fark edilir.
