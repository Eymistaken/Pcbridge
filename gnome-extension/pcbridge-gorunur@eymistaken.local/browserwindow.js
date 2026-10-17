/* The pcbridge browser's windows: hide, show, and say where they are.
 *
 * pcbridge runs its own Chrome/Brave for the browser tools and keeps it off
 * screen until someone asks for it (ROADMAP, "Show and hide the browser
 * window on demand"). A Wayland client cannot un-minimize itself, so the
 * compositor does it, here.
 *
 * The browser is found by its process id, never by name: the keeper's
 * record (`<state>/pcbridge/browser/keeper.json`) names the browser's pid,
 * and that pid must be a child of the keeper process, whose command line
 * names keeper.py. A caller cannot point this at another window. Nothing
 * here sends input, so the desktop grant does not apply (as for the
 * browser tools themselves).
 *
 * Mutter 14 offers no way to set another client's skip-taskbar, so a
 * hidden window is a minimized one: it stays in the task list as its own
 * entry, and is shown again without focus unless focus is asked for.
 */

import GLib from 'gi://GLib';

export const FOCUS_GUARD_MS = 2000;

export function defaultBrowserDir() {
    const override = GLib.getenv('PCBRIDGE_GORUNUR_BROWSER');
    if (override)
        return override;
    return GLib.build_filenamev([GLib.get_user_state_dir(), 'pcbridge', 'browser']);
}

function readText(path) {
    try {
        const [ok, bytes] = GLib.file_get_contents(path);
        return ok ? new TextDecoder().decode(bytes) : null;
    } catch {
        return null;
    }
}

function readJson(path) {
    const text = readText(path);
    if (text === null)
        return null;
    try {
        return JSON.parse(text);
    } catch {
        return null;
    }
}

/** The parent pid from /proc/<pid>/stat (the field after the command). */
export function parentPid(statText) {
    const close = statText.lastIndexOf(')');
    if (close < 0)
        return 0;
    const fields = statText.slice(close + 2).split(' ');
    return Number.parseInt(fields[1], 10) || 0;
}

/** The browser pid the keeper record names, or 0 when it does not check out. */
export function browserPid(dir, readFile = readText) {
    let info;
    try {
        info = JSON.parse(readFile(GLib.build_filenamev([dir, 'keeper.json'])) ?? 'null');
    } catch {
        return 0;
    }
    const keeper = Number(info?.keeper_pid) || 0;
    const browser = Number(info?.browser_pid) || 0;
    if (!keeper || !browser)
        return 0;
    const cmdline = readFile(`/proc/${keeper}/cmdline`) ?? '';
    if (!cmdline.includes('keeper.py'))
        return 0;
    if (parentPid(readFile(`/proc/${browser}/stat`) ?? '') !== keeper)
        return 0;
    return browser;
}

/** Whether the browser should be on screen: `view.json`, hidden by default. */
export function wantedVisible(dir, readFile = readText) {
    try {
        return JSON.parse(readFile(GLib.build_filenamev([dir, 'view.json'])) ?? 'null')?.visible === true;
    } catch {
        return false;
    }
}

function windowPid(window) {
    try {
        return window.get_pid();
    } catch {
        return -1;
    }
}

function field(window, method) {
    try {
        return String(window?.[method]?.() ?? '').slice(0, 200);
    } catch {
        return '';
    }
}

export class BrowserWindows {
    constructor({
        dir = defaultBrowserDir(),
        listWindows,
        focusedWindow,
        currentTime,
        display = null,
        readFile = readText,
    }) {
        this._dir = dir;
        this._listWindows = listWindows;
        this._focusedWindow = focusedWindow;
        this._currentTime = currentTime;
        this._display = display;
        this._readFile = readFile;
        this._createdId = 0;
        this._focusId = 0;
        this._focusTimer = 0;
    }

    /** Hide a new browser window before it is shown when it should be
     * hidden, so a (re)started browser neither flashes nor takes focus. */
    start() {
        if (!this._display || this._createdId)
            return;
        this._createdId = this._display.connect('window-created', (_display, window) => {
            try {
                const pid = browserPid(this._dir, this._readFile);
                if (pid && windowPid(window) === pid && !wantedVisible(this._dir, this._readFile))
                    window.minimize();
            } catch (error) {
                console.warn(`[pcbridge-gorunur] browser window: ${error}`);
            }
        });
    }

    stop() {
        this._releaseFocusGuard();
        if (this._createdId) {
            this._display.disconnect(this._createdId);
            this._createdId = 0;
        }
    }

    windows() {
        const pid = browserPid(this._dir, this._readFile);
        if (!pid)
            return [];
        return this._listWindows().filter(w => windowPid(w) === pid);
    }

    /** [found, visible, focused] */
    state() {
        const windows = this.windows();
        const focused = this._focusedWindow();
        return [
            windows.length > 0,
            windows.some(w => !w.minimized),
            windows.some(w => w === focused),
        ];
    }

    /** Minimize, or un-minimize (and with `focus` activate) every browser
     * window. Returns state() afterwards. */
    setVisible(visible, focus) {
        // A new request ends the previous one's focus guard: it would
        // otherwise take back focus that is now asked for.
        this._releaseFocusGuard();
        const windows = this.windows();
        const before = this._focusedWindow();
        for (const window of windows) {
            if (!visible) {
                window.minimize();
            } else {
                window.unminimize();
                if (focus)
                    window.activate(this._currentTime());
            }
        }
        if (visible && !focus && before && !windows.includes(before))
            this._keepFocus(before);
        return this.state();
    }

    /** Give focus back to `window` if a browser window takes it within
     * FOCUS_GUARD_MS. Measured in a nested GNOME 46 shell: a window that was
     * minimized when it was created is focused when it is first shown, about
     * half a second after the un-minimize (Mutter treats it as a new window);
     * later shows do not take focus. */
    _keepFocus(window) {
        if (!this._display)
            return;
        this._releaseFocusGuard();
        this._focusId = this._display.connect('notify::focus-window', () => {
            const now = this._focusedWindow();
            if (now && this.windows().includes(now)) {
                try {
                    window.activate(this._currentTime());
                } catch (error) {
                    console.warn(`[pcbridge-gorunur] focus back: ${error}`);
                }
                this._releaseFocusGuard();
            }
        });
        this._focusTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, FOCUS_GUARD_MS, () => {
            this._focusTimer = 0;
            this._releaseFocusGuard();
            return GLib.SOURCE_REMOVE;
        });
    }

    _releaseFocusGuard() {
        if (this._focusId) {
            this._display.disconnect(this._focusId);
            this._focusId = 0;
        }
        if (this._focusTimer) {
            GLib.Source.remove(this._focusTimer);
            this._focusTimer = 0;
        }
    }

    /** [title, app id, minimized, focused] per window, for status output. */
    describe() {
        const focused = this._focusedWindow();
        return this.windows().map(w => [
            field(w, 'get_title'),
            field(w, 'get_gtk_application_id') || field(w, 'get_wm_class'),
            Boolean(w.minimized),
            w === focused,
        ]);
    }
}
