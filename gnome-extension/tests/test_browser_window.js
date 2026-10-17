#!/usr/bin/env -S gjs -m
/* The pcbridge browser's windows without GNOME Shell: which windows count
 * as the browser, hiding and showing, and the focus guard. */

import {
    BrowserWindows,
    browserPid,
    parentPid,
    userHasBrowser,
    wantedVisible,
} from '../pcbridge-gorunur@eymistaken.local/browserwindow.js';
import {WindowControl} from '../pcbridge-gorunur@eymistaken.local/windowcontrol.js';

let passed = 0;
let failed = 0;

function check(name, condition, detail = '') {
    if (condition) {
        passed++;
        print(`  \x1b[32mPASS\x1b[0m  ${name}`);
    } else {
        failed++;
        print(`  \x1b[31mFAIL\x1b[0m  ${name}  ${detail}`);
    }
}

const DIR = '/state/pcbridge/browser';

function files({keeper = 100, browser = 101, parent = 100, cmd = 'python3\0-I\0/x/keeper.py\0',
    visible = null} = {}) {
    const map = {
        [`${DIR}/keeper.json`]: JSON.stringify({keeper_pid: keeper, browser_pid: browser}),
        [`/proc/${keeper}/cmdline`]: cmd,
        [`/proc/${browser}/stat`]: `${browser} (chrome) S ${parent} 1 1`,
    };
    if (visible !== null)
        map[`${DIR}/view.json`] = JSON.stringify({visible});
    return path => map[path] ?? null;
}

function fakeWindow(pid, title = '') {
    return {
        pid,
        minimized: false,
        activated: 0,
        get_pid() {
            return this.pid;
        },
        get_title: () => title,
        get_wm_class: () => 'google-chrome',
        get_gtk_application_id: () => '',
        minimize() {
            this.minimized = true;
        },
        unminimize() {
            this.minimized = false;
        },
        activate() {
            this.activated++;
            focus.window = this;
            display.emit('notify::focus-window');
        },
    };
}

const focus = {window: null};
const display = {
    handlers: new Map(),
    next: 1,
    connect(signal, fn) {
        const id = this.next++;
        this.handlers.set(id, [signal, fn]);
        return id;
    },
    disconnect(id) {
        this.handlers.delete(id);
    },
    emit(signal, ...args) {
        for (const [s, fn] of [...this.handlers.values()]) {
            if (s === signal)
                fn(this, ...args);
        }
    },
};

function manager(windows, readFile = files()) {
    return new BrowserWindows({
        dir: DIR,
        listWindows: () => windows,
        focusedWindow: () => focus.window,
        currentTime: () => 42,
        display,
        readFile,
    });
}

print('which pid is the browser');
check('the parent pid comes after the command, even with spaces in it',
    parentPid('7 (Web Content) S 3 7 7') === 3);
check('a record whose keeper runs keeper.py and owns the browser counts',
    browserPid(DIR, files()) === 101);
check('a keeper that is not keeper.py does not',
    browserPid(DIR, files({cmd: 'bash\0'})) === 0);
check('a browser pid that is not the keeper\'s child does not',
    browserPid(DIR, files({parent: 1})) === 0);
check('no record, no browser', browserPid(DIR, () => null) === 0);
check('the user has the browser only while control.json says so',
    userHasBrowser(DIR, p => (p.endsWith('control.json') ? '{"user": true}' : null)) &&
    !userHasBrowser(DIR, p => (p.endsWith('control.json') ? '{"user": false}' : null)) &&
    !userHasBrowser(DIR, () => null));
check('hidden unless view.json says visible',
    !wantedVisible(DIR, files()) && wantedVisible(DIR, files({visible: true})) &&
    !wantedVisible(DIR, files({visible: false})));

print('hide and show touch only the browser');
const browserWin = fakeWindow(101, 'Router - Google Chrome');
const userChrome = fakeWindow(555, 'Bank - Google Chrome');
const userEditor = fakeWindow(600, 'notes');
let m = manager([browserWin, userChrome, userEditor]);
focus.window = userEditor;
m.setVisible(false, false);
check('hide minimizes the browser window', browserWin.minimized);
check('the user\'s own Chrome is never touched', !userChrome.minimized && userChrome.activated === 0);
check('state: found, hidden, not focused', JSON.stringify(m.state()) === '[true,false,false]');
m.setVisible(true, false);
check('show without focus un-minimizes and does not activate',
    !browserWin.minimized && browserWin.activated === 0 && focus.window === userEditor);
browserWin.activate();
check('a focus jump to the browser right after a show is undone',
    focus.window === userEditor && userEditor.activated === 1);
m.setVisible(true, true);
check('show with focus activates it and is not undone by an earlier guard',
    focus.window === browserWin);
check('describe lists the browser windows only',
    JSON.stringify(m.describe()) === JSON.stringify([['Router - Google Chrome', 'google-chrome', false, true]]));
m.stop();
check('stop leaves no handler behind', display.handlers.size === 0);

print('a new window');
m = manager([], files());
m.start();
const fresh = fakeWindow(101);
display.emit('window-created', fresh);
check('a new browser window is minimized while it should be hidden', fresh.minimized);
const other = fakeWindow(700);
display.emit('window-created', other);
check('any other new window is left alone', !other.minimized);
m.stop();
m = manager([], files({visible: true}));
m.start();
const shown = fakeWindow(101);
display.emit('window-created', shown);
check('a new browser window stays when it should be visible', !shown.minimized);
m.stop();

print('the D-Bus methods');
const wc = new WindowControl({refresh() {}, active: false}, {browser: manager([browserWin])});
check('BrowserState needs no grant', wc.BrowserState()[0] === true);
check('SetBrowserVisible needs no grant', wc.SetBrowserVisible(false, false)[1] === false && browserWin.minimized);
check('without a browser manager the answer is "not found"',
    JSON.stringify(new WindowControl({refresh() {}, active: true}).BrowserState()) === '[false,false,false]');

print(`\n${passed} passed, ${failed} failed`);
if (failed)
    imports.system.exit(1);
