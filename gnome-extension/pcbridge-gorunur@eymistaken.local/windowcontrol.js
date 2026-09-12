/* Narrow GNOME Shell window activation surface for pcbridge.
 *
 * The exported D-Bus interface intentionally has one custom method. It never
 * lists, moves, resizes, or closes windows. Every call rereads the desktop
 * grant before touching the compositor.
 */

import Gio from 'gi://Gio';

export const BUS_NAME = 'io.github.eymistaken.Pcbridge.WindowFocus';
export const OBJECT_PATH = '/io/github/eymistaken/Pcbridge/WindowFocus';
export const INTERFACE_NAME = 'io.github.eymistaken.Pcbridge.WindowFocus';
export const MAX_TARGET_LENGTH = 200;

const INTERFACE_XML = `
<node>
  <interface name="${INTERFACE_NAME}">
    <method name="ActivateWindow">
      <arg name="target" type="s" direction="in"/>
      <arg name="activated" type="b" direction="out"/>
    </method>
  </interface>
</node>`;

function normalize(value) {
    return String(value ?? '')
        .normalize('NFKC')
        .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
        .toLowerCase()
        .replace(/[\s._-]+/g, ' ')
        .trim();
}

function readField(window, method) {
    try {
        return normalize(window?.[method]?.());
    } catch {
        return '';
    }
}

function isSkipTaskbar(window) {
    try {
        if (typeof window?.is_skip_taskbar === 'function')
            return Boolean(window.is_skip_taskbar());
        return Boolean(window?.skip_taskbar);
    } catch {
        return true;
    }
}

function fieldScore(query, value) {
    if (!value)
        return 0;
    if (value === query)
        return 3;
    if (value.startsWith(query) || value.endsWith(query))
        return 2;
    return value.includes(query) ? 1 : 0;
}

/** Return one deterministic best match, or null when none/ambiguous. */
export function chooseWindow(target, windows) {
    const query = normalize(target);
    if (!query || query.length > MAX_TARGET_LENGTH)
        return null;

    const ranked = [];
    for (const window of windows ?? []) {
        if (!window || isSkipTaskbar(window))
            continue;
        const fields = [
            readField(window, 'get_title'),
            readField(window, 'get_wm_class'),
            readField(window, 'get_gtk_application_id'),
        ];
        const score = Math.max(...fields.map(value => fieldScore(query, value)));
        if (score > 0)
            ranked.push({window, score});
    }

    if (!ranked.length)
        return null;
    const bestScore = Math.max(...ranked.map(item => item.score));
    const best = ranked.filter(item => item.score === bestScore);
    return best.length === 1 ? best[0].window : null;
}

function shellWindows() {
    return global.get_window_actors()
        .map(actor => actor.meta_window ?? actor.get_meta_window?.())
        .filter(Boolean);
}

/** Own and implement the extension's single custom D-Bus method. */
export class WindowControl {
    constructor(state, {
        listWindows = shellWindows,
        currentTime = () => global.get_current_time(),
        focusedWindow = () => global.display.focus_window,
        onActivated = null,
    } = {}) {
        this._state = state;
        this._listWindows = listWindows;
        this._currentTime = currentTime;
        this._focusedWindow = focusedWindow;
        this._onActivated = onActivated;
        this._ownerId = 0;
        this._exported = false;
        this._started = false;
        this._dbus = Gio.DBusExportedObject.wrapJSObject(INTERFACE_XML, this);
    }

    start() {
        if (this._ownerId)
            return;
        this._started = true;
        this._ownerId = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            connection => {
                if (!this._started)
                    return;
                this._dbus.export(connection, OBJECT_PATH);
                this._exported = true;
            },
            () => console.log('[pcbridge-gorunur] pencere etkinleştirme hazır'),
            () => {
                if (this._started)
                    console.warn('[pcbridge-gorunur] pencere etkinleştirme D-Bus adı alınamadı');
            },
        );
    }

    stop() {
        this._started = false;
        if (this._exported) {
            this._dbus.unexport();
            this._exported = false;
        }
        if (this._ownerId) {
            Gio.bus_unown_name(this._ownerId);
            this._ownerId = 0;
        }
    }

    /** Activate one already-open window while the pcbridge grant is current. */
    ActivateWindow(target) {
        if (typeof target !== 'string' || !target.trim() ||
            target.length > MAX_TARGET_LENGTH)
            return false;

        try {
            this._state.refresh();
            if (!this._state.active)
                return false;

            const window = chooseWindow(target, this._listWindows());
            if (!window)
                return false;
            window.activate(this._currentTime());
            const activated = this._focusedWindow() === window;
            try {
                this._onActivated?.(window, target);
            } catch (error) {
                console.warn(`[pcbridge-gorunur] etkinleştirme doğrulaması: ${error}`);
            }
            return activated;
        } catch (error) {
            console.warn(`[pcbridge-gorunur] pencere etkinleştirilemedi: ${error}`);
            return false;
        }
    }
}
