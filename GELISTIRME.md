# Geliştirme rehberi — yeni araç eklemek

Bu dosya, ilk kurulumda karşılaştığımız sorunların tekrar yaşanmaması için
yazıldı. Yeni bir araç eklemeden önce **"Spark'a özgü kurallar"** ve
**"Protokol tuzakları"** bölümlerini oku; ikisi de acı deneyimle öğrenildi.

---

## Dosya sorumlulukları

| Dosya | Ne var içinde | Ne zaman dokunursun |
|---|---|---|
| `pcbridge/tools.py` | MCP araçlarının tamamı | **Yeni araç eklerken burası** |
| `pcbridge/config.py` | Yapılandırma şeması | Yeni ayar eklerken |
| `pcbridge/jobs.py` | Arka plan işleri, çıktı ayrıştırıcılar | Yeni ajan çıktı formatı |
| `pcbridge/models.py` | Ajan/model/effort çözümleyicisi (**saf fonksiyon**) | Seçim mantığı değişirse — kurallar config'de, burası değil |
| `pcbridge/tmuxctl.py` | tmux sarmalayıcı | Nadiren |
| `pcbridge/auth.py` | OAuth 2.1 sunucusu, onay sayfası | Kimlik doğrulama davranışı |
| `pcbridge/server.py` | Uygulama kurulumu, ASGI ara katmanları | Protokol düzeltmeleri |
| `config.example.toml` | Ayarların belgelenmiş hâli | Yeni ayar eklediğinde **mutlaka** |
| `tests/test_models.py` | Çözümleyici testleri, sunucusuz koşar | Model/effort kuralı değişirse |
| `tests/test_e2e.py` | Uçtan uca testler | Her yeni araçta |

---

## Yeni araç ekleme — 5 adım

### 1. Aracı yaz

`pcbridge/tools.py` içindeki `register()` fonksiyonunun içine ekle. Şablon:

```python
    @mcp.tool(annotations={"title": "Check disk usage", "readOnlyHint": True})
    def disk_usage(
        path: Annotated[
            str, Field(description="Absolute path of the directory to measure.")
        ],
        top: Annotated[
            int, Field(ge=1, le=50, description="How many largest entries to show.")
        ] = 10,
    ) -> str:
        """Show which subdirectories take up the most space under a path on the
        user's Linux desktop. Use when the user asks what is filling up their
        disk."""
        d = _resolve_dir(cfg, path)
        if not d.is_dir():
            return f"Dizin yok: {d}"
        proc = subprocess.run(
            ["bash", "-lc", f"du -h --max-depth=1 {shlex.quote(str(d))} | sort -rh | head -{top}"],
            capture_output=True, text=True, timeout=120,
        )
        out = (proc.stdout or "").strip()
        return f"`{d}`\n```\n{out}\n```" if out else "Sonuc yok."
```

Dikkat edilecekler bir sonraki bölümde.

### 2. Ayar gerekiyorsa `config.py` ve `config.example.toml`'a ekle

İkisini birlikte güncelle. `config.example.toml` bu projenin asıl belgesi —
oradaki yorumlar ileride tek başına yol gösterecek.

### 3. Test yaz

`tests/test_e2e.py` içinde, 11. bölümdeki `call()` yardımcısını kullanarak:

```python
    out = call("disk_usage", {"path": "/tmp/pcb/work", "top": 5})
    check("disk_usage calisti", "```" in out, out[:300])
