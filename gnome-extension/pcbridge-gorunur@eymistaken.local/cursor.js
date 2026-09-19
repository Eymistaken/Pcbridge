/* Ajan çalışırken imleci kendi çizdiğimizle değiştirir ve yöne döndürür.
 *
 * VARSAYILAN KAPALI. Bu katman bir kez çıkarıldı (2026-08-04): gerçek
 * makinede FİZİKSEL fareyle tıklamalar basmıyor, fare donuyordu. Arıza
 * yeniden üretilemedi; bütün denemeler sentetik fareyle yapılmıştı ve tek
 * ölçülmemiş fark olay hızıydı. Geri gelirken iki şey değişti:
 *
 *   1. Konum artık HER olayda değil, kare başına bir kez uygulanıyor
 *      (`FrameCoalescer`). Fiziksel fare ~1000 Hz (ölçüldü 2026-09-13),
 *      ekranda görünebilecek en fazla değişiklik ise kare sayısı kadar.
 *   2. Aktör `Main.layoutManager.addTopChrome` yerine `Main.uiGroup`a
 *      ekleniyor. İzlenen bir chrome aktörünün her konum değişimi kabuğun
 *      girdi bölgesi hesabını yeniden kuyruğa sokuyordu; `uiGroup` düz bir
 *      kap ve aktör zaten `reactive: false`.
 *
 * Açmak için işaret dosyası: `~/.local/state/pcbridge/gorunur-imlec`
 * (`touch` aç, `rm` kapat) ya da `PCBRIDGE_GORUNUR_CURSOR=1`. İzin her
 * açıldığında yeniden okunuyor, yani kabuk yeniden başlatmak gerekmiyor.
 * GERÇEK OTURUMDA FİZİKSEL FAREYLE DOĞRULANMADI.
 *
 * ÖLÇÜLDÜ 2026-08-04 (bu makine, GERÇEK oturum, gerçek donanım)
 *     `Meta.CursorTracker.set_pointer_visible(false)` gerçek imleci
 *     gizliyor ve gizli KALIYOR: çağrıdan 6 sn sonra hâlâ `false`,
 *     kompozitörden tek bir geri açma gelmedi. Görsel kanıt: imleç durağan
 *     bir monitöre konup gizli/görünür kareleri karşılaştırıldı — fark tam
 *     olarak imlecin bulunduğu noktada, 13x21 px, ekranda başka hiçbir
 *     piksel değişmedi.
 *
 *     Bu ölçüm YAPILACAKLAR.md'nin "2 ve 3 tek bir soruya bağlı" dediği
 *     soruydu: gizleyemeseydik iki imleç birden görünür ve tema yedeğine
 *     düşmek gerekirdi (yön dönmesi de düşerdi).
 *
 * BEDELİ
 *     Kompozit imleç donanım imleç düzleminden çıkıyor, yani bir kare
 *     gecikme kazanıyor. Ajan çalışırken sorun değil — izin penceresi zaten
 *     kullanıcı makine başında değilken açılıyor (`idle_guard_seconds`) —
 *     ama kullanıcı fareye dokunursa hafif gecikme hisseder.
 *
 * GERİ ALMA
 *     `hold_max_seconds` ile aynı desen: bırakmayı unutmak yasak. Gerçek
 *     imleç `stop()` içinde ve her hata yolunda geri açılır. Gizleme
 *     GNOME oturumu boyunca yaşayan bir durum; eklenti çökerse kabuk
 *     yeniden başlar ve imleç zaten geri gelir (durum diskte tutulmuyor).
 *
 * MALİYET
 *     Olay güdümlü: `position-invalidated` gelmezse hiçbir şey çalışmıyor.
 *     Dönüş yumuşatması yalnızca dönüş sürerken tıklıyor ve hedefe
 *     oturunca kendini durduruyor. İmleç durduğunda maliyet SIFIR.
 */

import Cairo from 'gi://cairo';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {FrameCoalescer} from './frameclock.js';
import {selfTestEnabled} from './selftest.js';

