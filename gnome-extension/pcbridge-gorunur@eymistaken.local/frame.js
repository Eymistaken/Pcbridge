/* Ekran kenarlarında yumuşak, beyaz, gradyanlı çerçeve.
 *
 * NEDEN DÖRT ŞERİT, TEK TAM EKRAN AKTÖR DEĞİL
 *     Tam ekran bir `St.DrawingArea` monitör başına ~8 MB doku ayırır ve her
 *     karede 1920×1080 saydam bir dörtgenin harmanlanmasını gerektirir. Dört
 *     kenar şeridi aynı görüntüyü ~4 kat daha az piksel harmanlayarak veriyor
 *     (552 bin px yerine 2,07 milyon). Kısıt bunu istiyor: "yer kaplamayacak".
 *
 * KÖŞELER
 *     Yatay şeritler tam genişlik, dikey şeritler tam yükseklik. Köşede ikisi
 *     üst üste biniyor ve saydamlıklar OVER ile birleşiyor: köşe biraz daha
 *     parlak çıkıyor. Bu bir kusur değil — cam kenarındaki ışık böyle davranır.
 *
 * ÇİZİM BİR KEZ
 *     Şeritler yalnızca boyut değişince yeniden çiziliyor. Belirme/kaybolma
 *     `opacity` üzerinden, yani GPU'da: animasyon boyunca tek bir Cairo çağrısı
 *     bile yapılmıyor.
 *
 * `Clutter.Canvas` mutter çatalında YOK; çizim yolu `St.DrawingArea` + Cairo.
 */

import Cairo from 'gi://cairo';
import Clutter from 'gi://Clutter';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

/**
 * `addTopChrome` across GNOME versions. GNOME 50 dropped the
 * `affectsInputRegion` parameter (measured on 50.5: "Unrecognized parameter"
 * and the extension disabled itself); the chrome input region there follows
 * reactivity, and these strips are not reactive. The call adds the actor
 * before it parses the parameters, so a refused call is undone first.
 */
export function addTopChromeCompat(actor, params, layout = Main.layoutManager) {
    try {
        layout.addTopChrome(actor, params);
    } catch (e) {
        if (!('affectsInputRegion' in params) ||
            !String(e?.message ?? e).includes('affectsInputRegion'))
            throw e;
        if (actor.get_parent())
            actor.get_parent().remove_child(actor);
        const {affectsInputRegion: _dropped, ...rest} = params;
        layout.addTopChrome(actor, rest);
    }
}

/* Parlaklığın kenardaki en yüksek değeri (0-1).
 * 0,42 ve aşağıdaki dik sönüş eğrisi kullanıcı tarafından üç varyant
 * gerçek monitör görüntüsü üzerine bindirilip karşılaştırılarak seçildi
 * (0,50 daha "bant" gibi duruyordu, 0,30 parlak duvar kâğıdında kayboluyordu). */
const GLOW_ALPHA = 0.42;

/** Şerit kalınlığı: monitörün kısa kenarının oranı. 1080 px'te ~92 px. */
const DEPTH_RATIO = 0.085;
const DEPTH_MIN = 48;
const DEPTH_MAX = 150;

/** Yumuşak giriş/çıkış. Ani geçiş yok (kısıt). */
const FADE_IN_MS = 700;
const FADE_OUT_MS = 500;

/* Nefes alma: bant çok yavaşça incelip eski kalınlığına dönüyor.
 *
 * KALINLAŞMA YOK: ölçek tavanı 1.0, yani şerit hiçbir zaman çizildiğinden
 * kalın olmuyor. Aşağı doğru %12 inceliyor, o kadar.
 *
 * NASIL: şeridi DIŞ KENARINA doğru ölçekliyoruz (`pivot_point`). Dış kenar
 * yerinde sabit kalıyor, iç kenar geri çekiliyor — yani gradyan mekânsal
 * olarak daralıyor, gerçekten "incelme" oluyor. Yeniden çizim yok; ölçek bir
 * GPU dönüşümü, Cairo'ya hiç dönülmüyor.
 *
 * Saydamlık kullanılmadı bilerek: belirme/kaybolma zaten `opacity` üzerinden
 * gidiyor, ikisi aynı özelliği çekiştirirse animasyonlar birbirini eziyor.
 */
