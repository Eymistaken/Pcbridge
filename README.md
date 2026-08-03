# pcbridge

Bu ZorinOS makinesini **bir MCP sunucusu olarak** dışarı açar: bağlanan ajan
terminaldeki kodlama ajanlarına iş verebilir, tmux oturumuna yazabilir, kabuk
komutu çalıştırabilir, dosya okuyup yazabilir ve — izin verilirse — masaüstünü
fiilen sürebilir (klavye, fare, ekran okuma).

İki bağlanma yolu var ve **ikisi de aynı sunucu**:

```
  YEREL (stdio)                          UZAK (HTTP + OAuth)
  Claude Code · Codex · Claude Desktop   Gemini Spark · telefon
        │                                        │
        │ sunucuyu istemci başlatır              │ OAuth 2.1 + HTTPS
        │ ağ yok, OAuth yok                      ▼
        │                                 Tailscale Funnel
        │                                        │
        ▼                                        ▼
   python -m pcbridge.server --stdio     127.0.0.1:8765  pcbridge (systemd)
        └────────────────┬───────────────────────┘
                         │
      ┌──────────────────┼──────────────────┬───────────────────┐
      ▼                  ▼                  ▼                   ▼
 claude -p "..."   tmux oturumu    shell / dosyalar     masaüstü (uinput,
 (arka plan işi)  (canlı terminal)                       AT-SPI, ekran)
```

Kurulum komutlarını `./connect.sh` üretir.

**Proje Gemini Spark için tasarlandı ve o yol hâlâ birinci sınıf.** Ama artık
tek istemci o değil: Claude Code ve Codex MCP araç sonucundaki **görüntüyü
okuyabiliyor**, Spark okuyamıyor. Bu fark mimaride birkaç yerde görünür —
aşağıda geçtikçe belirtiliyor.

---

## MCP tam olarak nedir (kısa versiyon)

MCP, bir modelin dış dünyaya uzanmasını sağlayan standart bir **araç kataloğu
protokolü**. Sunucu "bende şu araçlar var, şu parametreleri alır" diye bir liste
yayınlar (`tools/list`), model uygun olanı seçip çağırır (`tools/call`), sunucu bir
metin döner. Hepsi bu. Sihir yok — asıl iş senin yazdığın araç fonksiyonlarında.

Buradaki 33 araç şu gruplara ayrılıyor:

| Grup | Ne yapar |
|---|---|
| **Ajan** | `list_agents`, `agent_run`, `computer_task` — Claude Code / Antigravity'ye prompt gönderir |
| **İş takibi** | `job_status`, `job_output`, `job_list`, `job_cancel` — uzun işleri izler |
| **Canlı terminal** | `tmux_start/send/keys/capture/list/kill` — açık bir terminale yazar |
| **Sistem** | `shell_run`, `shell_run_background`, `fs_list/read/write/search`, `system_status`, `notify` |
| **Masaüstü** | `desktop_unlock/lock`, `mouse`, `keyboard`, `computer_batch`, `screen_info`, `screen_capture`, `ui_dump/click/set_text`, `window_list/focus` |

Ajan çağrıları senkron değil: `agent_run` işi başlatır ve bir `job_id` döner
(istersen `wait_seconds` kadar bekler). Bu önemli, çünkü Claude Code bir görevde
10 dakika harcayabilir ve hiçbir HTTP isteği o kadar açık kalamaz.

---

## Neden bu kadar dolambaçlı: Spark'ın üç şartı

Araştırınca çıkan gerçekler:

1. **HTTPS + Streamable HTTP zorunlu.** Spark Google'ın bulutunda çalışıyor,
   `localhost` diye bir şey bilmiyor. Makinen dışarıdan erişilebilir olmalı →
   Tailscale Funnel.
2. **OAuth 2.1 zorunlu.** Düz "Bearer <token>" kabul etmiyor. Bu yüzden pcbridge
   kendi içinde tam bir mini OAuth sunucusu barındırıyor (DCR + PKCE + refresh).
   Sen sadece bir parola giriyorsun; gerisi otomatik.
3. **Resmî olarak ABD, İngilizce ve kişisel Google hesabı.** Ayrıca Keep Activity
   açık olmalı. Spark sende zaten çalışıyorsa sorun yok; araç açıklamalarını da
   bu yüzden İngilizce yazdım (Gemini aracı seçerken bu metinleri okuyor).

