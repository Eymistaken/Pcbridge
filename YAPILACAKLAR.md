# YAPILACAKLAR.md — GNOME eklentisi: ajan görünür olsun

## Başla

Kullanıcı "YAPILACAKLAR.md'yi uygula" dediyse:

1. Bu dosyanın tamamını oku
2. `CLAUDE.md`'yi oku — proje bağlamı ve değişmez kurallar orada
3. Kendi görev listeni çıkar, **kullanıcıya onaylat**
4. Onay gelmeden kod yazma
5. Adım adım ilerle, **her adımı fiilen test et** ("hata vermedi" kanıt değil)
6. Bölüm bitince commit at

**Teknik tasarım sana ait.** Bu dosya *ne olması gerektiğini* söylüyor, *nasıl
yapılacağını* değil. Araştırmayı, mimariyi ve yol seçimini sen yapacaksın;
kararlarını gerekçesiyle kullanıcıya sunacaksın.

---

## İş

**GNOME 46 (Wayland) için görsel bir kabuk eklentisi yaz.**

pcbridge bir ajana klavye, fare ve ekran erişimi verebiliyor. Bugün bunun tek
görünür işareti GNOME'un kendi ekran paylaşımı göstergesi — üst çubukta küçük
turuncu bir simge. Yeterince belirgin değil.

İstenen: **ajan makineyi kullanırken bunu bakar bakmaz anlamak.**

---

## Ne yapacak

### 1. Ekran kenarlarında çerçeve efekti

Ekran yayını açıkken her monitörün kenarlarında **hafif, beyaz, gradyanlı bir
çerçeve** belirsin. Kenardan içeri doğru yumuşakça sönen bir parlaklık — keskin
çizgi değil.

- Yayın açılınca **yumuşakça belirsin**, kapanınca **yumuşakça kaybolsun**
- İki monitörde de görünsün
- Altındaki pencerelere tıklamayı engellemesin, dikkat dağıtmasın

### 2. Değişen fare imleci

Ajan makineyi kullanırken imleç, **yumuşak köşeli, hafif parlayan** bir imlece
dönüşsün. Normal ok imleci gibi sert değil; yumuşak, hafif ışıldayan.

Ajan bıraktığında normal imlece geri dönsün.

### 3. İmleç yöne dönsün

İmleç hareket ederken **ucu gittiği yöne baksın**. Sol üste gidiyorsa ucu sol
üste, sağ alta gidiyorsa sağ alta. Dönüş ani olmasın — takip etsin, yumuşakça
dönsün.

---

## Kısıtlar

Bunlar tartışmaya kapalı:

- **Tamamen görsel.** Eklenti hiçbir şeye tıklamaz, hiçbir şey yazmaz, hiçbir
  şeyi değiştirmez. Yalnızca gösterir.
- **Yer kaplamayacak.** Animasyonlar akıcı olacak ama makineyi yormayacak.
  CPU/GPU maliyeti ölçülecek ve rapor edilecek — "hafif görünüyor" yetmez.
- **Yumuşak ve tatlı.** Ani geçiş, titreme, göze batan hareket yok. Efektler
  şık olacak, rahatsız etmeyecek.
- **GNOME 46 / Wayland.** Bu makinede çalışacak (Zorin OS 18.1). X11 hedef
  değil.

---

## Bilmen gerekenler

Bu makine hakkında ölçülmüş gerçekler `CLAUDE.md`'de. Bu iş için önemli olanlar:

- **İki monitör**, 1920×1080, yan yana. Tuval 3840×1080. Numaralandırma soldan
  sağa: DP-2 (x=0) → monitör 1, DP-1 (x=1920, **birincil**) → monitör 2. GNOME
  üst çubuğu sağdaki monitörde.
- **Ekran yayını** `desktop_unlock` ile açılıyor, `desktop_lock` ya da izin
  süresi dolunca kapanıyor. Yayın `pcbridge/desktop/screencast.py` ve
  `screencast_helper.py` tarafından yönetiliyor.
- **GNOME 46 bazı D-Bus arayüzlerini dışarıya kapatmış** (`Shell.Introspect`,
  `Shell.Screenshot` — ikisi de "Access denied"). Eklentinin pcbridge'in
  durumunu nasıl öğreneceği bir tasarım sorusu; hazır bir yol olduğunu varsayma,
  önce ölç.
- **`Shell.Eval` kapalı** (GNOME 41+ unsafe mode). Eklenti gerçekten kurulacak,
  kabuğa kod enjekte edilmeyecek.

---

## Çalışma tarzı

- **Ölçmeden yazma.** GNOME 46'da neyin mümkün olduğunu tahmin etme; dene, gör,
  sonra karar ver. Bu projede "hata vermedi" kanıt sayılmıyor.
- **Bu makinede test ediyorsun.** Eklenti kullanıcının kendi masaüstünde
  çalışacak. Bozuk bir eklenti GNOME kabuğunu düşürebilir — Wayland'de bu bütün
  pencereleri kapatır. Yükleme ve etkinleştirme adımlarını kullanıcıya söyleyerek
  yap, geri alma yolunu önce hazırla.
- **Kullanıcı görsel sonucu kendisi onaylayacak.** Efektin "yumuşak" ya da
  "tatlı" olup olmadığını ölçemezsin; göster, sor, düzelt.
- Bölüm bitince commit at, push için onay iste.

---

## Kapsam dışı

- Eklentinin ayar arayüzü (gerekirse sonra)
- extensions.gnome.org'a yayımlama
- GNOME 46 dışındaki sürümler
- pcbridge'in kendi davranışını değiştirmek — bu iş **yalnızca görsel katman**