const BREATH_SCALE = 0.88;
const BREATH_MS = 5500;   // yarım çevrim; tam nefes 11 saniye

/* Kenardan içeri sönüş eğrisi. Düz doğrusal bir rampa göze "bant" gibi
 * görünüyor; bu duraklar üstel bir sönüşe yaklaşıyor ve ışık gibi okunuyor.
 * [oran, saydamlık çarpanı] */
const FALLOFF = [
    [0.00, 1.000],
    [0.06, 0.700],
    [0.18, 0.380],
    [0.35, 0.160],
    [0.55, 0.055],
    [0.78, 0.012],
    [1.00, 0.000],
];

/** Bir kenarın yönü: gradyanın hangi eksende, hangi yöne söneceği. */
const KENARLAR = ['ust', 'alt', 'sol', 'sag'];

export class FrameOverlay {
    constructor() {
        this._actors = [];
        this._monitorsId = 0;
        this._visible = false;
    }

    /** Ölçüm için: kurulu şerit aktörleri. */
    get actors() {
        return this._actors;
    }

    /** Aktörleri kur ve monitör değişikliklerini izlemeye başla. */
    start() {
        this._monitorsId = Main.layoutManager.connect(
            'monitors-changed', () => this._rebuild());
        this._rebuild();
    }

    stop() {
        if (this._monitorsId) {
            Main.layoutManager.disconnect(this._monitorsId);
            this._monitorsId = 0;
        }
        this._destroyActors();
        this._visible = false;
    }

    /** Görünürlüğü değiştir. Aynı durum tekrar verilirse hiçbir şey yapmaz. */
    setVisible(visible) {
        if (visible === this._visible)
            return;
        this._visible = visible;

        for (const actor of this._actors) {
            if (visible) {
                actor.show();
                actor.ease({
                    opacity: 255,
                    duration: FADE_IN_MS,
                    mode: Clutter.AnimationMode.EASE_OUT_QUAD,
                });
                this._startBreathing(actor);
            } else {
                this._stopBreathing(actor);
                actor.ease({
                    opacity: 0,
                    duration: FADE_OUT_MS,
                    mode: Clutter.AnimationMode.EASE_IN_QUAD,
                    // Gizlemek şart: saydamlığı 0 olan bir aktör hâlâ
                    // harmanlanıyor ve boşuna GPU yakıyor.
                    onComplete: () => actor.hide(),
                });
            }
        }
    }

    /** Sonsuz nefes: incel → eski kalınlığa dön → incel …
     *
     * `Clutter.PropertyTransition` + `auto_reverse` denendi ve ÇALIŞMADI:
     * aktöre eklenmeden önce `set_from`/`set_to` çağrılınca geçiş özelliğin
     * tipini bilmiyor, aralık boş kalıyor ve ölçek 0'a düşüyor (ölçüldü:
     * 15 örneğin hepsi 0.000). `ease()` zincirlemesi doğrulanmış yol.
     *
     * Bedeli yarım çevrimde bir JS geri çağrısı — şerit başına 5,5 saniyede
     * bir, yani saniyede ~1,5 çağrı. Ölçülemeyecek kadar az. */
    _startBreathing(actor) {
        if (actor._pcbNefes)
            return;
        actor._pcbNefes = true;
        this._breathStep(actor, BREATH_SCALE);
    }

    _breathStep(actor, hedef) {
        if (!actor._pcbNefes)
            return;
        // With animations off (a headless shell, or "Reduce Animation" in
        // Settings) `ease()` jumps to the end and calls onComplete at once,
        // so this chain recursed without end (measured on GNOME 50.5:
        // "too much recursion", and the frame never showed). The frame then
        // stands still instead of breathing.
        if (!St.Settings.get().enable_animations) {
            actor._pcbNefes = false;
            actor.set_scale(1, 1);
            return;
        }
        const ozellik = actor._pcbYatay ? 'scale_y' : 'scale_x';
        actor.ease({
            [ozellik]: hedef,
            duration: BREATH_MS,
            mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
            onComplete: () => this._breathStep(actor, hedef === 1 ? BREATH_SCALE : 1),
        });
    }