/* Aktör kutusu. Uç TAM ORTADA: dönüş merkezi uç olunca uç imlecin gerçek
 * konumunda sabit kalıyor, gövde arkada savruluyor.
 *
 * Kutu neden gövdeden çok daha büyük: aktör ucun ETRAFINDA dönüyor, yani
 * gövdenin en uzak noktası bir daire çiziyor. Gereken yarıçap
 *     (34² + 16²)^½ ≈ 37,6   +  köşe 2  +  şerit 1,5  +  parıltı 12  ≈ 53
 * Küçük kutuda köşeler dönerken KIRPILIYOR — 64 px ile denendi, şekil çapraz
 * açılarda kesildi. */
const SIZE = 112;
const TIP = SIZE / 2;

/** Yumuşatma katsayısı: her tıkta hedefe kalan farkın bu kadarı kapatılır. */
const TURN_EASE = 0.22;
/** Yumuşatma tıkı (ms). ~60 Hz. */
const TURN_TICK_MS = 16;
/** Bu açının altında kalınca dönüş bitmiş sayılır (derece). */
const TURN_DONE_DEG = 0.4;
/** Yön güncellemesi için gereken en küçük hareket (px). Titremeyi keser. */
const MIN_MOVE_PX = 3;

/** Hiç hareket görmeden önceki duruş: alışıldık ok gibi sol yukarı. */
const DEFAULT_ANGLE = -135;

/* Şekil: uç + geriye süpürülmüş iki kanat + arka çentik (kâğıt uçak / gönder
 * oku). Kullanıcının verdiği görselden çıkarıldı. Uçtan (0,0) göreli, +X
 * yönünü gösteriyor; dönüş bunun üstüne `rotation_angle_z` ile geliyor. */
const SHAPE = [[0, 0], [-34, -16], [-24, 0], [-34, 16]];

/** Köşe yuvarlatma yarıçapı — yuvarlak birleşimli strokla elde ediliyor. */
const CORNER_R = 2.0;
/** Beyaz şeridin gövdeden dışarı taştığı miktar (px). İNCE olmalı. */
const RIM = 1.5;

/* Parıltı: şeridin dışına doğru ne kadar yayıldığı (px), kenardaki en yüksek
 * saydamlık, ve kaç halka ile çizildiği.
 *
 * Dört varyant (20/0,45 · 11/0,45 · 20/0,19 · 12/0,26) yan yana çizilip
 * kullanıcıya gösterildi; "dar ve sönük" olan seçildi. İlk hâli (20/0,45)
 * "fazla geniş" bulundu — şeklin çevresinde beyaz bir yığın gibi duruyordu. */
const GLOW_SPREAD = 12;
const GLOW_PEAK = 0.26;
const GLOW_LAYERS = 16;

/** Belirme/kaybolma. */
const FADE_IN_MS = 260;
const FADE_OUT_MS = 200;

/** İzin bittikten sonra emniyetin devreye girmesi için tanınan pay (sn).
 *  Normal yolda durum izleyicisi çoktan kapatmış olur; bu yalnızca o
 *  gelmediğinde çalışır. */
const DEADLINE_SLACK_S = 10;

/** Katmanı açan işaret dosyası. Ayar arayüzü yok; `touch` ve `rm` yeter. */
export function cursorMarkerPath() {
    return GLib.build_filenamev([
        GLib.get_user_state_dir(), 'pcbridge', 'gorunur-imlec',
    ]);
}

/** Kullanıcı bu katmanı açtı mı? İzin her açıldığında yeniden soruluyor. */
export function cursorEnabled() {
    if (GLib.getenv('PCBRIDGE_GORUNUR_CURSOR') === '1')
        return true;
    return GLib.file_test(cursorMarkerPath(), GLib.FileTest.EXISTS);
}

function derece(rad) {
    return rad * 180 / Math.PI;
}

/** Açıyı (-180, 180] aralığına indirger. */
function sarmala(a) {
    let d = a % 360;
    if (d > 180)
        d -= 360;
    if (d <= -180)
        d += 360;
    return d;
}

/** İki açı arasındaki EN KISA yay (-180, 180]. Dönüşün uzun yoldan
 *  gitmemesi için: 170° -> -170° iki derecelik bir dönüştür, 340'lık değil. */
function kisaYay(a, b) {
    return sarmala(b - a);
}

