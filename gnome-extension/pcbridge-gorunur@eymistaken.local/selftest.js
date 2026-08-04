/* Kabuğun İÇİNDEN ölçüm. `PCBRIDGE_GORUNUR_SELFTEST=1` ile açılır.
 *
 * NEDEN VAR
 *     Bu eklentinin iki iddiası dışarıdan doğrulanamıyor:
 *       1. çerçeve altındaki pencerelere tıklamayı ENGELLEMİYOR
 *       2. gerçek imleç gizlenebiliyor ve gizli KALIYOR
 *     `Shell.Eval` GNOME 41+ ile kapalı, yani kabuğa dışarıdan kod
 *     sokulamıyor. Ölçümü yapabilecek tek yer kabuğun içinde zaten çalışan
 *     bu eklenti.
 *
 *     "Hata vermedi" bu projede kanıt sayılmıyor; buradaki çıktılar
 *     `journalctl`/nested logunda okunabilir gerçek ölçümler.
 *
 * Kapalıyken hiçbir maliyeti yok: `enable()` içinde tek bir `getenv`.
 */

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const ETIKET = '[pcbridge-gorunur][SELFTEST]';

export function selfTestEnabled() {
    return GLib.getenv('PCBRIDGE_GORUNUR_SELFTEST') === '1';
}

function yaz(satir) {
    console.log(`${ETIKET} ${satir}`);
}

function sonuc(ad, gecti, ayrinti = '') {
    console.log(`${ETIKET} ${gecti ? 'PASS' : 'FAIL'}  ${ad}${ayrinti ? `  ${ayrinti}` : ''}`);
}

/** Monitör tablosu — koordinatların beklenen yerde olduğunu görmek için. */
export function reportMonitors() {
    const ms = Main.layoutManager.monitors;
    yaz(`monitör sayısı: ${ms.length}`);
    for (const m of ms)
        yaz(`  #${m.index}  ${m.width}x${m.height} @ (${m.x},${m.y})` +
            `${m.index === Main.layoutManager.primaryIndex ? '  [birincil]' : ''}`);
}

/**
 * Çerçevenin altındaki tıklamayı engellemediğini ölç.
 *
 * `get_actor_at_pos(REACTIVE, ...)` tam olarak bir tıklamanın hangi aktöre
 * gideceğini söylüyor. Şeritlerimizden biri dönerse tıklama YUTULUYOR demektir.
 */
export function checkClickThrough() {
    const ms = Main.layoutManager.monitors;
    let hepsiGecti = true;

    for (const m of ms) {
        // Kenardan 6 px içeri: şeridin en parlak, en kalın olduğu yer.
        const noktalar = [
            ['üst', m.x + Math.floor(m.width / 2), m.y + 6],
            ['alt', m.x + Math.floor(m.width / 2), m.y + m.height - 6],
            ['sol', m.x + 6, m.y + Math.floor(m.height / 2)],
            ['sağ', m.x + m.width - 6, m.y + Math.floor(m.height / 2)],
        ];
        for (const [kenar, px, py] of noktalar) {
            const actor = global.stage.get_actor_at_pos(Clutter.PickMode.REACTIVE, px, py);
            const ad = actor ? (actor.name || actor.constructor?.$gtype?.name || `${actor}`) : '(yok)';
            const bizimki = typeof ad === 'string' && ad.startsWith('pcbridge-gorunur-');
            if (bizimki)
                hepsiGecti = false;
            sonuc(`tıklama geçiyor · monitör ${m.index} ${kenar} (${px},${py})`,
                !bizimki, `→ ${ad}`);
        }
    }
    sonuc('ÖZET: çerçeve tıklamayı engellemiyor', hepsiGecti);
    return hepsiGecti;
}

/**
 * Gerçek imleci gizleyebiliyor muyuz, ve gizli KALIYOR mu?
 *
 * Bu, YAPILACAKLAR.md'nin "2 ve 3 tek bir soruya bağlı" dediği soru.
 * Gizleyemezsek kendi imlecimizi çizmek iki imleçle sonuçlanır.
 *
 * EMNİYET: ne olursa olsun `SURE_SN` sonunda imleç geri açılır. Ölçüm
 * sırasında kabuk çökse bile kullanıcıda imleçsiz bir makine kalmasın.
 */
export function probeCursorHiding(sureSn = 8) {
    let tracker;
    try {
        tracker = Meta.CursorTracker.get_for_display(global.display);
    } catch (error) {
        sonuc('CursorTracker alınabildi', false, `${error}`);
        return;
    }
    sonuc('CursorTracker alınabildi', true);

    const oncesi = tracker.get_pointer_visible();
    yaz(`başlangıçta görünür: ${oncesi}`);

    // EMNİYET ÖNCE: gizlemeden ÖNCE geri açma zamanlayıcısını kur.
    const emniyet = GLib.timeout_add_seconds(GLib.PRIORITY_HIGH, sureSn, () => {
        try {
            tracker.set_pointer_visible(true);
            yaz(`emniyet: imleç geri açıldı (${sureSn} sn doldu)`);
        } catch (error) {
            console.error(`${ETIKET} emniyet BAŞARISIZ: ${error}`);
        }
        return GLib.SOURCE_REMOVE;
    });

    let gorunurlukOlaylari = 0;
    let id = 0;
    try {
        id = tracker.connect('visibility-changed', () => {
            gorunurlukOlaylari++;
            yaz(`visibility-changed → ${tracker.get_pointer_visible()}`);
        });
    } catch (error) {
        yaz(`visibility-changed bağlanamadı: ${error}`);
    }

    try {
        tracker.set_pointer_visible(false);
    } catch (error) {
        sonuc('set_pointer_visible(false) çağrıldı', false, `${error}`);
        GLib.Source.remove(emniyet);
        return;
    }
    sonuc('set_pointer_visible(false) çağrıldı', true);

    const hemen = tracker.get_pointer_visible();
    sonuc('çağrıdan hemen sonra gizli', hemen === false, `get_pointer_visible()=${hemen}`);

    // Bir süre sonra hâlâ gizli mi? Kompozitör kendiliğinden geri açıyorsa
    // (imleç teması değişimi, odak, overview) burada yakalanır.
    GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, Math.max(1, sureSn - 3), () => {
        const sonra = tracker.get_pointer_visible();
        sonuc('birkaç saniye sonra HÂLÂ gizli', sonra === false,
            `get_pointer_visible()=${sonra} · visibility-changed olayı: ${gorunurlukOlaylari}`);
        return GLib.SOURCE_REMOVE;
    });

    // Ölçüm bitince temizlik: emniyet zamanlayıcısı zaten geri açacak.
    GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, sureSn + 2, () => {
        if (id) {
            try {
                tracker.disconnect(id);
            } catch { /* kabuk kapanıyorsa önemsiz */ }
        }
        const son = tracker.get_pointer_visible();
        sonuc('ÖLÇÜM SONRASI imleç geri açıldı', son === true, `get_pointer_visible()=${son}`);
        return GLib.SOURCE_REMOVE;
    });
}
