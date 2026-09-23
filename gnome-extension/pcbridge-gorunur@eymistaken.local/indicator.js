/* The panel indicator: pcbridge's state at a glance, and the kill switch.
 *
 * It READS two files (status.json, desktop_unlock.json) and never writes
 * either. Its only action with an effect is "Lock desktop control now",
 * which runs `pcbridge lock` -- the same command as the `bridgekilit` alias.
 * Nothing here is a step before work can start (invariant I7): a closed
 * menu, a missing CLI or a dead daemon changes nothing about how an agent
 * unlocks the desktop.
 */

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {findCli, indicatorVisible, summarize} from './status.js';

const ICONS = {
    open: 'input-mouse-symbolic',
    idle: 'computer-symbolic',
    down: 'computer-fail-symbolic',
};

/** A terminal to show logs and status in; the first one installed wins. */
function terminalArgv(command) {
    for (const [program, flag] of [['gnome-terminal', '--'], ['kgx', '--'],
        ['x-terminal-emulator', '-e']]) {
        const found = GLib.find_program_in_path(program);
        if (found)
            return [found, flag, ...command];
    }
    return null;
}

export const PcbridgeIndicator = GObject.registerClass(
class PcbridgeIndicator extends PanelMenu.Button {
    _init(grant, statusWatcher, settings = null) {
        super._init(0.0, 'pcbridge', false);
        this._grant = grant;
        this._watcher = statusWatcher;
        this._tickId = 0;
        // `indicator-mode`; without a compiled schema the icon always shows.
        this._settings = settings;
        this._modeId = settings?.connect('changed::indicator-mode', () => this.update()) ?? 0;
        this._shown = null;

        const box = new St.BoxLayout({style_class: 'panel-status-menu-box'});
        this._icon = new St.Icon({icon_name: ICONS.down, style_class: 'system-status-icon'});
        this._label = new St.Label({text: '', y_align: Clutter.ActorAlign.CENTER});
        box.add_child(this._icon);
        box.add_child(this._label);
        this.add_child(box);

        this._lines = [];
        for (let i = 0; i < 4; i++) {
            const item = new PopupMenu.PopupMenuItem('', {reactive: false, can_focus: false});
            this.menu.addMenuItem(item);
            this._lines.push(item);
        }
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Lock desktop control now', () => this.lockNow());
        this.menu.addAction('Open logs', () => this._inTerminal(['logs', '-f']));
        this.menu.addAction('Status…', () => this._inTerminal(['status'], true));
        this.menu.connect('open-state-changed', (_menu, open) => {
            if (open)
                this.update();
        });

        // The minutes left change without any file event.
        this._tickId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 30, () => {
            this.update();
            return GLib.SOURCE_CONTINUE;
        });
        this.update();
    }

    mode() {
        return this._settings?.get_string('indicator-mode') ?? 'always';
    }

    update() {
        const now = GLib.get_real_time() / 1e6;
        const s = summarize(this._watcher.status,
            {active: this._grant.active, until: this._grant.until}, now);
        const shown = indicatorVisible(this.mode(), s.grantOpen);
        // Applied every time: the panel shows a container when it adds it.
        this.container.visible = shown;
        if (shown !== this._shown) {
            if (!shown)
                this.menu.close();
            this._shown = shown;
            console.log(`[pcbridge-gorunur] indicator ${shown ? 'shown' : 'hidden'} ` +
                `(mode ${this.mode()}, desktop control ${s.grantOpen ? 'open' : 'closed'})`);
        }
        this._icon.icon_name = ICONS[s.icon];
        this._label.text = s.grantOpen ? ` ${Math.max(1, Math.round(s.grantLeft / 60))}m` : '';
        this._lines.forEach((item, i) => {
            item.label.text = s.lines[i] ?? '';
            item.visible = Boolean(s.lines[i]);
        });
    }

    /** The kill switch. Works with the daemon down: `pcbridge lock` needs none. */
    lockNow() {
        const cli = findCli(this._watcher.status);
        if (!cli) {
            Main.notify('pcbridge', 'The pcbridge command was not found; run bridgekilit in a terminal.');
            return;
        }
        try {
            GLib.spawn_async(null, [cli, 'lock'], null, GLib.SpawnFlags.DEFAULT, null);
            console.log('[pcbridge-gorunur] kill switch: pcbridge lock');
        } catch (error) {
            Main.notify('pcbridge', `Could not run pcbridge lock: ${error.message}`);
        }
    }

    _inTerminal(args, hold = false) {
        const cli = findCli(this._watcher.status);
        if (!cli) {
            Main.notify('pcbridge', 'The pcbridge command was not found.');
            return;
        }
        const line = [cli, ...args].map(a => GLib.shell_quote(a)).join(' ');
        const script = hold ? `${line}; echo; read -r -p "Press Enter to close. "` : line;
        const argv = terminalArgv(['bash', '-c', script]);
        if (!argv) {
            Main.notify('pcbridge', `No terminal found; run: ${line}`);
            return;
        }
        try {
            GLib.spawn_async(null, argv, null, GLib.SpawnFlags.DEFAULT, null);
        } catch (error) {
            Main.notify('pcbridge', `Could not open a terminal: ${error.message}`);
        }
    }

    destroy() {
        if (this._modeId) {
            this._settings.disconnect(this._modeId);
            this._modeId = 0;
        }
        if (this._tickId) {
            GLib.Source.remove(this._tickId);
            this._tickId = 0;
        }
        super.destroy();
    }
});