```

### 4. Çalıştır ve doğrula

```bash
cd ~/Belgeler/mcp_server
systemctl --user restart pcbridge
./.venv/bin/python tests/test_e2e.py
```

Test dosyası sunucunun ayakta olmasını bekler. Parola varsayılandan farklıysa:

```bash
PCBRIDGE_TEST_PASSWORD='<parolan>' ./.venv/bin/python tests/test_e2e.py
```

### 5. Gemini'ye yeni aracı tanıt

**Servisi yeniden başlatmak yetmez.** Gemini araç listesini önbelleğe alıyor
(Connected Apps'te "Son senkronizasyon zamanı" yazıyor). Yeni araçların
görünmesi için:

1. gemini.google.com → Connected Apps → pcbridge → **Diğer ayrıntılar**
2. Senkronizasyonu yenile

Görünmezse kesin çözüm: uygulamayı **kaldır ve baştan ekle**. Parolayı bir kez
daha girersin, 30 saniye sürer.

---

## Spark'a özgü kurallar

Bunlar isteğe bağlı değil; ihlal edersen araç ya hiç çağrılmaz ya da yanlış
çağrılır.

**Açıklamalar İngilizce.** Docstring ve `Field(description=...)` metinleri
Gemini'nin araç seçerken okuduğu tek şey. Spark özel uygulamaları resmî olarak
sadece İngilizce destekliyor. Kullanıcıya dönen metinler Türkçe olabilir ve
olmalı — ayrım bu.

**Docstring "ne zaman kullanılır"ı söylesin.** "Show disk usage" yetmez.
"Use when the user asks what is filling up their disk" cümlesi seçim
doğruluğunu belirgin şekilde artırıyor.

**`readOnlyHint` / `destructiveHint` doğru işaretlensin.** Google, yazma
yapan araçlarda kullanıcıya onay soruyor. Salt okunur bir aracı yanlışlıkla
`destructiveHint` yaparsan kullanıcı gereksiz yere onay tıklar; tersi daha
kötü — tehlikeli bir araç sessizce çalışır.

**Dönüş tipi `str` olsun.** Karmaşık nesneler yerine okunabilir metin dön.
Gemini bunu kullanıcıya özetleyecek. Markdown tablo ve kod bloğu iyi çalışıyor.

**Çıktıyı kırp.** `jobslib.tail_chars(metin, 4000)` kullan. Telefonda 50.000
karakterlik çıktının kimseye faydası yok ve yanıtı yavaşlatıyor.

**110 saniyeden uzun bloklama yapma.** Spark'ın isteği zaman aşımına uğrar.
Uzun sürecek her şey arka plan işi olmalı (aşağıya bak).

**Parametre adları açık olsun.** `p` değil `path`, `n` değil `max_results`.
Gemini bunları doldurmak zorunda.

**Yol parametrelerini `_resolve_dir` / `_resolve_file` ile çöz.** `~` ve
göreli yolları hallediyor, kullanıcı "İndirilenler klasörü" dediğinde işe
yarıyor.

---

## Uzun süren işler için desen

Doğrudan bekleme yerine iş kuyruğunu kullan:

```python
    @mcp.tool(annotations={"title": "Run a long thing", "destructiveHint": True})
    def uzun_is(command: str, workdir: str | None = None) -> str:
        """..."""
        cwd = _resolve_dir(cfg, workdir)
        job_id = jm.start(
            kind="shell",                    # job_list'te görünen tür
            argv=["bash", "-lc", command],
            cwd=cwd,
            label=jobslib._short(command, 90),
            parser="plain",                  # veya "claude_stream_json"
            timeout=None,                    # None -> config'teki varsayılan
            pty=False,                       # TTY isteyen CLI'lar için True
        )
        return f"Baslatildi: `{job_id}`\nDurum icin: job_status('{job_id}')"
