#!/usr/bin/env -S gjs -m
/* `state.js` testleri — KABUK GEREKMEZ.
 *
 *     gjs -m gnome-extension/tests/test_state.js
 *
 * `state.js` bilerek yalnızca GLib + Gio kullanıyor, hiçbir kabuk modülü
 * (`resource:///org/gnome/shell/...`) içe aktarmıyor. Bunun karşılığı bu dosya:
 * izin penceresinin mantığı, kabuğu yeniden başlatmadan — yani çıkış/giriş
 * yapmadan — sınanabiliyor.
 *
 * Testler zamana bağlı olduğu için ana döngü üzerinde adım adım yürüyor;
 * her adım bir sonrakini zamanlayıcıyla tetikliyor.
 */

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

import {UnlockState, defaultStatePath} from '../pcbridge-gorunur@eymistaken.local/state.js';

let ok = 0;
let fail = 0;

function check(name, cond, detail = '') {
    if (cond) {
        ok++;
        print(`  \x1b[32mPASS\x1b[0m  ${name}`);
    } else {
        fail++;
        print(`  \x1b[31mFAIL\x1b[0m  ${name}  ${detail}`);
    }
}

function section(title) {
    print(`\n\x1b[1m${title}\x1b[0m`);
}

// ---------------------------------------------------------------- yardımcılar
const tmpDir = GLib.dir_make_tmp('pcbridge-state-test-XXXXXX');
const tmpFile = GLib.build_filenamev([tmpDir, 'desktop_unlock.json']);

function yaz(icerik) {
    GLib.file_set_contents(tmpFile, icerik);
}

function sil() {
    try {
        Gio.File.new_for_path(tmpFile).delete(null);
    } catch { /* zaten yok */ }
}

function simdi() {
    return GLib.get_real_time() / 1e6;
}

function temizle() {
    sil();
    try {
        Gio.File.new_for_path(tmpDir).delete(null);
    } catch { /* boş değilse bırak */ }
}

// Değişim geri çağrılarını kaydeden izleyici.
const olaylar = [];
let state = null;

const loop = GLib.MainLoop.new(null, false);

/** Adımları sırayla, aralarında bekleyerek koşturur. */
function adimlar(liste) {
    let i = 0;
    const sonraki = () => {
        if (i >= liste.length) {
            loop.quit();
            return GLib.SOURCE_REMOVE;
        }
        const [gecikmeMs, fn] = liste[i++];
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, gecikmeMs, () => {
            try {
                fn();
            } catch (error) {
                check(`adım ${i} çalıştı`, false, `${error}`);
            }
            sonraki();
            return GLib.SOURCE_REMOVE;
        });
        return GLib.SOURCE_REMOVE;
    };
    sonraki();
}

// ------------------------------------------------------------------- testler
section('1. Dosya yokken');
sil();
state = new UnlockState(tmpFile, (aktif, until) => olaylar.push([aktif, until]));
state.start();
check('dosya yokken pasif', state.active === false, `active=${state.active}`);
check('dosya yokken olay yok', olaylar.length === 0, `olaylar=${olaylar.length}`);