Ek olarak Google, **yazma işlemlerinde her seferinde onay soruyor**. Yani
`agent_run` çağrıldığında telefonunda bir onay çıkacak. Bu can sıkıcı ama
aslında iyi bir güvenlik ağı.

---

## Kurulum

### 1. Tailscale (yoksa)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Funnel'ı kullanabilmek için tailnet ayarlarında **HTTPS sertifikaları** ve
**Funnel** açık olmalı: <https://login.tailscale.com/admin/dns> → "HTTPS
Certificates: Enable", ve Access Controls içinde `nodeAttrs` altında
`funnel` yetkisi. Tailscale ilk `funnel` komutunda eksik olanı zaten söylüyor.

### 2. pcbridge

```bash
sudo tailscale set --operator=$USER      # tünel komutları sudo istemesin
cd ~/Belgeler/mcp_server
chmod +x *.sh
./install.sh
source ~/.bashrc
```

`install.sh` şunları yapar: sanal ortamı kurar, rastgele bir **parola** üretip
ekrana basar (kaydet), Tailscale adını okuyup `public_url`'i otomatik doldurur,
systemd birimini tanımlar (**açılışta başlamaz**) ve alias'ları `~/.bashrc`'ye ekler.

### 3. Aç

```bash
sparkac
```

Sunucuyu başlatır, tüneli açar, dışarıdan erişilebilir mi diye kontrol eder ve
Spark'a gireceğin adresi ekrana yazar. Kapatmak için `sparkkapat`, durum için
`sparkdurum`, ayrıntılı tanı için `./doctor.sh`.

### 4. Spark'a bağla

Bu adım **sadece bilgisayardaki web arayüzünden** yapılabiliyor (telefondan değil):

1. <https://gemini.google.com> → alt taraftaki **Settings & help** → **Connected Apps**
   (görünmüyorsa önce **Personal Intelligence** → **Connected Apps**)
2. "Custom apps for Spark" altında **Add a custom app**
3. Adres olarak şunu gir:
   ```
   https://thinkstation.tailXXXX.ts.net/mcp
   ```
4. Karşına pcbridge'in koyu temalı **onay sayfası** çıkacak → kurulumda aldığın
   parolayı gir → **Onayla ve bağlan**

Bağlandıktan sonra telefondaki Gemini uygulamasında da kullanılabilir hale gelir.
Bu adımı **bir kez** yaparsın; sonrasında sadece `sparkac` / `sparkkapat`.

### 5. Yerel istemcilere bağla (Claude Code, Codex, Claude Desktop)

```bash
./connect.sh            # komutları yazdırır
./connect.sh --apply    # claude / codex kayıtlarını yapar
```

Yerel istemciler sunucuya **stdio** ile bağlanır: tünel yok, `sparkac` gerekmez,
sunucuyu istemcinin kendisi başlatır. Claude Code için tek komut:

```bash
claude mcp add pcbridge -- /YOL/Pcbridge/.venv/bin/python -m pcbridge.server --stdio
```

Claude Desktop'ın yapılandırma dosyası (`~/.config/Claude/claude_desktop_config.json`)
**elle** düzenlenir — `connect.sh` eklenecek JSON parçasını basar ama dosyanın
üstüne yazmaz, içinde başka ayarların var.

> **Codex:** komutlar üretiliyor ve yapılandırmaya doğru yazıldığı `codex mcp get
> pcbridge` ile doğrulandı, ama **bu makinede denenmedi** — Codex aboneliği yok.
> Gerçekten bağlanıp araçları aldığı ve görüntü bloğunu işlediği ölçülmemiştir.

⚠️ **stdio'da kimlik doğrulama yoktur.** Ayrıntı: [Güvenlik](#güvenlik--dürüst-değerlendirme).

---

## Günlük kullanım

| Komut | Ne yapar |
|---|---|
| `sparkac` | Sunucuyu başlatır + tüneli açar + dışarıdan erişimi doğrular |
| `sparkkapat` | Tüneli kapatır + sunucuyu durdurur (dışarıya tamamen kapanır) |
| `sparkdurum` | Sunucu / tünel / dış erişim durumu |
| `./doctor.sh` | Bir şey çalışmıyorsa ayrıntılı tanı |
| `journalctl --user -u pcbridge -f` | Canlı log |