export class CursorOverlay {
    constructor() {
        this._actor = null;
        this._tracker = null;
        this._posId = 0;
        this._visId = 0;
        this._turnId = 0;
        this._deadlineId = 0;
        this._laters = null;
        // Konumu kare başına bir kez uygula. Zamanlayıcı `start()` içinde
        // bağlanıyor; burada yalnızca alan duruyor.
        this._frames = null;
        this._fadingOut = false;
        this._active = false;
        this._angle = DEFAULT_ANGLE;
        this._target = DEFAULT_ANGLE;
        this._lastX = 0;
        this._lastY = 0;
    }

    get active() {
        return this._active;
    }

    /** Kaç fare olayı geldi, kaçı ekrana yansıdı (ölçüm kipi). */
    get stats() {
        return this._frames?.stats ?? null;
    }

    resetStats() {
        this._frames?.resetStats();
        this._t0 = GLib.get_monotonic_time();
    }

    /** Kendi imlecimizi devreye al: gerçeği gizle, aktörü göster, takibe başla. */
    start() {
        if (this._active)
            return;
        try {
            this._tracker = Meta.CursorTracker.get_for_display(global.display);
            this._laters = global.compositor.get_laters();
        } catch (error) {
            console.error(`[pcbridge-gorunur] CursorTracker alınamadı: ${error}`);
            this._tracker = null;
            return;     // gerçek imleç dokunulmadan duruyor — güvenli taraf
        }

        this._buildActor();
        if (!this._actor) {
            // Aktör yoksa gerçek imleci GİZLEME. Yoksa kullanıcı hiç imleçsiz
            // kalırdı — iki imleçten çok daha kötü bir sonuç.
            console.error('[pcbridge-gorunur] imleç aktörü kurulamadı, ' +
                'gerçek imleç gizlenmiyor');
            this._tracker = null;
            return;
        }

        const [px, py] = global.get_pointer();
        this._lastX = px;
        this._lastY = py;
        this._angle = DEFAULT_ANGLE;
        this._target = DEFAULT_ANGLE;
        this._place(px, py);
        this._actor.rotation_angle_z = this._angle;

        this._active = true;
        this._debug = selfTestEnabled();
        this._t0 = GLib.get_monotonic_time();
        this._frames = new FrameCoalescer(() => this._applyPointer(), {
            schedule: (run) => this._laters.add(Meta.LaterType.BEFORE_REDRAW, () => {
                run();
                return GLib.SOURCE_REMOVE;
            }),
            cancel: (id) => this._laters.remove(id),
        });

        // Gizlemeden ÖNCE bağlan: kompozitör imleci geri açarsa duyalım.
        this._visId = this._tracker.connect('visibility-changed',
            () => this._guarded(() => this._onVisibilityChanged()));
        this._posId = this._tracker.connect('position-invalidated',
            () => this._guarded(() => this._onPointerMoved()));

        this._tracker.set_pointer_visible(false);

        this._actor.show();
        this._actor.ease({
            opacity: 255,
            duration: FADE_IN_MS,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });
    }

    /** Gerçek imleci GERİ AÇ ve aktörü kaldır. Hata yolunda da çağrılır. */
    stop() {
        this._active = false;
        this._fadingOut = false;
        this._clearDeadline();

        for (const alan of ['_posId', '_visId']) {
            if (this[alan] && this._tracker) {
                try {
                    this._tracker.disconnect(this[alan]);
                } catch { /* kabuk kapanıyorsa önemsiz */ }
            }
            this[alan] = 0;
        }
        if (this._turnId) {
            GLib.Source.remove(this._turnId);
            this._turnId = 0;
        }
        // Bekleyen kare işi aktörden SONRA çalışmasın.
        this._frames?.cancel();
        this._frames = null;
        this._laters = null;

        // Bırakmayı unutmak yasak: imleci geri açmak her şeyden önce gelir.
        if (this._tracker) {
            try {
                this._tracker.set_pointer_visible(true);
            } catch (error) {
                console.error(`[pcbridge-gorunur] imleç geri açılamadı: ${error}`);
            }
            this._tracker = null;
        }

        if (this._actor) {
            this._actor.destroy();     // `uiGroup`tan da düşürür
            this._actor = null;
        }
    }

