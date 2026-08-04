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

import {CursorOverlay} from './cursor.js';
import {FrameOverlay} from './frame.js';
import * as SelfTest from './selftest.js';
import {UnlockState, defaultStatePath} from './state.js';

export const LOG = '[pcbridge-gorunur]';

export default class PcbridgeGorunurExtension extends Extension {
    enable() {
        this._state = null;
        this._frame = null;
        this._cursor = null;
        this._selfTestId = 0;
        this._markerMonitor = null;
        this._markerId = 0;
        this._markerDebounce = 0;
        try {
            this._frame = new FrameOverlay();
            this._frame.start();
            this._cursor = new CursorOverlay();

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

                    // Gözlenip TEKRARLANAMAYAN arızayı taklit et: durum
                    // izleyicisi susarsa ne oluyor? Emniyet zamanlayıcısının
                    // (cursor.js `_armDeadline`) fiilen çalıştığını başka
                    // türlü kanıtlayamıyoruz.
                    if (this._markerContent(yol) === 'durum-izleyiciyi-durdur') {
                        console.warn(`${LOG} TEST: durum izleyicisi bilerek ` +
                            'durduruldu — emniyet zamanlayıcısı devralmalı');
                        this._state?.stop();
                        return GLib.SOURCE_REMOVE;
                    }
                    // Kendi imlecimiz zaten çalışıyorsa ölçüm ANLAMSIZ ve
                    // YANILTICI olur: ölçüm imleci geri açar, katman
                    // `visibility-changed`'de hemen yeniden gizler, test de
                    // "geri açılmadı" diye yanlış FAIL verir.
                    if (this._cursor?.active) {
                        console.log(`${LOG} ölçüm atlandı: kendi imlecimiz etkin ` +
                            '(gizleme zaten çalışıyor demektir)');
                        return GLib.SOURCE_REMOVE;
                    }
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

    /** İşaret dosyasının içeriği (varsa), kırpılmış. Yalnızca ölçüm kipinde. */
    _markerContent(yol) {
        try {
            const [ok, bytes] = GLib.file_get_contents(yol);
            return ok ? new TextDecoder().decode(bytes).trim() : '';
        } catch {
            return '';
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
            // İmleç ÖNCE: gerçek imleci geri açmak her şeyden önce gelir.
            // Bırakmayı unutmak yasak (`hold_max_seconds` deseni).
            this._cursor?.stop();
            this._cursor = null;
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
        // `until` de veriliyor: imleç katmanı kendi son kullanma zamanını
        // tutuyor ve durum izleyicisi susarsa bile izin penceresinden uzun
        // yaşamıyor (bkz. cursor.js `_armDeadline`).
        this._cursor?.setVisible(aktif, until);

        // Ölçüm çerçeve GÖRÜNÜRKEN yapılmalı: tıklama testi görünmeyen bir
        // aktörle anlamsız olurdu. Belirme animasyonunun bitmesini bekliyoruz.
        if (aktif && SelfTest.selfTestEnabled() && !this._selfTestId) {
            this._selfTestId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
                this._selfTestId = 0;
                SelfTest.checkClickThrough();
                // İmleç gizleme ölçümü BURADA çalışmıyor: artık kendi imleç
                // katmanımız gizlemeyi zaten yapıyor. Ölçüm gerekirse işaret
                // dosyasına dokunulur ve katman kapalıyken koşar.
                return GLib.SOURCE_REMOVE;
            });
        }
    }
}
