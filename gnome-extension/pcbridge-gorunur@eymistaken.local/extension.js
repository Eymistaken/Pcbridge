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
 * geliştirme için `dbus-run-session -- gnome-shell --nested --wayland`.
 */

import GLib from 'gi://GLib';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export const LOG = '[pcbridge-gorunur]';

/** pcbridge'in izin durumunu yazdığı dosya (`pcbridge/desktop/safety.py`). */
export function statePath() {
    // pcbridge `~/.local/state/pcbridge` kullanıyor; XDG_STATE_HOME'a saygılı
    // olmak için sabit yol yerine GLib'den soruyoruz.
    return GLib.build_filenamev([GLib.get_user_state_dir(), 'pcbridge', 'desktop_unlock.json']);
}

export default class PcbridgeGorunurExtension extends Extension {
    enable() {
        try {
            console.log(`${LOG} etkin · durum dosyası: ${statePath()}`);
        } catch (error) {
            console.error(`${LOG} enable: ${error}`);
        }
    }

    disable() {
        try {
            console.log(`${LOG} kapatıldı`);
        } catch (error) {
            console.error(`${LOG} disable: ${error}`);
        }
    }
}
