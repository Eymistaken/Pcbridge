/* pcbridge — Ajan Görünür
 *
 * pcbridge bir ajana klavye/fare/ekran erişimi verdiğinde bunun tek görünür
 * işareti GNOME'un üst çubuktaki küçük paylaşım simgesi. Bu eklenti aynı
 * durumu göz kaçırmayacak şekilde gösterir.
 *
 * Görsel katmana ek olarak tek, dar bir D-Bus yöntemi sunar:
 * `ActivateWindow(hedef) -> bool`. Pencere listelemez, taşımaz, kapatmaz veya
 * boyutlandırmaz; her çağrıda `desktop_unlock.json` grant'ini yeniden okur.
 *
 * GNOME 46 / Wayland. Kod değişince kabuk yeniden başlamalı (ESM önbelleği):
 * geliştirme için `./nested.sh`.
 */

import GLib from 'gi://GLib';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {FrameOverlay} from './frame.js';
import * as SelfTest from './selftest.js';
import {UnlockState, defaultStatePath} from './state.js';
import {WindowControl} from './windowcontrol.js';

export const LOG = '[pcbridge-gorunur]';

export default class PcbridgeGorunurExtension extends Extension {
    enable() {
        this._state = null;
        this._frame = null;
        this._selfTestId = 0;
        this._watchdogId = 0;
        this._windowControl = null;
        try {
            this._frame = new FrameOverlay();
            this._frame.start();

            if (SelfTest.selfTestEnabled()) {
                SelfTest.reportMonitors();
                this._watchdogId = SelfTest.startLoopWatchdog();
            }

            const yol = defaultStatePath();
            this._state = new UnlockState(yol, (aktif, until) => this._onState(aktif, until));
            this._state.start();
            this._windowControl = new WindowControl(this._state, {
                onActivated: SelfTest.selfTestEnabled()
                    ? (window, target) => SelfTest.reportWindowActivation(window, target)
                    : null,
            });
            this._windowControl.start();
            console.log(`${LOG} etkin · durum dosyası: ${yol} · başlangıç: ` +
                `${this._state.active ? 'AKTİF' : 'pasif'}`);
        } catch (error) {
            console.error(`${LOG} enable: ${error}`);
            // Yarım kurulmuş bir eklenti bırakma: ne kurulduysa geri al.
            this.disable();
        }
    }

    disable() {
        try {
            for (const alan of ['_selfTestId', '_watchdogId']) {
                if (this[alan]) {
                    GLib.Source.remove(this[alan]);
                    this[alan] = 0;
                }
            }
            this._windowControl?.stop();
            this._windowControl = null;
            this._state?.stop();
            this._state = null;
            this._frame?.stop();
            this._frame = null;
            console.log(`${LOG} kapatıldı`);
        } catch (error) {
            console.error(`${LOG} disable: ${error}`);
        }
    }

    /** pcbridge'in masaüstü izni açıldı/kapandı. */
    _onState(aktif, until) {
        const kalan = Math.max(0, Math.round(until - Date.now() / 1000));
        console.log(`${LOG} durum: ${aktif ? `AKTİF (${kalan} sn kaldı)` : 'pasif'}`);
        this._frame?.setVisible(aktif);

        // Ölçüm çerçeve GÖRÜNÜRKEN yapılmalı: tıklama testi görünmeyen bir
        // aktörle anlamsız olurdu. Belirme animasyonunun bitmesini bekliyoruz.
        if (aktif && SelfTest.selfTestEnabled() && !this._selfTestId) {
            this._selfTestId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
                this._selfTestId = 0;
                SelfTest.checkClickThrough();
                SelfTest.reportBreathing(this._frame?.actors ?? []);
                return GLib.SOURCE_REMOVE;
            });
        }
    }
}
