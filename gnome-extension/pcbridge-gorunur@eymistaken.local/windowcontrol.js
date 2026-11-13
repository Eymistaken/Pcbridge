/* Narrow GNOME Shell window surface for pcbridge.
 *
 * The exported D-Bus interface intentionally has three custom methods: one
 * activates an already-open window, one names the window that has focus
 * (step 8.1), and one names it together with its frame, for window capture
 * (2.8). It never lists, moves, resizes, or closes windows. Every call
 * rereads the desktop grant before touching the compositor.
 */

import Gio from 'gi://Gio';

export const BUS_NAME = 'io.github.eymistaken.Pcbridge.WindowFocus';
export const OBJECT_PATH = '/io/github/eymistaken/Pcbridge/WindowFocus';
export const INTERFACE_NAME = 'io.github.eymistaken.Pcbridge.WindowFocus';
export const MAX_TARGET_LENGTH = 200;
// Longest identity field FocusedWindow returns; a longer title is cut.
export const MAX_FIELD_LENGTH = 200;

const INTERFACE_XML = `
<node>
  <interface name="${INTERFACE_NAME}">
    <method name="ActivateWindow">
      <arg name="target" type="s" direction="in"/>
      <arg name="activated" type="b" direction="out"/>
    </method>
    <method name="FocusedWindow">
      <arg name="found" type="b" direction="out"/>
      <arg name="wm_class" type="s" direction="out"/>
      <arg name="app_id" type="s" direction="out"/>
      <arg name="title" type="s" direction="out"/>
    </method>
    <method name="FocusedWindowFrame">
      <arg name="found" type="b" direction="out"/>
      <arg name="wm_class" type="s" direction="out"/>
      <arg name="app_id" type="s" direction="out"/>
      <arg name="title" type="s" direction="out"/>
      <arg name="x" type="i" direction="out"/>
      <arg name="y" type="i" direction="out"/>
      <arg name="width" type="i" direction="out"/>
      <arg name="height" type="i" direction="out"/>
    </method>
    <property name="Version" type="s" access="read"/>
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

function rawField(window, method) {
    try {
        return String(window?.[method]?.() ?? '').slice(0, MAX_FIELD_LENGTH);
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
        version = '',
    } = {}) {
        this._state = state;
        this._version = String(version);
        this._listWindows = listWindows;
        this._currentTime = currentTime;
        this._focusedWindow = focusedWindow;
        this._onActivated = onActivated;
        this._ownerId = 0;
        this._exported = false;
        this._started = false;
        this._dbus = Gio.DBusExportedObject.wrapJSObject(INTERFACE_XML, this);
    }

    /** D-Bus property (2.0): lets the server tell an old extension from a new
     * one. The two methods above are unchanged byte for byte. */
    get Version() {
        return this._version;
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
            () => console.log('[pcbridge-gorunur] window activation ready'),
            () => {
                if (this._started)
                    console.warn('[pcbridge-gorunur] could not own the window activation D-Bus name');
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
                console.warn(`[pcbridge-gorunur] activation check: ${error}`);
            }
            return activated;
        } catch (error) {
            console.warn(`[pcbridge-gorunur] could not activate the window: ${error}`);
            return false;
        }
    }

    /** Name the window that has keyboard focus, while the grant is current.
     *
     * pcbridge's second focus source (step 8.1). AT-SPI marks no window
     * ACTIVE when the focused one does not take part in accessibility -- a
     * game, many Java and Electron windows -- and a batch that cannot read
     * focus refuses to click. The compositor always knows. Returns
     * `[found, wm_class, app_id, title]`; `found` is false with the grant
     * closed or when no window has focus (the overview, an empty desktop),
     * and pcbridge then keeps refusing exactly as before.
     */
    FocusedWindow() {
        const none = [false, '', '', ''];
        try {
            this._state.refresh();
            if (!this._state.active)
                return none;
            const window = this._focusedWindow();
            if (!window)
                return none;
            return [
                true,
                rawField(window, 'get_wm_class'),
                rawField(window, 'get_gtk_application_id'),
                rawField(window, 'get_title'),
            ];
        } catch (error) {
            console.warn(`[pcbridge-gorunur] cannot read the focused window: ${error}`);
            return none;
        }
    }

    /** The focused window and its frame, while the grant is current (2.8).
     *
     * Window capture on GNOME takes this box out of the native monitor frame,
     * as on KDE Plasma and Hyprland, instead of running gnome-screenshot (a
     * white flash and a sound on every capture, and a picture with no place
     * on the screen). The frame is `Meta.Window.get_frame_rect()`, in the
     * stage coordinates DisplayConfig lays the monitors out in. Returns
     * `[found, wm_class, app_id, title, x, y, width, height]`; `found` is
     * false with the grant closed, with no focused window, or with a frame
     * of no size. `FocusedWindow` above stays as it was for older servers.
     */
    FocusedWindowFrame() {
        const none = [false, '', '', '', 0, 0, 0, 0];
        try {
            this._state.refresh();
            if (!this._state.active)
                return none;
            const window = this._focusedWindow();
            if (!window)
                return none;
            const rect = window.get_frame_rect();
            const box = [rect?.x, rect?.y, rect?.width, rect?.height].map(Number);
            if (!box.every(Number.isInteger) || box[2] <= 0 || box[3] <= 0)
                return none;
            return [
                true,
                rawField(window, 'get_wm_class'),
                rawField(window, 'get_gtk_application_id'),
                rawField(window, 'get_title'),
                ...box,
            ];
        } catch (error) {
            console.warn(`[pcbridge-gorunur] cannot read the focused window frame: ${error}`);
            return none;
        }
    }
}
