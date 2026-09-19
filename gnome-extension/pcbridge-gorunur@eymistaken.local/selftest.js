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

/** Fiziksel fareyi taklit et: sanal bir işaretçiyle hızlı hareket üret.
 *
 * NEDEN VAR: imleç katmanı gerçek makinede FİZİKSEL fareyle tıklamayı
 * bozmuştu ve bütün denemeler sentetik fareyle (~50 olay/sn) yapıldığı için
 * yeniden üretilemedi. Fiziksel fare ~1000 Hz rapor ediyor (ölçüldü
 * 2026-09-13). Kabuğun kendi sanal aygıtı o hızı üretebiliyor, yani fark
 * nested kabukta da ölçülebilir hale geliyor.
 *
 * Ölçülen: kaç hareket gönderildi, imleç katmanı kaçını ekrana yansıttı ve
 * bu sırada ana döngü ne kadar geciktirdi. Tıklama göndermiyor.
 *
 * @param {object} cursor imleç katmanı (sayaçları için; olmayabilir).
 * @param {object} options
 * @param {number} [options.events] gönderilecek hareket sayısı.
 * @param {number} [options.hz] hedef olay hızı.
 */
export function pointerBurst(cursor, {events = 2000, hz = 1000} = {}) {
    let seat;
    let device;
    try {
        seat = Clutter.get_default_backend().get_default_seat();
        device = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    } catch (error) {
        sonuc('fare fırtınası: sanal aygıt', false, `${error}`);
        return;
    }

    const monitor = Main.layoutManager.primaryMonitor;
    const merkezX = monitor.x + Math.floor(monitor.width / 2);
    const merkezY = monitor.y + Math.floor(monitor.height / 2);
    const yariCap = Math.floor(Math.min(monitor.width, monitor.height) / 4);
    // Tempo GERÇEKÇİ olmalı: olayları tek seferde boşaltmak ana döngüyü
    // doldurur ve kabuk arada hiç çizim yapamaz (ilk denemede oldu: 2000
    // hareket 0,12 sn'de gitti, 0 çizim). Fiziksel fare olayları zamana
    // yayılmış geliyor, biz de öyle gönderiyoruz.
    const tikMs = 4;
    const tikBasina = Math.max(1, Math.round((hz * tikMs) / 1000));

    cursor?.resetStats?.();
    const baslangic = GLib.get_monotonic_time();
    let sonUyanma = baslangic;
    let enKotuGecikme = 0;
    let gonderilen = 0;

    // Olayları ana döngüden gönderiyoruz: `sleep` ile döngüyü kilitlemek
    // ölçülecek şeyi (kabuğun tepki verebilmesini) yok ederdi.
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, tikMs, () => {
        const simdi = GLib.get_monotonic_time();
        const gecikme = (simdi - sonUyanma) / 1000 - tikMs;
        if (gonderilen > 0 && gecikme > enKotuGecikme)
            enKotuGecikme = gecikme;
        sonUyanma = simdi;

        for (let i = 0; i < tikBasina && gonderilen < events; i++) {
            // Bir daire üstünde ilerle: her olay gerçek bir konum değişimi
            // olsun, yoksa kompozitör hareketi hiç görmez.
            const aci = (gonderilen / hz) * 4 * Math.PI;
            const x = merkezX + Math.cos(aci) * yariCap;
            const y = merkezY + Math.sin(aci) * yariCap;
            try {
                device.notify_absolute_motion(simdi + i, x, y);
            } catch (error) {
                sonuc('fare fırtınası: hareket gönderildi', false, `${error}`);
                return GLib.SOURCE_REMOVE;
            }
            gonderilen++;
        }

        if (gonderilen < events)
            return GLib.SOURCE_CONTINUE;

        const gecen = (GLib.get_monotonic_time() - baslangic) / 1e6;
        const stats = cursor?.stats ?? null;
        yaz(`fare fırtınası: ${gonderilen} hareket · ${gecen.toFixed(2)} sn · ` +
            `${(gonderilen / gecen).toFixed(0)} hareket/sn · ` +
            `ana döngü en kötü ${enKotuGecikme.toFixed(1)} ms`);
        if (stats) {
            yaz(`  imleç: ${stats.requests} olay / ${stats.applied} çizim · ` +
                `${(stats.applied / gecen).toFixed(0)} çizim/sn`);
            sonuc('çizim sayısı olay sayısının altında (kare saati)',
                stats.applied < stats.requests / 2,
                `${stats.applied} < ${stats.requests} / 2`);
        } else {
            yaz('  imleç katmanı kapalı: yalnızca hareket üretildi');
        }
        // Aygıtı bırak: nested kabuk kapanınca sahipsiz kalmasın.
        try {
            seat.get_pointer?.();
        } catch { /* yalnızca tanı */ }
        return GLib.SOURCE_REMOVE;
    });
    yaz(`fare fırtınası başladı: ${events} hareket, hedef ${hz} Hz ` +
        `(${tikBasina} hareket / ${tikMs} ms)`);
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