Bilgisayarı açtığında hiçbir şey kendiliğinden başlamaz. Kullanmak istediğinde
terminale `sparkac` yazarsın, işin bitince `sparkkapat`. Terminali kapatman
sorun değil — sunucu systemd altında arka planda çalışmaya devam eder;
`sparkkapat` diyene kadar açık kalır.

`sparkkapat` çalışan işleri öldürmez, sadece dışarıdan erişimi keser. Arka
plandaki bir Claude Code görevi devam eder, sonucunu bir dahaki `sparkac`'ta
`job_list` ile görebilirsin.

---

## Belgeler

- **[KULLANIM.md](KULLANIM.md)** — 20 aracın tamamı ve telefondan yazabileceğin
  gerçek örnek cümleler
- **[GELISTIRME.md](GELISTIRME.md)** — yeni araç eklemek, ajan tanımlamak,
  Spark'a özgü kurallar ve yaşadığımız protokol tuzakları

## Kullanım örnekleri

Telefondan Spark'a yazabileceklerin:

> "pcbridge ile `~/projeler/site` klasöründe claude'a şunu yaptır: login formundaki
> validasyon hatasını bul ve düzelt."

> "Az önceki işin durumu ne?"

> "Bilgisayarın durumunu göster — disk ve GPU dahil."

> "`~/İndirilenler` klasöründe 500 MB'tan büyük ne var?"

> "Açık terminal oturumlarını listele, `cc` oturumunun ekranını göster."

> "Claude bir onay soruyor galiba — `cc` oturumunda ekrana bak, uygunsa Enter'a bas."

Son örnek `tmux_keys` sayesinde çalışıyor: PC'de açık duran gerçek bir Claude Code
oturumuna telefondan tuş gönderiyorsun. Bilgisayarın başına geçince
`tmux attach -t cc` diyip aynı oturumdan devam edebilirsin.

---

## Ajan komutlarını kendine göre ayarla

`config.toml` içindeki `[agents.*]` blokları serbest. Claude Code için varsayılan:

```toml
[agents.claude]
command = ["claude", "-p", "{prompt}", "--output-format", "stream-json",
           "--verbose", "--dangerously-skip-permissions"]
resume_args = ["--resume", "{session_id}"]
parser = "claude_stream_json"
```

`--dangerously-skip-permissions` olmadan Claude Code arka planda onay bekler ve iş
asılı kalır. Tam erişim istediğin için varsayılan böyle; rahatsız olursan çıkar,
ama o zaman `tmux_*` araçlarıyla onayları elle vermen gerekir. Bu taraf hazır,
dokunman gereken bir şey yok.

### Antigravity (agy) tarafında iki tuzak vardı

**1. Komutun adı `antigravity` değil, `agy`.** Paket adı `antigravity-cli`,
çalıştırılabilir dosya `agy`. Düzeltildi.

