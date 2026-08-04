/* Ajan çalışırken imleci kendi çizdiğimizle değiştirir ve yöne döndürür.
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

import {selfTestEnabled} from './selftest.js';

/* Aktör kutusu. Uç TAM ORTADA: dönüş merkezi uç olunca uç imlecin gerçek
 * konumunda sabit kalıyor, gövde arkada savruluyor.
 *
 * Kutu neden gövdeden çok daha büyük: aktör ucun ETRAFINDA dönüyor, yani
 * gövdenin en uzak noktası bir daire çiziyor. Yarıçap (38² + 11²)^½ ≈ 39,6 <
 * 44 olmalı; küçük kutuda köşeler dönerken KIRPILIYOR (64 px ile denendi,
 * şekil çapraz açılarda kesildi). Kalanı parıltıya kalıyor. */
const SIZE = 88;
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

/** Belirme/kaybolma. */
const FADE_IN_MS = 260;
const FADE_OUT_MS = 200;

/** İzin bittikten sonra emniyetin devreye girmesi için tanınan pay (sn).
 *  Normal yolda durum izleyicisi çoktan kapatmış olur; bu yalnızca o
 *  gelmediğinde çalışır. */
const DEADLINE_SLACK_S = 10;

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

    /** Kendi imlecimizi devreye al: gerçeği gizle, aktörü göster, takibe başla. */
    start() {
        if (this._active)
            return;
        try {
            this._tracker = Meta.CursorTracker.get_for_display(global.display);
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
        this._events = 0;
        this._t0 = GLib.get_monotonic_time();

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
            Main.layoutManager.removeChrome(this._actor);
            this._actor.destroy();
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
        Main.layoutManager.addTopChrome(this._actor, {
            affectsInputRegion: false,
            affectsStruts: false,
        });
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

    _onPointerMoved() {
        if (!this._active || !this._actor)
            return;
        const [px, py] = global.get_pointer();
        this._place(px, py);

        // Ölçüm kipinde: sinyalin gerçekten aktığını ve hangi sıklıkta
        // geldiğini gör. `position-invalidated` hiç tetiklenmeseydi imleç
        // yerinde donar ve bunu ancak ekrana bakarak fark ederdik.
        if (this._debug) {
            if (++this._events % 60 === 0) {
                const gecen = (GLib.get_monotonic_time() - this._t0) / 1e6;
                console.log(`[pcbridge-gorunur][SELFTEST] imleç olayı ` +
                    `${this._events} · ${(this._events / gecen).toFixed(0)} olay/sn ` +
                    `· konum (${px},${py}) · açı ${this._angle.toFixed(0)}°`);
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

    /** Yumuşak köşeli, hafif parlayan bir damla. BİR KEZ çizilir. */
    _paint() {
        const cr = this._actor.get_context();
        try {
            const t = TIP;

            // 1) Parıltı: uçtan dışarı sönen beyaz hale.
            const halo = new Cairo.RadialGradient(t, t, 1, t, t, 32);
            halo.addColorStopRGBA(0.00, 1, 1, 1, 0.30);
            halo.addColorStopRGBA(0.35, 1, 1, 1, 0.14);
            halo.addColorStopRGBA(0.70, 1, 1, 1, 0.04);
            halo.addColorStopRGBA(1.00, 1, 1, 1, 0.0);
            cr.setSource(halo);
            cr.arc(t, t, 32, 0, 2 * Math.PI);
            cr.fill();

            // Uzun damla, +X yönünü gösterecek şekilde çiziliyor (açı 0 = sağ).
            // Dönüş bunun üstüne rotation_angle_z ile geliyor.
            // Şekil dört aday arasından kullanıcı tarafından seçildi: yumuşak
            // ve köşesiz kalırken yönü okunabilen tek aday buydu (kısa damla
            // yönsüz, çentikli ok tarifteki "sert değil"e ters düşüyordu).
            const govde = () => {
                cr.moveTo(t, t);                                  // uç
                cr.curveTo(t - 9, t - 4, t - 19, t - 8, t - 27, t - 10);
                cr.curveTo(t - 35, t - 11, t - 35, t + 11, t - 27, t + 10);
                cr.curveTo(t - 19, t + 8, t - 9, t + 4, t, t);
                cr.closePath();
            };

            // 2) Koyu yumuşak kenarlık. Beyaz bir imleç beyaz zeminde
            //    KAYBOLUR; gerçek imleçlerin siyah konturu tam da bunun için.
            //    Sert bir çizgi yerine yarı saydam, kalın ve yuvarlak uçlu.
            govde();
            cr.setLineWidth(3.0);
            cr.setLineJoin(Cairo.LineJoin.ROUND);
            cr.setLineCap(Cairo.LineCap.ROUND);
            cr.setSourceRGBA(0, 0, 0, 0.42);
            cr.stroke();

            // 3) Gövde: uçta parlak, kuyrukta hafif saydam.
            const dolgu = new Cairo.LinearGradient(t, t, t - 38, t);
            dolgu.addColorStopRGBA(0.0, 1, 1, 1, 0.98);
            dolgu.addColorStopRGBA(0.6, 1, 1, 1, 0.90);
            dolgu.addColorStopRGBA(1.0, 1, 1, 1, 0.72);
            govde();
            cr.setSource(dolgu);
            cr.fill();
        } finally {
            cr.$dispose();
        }
    }
}