    /** Görünürlüğü izin durumuna bağla.
     *
     * @param {boolean} visible
     * @param {number} until pcbridge izninin bittiği an (unix saniye). Kendi
     *   son kullanma zamanlayıcımızı buna göre kuruyoruz — aşağıya bak.
     */
    setVisible(visible, until = 0) {
        if (visible)
            this._armDeadline(until);
        else
            this._clearDeadline();

        if (visible) {
            if (this._fadingOut && this._actor) {
                // Kaybolma animasyonunun ORTASINDA yeniden açıldı
                // (`desktop_lock` hemen ardından `desktop_unlock`). Buraya
                // bakılmazsa `start()` "zaten etkin" diyip erken dönerdi,
                // animasyon tamamlanır ve aktör YOK EDİLİRDİ: imleç ne
                // bizimki ne de gerçek olurdu.
                this._fadingOut = false;
                this._actor.remove_all_transitions();
                this._tracker?.set_pointer_visible(false);
                this._actor.show();
                this._actor.ease({
                    opacity: 255,
                    duration: FADE_IN_MS,
                    mode: Clutter.AnimationMode.EASE_OUT_QUAD,
                });
                return;
            }
            this.start();
        } else if (this._active && !this._fadingOut) {
            this._fadeOutAndStop();
        }
    }

    // ------------------------------------------------------------------ iç
    /** İZİN PENCERESİNDEN UZUN YAŞAMA emniyeti.
     *
     * NEDEN VAR: geliştirme sırasında bir kez `state.js`'in dosya izleyicisi
     * ve emniyet taraması cevap vermez oldu (2026-08-04, nested kabuk).
     * TEKRARLANAMADI — sonraki koşumda 5/5 durum değişimi ve 3/3 tarama
     * sorunsuz çalıştı, kodumuzdan tek hata çıkmadı, sebebi BİLİNMİYOR.
     *
     * Sebebi bilmemek "yok sayalım" demek değil: izleyici izin AÇIKKEN
     * ölürse gerçek imleç sonsuza kadar gizli kalır ve kullanıcının elinde
     * hiç imleç olmaz. Bu yüzden imleç katmanı kendi son kullanma zamanını
     * da tutuyor — izin penceresinden daha uzun yaşayamıyor, izleyici ölse
     * bile. `hold_max_seconds` ile aynı desen: bırakmayı unutmak yasak.
     */
    _armDeadline(until) {
        this._clearDeadline();
        const kalan = until - Date.now() / 1000;
        // Tavan: izin en fazla `unlock_max_minutes` (120) sürebiliyor.
        const sn = Math.min(Math.max(kalan, 0) + DEADLINE_SLACK_S, 125 * 60);
        if (sn <= 0)
            return;
        this._deadlineId = GLib.timeout_add(GLib.PRIORITY_DEFAULT,
            Math.ceil(sn * 1000), () => {
                this._deadlineId = 0;
                console.warn('[pcbridge-gorunur] izin süresi geçti ama durum ' +
                    'izleyicisinden haber gelmedi — imleç katmanı emniyetle ' +
                    'kapatılıyor, gerçek imleç geri veriliyor');
                this.stop();
                return GLib.SOURCE_REMOVE;
            });
    }

    _clearDeadline() {
        if (this._deadlineId) {
            GLib.Source.remove(this._deadlineId);
            this._deadlineId = 0;
        }
    }

    _fadeOutAndStop() {
        const actor = this._actor;
        if (!actor) {
            this.stop();
            return;
        }
        this._fadingOut = true;
        // Gerçek imleci HEMEN geri aç: kaybolma animasyonu boyunca
        // kullanıcının imleçsiz kalmaması için. Kısa süre iki imleç görünür,
        // bu tersinden iyi (hiç imleç olmaması).
        actor.ease({
            opacity: 0,
            duration: FADE_OUT_MS,
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
            onComplete: () => {
                if (this._fadingOut)
                    this.stop();
            },
        });
        if (this._tracker) {
            try {
                this._tracker.set_pointer_visible(true);
            } catch { /* stop() yine deneyecek */ }
        }
    }