**2. `agy -p` çıktısını yalnızca gerçek bir terminale yazıyor.** Bu bilinen bir
hata ([upstream issue #76](https://github.com/google-antigravity/antigravity-cli/issues/76)):
boruya, dosyaya veya alt sürece yönlendirildiğinde exit kodu 0 dönüyor ama
**sıfır bayt** çıktı veriyor. pcbridge tam olarak böyle çalıştığı için bu hata
projeyi sessizce işe yaramaz hale getirirdi.

Çözüm: `config.toml`'da `pty = true`. Komut `script` ile sahte bir terminale
sarılıyor, çıktı kurtarılıyor. Test ettim — aynı CLI `pty = false` iken boş
dönüyor, `pty = true` iken tam çıktı geliyor.

```toml
[agents.antigravity]
command = ["agy", "-p", "{prompt}", "--dangerously-skip-permissions"]
resume_args = ["--conversation", "{session_id}"]
parser = "plain"
pty = true
```

**Senin yapman gereken tek şey:** `agy` kurulu mu diye bakmak.

```bash
command -v agy && agy --version
```

Çıkmazsa ya kur ya da `config.toml`'da `[agents.antigravity]` altında
`enabled = false` yap — Claude Code tek başına sorunsuz çalışır.

Aynı kalıpla `codex`, `gemini` gibi başka CLI'lar da ekleyebilirsin; çıktısını
yutan bir CLI'ya denk gelirsen `pty = true` yeter.

---

## Güvenlik — dürüst değerlendirme

Bu sunucu **kısıtsız**: verdiğin yetkiyle her komut çalışır, her dosya okunup
yazılır. Kapı, hangi yoldan bağlandığına göre değişiyor.

### stdio bir güvenlik gerilemesidir — bilinçli

HTTP yolunda üç kat var: **Tailscale ağı** (makineye ulaşabilmek) + **OAuth 2.1**
(parola) + masaüstü için **`desktop_unlock`**. stdio **ilk ikisini kaldırır.**

Orada yetki, süreci başlatabilmenin kendisidir: `python -m pcbridge.server --stdio`
komutunu çalıştırabilen her yerel program pcbridge'in bütün araçlarına erişir.
Parola sorulmaz, token istenmez, `audit.log` yazılır ama kimse durdurmaz.

Bunu kabul etmenin gerekçesi şu: **o eşiği zaten geçmiş birinin pcbridge'e
ihtiyacı yok.** Senin kullanıcınla kod çalıştırabilen biri `claude -p` de
çağırabilir, `~/.ssh`'i de okuyabilir. stdio yeni bir kapı açmıyor, var olan
kapının arkasındakini daha kullanışlı hale getiriyor. Yine de fark gerçek ve
bilinerek kabul edildi:

| | HTTP (Spark, uzak) | stdio (yerel istemci) |
|---|---|---|
| Ağ katmanı | Tailscale Funnel | **yok** |
| Kimlik doğrulama | OAuth 2.1 + parola | **yok** |
| Masaüstü kapısı | `[desktop] enabled` + `desktop_unlock` | aynen geçerli |
| Denetim kaydı | `audit.log` | aynen geçerli |
| Ekran görüntüsü | `/shot/<token>.png` bağlantısı | araç sonucunda **görüntünün kendisi** |

Yerel istemciyi kısıtlamak istersen `[desktop] enabled = false` bırakmak
masaüstünü kapatır ama **`shell_run`, `agent_run`, `fs_*` ve `tmux_*` o kapıdan
geçmez** — onlar masaüstü kapalıyken de çalışır. Bu bilinçli: koruma engelleme
değil, iz bırakma.

### HTTP yolunda geçerli olanlar

- **Adresin gizli değil.** `*.ts.net` adresleri sertifika şeffaflık günlüklerinde
  (CT logs) herkese açık listelenir. "Beni bulamazlar" varsayımına güvenme —
  koruma parolanın uzunluğundan geliyor, adresin gizliliğinden değil.
- Parolayı **kimseyle paylaşma**, başka yerde kullandığın bir parola olmasın.
  `install.sh` zaten rastgele üretiyor; öyle bırak.
- 8 yanlış denemeden sonra o IP 15 dakika kilitleniyor (`config.toml`'dan ayarlanır).
- Tüm yetkilendirme olayları `~/.local/state/pcbridge/audit.log` dosyasına yazılıyor.
  Ara sıra bak.
- Şüphelenirsen: `rm ~/.local/state/pcbridge/oauth.db && systemctl --user restart pcbridge`
  → tüm tokenlar ölür, Spark'ın yeniden yetki alması gerekir.
- Tüneli tamamen kapatmak: `sudo tailscale funnel --bg off` veya `tailscale funnel reset`.

Daha sıkı istersen: `config.toml`'a bir izinli klasör listesi eklemek 20 satırlık
bir iş, söyle ekleyeyim.

### Masaüstü kontrolü (`[desktop]`) — ayrı bir risk sınıfı

`[desktop] enabled = true` yaptığın anda bu sunucu bilgisayarını **kullanabilir**
hale geliyor: sanal bir klavye ve fare yaratıp tıklıyor, yazıyor. Bu, "uzaktan
komut çalıştırma"dan daha geniş bir yetki — çünkü komut çalıştırmanın erişemediği
şeylere erişiyor:

- **Oturum açmış olduğun her şey.** Tarayıcında açık bankacılık sekmesi, parola
  yöneticin, e-postan. Girdi, senin klavyeni kullanmakla aynı şey; hiçbir
  uygulama aradaki farkı göremez.
- **Klavye simülasyonu izin sormaz.** uinput çekirdek seviyesinde çalıştığı için
  Wayland'in izin mekanizmaları devreye girmiyor. Koruma tamamen pcbridge'in
  kendi kapısında (`desktop_unlock` süreli izni, ekran kilidi kontrolü, idle
  koruması, hız sınırı, `audit.log`).
- **Ekran görüntüsü ayrıca okunuyor.** `screen_capture` ekranda ne varsa onu
  yakalar: açık mesajlar, e-posta, ekranda görünen parolalar. O yüzden o da
  `desktop_unlock` istiyor. Tek gevşetme, "makinenin başındasın" korumasının
  ekran görüntüsüne uygulanmaması — başında olman ekranına bakmanı engellememeli.
- **`ui_dump` ekranı metin olarak okuyor** — düğme etiketleri, menü öğeleri,
  metin kutularının içeriği. Görüntü kadar açık edici, o yüzden aynı kapıdan
  geçiyor. `ui_set_text` ise metin kutularına doğrudan yazıyor; yazılan metnin
  **kendisi denetim kaydına düşmüyor** (parola girilmiş olabilir), yalnızca
  karakter sayısı.
- **Ekran görüntüsü bağlantısı OAuth'un DIŞINDA.** `https://<host>/shot/<token>.png`
  adresini açmak için parola ya da token istenmiyor; yetki, adresteki 128 bitlik
  token'ın kendisi. Sebebi pratik: telefonun tarayıcısında görüntüyü açabilmen
  gerekiyor ve Gemini görseli gösteremiyor. Bu yüzden **bağlantıyı kimseye
  iletme**; 5 dakika sonra kendiliğinden ölüyor, PNG'ler de 24 saat içinde
  siliniyor (`[desktop] shot_ttl_seconds` / `shot_keep_hours`).
- **`enabled = false` varsayılanı bilinçli.** Açmadan önce "telefonumu
  kaybedersem ne olur" sorusuna cevabın olsun. Tek koruma yine OAuth parolası.

Bu yüzden varsayılan akış: kapalı gelir, `sudo ./setup_uinput.sh` bir kez
çalıştırılır, `config.toml`'da açılır, ve her kullanım öncesi `desktop_unlock`
ile **süreli** izin verilir. İzin kendiliğinden kapanır.

> **`enabled = false` ne KAPATMAZ.** Yalnızca yukarıdakileri kapatır — sanal
> klavye/fare ve ekran okuma. `shell_run`, `agent_run`, `fs_*` ve `tmux_*` bu
> kapıdan geçmez; masaüstü kontrolü kapalıyken de komut çalışır, uygulama
> açılır, dosya okunur. Gemini ekranı bile okuyabilir: `agent_run` ile
> makinedeki bir ajanı çalıştırıp ona ekran görüntüsü aldırarak (ölçüldü
> 2026-08-02). Dolayısıyla `config.toml` da okunabilir, yani parolan.
> Engellemedik çünkü `shell_run` keyfi komut çalıştırdığı sürece engel gerçek
> olmazdı; onun yerine **iz bırakılıyor** — her `shell_run`, `fs_read`,
> `agent_run`, `tmux_send` çağrısı `audit.log`'a düşüyor (ne yapıldığı yazılır,
> içerik yazılmaz). Araç-izin haritasının tamamı `KULLANIM.md`'de.

### `computer_task` — en büyük yetki artışı

Yukarıdaki her şeyde **komutu Gemini veriyor**: nereye tıklanacağını,
ne yazılacağını o söylüyor, pcbridge uyguluyor. `computer_task` bu zinciri
kırıyor — görevi makinendeki bir ajana devrediyor ve **kararı o veriyor**.
Ekran görüntüsüne bakıp nereye tıklayacağını kendisi seçiyor, sonucu kendisi
değerlendiriyor, gerekirse tekrar deniyor. Yani gözü ve elleri olan, kendi
kararıyla hareket eden bir süreç, senin açık oturumunun içinde.

Bunu yumuşatmanın anlamı yok: bir hedef cümlesi veriyorsun ("şu kişiye şunu
yaz") ve aradaki bütün adımları başka bir model seçiyor. Yanlış pencereye
tıklaması, yanlış kişiye yazması, yanlış düğmeye basması mümkün. Geliştirme
sırasında ikisi de yaşandı: doğrulanmamış bir tıklama masaüstündeki 23 öğeyi
çöpe gönderdi (2 Ağustos), bayatlamış bir ekran görüntüsüne göre yapılan bir
tıklama başka uygulamaya düştü (3 Ağustos). Her ikisinin de karşılığı koda
girdi, ama koruma **hasarı sınırlamak** için; hatayı sıfırlamıyor.

Karşılığında aldığın şey gerçek: erişilebilirlik ağacını yayınlamayan
uygulamalarda (Discord, VS Code, oyunlar) başka yol yok. Ölçüldü — Vesktop'ta
`ui_dump` sıfır düğüm döndürüyor.

> **Bu aracın gerekçesi daraldı.** Var oluş sebebi, Spark'ın araç sonucundaki
> görüntüyü görememesiydi: birinin ekrana bakması gerekiyorsa o biri makinedeki
> ajan olmalıydı. Claude Code görüyor (ölçüldü), yani gören bir istemci
> `screen_capture` + `computer_batch` ile işi kendisi yapabilir — arada ikinci
> bir model olmadan, daha ucuza, daha denetlenebilir şekilde.
>
> Bugün geriye kalan gerekçesi: **uzun süren** bir GUI işini arka plana atmak.
> Ana ajan bloke olmasın diye. Gören bir istemcideysen ve iş kısaysa bu araca
> ihtiyacın yok.

Aynı beş kat koruma burada da geçerli, bir farkla: "kullanıcı makinede"
kontrolü **görev başına** yapılıyor, eylem başına değil. Sebebi ölçülmüş —
pcbridge'in kendi tuşu o sayacı sıfırlıyor, dolayısıyla eylem başına kontrol
ajanı kendi ilk tuşu yüzünden durdururdu. Ekran kilidi, `[desktop] enabled` ve
süreli izin ajanın **her** eyleminde okunuyor; bu yüzden `desktop_lock`
telefondan verilince ajanın elleri bir sonraki eylemde duruyor.

Ajanın attığı her tıklama `audit.log`'a görev kimliğiyle yazılıyor. Bu süs
değil: `agy` çıktısında adım listesi vermiyor, yani o sürücüyle "ajan ne yaptı"
sorusunun tek dürüst cevabı denetim kaydı. (Varsayılan sürücü artık `claude`;
`agy` isteyerek seçilebilir, `[agents.antigravity]` bloğu duruyor.)

Acil durdurma (kaçak bir döngü ihtimaline karşı):

```bash
systemctl --user stop pcbridge
```

Bu komut **çalışan ajan işlerini de öldürüyor** — ölçüldü 2026-08-03. İşler
`start_new_session` ile ayrı oturum grubunda başlasa da servisin cgroup'unda
kalıyor ve systemd hepsini alıyor. Aynı sebeple `restart` de öldürür: kod
değiştirip yeniden başlatmadan önce `job_list` ile bakmakta fayda var.

Yalnızca izni kapatmak (işler devam etsin, elleri dursun):

```bash
desktop_lock
```

Süreç ölünce sanal klavye/fare cihazı da yok olur. Geri almak için:
`config.toml`'da `enabled = false`, ya da tamamen:
`sudo rm /etc/udev/rules.d/60-pcbridge-uinput.rules` (geri alma komutlarının
tamamı `setup_uinput.sh` dosyasının başında).

---

## Sorun giderme

### Gemini "hesap bağlamanız gerekir" diyorsa

Bu üç sorun üst üste bindiği için ilk kurulumda uzun sürdü. Üçü de çözüldü,
ama tekrar karşılaşırsan sıra şu:

1. **Google başarısızlığı önbelleğe alıyor.** Birkaç kez 401 aldıktan sonra
   token adımını denemeyi tamamen bırakıyor — loglarda `consent_granted`
   görürsün ama `POST /token` hiç gelmez. Çözüm: gemini.google.com çerezlerini
   temizle (adres çubuğundaki kilit → Çerezler ve site verileri → Sil), sayfayı
   yenile, baştan ekle. Bu adım gerçekten gerekli oldu.
2. **`issuer` sonundaki eğik çizgi.** RFC 8414 keşif adresiyle birebir eşleşme
   ister; pydantic `https://host` → `https://host/` yapıyordu. `MetadataNormalizer`
   düzeltiyor.
3. **MCP SDK'sı `client_secret_basic`'te `client_id`'yi form gövdesinde arıyor.**
   Google ise RFC 6749 §2.3.1'e uyup yalnızca `Authorization: Basic` başlığında
   gönderiyor → "Missing client_id" → 401. `BasicAuthFormShim` başlıktaki
   değerleri gövdeye kopyalıyor.

Teşhis için: `manual_redirect = true` yap (parola sonrası otomatik dönmek
yerine tıklanabilir bağlantı gösterir, karşı tarafın hata sayfasını görebilirsin)
ve `./capture.sh` ile denemeyi kaydet.

### Diğer sorunlar

| Belirti | Bak |
|---|---|
| `sparkac: command not found` | `source ~/.bashrc` (veya yeni bir terminal aç) |
| Spark "couldn't connect" diyor | `sparkac` yaptın mı? Sonra `./doctor.sh` |
| Onay sayfası açılmıyor | `public_url` ile tarayıcıdaki adres birebir aynı olmalı (sonda `/` yok) |
| Parola doğru ama kabul etmiyor | `config.toml` düzenledikten sonra `spark.sh restart` |
| Araçlar görünüyor ama çalışmıyor | `journalctl --user -u pcbridge -f` |
| `claude: command not found` | `systemd/pcbridge.service` içindeki `PATH` satırına npm global bin dizinini ekle, sonra `./install.sh` |
| `agy` boş sonuç dönüyor | `config.toml`'da `pty = true` olduğundan emin ol |
| İş hep `running` kalıyor | `job_output` ile ham çıktıya bak; ajan muhtemelen onay bekliyor |
| Tünel kapanmıyor | `sudo tailscale funnel reset` |

Testleri çalıştırmak (sunucu ayakta iken):

```bash
PCBRIDGE_TEST_BASE=http://127.0.0.1:8765 \
PCBRIDGE_TEST_PASSWORD='<parolan>' \
./.venv/bin/python tests/test_e2e.py
```

---

## Düşündüğün diğer yol: Hermes + Gemini API

Kendi ajanını kurup Gemini API'ye bağlamak da çalışır ama farklı bir şey çözer:
o durumda **kendi ajanını** yazmış olursun ve telefondaki Gemini uygulamasıyla
bağlantısı olmaz — ayrı bir arayüz (Telegram botu, web sayfası) lazım olur.
Buradaki yaklaşımın avantajı, zaten cebinde duran Gemini uygulamasının doğrudan
giriş noktası hâline gelmesi. Dezavantajı, Spark'ın kurallarına (OAuth, onay
istemleri, ABD/İngilizce) tabi olman.

İkisi birbirini dışlamıyor: aynı MCP sunucusuna Claude Desktop, Gemini CLI veya
kendi ajanın da bağlanabilir — `static_token` tam olarak bunun için var:

```bash
curl -H "Authorization: Bearer <static_token>" \
     -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
     https://thinkstation.tailXXXX.ts.net/mcp
```

---

## Dosya düzeni

```
Pcbridge/
├── install.sh              kurulum (venv, config, systemd, alias'lar)
├── setup_uinput.sh         /dev/uinput izinleri — sudo, bir kez
├── spark.sh                aç/kapa (sparkac / sparkkapat / sparkdurum)
├── run.sh                  ön planda çalıştır
├── doctor.sh               tanılama
├── config.example.toml     örnek yapılandırma
├── requirements.txt
├── systemd/pcbridge.service
├── tests/
│   ├── test_models.py      model/effort çözümleyici + ajan çıktı ayrıştırıcı
│   ├── test_desktop.py     masaüstü katmanı — sunucusuz, girdi göndermez
│   ├── test_e2e.py         OAuth + MCP uçtan uca (sunucu ayakta olmalı)
│   └── fake_agents/claude  sabit çıktılı sahte ajan (ayrıştırıcı testi için)
└── pcbridge/
    ├── config.py           yapılandırma
    ├── auth.py             OAuth 2.1 sunucusu + onay sayfası
    ├── jobs.py             arka plan işleri + ajan çıktı ayrıştırıcıları
    ├── tmuxctl.py          canlı terminal kontrolü
    ├── models.py           model/effort çözümleyici
    ├── shots.py            ekran görüntüsü bağlantıları (token'lı, süreli)
    ├── tools.py            MCP araçları
    ├── server.py           giriş noktası
    └── desktop/
        ├── monitors.py     monitör tablosu — koordinat uzayının tek kaynağı
        ├── input.py        sanal klavye + mutlak fare (/dev/uinput)
        ├── capture.py      ekran görüntüsü: yakala, kırp, ölçekle
        ├── uitree.py       erişilebilirlik ağacı → metin, kararlı #id'ler
        ├── atspi_helper.py AT-SPI yardımcısı — SİSTEM python3'ü, ayrı süreç
        └── safety.py       süreli izin, kilit/idle kontrolü, denetim kaydı
```
