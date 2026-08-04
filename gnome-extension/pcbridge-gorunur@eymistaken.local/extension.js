/* pcbridge — Ajan Görünür
 *
 * pcbridge bir ajana klavye/fare/ekran erişimi verdiğinde bunun tek görünür
 * işareti GNOME'un üst çubuktaki küçük paylaşım simgesi. Bu eklenti aynı
 * durumu göz kaçırmayacak şekilde gösterir.
 *
 * TAMAMEN GÖRSEL. Hiçbir şeye tıklamaz, hiçbir şey yazmaz, pcbridge'in
 * davranışını değiştirmez. Yalnızca `desktop_unlock.json`'ı OKUR.
 *
 * GNOME 46 / Wayland. Kod değişince kabuk yeniden başlamalı (ESM önbelleği):
 * geliştirme için `./nested.sh`.
 */

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {FrameOverlay} from './frame.js';
import * as SelfTest from './selftest.js';
import {UnlockState, defaultStatePath} from './state.js';

export const LOG = '[pcbridge-gorunur]';

export default class PcbridgeGorunurExtension extends Extension {
    enable() {
        this._state = null;
        this._frame = null;
        this._selfTestId = 0;
        this._markerMonitor = null;
        this._markerId = 0;
        this._markerDebounce = 0;
        try {
            this._frame = new FrameOverlay();
            this._frame.start();

            if (SelfTest.selfTestEnabled()) {
                SelfTest.reportMonitors();
                this._watchSelfTestMarker();
            }

            const yol = defaultStatePath();
            this._state = new UnlockState(yol, (aktif, until) => this._onState(aktif, until));
            this._state.start();
            console.log(`${LOG} etkin · durum dosyası: ${yol} · başlangıç: ` +
                `${this._state.active ? 'AKTİF' : 'pasif'}`);
        } catch (error) {
            console.error(`${LOG} enable: ${error}`);
            // Yarım kurulmuş bir eklenti bırakma: ne kurulduysa geri al.
            this.disable();
        }
    }

    /** İşaret dosyasına her dokunulduğunda ölçümü yeniden koştur.
     *
     * Bir kere koşan ölçüm yetmiyor: imleç gizliyken ekran görüntüsü almak
     * için ölçümün İSTENDİĞİ AN başlaması gerekiyor. `touch` yeter. */
    _watchSelfTestMarker() {
        const yol = SelfTest.selfTestMarkerPath();
        try {
            this._markerMonitor = Gio.File.new_for_path(yol).monitor_file(
                Gio.FileMonitorFlags.NONE, null);
            this._markerId = this._markerMonitor.connect('changed', () => {
                if (this._markerDebounce)
                    return;
                this._markerDebounce = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, () => {
                    this._markerDebounce = 0;
                    console.log(`${LOG} işaret dosyasına dokunuldu → ölçüm`);
                    SelfTest.probeCursorHiding();
                    return GLib.SOURCE_REMOVE;
                });
            });
            console.log(`${LOG} ölçüm işaret dosyası izleniyor: ${yol}`);
        } catch (error) {
            console.warn(`${LOG} işaret dosyası izlenemedi: ${error}`);
        }
    }

    disable() {
        try {
            for (const alan of ['_selfTestId', '_markerDebounce']) {
                if (this[alan]) {
                    GLib.Source.remove(this[alan]);
                    this[alan] = 0;
                }
            }
            if (this._markerMonitor) {
                if (this._markerId)
                    this._markerMonitor.disconnect(this._markerId);
                this._markerMonitor.cancel();
                this._markerMonitor = null;
                this._markerId = 0;
            }
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
                SelfTest.probeCursorHiding();
                return GLib.SOURCE_REMOVE;
            });
        }
    }
}
