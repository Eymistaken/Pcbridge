/* Kabuğun İÇİNDEN ölçüm. `PCBRIDGE_GORUNUR_SELFTEST=1` ile açılır.
 *
 * NEDEN VAR
 *     Eklentinin iddiaları dışarıdan doğrulanamıyor: çerçevenin altındaki
 *     pencerelere tıklamayı engellemediği, ve kabuğun ana döngüsünü
 *     tıkamadığı. `Shell.Eval` GNOME 41+ ile kapalı, yani kabuğa dışarıdan
 *     kod sokulamıyor. Ölçümü yapabilecek tek yer kabuğun içinde zaten
 *     çalışan bu eklenti.
 *
 *     "Hata vermedi" bu projede kanıt sayılmıyor; buradaki çıktılar
 *     `journalctl`/nested logunda okunabilir gerçek ölçümler.
 *
 * Kapalıyken hiçbir maliyeti yok: `enable()` içinde tek bir `getenv`.
 */

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const ETIKET = '[pcbridge-gorunur][SELFTEST]';

/** Ölçüm kipini açan işaret dosyası.
 *
 * Neden env değişkeni YETMİYOR: gerçek oturumda gnome-shell'in ortamını
 * değiştirmek `~/.config/environment.d/` altına kalıcı bir dosya koymak ve
 * bir çıkış/giriş daha demek. İşaret dosyası hiçbir yapılandırmaya dokunmuyor;
 * `touch` yeter, silmek de kapatır.
 */
export function selfTestMarkerPath() {
    return GLib.build_filenamev([
        GLib.get_user_state_dir(), 'pcbridge', 'gorunur-selftest',
    ]);
}

export function selfTestEnabled() {
    if (GLib.getenv('PCBRIDGE_GORUNUR_SELFTEST') === '1')
        return true;
    return GLib.file_test(selfTestMarkerPath(), GLib.FileTest.EXISTS);
}

function yaz(satir) {
    console.log(`${ETIKET} ${satir}`);
}

function sonuc(ad, gecti, ayrinti = '') {
    console.log(`${ETIKET} ${gecti ? 'PASS' : 'FAIL'}  ${ad}${ayrinti ? `  ${ayrinti}` : ''}`);
}

/** Kabuğun ANA DÖNGÜSÜ tıkanıyor mu?
 *
 * "Fare donuyor, tıklama basmıyor" şikâyeti ancak böyle ölçülür: sabit
 * aralıklı bir zamanlayıcı kurup GERÇEKTE ne zaman uyandığına bakıyoruz.
 * Ana döngü meşgulse zamanlayıcı geç uyanır ve gecikme doğrudan okunur.
 * CPU yüzdesi bunu göstermez — donma, toplam yükten değil tek bir uzun
 * işten de gelebilir.
 */
export function startLoopWatchdog(aralikMs = 100, esikMs = 60) {
    let son = GLib.get_monotonic_time();
    let enKotu = 0;
    let gec = 0;
    let tik = 0;

    const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, aralikMs, () => {
        const simdi = GLib.get_monotonic_time();
        const gecikme = (simdi - son) / 1000 - aralikMs;   // ms
        son = simdi;
        tik++;
        if (gecikme > esikMs) {
            gec++;
            if (gecikme > enKotu)
                enKotu = gecikme;
        }
        if (tik % 50 === 0) {
            yaz(`ana döngü: ${tik} tık · ${gec} gecikmeli (>${esikMs} ms) · ` +
                `en kötü ${enKotu.toFixed(0)} ms`);
            enKotu = 0;
            gec = 0;
        }
        return GLib.SOURCE_CONTINUE;
    });
    yaz(`ana döngü gözcüsü açıldı (${aralikMs} ms aralık)`);
    return id;
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
    // DİKKAT: bu `reactive = false`'un çalıştığını kanıtlıyor, `affectsInputRegion`
    // = false'u DEĞİL. İkincisi kabuğun Wayland girdi bölgesiyle ilgili ve ancak
    // gerçek bir tıklamayla ölçülür — gerçek oturum kontrol listesinde var.
    sonuc('ÖZET: çerçeve aktörleri tıklama hedefi değil', hepsiGecti);
    return hepsiGecti;
}