```

İşler `setsid` ile ayrı oturum grubunda çalışır: servis yeniden başlasa da
devam ederler, durumları diskte tutulur.

---

## Yeni ajan CLI eklemek

Kod yazmana gerek yok, `config.toml` yeter:

```toml
[agents.codex]
enabled = true
description = "OpenAI Codex CLI"
command = ["codex", "exec", "{prompt}"]
resume_args = ["--session", "{session_id}"]
parser = "plain"
pty = false
```

Sonra `systemctl --user restart pcbridge`.

**Önce mutlaka elle dene:**

```bash
codex exec "merhaba" | cat
```

Sona eklediğin `| cat` kritik: çıktıyı boruya yönlendirir. **Hiçbir şey
çıkmıyorsa** o CLI TTY istiyor demektir → `pty = true` yap. Antigravity'nin
`agy` komutunda bu sorun vardı (upstream issue #76) ama **1.1.9'da düzeldi**;
ölçüldükten sonra `pty = false` yapıldı. `pty = true` komutu `script` ile sahte
terminale sarar — gerekmiyorsa açma, ANSI gürültüsü ekliyor.

`parser` seçenekleri:

- `plain` — çıktıyı olduğu gibi verir, ANSI kodlarını temizler, metinde
  konuşma kimliği geçiyorsa (UUID) yakalar
- `claude_stream_json` — Claude Code'un `--output-format stream-json`
  çıktısını ayrıştırır: adımlar, araç çağrıları, maliyet, oturum kimliği,
  `modelUsage`'den **gerçekte çalışan model**
- `agy_json` — Antigravity'nin `--output-format json` çıktısını ayrıştırır:
  `conversation_id`, `status`, `response`, jeton kullanımı

Başka bir format lazımsa `jobs.py` içine yeni bir ayrıştırıcı yazıp
`summarize()` fonksiyonuna bağla. Ayrıştırıcının döndürdüğü sözlükte
`warnings` (iş özetinin **en üstüne** basılır) ve `actual_model` (istenen
modelle karşılaştırılır) alanlarını doldurabilirsin.

### Model ve effort seçimini yapılandırmak

Bir CLI `--model` / `--effort` kabul ediyorsa bunu **koda değil config'e**
yazarsın. Çözümleyici `pcbridge/models.py`'da ve saf fonksiyondur; yeni ajan
eklerken o dosyaya dokunmak gerekmez.

```toml
[agents.codex]
# ...
model_args  = ["--model", "{model}"]     # bayrak sozdizimi
effort_args = ["--effort", "{effort}"]
default_model = "gpt-5-mini"             # model demezsen bu
models  = ["gpt-5-mini", "gpt-5"]        # serbestce secilebilenler
restricted_models = ["gpt-5-pro"]        # YALNIZCA acikca istenirse
blocked_models    = ["o1-preview"]       # hicbir kosulda
efforts = ["low", "medium", "high"]
effort_required_with_model = false

[agents.codex.model_efforts]             # model basina; `efforts`i ezer
"gpt-5"     = ["low", "medium", "high"]
"gpt-5-pro" = []                         # BOS = --effort kabul etmiyor

[agents.codex.model_effort]              # model basina VARSAYILAN effort
"gpt-5-mini" = "low"

[agents.codex.aliases]                   # serbest metin -> kanonik ad
"mini" = "gpt-5-mini"
"yuksek" = "high"
```

Dört tuzak, dördü de fiilen yaşandı:

1. **Model kimliğini tahmin etme, CLI'a sor.** `agy models` gerçek listeyi
   veriyor ve tahminlerin hepsi yanlıştı (`claude-sonnet-4.6` değil
   `claude-sonnet-4-6`). CLI'ın böyle bir alt komutu yoksa tek tek dene.
2. **Effort ajan geneli olmayabilir.** agy'de `gemini-3.1-pro`'nun `medium`'u
   yok, Claude/GPT-OSS modelleri `--effort` verilirse **exit 1** ediyor. Bunu
   `model_efforts` ile ifade et; boş liste "bayrağı hiç ekleme" demektir.
3. **Alias yazmadan önce normalizasyonu hatırla.** Eşleştirme küçük harfe
   indirir, nokta/tire/alt çizgiyi ayıraç sayar ve harf-rakam sınırını böler:
   `"Gemini 3.6 Flash" == "gemini-3.6-flash"`, `"Opus5" == "opus 5"`. Alias
   yalnızca *gerçekten farklı* adlar için gerekli (`"flash"`, `"pro"`).
4. **Sistemd birimine `ANTHROPIC_MODEL` / `CLAUDE_CODE_EFFORT_LEVEL` ekleme.**
   İşler `os.environ.copy()` ile başlıyor (`jobs.py`), yani birimin ortamı
   ajanlara aynen geçiyor ve `CLAUDE_CODE_EFFORT_LEVEL` `--effort` bayrağını
   **sessizce** etkisiz kılıyor.

`config.py` içindeki `_check_agents()` bu hataların çoğunu **yüklemede**
yakalayıp servisi açık bir mesajla durdurur; sessiz yanlış davranıştansa
açık hata iyidir.

---

## Protokol tuzakları

İlk kurulumda saatlerimizi alan üç sorun. Kodda düzeltildiler; kaldırma.

### 1. `issuer` sonunda eğik çizgi olmamalı

RFC 8414, `issuer` değerinin keşif adresinden `/.well-known/...` çıkarılınca
kalan değere **birebir** eşit olmasını ister. Pydantic'in `AnyHttpUrl` tipi
`https://host` adresini `https://host/` yapıyor. Google bunu görünce keşfi ve
yetkilendirmeyi tamamlıyor ama token adımına hiç geçmiyor — sessiz başarısızlık.

