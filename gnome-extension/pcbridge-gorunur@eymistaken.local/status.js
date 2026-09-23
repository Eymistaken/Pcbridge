/* What the panel indicator shows, worked out from two files it only READS.
 *
 * `status.json` is written by the pcbridge 2.0 daemon (version, pid, running
 * jobs, remote access, the CLI path) whenever one of them changes; the grant
 * still comes from `desktop_unlock.json` through `UnlockState`, so there is a
 * single source for it. The indicator adds no step to starting work: it
 * shows state and offers the kill switch, nothing else.
 *
 * An older server writes no `status.json`; the indicator then says the
 * status is unknown and the kill switch still works through the CLI.
 *
 * No shell modules here (GLib and Gio only), so `gjs -m
 * gnome-extension/tests/test_status.js` tests it without a shell.
 */

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

/** How often the daemon's pid is checked; the daemon writes only on change. */
const PID_CHECK_SECONDS = 5;
const DEBOUNCE_MS = 120;

export function defaultStatusPath() {
    const override = GLib.getenv('PCBRIDGE_GORUNUR_STATUS');
    if (override)
        return override;
    return GLib.build_filenamev([GLib.get_user_state_dir(), 'pcbridge', 'status.json']);
}

/** The parsed file, or null when it is missing or not (yet) valid JSON. */
export function readStatus(path) {
    try {
        const [ok, bytes] = GLib.file_get_contents(path);
        if (!ok)
            return null;
        const data = JSON.parse(new TextDecoder().decode(bytes));
        return data && typeof data === 'object' ? data : null;
    } catch {
        return null;
    }
}

export function processAlive(pid) {
    return Number.isInteger(pid) && pid > 0 &&
        GLib.file_test(`/proc/${pid}`, GLib.FileTest.EXISTS);
}

/** "under a minute", "12 min", "1 h 5 min". */
export function formatLeft(seconds) {
    const s = Math.max(0, Math.round(seconds));
    if (s < 60)
        return 'under a minute';
    const minutes = Math.round(s / 60);
    if (minutes < 60)
        return `${minutes} min`;
    return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/**
 * @param {object|null} status  the parsed status.json, or null
 * @param {{active: boolean, until: number}} grant  from UnlockState
 * @param {number} now  unix seconds
 * @param {(pid: number) => boolean} alive
 */
export function summarize(status, grant, now, alive = processAlive) {
    let daemon = 'unknown';
    if (status) {
        daemon = status.daemon === 'running' && alive(Number(status.pid))
            ? 'running' : 'stopped';
    }
    const grantOpen = Boolean(grant?.active) && Number(grant?.until) > now;
    const grantLeft = grantOpen ? Number(grant.until) - now : 0;
    const jobs = daemon === 'running' ? Number(status.jobs_running) || 0 : 0;
    const remote = daemon === 'running' ? status.remote : null;

    const lines = [];
    if (daemon === 'running')
        lines.push(`pcbridge ${status.version ?? ''}: running`.replace(' :', ':'));
    else if (daemon === 'stopped')
        lines.push('pcbridge: not running (it starts when a client connects)');
    else
        lines.push('pcbridge: status unknown (an older server, or not started yet)');
    lines.push(grantOpen
        ? `Desktop control: OPEN, ${formatLeft(grantLeft)} left`
        : 'Desktop control: closed');
    if (daemon === 'running')
        lines.push(`Jobs running: ${jobs}`);
    if (remote === true || remote === false)
        lines.push(`Remote access: ${remote ? 'on' : 'off'}`);

    // The panel icon: grant open wins, then down, then idle.
    const icon = grantOpen ? 'open' : daemon === 'running' ? 'idle' : 'down';
    return {daemon, grantOpen, grantLeft, jobs, remote, icon, lines,
        cli: status?.cli ?? '', version: status?.version ?? ''};
}

/** The pcbridge command to run: status.json's own path, else PATH. */
/** Is the panel icon shown? "when-granted" hides it only while desktop
 * control is closed: an open grant always shows, whatever the mode, so an
 * agent that hid the icon cannot hide its own access with it. */
export const INDICATOR_MODES = ['always', 'when-granted'];

export function indicatorVisible(mode, grantOpen) {
    return Boolean(grantOpen) || mode !== 'when-granted';
}

export function findCli(status) {
    const own = status?.cli;
    if (own && GLib.file_test(own, GLib.FileTest.IS_EXECUTABLE))
        return own;
    const inPath = GLib.find_program_in_path('pcbridge');
    if (inPath)
        return inPath;
    const local = GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'pcbridge']);
    return GLib.file_test(local, GLib.FileTest.IS_EXECUTABLE) ? local : '';
}

/** Watches status.json and the daemon's pid; calls back with the raw status. */
export class StatusWatcher {
    constructor(path, onChange) {
        this._path = path;
        this._onChange = onChange;
        this._status = null;
        this._key = undefined;
        this._monitor = null;
        this._monitorId = 0;
        this._debounceId = 0;
        this._timerId = 0;
    }

    get status() {
        return this._status;
    }

    start() {
        try {
            this._monitor = Gio.File.new_for_path(this._path)
                .monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, null);
            this._monitorId = this._monitor.connect('changed', () => this._schedule());
        } catch (error) {
            console.warn(`[pcbridge-gorunur] cannot watch ${this._path}: ${error}`);
        }
        // Also catches a daemon that died without writing "stopped".
        this._timerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, PID_CHECK_SECONDS, () => {
            this.refresh();
            return GLib.SOURCE_CONTINUE;
        });
        this.refresh();
    }

    stop() {
        for (const field of ['_debounceId', '_timerId']) {
            if (this[field]) {
                GLib.Source.remove(this[field]);
                this[field] = 0;
            }
        }
        if (this._monitor) {
            if (this._monitorId)
                this._monitor.disconnect(this._monitorId);
            this._monitor.cancel();
            this._monitor = null;
            this._monitorId = 0;
        }
    }

    refresh() {
        const status = readStatus(this._path);
        const alive = status ? processAlive(Number(status.pid)) : false;
        const key = JSON.stringify([status, alive]);
        this._status = status;
        if (key === this._key)
            return;
        this._key = key;
        try {
            this._onChange(status);
        } catch (error) {
            console.error(`[pcbridge-gorunur] status callback: ${error}`);
        }
    }

    _schedule() {
        if (this._debounceId)
            GLib.Source.remove(this._debounceId);
        this._debounceId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, DEBOUNCE_MS, () => {
            this._debounceId = 0;
            this.refresh();
            return GLib.SOURCE_REMOVE;
        });
    }
}