    _stopBreathing(actor) {
        actor._pcbNefes = false;
        actor.remove_transition('scale-x');
        actor.remove_transition('scale-y');
        // Ölçeği geri al: kaybolma tam kalınlıktan başlasın.
        actor.set_scale(1, 1);
    }

    // ------------------------------------------------------------------ iç
    _destroyActors() {
        for (const actor of this._actors) {
            Main.layoutManager.removeChrome(actor);
            actor.destroy();
        }
        this._actors = [];
    }

    _rebuild() {
        this._destroyActors();

        for (const monitor of Main.layoutManager.monitors) {
            const depth = Math.round(Math.max(DEPTH_MIN, Math.min(DEPTH_MAX,
                Math.min(monitor.width, monitor.height) * DEPTH_RATIO)));

            for (const kenar of KENARLAR)
                this._actors.push(this._makeStrip(monitor, kenar, depth));
        }

        // Yeniden kurulum görünürken olduysa (monitör takıldı/çıkarıldı)
        // yeni aktörler de görünür başlamalı — yoksa çerçeve sessizce kaybolur.
        if (this._visible) {
            for (const actor of this._actors) {
                actor.show();
                actor.opacity = 255;
                this._startBreathing(actor);
            }
        }
    }

    _makeStrip(monitor, kenar, depth) {
        const yatay = kenar === 'ust' || kenar === 'alt';
        const w = yatay ? monitor.width : depth;
        const h = yatay ? depth : monitor.height;
        const x = kenar === 'sag' ? monitor.x + monitor.width - depth : monitor.x;
        const y = kenar === 'alt' ? monitor.y + monitor.height - depth : monitor.y;

        const area = new St.DrawingArea({
            name: `pcbridge-gorunur-${kenar}-${monitor.index}`,
            reactive: false,
            can_focus: false,
            track_hover: false,
            opacity: 0,
            visible: false,
            width: w,
            height: h,
            x,
            y,
        });
        // Nefes ölçeği DIŞ KENARA sabitleniyor: dış kenar yerinde kalsın,
        // iç kenar geri çekilsin. Pivot normalleştirilmiş (0-1).
        area._pcbYatay = yatay;
        area.set_pivot_point(
            kenar === 'sag' ? 1 : (kenar === 'sol' ? 0 : 0.5),
            kenar === 'alt' ? 1 : (kenar === 'ust' ? 0 : 0.5));
        area.connect('repaint', () => this._paint(area, kenar));

        // affectsInputRegion: false -> altındaki pencerelere tıklamayı
        // ENGELLEMEZ (kısıt). affectsStruts: false -> pencere yerleşimini
        // bozmaz, yani maksimize pencereler küçülmez.
        addTopChromeCompat(area, {
            affectsInputRegion: false,
            affectsStruts: false,
        });
        return area;
    }

    /** Kenardan içeri sönen tek yönlü gradyan. Şerit başına BİR kez çalışır. */
    _paint(area, kenar) {
        const cr = area.get_context();
        try {
            const [w, h] = area.get_surface_size();

            // Gradyan kenardan (saydamlık en yüksek) içeriye doğru.
            const [x0, y0, x1, y1] = {
                ust: [0, 0, 0, h],
                alt: [0, h, 0, 0],
                sol: [0, 0, w, 0],
                sag: [w, 0, 0, 0],
            }[kenar];

            const grad = new Cairo.LinearGradient(x0, y0, x1, y1);
            for (const [oran, carpan] of FALLOFF)
                grad.addColorStopRGBA(oran, 1, 1, 1, GLOW_ALPHA * carpan);

            cr.setSource(grad);
            cr.rectangle(0, 0, w, h);
            cr.fill();
        } finally {
            // GJS'de Cairo bağlamı elle bırakılmazsa sızıyor (klasik tuzak).
            cr.$dispose();
        }
    }
}