`server.py` içindeki `MetadataNormalizer` ara katmanı düzeltiyor.

### 2. MCP SDK'sı Basic auth'ta `client_id`'yi yanlış yerde arıyor

SDK'nın `ClientAuthenticator` sınıfı, istemci `client_secret_basic` yöntemiyle
kayıtlı olsa bile `client_id`'yi **form gövdesinde** arıyor. Google ise
RFC 6749 §2.3.1'e uyup kimliği yalnızca `Authorization: Basic` başlığında
gönderiyor → "Missing client_id" → 401.

`server.py` içindeki `BasicAuthFormShim` başlıktaki değerleri gövdeye kopyalıyor.
SDK'yı güncellersen bu düzeltmenin hâlâ gerekli olup olmadığını test paketinin
16. bölümü söyler.

### 3. Google başarısızlığı önbelleğe alıyor

Art arda birkaç 401'den sonra Google token adımını denemeyi tamamen bırakıyor.
Loglarda `consent_granted` görürsün ama `POST /token` hiç gelmez — sunucu
tarafında hiçbir hata yokken sonsuza kadar aynı ekranda kalırsın.

Çözüm: **gemini.google.com çerezlerini temizle**, sayfayı yenile, baştan ekle.
Sunucu tarafında bir şey düzelttikten sonra hâlâ aynı hatayı alıyorsan ilk
yapacağın şey bu olsun.

### Ayrıca

- Spark yalnızca **HTTPS + Streamable HTTP** kabul ediyor, `localhost` olmaz
- **OAuth 2.1** şart; düz bearer token kabul edilmiyor
- Kendi makinenden `curl https://<makine>.ts.net/...` **aldatıcıdır** —
  MagicDNS adı tailnet IP'sine çevirir, istek tünele hiç uğramaz ve hep
  başarılı görünür. Gerçek test için adı harici DNS'ten çözüp `--resolve`
  kullan; `doctor.sh` bunu zaten yapıyor.

---

## Hata ayıklama araçları

```bash
./doctor.sh                    # 7 başlıkta tam tanı
./capture.sh                   # bir bağlantı denemesini kaydet ve özetle
journalctl --user -u pcbridge -f
```

`config.toml` içinde:

```toml
[auth]
manual_redirect = true    # parola sonrası otomatik dönme, tıklanabilir bağlantı göster
```

Bu mod, karşı tarafın (Gemini'nin) hata sayfasını görmeni sağlar. Normalde
popup anında kapandığı için hata mesajı okunamıyor. **İşin bitince `false` yap.**

`/token` istekleri her zaman loglanıyor: gövdede hangi alanların geldiği,
Basic köprüsünün devreye girip girmediği ve 200 dışı yanıtlarda tam hata
gövdesi. Bir OAuth sorununda ilk bakacağın yer burası.

---

## Değişiklikten sonra kontrol listesi

- [ ] `./.venv/bin/python tests/test_models.py` → hepsi geçiyor (sunucu gerekmez)
- [ ] `./.venv/bin/python tests/test_e2e.py` → hepsi geçiyor
- [ ] Yeni ayar varsa `config.example.toml`'a yorumuyla eklendi
- [ ] `config.example.toml` değiştiyse `config.toml` da aynı hizaya getirildi
- [ ] Docstring İngilizce ve "ne zaman kullanılır" içeriyor
- [ ] `readOnlyHint` / `destructiveHint` doğru
- [ ] Uzun işler bloklamıyor, iş kimliği dönüyor
- [ ] `systemctl --user restart pcbridge`
- [ ] Gemini'de araç listesi yenilendi (görünmüyorsa kaldır–ekle)