adimlar([
    // ---------------------------------------------------------------------
    [50, () => {
        section('2. İzin açılınca aktife geçer');
        yaz(JSON.stringify({until: simdi() + 2, reason: 'test', granted: simdi()}));
    }],
    [600, () => {
        check('dosya yazılınca aktif oldu', state.active === true, `active=${state.active}`);
        check('tam bir olay yayınlandı', olaylar.length === 1, `olaylar=${JSON.stringify(olaylar)}`);
        check('olay aktif diyor', olaylar[0]?.[0] === true);
    }],

    // ---------------------------------------------------------------------
    [100, () => {
        section('3. Aynı dosya yeniden yazılınca TEKRAR olay yayınlanmaz');
        yaz(JSON.stringify({until: simdi() + 2, reason: 'test2', granted: simdi()}));
    }],
    [600, () => {
        check('değişim yoksa olay da yok', olaylar.length === 1, `olaylar=${olaylar.length}`);
        check('hâlâ aktif', state.active === true);
    }],

    // ---------------------------------------------------------------------
    [2600, () => {
        section('4. Süre dolunca KENDİLİĞİNDEN pasife düşer');
        // pcbridge süre dolunca dosyayı yeniden yazmıyor: bu geçişi yalnızca
        // state.js'in kendi zamanlayıcısı üretebilir. Testin asıl sebebi bu.
        check('süre dolunca pasif', state.active === false, `active=${state.active}`);
        check('pasif olayı yayınlandı', olaylar.length === 2, `olaylar=${JSON.stringify(olaylar)}`);
        check('olay pasif diyor', olaylar[1]?.[0] === false);
    }],

    // ---------------------------------------------------------------------
    [100, () => {
        section('5. desktop_lock: {"until": 0} anında pasif');
        yaz(JSON.stringify({until: simdi() + 60}));
    }],
    [600, () => {
        check('yeniden aktif', state.active === true, `active=${state.active}`);
        yaz(JSON.stringify({until: 0}));
    }],
    [600, () => {
        check('until=0 ile pasif', state.active === false, `active=${state.active}`);
        check('toplam 4 olay', olaylar.length === 4, `olaylar=${JSON.stringify(olaylar)}`);
    }],

    // ---------------------------------------------------------------------
    [100, () => {
        section('6. Bozuk içerik çökertmiyor');
        yaz(JSON.stringify({until: simdi() + 60}));
    }],
    [600, () => {
        check('önce aktif', state.active === true);
        yaz('{bu gecerli json degil');
    }],
    [600, () => {
        check('bozuk JSON pasife düşürür, çökmez', state.active === false, `active=${state.active}`);
    }],

    // ---------------------------------------------------------------------
    [100, () => {
        section('7. Dosya silinince pasif kalır');
        yaz(JSON.stringify({until: simdi() + 60}));
    }],
    [600, () => {
        check('önce aktif', state.active === true);
        sil();
    }],
    [600, () => {
        check('dosya silinince pasif', state.active === false, `active=${state.active}`);
    }],

    // ---------------------------------------------------------------------
    [50, () => {
        section('8. Açılışta izin AÇIKSA baştan aktif başlar');
        // Eklenti oturum açılışında ya da izin açıkken etkinleştirilebilir;
        // ilk okuma olmazsa çerçeve hiç görünmezdi.
        yaz(JSON.stringify({until: simdi() + 60}));
        const olaylar2 = [];
        const s2 = new UnlockState(tmpFile, (a, u) => olaylar2.push([a, u]));
        s2.start();
        check('start() anında aktif', s2.active === true, `active=${s2.active}`);
        check('start() aktif olayı yayınladı', olaylar2.length === 1 && olaylar2[0][0] === true,
            `olaylar2=${JSON.stringify(olaylar2)}`);
        check('until okundu', s2.until > simdi(), `until=${s2.until}`);
        s2.stop();
    }],

    // ---------------------------------------------------------------------
    [50, () => {
        section('9. stop() sonrası olay gelmez');
        state.stop();
        yaz(JSON.stringify({until: simdi() + 60}));
    }],
    [700, () => {
        check('stop() sonrası sessiz', state.active === false, `active=${state.active}`);
    }],

    // ---------------------------------------------------------------------
    [50, () => {
        section('11. Kayan kira — pcbridge\'in YENİ dosya biçimi');
        // pcbridge artık lease kimliği alanlarını da yazıyor ve `until`
        // her masaüstü eyleminde ileri KAYIYOR. Eklenti kodu bu yüzden
        // değişmedi — yalnızca `until` okuduğu için etkilenmemesi gerekiyor.
        // "Gerekiyor" ölçüm değil; sözleşme burada fiilen sınanıyor.
        state = new UnlockState(tmpFile, (aktif, until) => olaylar.push([aktif, until]));
        olaylar.length = 0;
        yaz(JSON.stringify({
            schema_version: 1,
            grant_id: 'contract-grant',
            revoke_epoch: 7,
            until: simdi() + 90,
            hard_until: simdi() + 900,
            reason: 'olcum',
            granted: simdi(),
            granted_by: 'desktop_unlock',
        }));
        state.start();
        check('yeni biçim aktif okundu', state.active === true, `active=${state.active}`);
        check('bilinmeyen lease kimliği alanları sorun çıkarmadı',
            olaylar.length === 1 && olaylar[0][0] === true, JSON.stringify(olaylar));

        // Kira kaydı: `until` küçüldü ama izin hâlâ açık. Bu SIK oluyor
        // (her eylemde) ve DEĞİŞİM olayı üretmemeli, yoksa çerçeve titrer.
        yaz(JSON.stringify({
            until: simdi() + 12, hard_until: simdi() + 900,
            granted_by: 'desktop_unlock',
        }));
        state._reread();
        check('kayan until sonrası hâlâ aktif', state.active === true);
        check('kayma değişim olayı üretmedi (çerçeve titremiyor)',
            olaylar.length === 1, JSON.stringify(olaylar));

        // Kritik durum: kira bitti ama SERT TAVAN hâlâ gelecekte. Eklenti
        // yalnızca `until`e baktığı için pasif görmeli; `hard_until`e
        // bakılsaydı çerçeve izin kapandığı hâlde ekranda kalırdı.
        yaz(JSON.stringify({
            until: simdi() - 1, hard_until: simdi() + 800,
            granted_by: 'desktop_unlock',
        }));
        state._reread();
        check('kira düştü -> pasif (hard_until gelecekte olsa bile)',
            state.active === false, `active=${state.active}`);
        check('pasife geçiş olayı yayınlandı',
            olaylar.length === 2 && olaylar[1][0] === false, JSON.stringify(olaylar));

        // desktop_lock artık iki alanı da sıfırlıyor.
        yaz(JSON.stringify({until: 0, hard_until: 0}));
        state._reread();
        check('lock biçimi ({until:0, hard_until:0}) pasif', state.active === false);

        // Geriye dönük: diskte hard_until içermeyen ESKİ bir dosya olabilir.
        yaz(JSON.stringify({until: simdi() + 60, reason: 'eski', granted: simdi()}));
        state._reread();
        check('eski biçim (hard_until YOK) hâlâ aktif okunuyor',
            state.active === true, `active=${state.active}`);
        state.stop();
    }],

    // ---------------------------------------------------------------------
    [50, () => {
        section('10. Varsayılan yol pcbridge ile aynı yeri gösteriyor');
        const yol = defaultStatePath();
        check('yol pcbridge/desktop_unlock.json ile bitiyor',
            yol.endsWith('/pcbridge/desktop_unlock.json'), yol);
        check('XDG durum dizininin altında',
            yol.startsWith(GLib.get_user_state_dir()), yol);
    }],
]);

loop.run();
temizle();

print(`\n\x1b[1mSonuc:\x1b[0m ${ok} gecti, ${fail} kaldi`);
imports.system.exit(fail ? 1 : 0);
