#!/usr/bin/env -S gjs -m
/* Tests for `status.js`, the panel indicator's logic. NO SHELL NEEDED.
 *
 *     gjs -m gnome-extension/tests/test_status.js
 */

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

import {
    StatusWatcher, findCli, formatLeft, processAlive, readStatus, summarize,
} from '../pcbridge-gorunur@eymistaken.local/status.js';

let ok = 0;
let fail = 0;

function check(name, cond, detail = '') {
    if (cond) {
        ok++;
        print(`  \x1b[32mPASS\x1b[0m  ${name}`);
    } else {
        fail++;
        print(`  \x1b[31mFAIL\x1b[0m  ${name}  ${detail}`);
    }
}

function section(title) {
    print(`\n\x1b[1m${title}\x1b[0m`);
}

const tmpDir = GLib.dir_make_tmp('pcbridge-status-test-XXXXXX');
const path = GLib.build_filenamev([tmpDir, 'status.json']);
const write = obj => GLib.file_set_contents(path, typeof obj === 'string' ? obj : JSON.stringify(obj));
const NOW = 1_000_000;
const alive = () => true;
const dead = () => false;
const running = {schema: 1, version: '2.0.0', daemon: 'running', pid: 4242,
    jobs_running: 2, remote: false, cli: '/nonexistent/pcbridge'};

section('1. reading the file');
check('a missing file is null', readStatus(path) === null);
write('{"daemon": "runn');
check('a half-written file is null, not an error', readStatus(path) === null);
write(running);
check('a valid file is read', readStatus(path)?.pid === 4242);
check('our own pid is alive', processAlive(Number(GLib.getenv('PPID') ?? 0)) || processAlive(1));
check('pid 0 is never alive', !processAlive(0));

section('2. summary');
let s = summarize(null, {active: false, until: 0}, NOW);
check('no status.json (older server) -> unknown', s.daemon === 'unknown', s.daemon);
check('... and says so', s.lines[0].includes('status unknown'), s.lines[0]);
check('... the icon is down', s.icon === 'down');

s = summarize(running, {active: false, until: 0}, NOW, alive);
check('running with a live pid', s.daemon === 'running');
check('version shown', s.lines[0] === 'pcbridge 2.0.0: running', s.lines[0]);
check('jobs shown', s.lines.includes('Jobs running: 2'), s.lines.join(' | '));
check('remote off shown', s.lines.includes('Remote access: off'));
check('grant closed', s.lines.includes('Desktop control: closed') && s.icon === 'idle');

s = summarize(running, {active: false, until: 0}, NOW, dead);
check('"running" with a dead pid is not running', s.daemon === 'stopped', s.daemon);
check('... jobs are not claimed', !s.lines.some(l => l.startsWith('Jobs')));

s = summarize({...running, daemon: 'stopped', pid: 0}, {active: false, until: 0}, NOW, alive);
check('a clean stop reads as stopped', s.daemon === 'stopped' && s.icon === 'down');

s = summarize(running, {active: true, until: NOW + 12 * 60}, NOW, alive);
check('an open grant wins the icon', s.icon === 'open');
check('time left shown', s.lines.includes('Desktop control: OPEN, 12 min left'), s.lines[1]);

s = summarize(null, {active: true, until: NOW + 30}, NOW);
check('the grant is shown even without status.json', s.grantOpen && s.icon === 'open');

s = summarize(running, {active: true, until: NOW - 1}, NOW, alive);
check('an expired grant is closed', !s.grantOpen);

s = summarize({...running, remote: null}, {active: false, until: 0}, NOW, alive);
check('remote unknown is left out', !s.lines.some(l => l.startsWith('Remote')));

section('3. formatting and the CLI path');
check('under a minute', formatLeft(59) === 'under a minute');
check('minutes', formatLeft(12 * 60 + 10) === '12 min');
check('hours', formatLeft(65 * 60) === '1 h 5 min', formatLeft(65 * 60));
const sh = GLib.find_program_in_path('sh');
check('own CLI path used when executable', findCli({cli: sh}) === sh);
check('a missing own path falls back to PATH or ~/.local/bin',
    findCli({cli: '/nonexistent/pcbridge'}) !== '/nonexistent/pcbridge');

section('4. watcher');
const loop = new GLib.MainLoop(null, false);
const seen = [];
write(running);
const watcher = new StatusWatcher(path, status => seen.push(status?.jobs_running ?? null));
watcher.start();
check('the first state is published at once', seen.length === 1 && seen[0] === 2, JSON.stringify(seen));
GLib.timeout_add(GLib.PRIORITY_DEFAULT, 200, () => {
    write({...running, jobs_running: 3});
    return GLib.SOURCE_REMOVE;
});
GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
    check('a change is picked up from the file monitor', seen.at(-1) === 3, JSON.stringify(seen));
    const before = seen.length;
    watcher.refresh();
    check('an unchanged file does not call back again', seen.length === before);
    watcher.stop();
    loop.quit();
    return GLib.SOURCE_REMOVE;
});
loop.run();

for (const p of [path, tmpDir]) {
    try {
        Gio.File.new_for_path(p).delete(null);
    } catch { /* already gone */ }
}
print(`\n\x1b[1m${ok} passed, ${fail} failed\x1b[0m`);
imports.system.exit(fail ? 1 : 0);
