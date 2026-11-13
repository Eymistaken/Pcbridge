/* pcbridge's panel item on Cinnamon: the grant at a glance, and the kill switch.
 *
 * The port of the GNOME extension's indicator. The frame around the screens
 * is drawn by pcbridge's native helper on X11, so this applet only shows the
 * grant (an icon and the minutes left while desktop control is open) and
 * offers "Lock desktop control now", which runs `pcbridge lock`.
 *
 * It READS the grant file (`~/.local/state/pcbridge/desktop_unlock.json`,
 * written by pcbridge's safety gate) and `status.json` (written by the
 * daemon) and never writes either. A missing or unreadable file reads as
 * "closed"; nothing here is a step before an agent can work.
 */

const Applet = imports.ui.applet;
const PopupMenu = imports.ui.popupMenu;
const Main = imports.ui.main;
const GLib = imports.gi.GLib;
const Gio = imports.gi.Gio;
const Mainloop = imports.mainloop;

const ICON_OPEN = 'input-mouse-symbolic';
const ICON_IDLE = 'computer-symbolic';
const POLL_SECONDS = 2;

function stateDir() {
    return GLib.build_filenamev([GLib.get_user_state_dir(), 'pcbridge']);
}

function readJson(name) {
    try {
        const [ok, bytes] = GLib.file_get_contents(GLib.build_filenamev([stateDir(), name]));
        if (!ok)
            return null;
        const text = imports.byteArray.toString(bytes);
        return JSON.parse(text);
    } catch (error) {
        return null;
    }
}

/** The grant as this applet shows it: open while `until` is in the future. */
function grant(now) {
    const data = readJson('desktop_unlock.json');
    const until = Number(data && data.until);
    const hard = Number(data && data.hard_until);
    const end = Number.isFinite(hard) && hard > 0 ? Math.min(until, hard) : until;
    if (!Number.isFinite(end) || end <= now)
        return {open: false, left: 0};
    return {open: true, left: end - now};
}

/** The pcbridge command: the one the daemon names, else the usual places. */
function findCli() {
    const status = readJson('status.json');
    const candidates = [];
    if (status && typeof status.cli === 'string')
        candidates.push(status.cli);
    candidates.push(GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'pcbridge']));
    const onPath = GLib.find_program_in_path('pcbridge');
    if (onPath)
        candidates.push(onPath);
    candidates.push('/usr/bin/pcbridge');
    for (const path of candidates) {
        if (path && GLib.file_test(path, GLib.FileTest.IS_EXECUTABLE))
            return path;
    }
    return null;
}

class PcbridgeApplet extends Applet.TextIconApplet {
    constructor(metadata, orientation, panelHeight, instanceId) {
        super(orientation, panelHeight, instanceId);
        this.set_applet_icon_symbolic_name(ICON_IDLE);
        this.set_applet_label('');
        this.set_applet_tooltip('pcbridge: desktop control closed');

        this.menuManager = new PopupMenu.PopupMenuManager(this);
        this.menu = new Applet.AppletPopupMenu(this, orientation);
        this.menuManager.addMenu(this.menu);
        this._line = new PopupMenu.PopupMenuItem('', {reactive: false});
        this.menu.addMenuItem(this._line);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Lock desktop control now', () => this.lockNow());

        this._timer = 0;
        this._monitor = null;
        try {
            const file = Gio.File.new_for_path(GLib.build_filenamev([stateDir(), 'desktop_unlock.json']));
            this._monitor = file.monitor_file(Gio.FileMonitorFlags.NONE, null);
            this._monitor.connect('changed', () => this.update());
        } catch (error) {
            this._monitor = null;
        }
        this.update();
        // The minutes left change without a file event, and the grant ends
        // by time, so the file is read again on a short timer as well.
        this._timer = Mainloop.timeout_add_seconds(POLL_SECONDS, () => {
            this.update();
            return true;
        });
    }

    update() {
        const now = GLib.get_real_time() / 1e6;
        const state = grant(now);
        if (state.open) {
            const minutes = Math.max(1, Math.round(state.left / 60));
            this.set_applet_icon_symbolic_name(ICON_OPEN);
            this.set_applet_label(` ${minutes}m`);
            this.set_applet_tooltip(`pcbridge: desktop control granted, ${minutes} min left`);
            this._line.label.text = `Desktop control: granted, ${minutes} min left`;
        } else {
            this.set_applet_icon_symbolic_name(ICON_IDLE);
            this.set_applet_label('');
            this.set_applet_tooltip('pcbridge: desktop control closed');
            this._line.label.text = 'Desktop control: closed';
        }
    }

    on_applet_clicked() {
        this.update();
        this.menu.toggle();
    }

    /** The kill switch. Works with the daemon down: `pcbridge lock` needs none. */
    lockNow() {
        const cli = findCli();
        if (!cli) {
            Main.notify('pcbridge', 'The pcbridge command was not found; run `pcbridge lock` in a terminal.');
            return;
        }
        try {
            GLib.spawn_async(null, [cli, 'lock'], null, GLib.SpawnFlags.DEFAULT, null);
            global.log('[pcbridge] kill switch: pcbridge lock');
        } catch (error) {
            Main.notify('pcbridge', `Could not run pcbridge lock: ${error.message}`);
        }
    }

    on_applet_removed_from_panel() {
        if (this._timer) {
            Mainloop.source_remove(this._timer);
            this._timer = 0;
        }
        if (this._monitor) {
            this._monitor.cancel();
            this._monitor = null;
        }
    }
}

function main(metadata, orientation, panelHeight, instanceId) {
    return new PcbridgeApplet(metadata, orientation, panelHeight, instanceId);
}
