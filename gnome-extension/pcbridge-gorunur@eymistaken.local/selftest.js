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

/** Nefes animasyonu gerçekten koşuyor mu, ve aralığı doğru mu?
 *
 * Ekran görüntüsünden ölçmek zor: bant zaten ince, değişim birkaç piksel ve
 * nested pencereyi ekranda bulmak gerekiyor. Aktörün ölçeğini doğrudan
 * örneklemek hem kesin hem ucuz. Beklenen: 1.0 ile BREATH_SCALE arasında
 * gidip gelmesi ve 1.0'ı ASLA aşmaması (kullanıcı kısıtı: kalınlaşma yok).
 */
export function reportBreathing(actorlar, sureSn = 13, aralikMs = 900) {
    if (!actorlar.length) {
        sonuc('nefes: aktör var', false);
        return;
    }
    const a = actorlar[0];
    const yatay = a._pcbYatay;
    const oku = () => (yatay ? a.scale_y : a.scale_x);
    const ornekler = [];
    const bitis = GLib.get_monotonic_time() + sureSn * 1e6;

    GLib.timeout_add(GLib.PRIORITY_DEFAULT, aralikMs, () => {
        ornekler.push(oku());
        if (GLib.get_monotonic_time() < bitis)
            return GLib.SOURCE_CONTINUE;

        const enAz = Math.min(...ornekler);
        const enCok = Math.max(...ornekler);
        yaz(`nefes örnekleri: ${ornekler.map(v => v.toFixed(3)).join(' ')}`);
        sonuc('nefes çalışıyor (ölçek değişiyor)', enCok - enAz > 0.02,
            `aralık ${enAz.toFixed(3)} – ${enCok.toFixed(3)}`);
        sonuc('KALINLAŞMA YOK (ölçek 1.0 üstüne çıkmıyor)', enCok <= 1.0001,
            `en yüksek ${enCok.toFixed(4)}`);
        return GLib.SOURCE_REMOVE;
    });
    yaz(`nefes ölçümü başladı (${sureSn} sn, ${aralikMs} ms aralık)`);
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

/** D-Bus etkinleştirmesinin gerçekten odak değiştirdiğini kabuğun içinden doğrula. */
export function reportWindowActivation(window, target) {
    const focused = global.display.focus_window;
    let title = '(başlık yok)';
    try {
        title = focused?.get_title?.() || title;
    } catch { /* yalnızca tanı */ }
    sonuc(`ActivateWindow odak · ${target}`, focused === window, `→ ${title}`);
}
