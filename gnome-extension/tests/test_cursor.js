#!/usr/bin/env -S gjs -m
/* İmleç katmanının kare saati mantığı — KABUK GEREKMEZ.
 *
 *     gjs -m gnome-extension/tests/test_cursor.js
 *
 * `cursor.js` kabuk modüllerini içe aktarıyor (Clutter, Meta, St, Main), yani
 * kabuk dışında yüklenemez. Asıl düzeltme -- konumu kare başına bir kez
 * uygulamak -- bu yüzden `frameclock.js`te duruyor ve burada sınanıyor.
 *
 * Ölçülen gerçek: fiziksel fare ~1000 Hz rapor ediyor (2026-09-13), ekranda
 * görünebilecek en fazla değişiklik ise kare sayısı kadar. Katmanın ilk hâli
 * her olayda aktörü taşıyordu ve gerçek makinede fareyi bozmuştu.
 */

import {FrameCoalescer} from '../pcbridge-gorunur@eymistaken.local/frameclock.js';

let passed = 0;
let failed = 0;

function check(name, condition, detail = '') {
    if (condition) {
        passed++;
        print(`  \x1b[32mPASS\x1b[0m  ${name}`);
    } else {
        failed++;
        print(`  \x1b[31mFAIL\x1b[0m  ${name}  ${detail}`);
    }
}

function section(title) {
    print(`\n\x1b[1m${title}\x1b[0m`);
}

/** Kareleri elle çeviren sahte zamanlayıcı. */
class FakeFrames {
    constructor() {
        this.queue = new Map();
        this.next = 1;
        this.canceled = [];
    }

    schedule(run) {
        const id = this.next++;
        this.queue.set(id, run);
        return id;
    }

    cancel(id) {
        this.canceled.push(id);
        this.queue.delete(id);
    }

    /** Bir kare çiz: o an bekleyen bütün işleri çalıştır. */
    tick() {
        const işler = [...this.queue.entries()];
        this.queue.clear();
        for (const [, run] of işler)
            run();
        return işler.length;
    }
}


section('1. Bin olay, bir kare');
{
    const frames = new FakeFrames();
    let applied = 0;
    const coalescer = new FrameCoalescer(() => applied++, {
        schedule: (run) => frames.schedule(run),
        cancel: (id) => frames.cancel(id),
    });

    for (let i = 0; i < 1000; i++)
        coalescer.request();
    check('kare gelmeden hiç uygulanmadı', applied === 0, String(applied));
    check('tek bir iş kuyruğa girdi', frames.queue.size === 1, String(frames.queue.size));

    frames.tick();
    check('kare başına bir kez uygulandı', applied === 1, String(applied));
    check('sayaçlar doğru', coalescer.stats.requests === 1000 && coalescer.stats.applied === 1,
        JSON.stringify(coalescer.stats));

    // 60 kare boyunca 1000 Hz: 60 çizim, 60000 olay.
    let events = 0;
    for (let kare = 0; kare < 60; kare++) {
        for (let i = 0; i < 1000 / 60; i++) {
            coalescer.request();
            events++;
        }
        frames.tick();
    }
    check('60 karede 60 çizim', applied === 61, String(applied));
    check('olaylar çizimden çok daha fazla', coalescer.stats.requests > coalescer.stats.applied * 10,
        JSON.stringify(coalescer.stats));
}

section('2. Kare içinden gelen istek düşmüyor');
{
    const frames = new FakeFrames();
    const applied = [];
    const coalescer = new FrameCoalescer(() => {
        applied.push(applied.length);
        if (applied.length === 1)
            coalescer.request();    // işin İÇİNDEN yeni bir istek
    }, {schedule: (run) => frames.schedule(run), cancel: (id) => frames.cancel(id)});

    coalescer.request();
    frames.tick();
    check('ilk kare uygulandı', applied.length === 1, String(applied.length));
    check('içeriden gelen istek bir sonraki kareye kuruldu', frames.queue.size === 1,
        String(frames.queue.size));
    frames.tick();
    check('ikinci kare de uygulandı', applied.length === 2, String(applied.length));
}

section('3. Kapanışta bekleyen iş iptal ediliyor');
{
    const frames = new FakeFrames();
    let applied = 0;
    const coalescer = new FrameCoalescer(() => applied++, {
        schedule: (run) => frames.schedule(run),
        cancel: (id) => frames.cancel(id),
    });

    coalescer.request();
    coalescer.cancel();
    check('iş kuyruktan düştü', frames.queue.size === 0, String(frames.queue.size));
    check('iptal edilen kimlik bildirildi', frames.canceled.length === 1,
        JSON.stringify(frames.canceled));
    frames.tick();
    check('aktör yokken çalışmadı', applied === 0, String(applied));

    // İkinci `cancel` zararsız olmalı: kapanış yolu iki kez geçebiliyor.
    coalescer.cancel();
    check('ikinci iptal zararsız', frames.canceled.length === 1, JSON.stringify(frames.canceled));

    // İptalden sonra yeniden istenebilir.
    coalescer.request();
    frames.tick();
    check('iptalden sonra yeniden kurulabiliyor', applied === 1, String(applied));
}

section('4. İptal hatası kapanışı durdurmuyor');
{
    const coalescer = new FrameCoalescer(() => {}, {
        schedule: () => 7,
        cancel: () => {
            throw new Error('kabuk kapanıyor');
        },
    });
    coalescer.request();
    let threw = false;
    try {
        coalescer.cancel();
    } catch {
        threw = true;
    }
    check('iptal hatası yutuldu', !threw);
    coalescer.request();
    check('iptalden sonra durum temiz', coalescer.stats.requests === 2,
        JSON.stringify(coalescer.stats));
}

section('5. Zamanlayıcı kimlik vermezse bir daha kurulur');
{
    // `Meta.Laters.add` 0 döndürmemeli, ama dönerse bekleyen iş yok demektir:
    // bir sonraki istek yeniden kurmalı, yoksa imleç yerinde donar.
    let scheduled = 0;
    const coalescer = new FrameCoalescer(() => {}, {
        schedule: () => {
            scheduled++;
            return 0;
        },
    });
    coalescer.request();
    coalescer.request();
    check('her istek yeniden kurdu', scheduled === 2, String(scheduled));
}

print(`\n\x1b[1m${passed} geçti, ${failed} kaldı\x1b[0m`);
if (failed > 0)
    imports.system.exit(1);