    _buildActor() {
        this._actor = new St.DrawingArea({
            name: 'pcbridge-gorunur-imlec',
            reactive: false,
            can_focus: false,
            track_hover: false,
            width: SIZE,
            height: SIZE,
            opacity: 0,
            visible: false,
        });
        // Dönüş merkezi UCUN kendisi: uç imlecin gerçek konumunda sabit
        // kalsın, gövde arkada savrulsun.
        this._actor.set_pivot_point(0.5, 0.5);
        this._actor.connect('repaint', () => this._paint());
        // `addTopChrome` DEĞİL: izlenen bir chrome aktörünün her konum
        // değişimi kabuğun girdi bölgesi hesabını yeniden kuyruğa sokuyor ve
        // bu aktör saniyede onlarca kez taşınıyor. Aktör zaten
        // `reactive: false`, yani girdiye hiç karışmıyor.
        Main.uiGroup.add_child(this._actor);
        Main.uiGroup.set_child_above_sibling(this._actor, null);
    }

    /** Sinyal işleyicilerini saran koruma.
     *
     * Bu iki işleyici imlecin YAŞAM DESTEĞİ: hata verirlerse gerçek imleç
     * gizli kalır ve bizimki yerinde donar — kullanıcı kullanılamaz bir
     * imleçle baş başa kalır. Üst üste hata gelirse katmanı kapatıp gerçek
     * imleci geri veriyoruz. Kötü görünmek, kullanılamaz olmaktan iyidir.
     */
    _guarded(fn) {
        try {
            fn();
            this._fails = 0;
        } catch (error) {
            this._fails = (this._fails || 0) + 1;
            console.error(`[pcbridge-gorunur] imleç işleyicisi (${this._fails}): ${error}`);
            if (this._fails >= 3) {
                console.error('[pcbridge-gorunur] imleç katmanı kapatılıyor, ' +
                    'gerçek imleç geri veriliyor');
                this.stop();
            }
        }
    }

    _place(x, y) {
        this._actor?.set_position(Math.round(x - TIP), Math.round(y - TIP));
    }

    _onVisibilityChanged() {
        // Kompozitör imleci geri açtıysa yeniden gizle — yoksa iki imleç.
        if (!this._active || !this._tracker)
            return;
        if (this._tracker.get_pointer_visible())
            this._tracker.set_pointer_visible(false);
    }

    /** Sinyal geldi. Konum bir SONRAKI karede okunacak.
     *
     * Burada `global.get_pointer()` bile çağrılmıyor: olay saniyede bine
     * kadar çıkabiliyor ve o bin okumanın 940'ı ekranda hiçbir şey
     * değiştirmiyor.
     */
    _onPointerMoved() {
        if (!this._active || !this._frames)
            return;
        this._frames.request();
    }

    /** Kare başına bir kez: imleci son konumuna taşı ve yönünü güncelle. */
    _applyPointer() {
        if (!this._active || !this._actor)
            return;
        const [px, py] = global.get_pointer();
        this._place(px, py);

        // Ölçüm kipinde: sinyalin gerçekten aktığını, hangi sıklıkta
        // geldiğini ve kaçının ekrana yansıdığını gör. `position-invalidated`
        // hiç tetiklenmeseydi imleç yerinde donar ve bunu ancak ekrana
        // bakarak fark ederdik.
        if (this._debug) {
            const {requests, applied} = this._frames.stats;
            if (applied % 60 === 0) {
                const gecen = (GLib.get_monotonic_time() - this._t0) / 1e6;
                console.log(`[pcbridge-gorunur][SELFTEST] imleç ` +
                    `${requests} olay / ${applied} çizim · ` +
                    `${(requests / gecen).toFixed(0)} olay/sn · ` +
                    `${(applied / gecen).toFixed(0)} çizim/sn · ` +
                    `konum (${px},${py}) · açı ${this._angle.toFixed(0)}°`);
            }
        }

        const dx = px - this._lastX;
        const dy = py - this._lastY;
        if (Math.hypot(dx, dy) < MIN_MOVE_PX)
            return;             // titreme yön değiştirmesin
        this._lastX = px;
        this._lastY = py;

        this._target = derece(Math.atan2(dy, dx));
        this._armTurn();
    }

