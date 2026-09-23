/* pcbridge — Ajan Görünür
 *
 * pcbridge bir ajana klavye/fare/ekran erişimi verdiğinde bunun tek görünür
 * işareti GNOME'un üst çubuktaki küçük paylaşım simgesi. Bu eklenti aynı
 * durumu göz kaçırmayacak şekilde gösterir.
 *
 * Görsel katmana ek olarak iki dar D-Bus yöntemi sunar:
 * `ActivateWindow(hedef) -> bool` açık bir pencereyi öne alır,
 * `FocusedWindow() -> (bool, wm_class, app_id, başlık)` odaktaki pencerenin
 * adını söyler (Adım 8.1: AT-SPI'ın göremediği pencerede odak kontrolü).
 * Pencere listelemez, taşımaz, kapatmaz veya boyutlandırmaz; her çağrıda
 * `desktop_unlock.json` grant'ini yeniden okur.
 *
 * GNOME 46 / Wayland. Kod değişince kabuk yeniden başlamalı (ESM önbelleği):
 * geliştirme için `./nested.sh`.
 */

import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {CursorOverlay, cursorEnabled} from './cursor.js';
import {FrameOverlay} from './frame.js';
import {PcbridgeIndicator} from './indicator.js';
import * as SelfTest from './selftest.js';
import {UnlockState, defaultStatePath} from './state.js';
import {StatusWatcher, defaultStatusPath} from './status.js';
import {WindowControl} from './windowcontrol.js';

export const LOG = '[pcbridge-gorunur]';

export default class PcbridgeGorunurExtension extends Extension {
    enable() {
        this._state = null;
        this._frame = null;
        this._cursor = null;
        this._selfTestId = 0;
        this._watchdogId = 0;
        this._windowControl = null;
        this._status = null;
        this._indicator = null;
        this._shortcut = false;
        try {
            this._frame = new FrameOverlay();
            this._frame.start();
            // İmleç katmanı VARSAYILAN KAPALI. Bir kez çıkarılmıştı (gerçek
            // farede tıklamayı bozmuştu) ve düzeltmesi gerçek oturumda
            // doğrulanmadı. İşaret dosyası her izin açılışında yeniden
            // okunuyor (`_onState`), yani açıp kapatmak kabuğu yeniden
            // başlatmayı gerektirmiyor.
            this._cursor = new CursorOverlay();

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
                version: this.metadata['version-name'] ?? '',
            });
            this._windowControl.start();
            this._startIndicator();
            console.log(`${LOG} enabled · state file: ${yol} · initially: ` +
                `${this._state.active ? 'ACTIVE' : 'inactive'}`);
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
            this._stopIndicator();
            this._windowControl?.stop();
            this._windowControl = null;
            this._state?.stop();
            this._state = null;
            // İmleç ÖNCE: gerçek imleci geri vermek her şeyden önce gelir.
            this._cursor?.stop();
            this._cursor = null;
            this._frame?.stop();
            this._frame = null;
            console.log(`${LOG} disabled`);
        } catch (error) {
            console.error(`${LOG} disable: ${error}`);
        }
    }

    /** Panel indicator + optional kill-switch shortcut (2.0). A failure here
     * only loses the indicator: the frame and the D-Bus methods keep working. */
    _startIndicator() {
        try {
            this._status = new StatusWatcher(defaultStatusPath(), () => this._indicator?.update());
            this._status.start();
            this._indicator = new PcbridgeIndicator(this._state, this._status);
            Main.panel.addToStatusArea(this.uuid, this._indicator);
            if (SelfTest.selfTestEnabled())
                SelfTest.reportIndicator(this._indicator);
        } catch (error) {
            console.error(`${LOG} indicator: ${error}`);
        }
        // OFF by default: `lock-shortcut` is empty until the user sets it.
        // Without the compiled schema (a copy made by hand) only the shortcut
        // is skipped.
        try {
            Main.wm.addKeybinding('lock-shortcut', this.getSettings(),
                Meta.KeyBindingFlags.NONE,
                Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW | Shell.ActionMode.POPUP,
                () => this._indicator?.lockNow());
            this._shortcut = true;
        } catch (error) {
            console.warn(`${LOG} kill-switch shortcut unavailable: ${error}`);
        }
    }

    _stopIndicator() {
        if (this._shortcut) {
            Main.wm.removeKeybinding('lock-shortcut');
            this._shortcut = false;
        }
        this._indicator?.destroy();
        this._indicator = null;
        this._status?.stop();
        this._status = null;
    }

    /** pcbridge'in masaüstü izni açıldı/kapandı. */
    _onState(aktif, until) {
        this._indicator?.update();
        const kalan = Math.max(0, Math.round(until - Date.now() / 1000));
        console.log(`${LOG} state: ${aktif ? `ACTIVE (${kalan} s left)` : 'inactive'}`);
        this._frame?.setVisible(aktif);
        // Katman kapalıysa hiçbir şey yapılmıyor ve gerçek imleç el
        // değmeden duruyor. Açıkken izin bitince kendi kendini kapatıyor.
        if (this._cursor) {
            if (aktif && cursorEnabled())
                this._cursor.setVisible(true, until);
            else
                this._cursor.setVisible(false);
        }

        // Ölçüm çerçeve GÖRÜNÜRKEN yapılmalı: tıklama testi görünmeyen bir
        // aktörle anlamsız olurdu. Belirme animasyonunun bitmesini bekliyoruz.
        if (aktif && SelfTest.selfTestEnabled() && !this._selfTestId) {
            this._selfTestId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
                this._selfTestId = 0;
                SelfTest.checkClickThrough();
                SelfTest.reportBreathing(this._frame?.actors ?? []);
                // Fare fırtınası AYRICA isteniyor: imleci gerçekten
                // oynatıyor, yani her ölçüm koşumunda kendiliğinden
                // çalışmamalı.
                if (GLib.getenv('PCBRIDGE_GORUNUR_BURST') === '1')
                    SelfTest.pointerBurst(this._cursor);
                return GLib.SOURCE_REMOVE;
            });
        }
    }
}
