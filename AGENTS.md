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

- Son tamamlanan task: **2.3 — Atomik grant ve süreçler arası revoke** (`c4aa76b`)
- Aktif task: **Yok** (`Task 2.3 tamamlandı`)
- Sıradaki task: **2.4 — Native lock/activity observations ve safety ayrımı**
- Blocker: Yok
- Son doğrulanan gate: **Gate 2 geçti.** Atomik grant, süreçler arası revoke,
  native watchdog ve PID-reuse güvenli registry acceptance'ı geçti.