    /** Dönüş yumuşatmasını çalıştır. Zaten çalışıyorsa dokunma. */
    _armTurn() {
        if (this._turnId)
            return;
        this._turnId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, TURN_TICK_MS, () => {
            if (!this._active || !this._actor) {
                this._turnId = 0;
                return GLib.SOURCE_REMOVE;
            }
            const fark = kisaYay(this._angle, this._target);
            if (Math.abs(fark) < TURN_DONE_DEG) {
                // Hedefe oturdu: kendini durdur. İmleç dururken maliyet sıfır.
                this._angle = sarmala(this._target);
                this._actor.rotation_angle_z = this._angle;
                this._turnId = 0;
                return GLib.SOURCE_REMOVE;
            }
            // Sarmalamak ŞART: `_angle` toplaya toplaya gidiyor ve
            // sınırlanmazsa büyüyor (ölçüldü: birkaç saniyelik gezinmede
            // -867°'ye çıktı). Dönüş yönü `kisaYay` sayesinde yine doğruydu,
            // ama biriken sayı hem logu okunmaz yapıyor hem de uzun oturumda
            // gereksiz yere büyüyor.
            this._angle = sarmala(this._angle + fark * TURN_EASE);
            this._actor.rotation_angle_z = this._angle;
            return GLib.SOURCE_CONTINUE;
        });
    }

    /** İçi siyah, ince beyaz şeritli, beyaz parıltılı ok. BİR KEZ çizilir. */
    _paint() {
        const cr = this._actor.get_context();
        try {
            const t = TIP;

            /** Şekli yola koyar; `r` köşe yuvarlatma yarıçapı (stroke ile). */
            const yol = (r) => {
                cr.moveTo(t + SHAPE[0][0], t + SHAPE[0][1]);
                for (let i = 1; i < SHAPE.length; i++)
                    cr.lineTo(t + SHAPE[i][0], t + SHAPE[i][1]);
                cr.closePath();
                cr.setLineJoin(Cairo.LineJoin.ROUND);
                cr.setLineCap(Cairo.LineCap.ROUND);
                cr.setLineWidth(r * 2);
            };

            // 1) Beyaz parıltı — çerçeveyle aynı dil: renksiz, yumuşak.
            //
            // ŞEKLİN ETRAFINI sarıyor, ucun etrafını değil. İlk sürüm uca
            // merkezli bir daireydi ve kuyruk sönük kalıyordu.
            //
            // Cairo'da bulanıklık yok; giderek genişleyen eş saydamlıkta
            // halkalarla yapıyoruz. Şekilden d kadar uzaktaki bir nokta,
            // yarım genişliği d'yi aşan HER halkanın altında kalıyor; yani
            // birikmiş saydamlık 1-(1-a)^n(d) oluyor ve dışarı doğru
            // kendiliğinden üstel sönüyor. Katman başına saydamlığı bu
            // birikime göre seçiyoruz, yoksa şeklin dibinde beyaz bir yığın
            // oluşuyor (ölçüldü, ilk denemede oldu).
            const kat = 1 - Math.pow(1 - GLOW_PEAK, 1 / GLOW_LAYERS);
            for (let i = GLOW_LAYERS; i >= 1; i--) {
                yol(CORNER_R + RIM + GLOW_SPREAD * (i / GLOW_LAYERS));
                cr.setSourceRGBA(1, 1, 1, kat);
                cr.stroke();
            }

            // 2) İnce beyaz şerit. Gövdeden `RIM` kadar geniş çizilip altta
            //    bırakılıyor, üstüne gövde geliyor: kalan fark şerit oluyor.
            yol(CORNER_R + RIM);
            cr.setSourceRGBA(1, 1, 1, 0.95);
            cr.strokePreserve();
            cr.fill();

            // 3) Gövde: koyu gri → siyah, çapraz gradyan. TAM OPAK olmalı:
            //    yarı saydam bırakılırsa alttaki beyaz şerit içeriden sızıp
            //    kenar boyunca gri bir çizgi bırakıyor (ölçüldü).
            const dolgu = new Cairo.LinearGradient(t, t - 16, t - 34, t + 16);
            dolgu.addColorStopRGBA(0.0, 0.27, 0.28, 0.31, 1);
            dolgu.addColorStopRGBA(1.0, 0.05, 0.05, 0.06, 1);
            yol(CORNER_R);
            cr.setSource(dolgu);
            cr.strokePreserve();
            cr.fill();
        } finally {
            cr.$dispose();
        }
    }
}
