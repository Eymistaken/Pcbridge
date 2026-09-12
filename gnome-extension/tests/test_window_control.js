#!/usr/bin/env -S gjs -m
/* GNOME Shell gerektirmeyen pencere etkinleştirme sözleşmeleri. */

import {
    MAX_TARGET_LENGTH,
    WindowControl,
    chooseWindow,
} from '../pcbridge-gorunur@eymistaken.local/windowcontrol.js';

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

function fakeWindow({title = '', wmClass = '', appId = '', skipTaskbar = false} = {}) {
    return {
        activations: [],
        get_title: () => title,
        get_wm_class: () => wmClass,
        get_gtk_application_id: () => appId,
        is_skip_taskbar: () => skipTaskbar,
        activate(timestamp) {
            this.activations.push(timestamp);
        },
    };
}

class FakeState {
    constructor(active) {
        this.active = active;
        this.refreshes = 0;
    }

    refresh() {
        this.refreshes++;
        return this.active;
    }
}

print('\nWindowControl');

const exact = fakeWindow({title: 'Pcbridge Nested A', wmClass: 'zenity'});
const partial = fakeWindow({title: 'Pcbridge Nested A — Notes', wmClass: 'zenity'});
check('exact title outranks a partial title',
    chooseWindow('Pcbridge Nested A', [partial, exact]) === exact);

const punctuation = fakeWindow({wmClass: 'org.gnome.TextEditor'});
check('space, dot, hyphen and underscore separators normalize together',
    chooseWindow('org gnome text editor', [punctuation]) === punctuation);

const duplicateA = fakeWindow({title: 'Duplicate'});
const duplicateB = fakeWindow({title: 'Duplicate'});
check('an ambiguous best match is rejected',
    chooseWindow('Duplicate', [duplicateA, duplicateB]) === null);

const hidden = fakeWindow({title: 'Hidden', skipTaskbar: true});
check('skip-taskbar windows are ignored', chooseWindow('Hidden', [hidden]) === null);

const activeState = new FakeState(true);
const target = fakeWindow({title: 'Target'});
const activeControl = new WindowControl(activeState, {
    listWindows: () => [target],
    currentTime: () => 4242,
    focusedWindow: () => target,
});
check('active grant activates the unique matching window',
    activeControl.ActivateWindow('Target') === true);
check('activation uses the compositor timestamp',
    JSON.stringify(target.activations) === '[4242]');
check('grant is refreshed for every activation', activeState.refreshes === 1);

const notFocused = fakeWindow({title: 'Not Focused'});
const unconfirmedControl = new WindowControl(new FakeState(true), {
    listWindows: () => [notFocused],
    currentTime: () => 7,
    focusedWindow: () => target,
});
check('activation is false unless the compositor confirms focus',
    unconfirmedControl.ActivateWindow('Not Focused') === false);

const inactiveState = new FakeState(false);
const blocked = fakeWindow({title: 'Blocked'});
const inactiveControl = new WindowControl(inactiveState, {
    listWindows: () => [blocked],
    currentTime: () => 99,
});
check('inactive grant fails closed', inactiveControl.ActivateWindow('Blocked') === false);
check('inactive grant sends no activation', blocked.activations.length === 0);

check('blank targets are rejected', activeControl.ActivateWindow('   ') === false);
check('oversized targets are rejected',
    activeControl.ActivateWindow('x'.repeat(MAX_TARGET_LENGTH + 1)) === false);

const broken = fakeWindow({title: 'Broken'});
broken.activate = () => {
    throw new Error('activation failed');
};
const brokenControl = new WindowControl(new FakeState(true), {
    listWindows: () => [broken],
    currentTime: () => 1,
    focusedWindow: () => broken,
});
check('activation errors return false instead of escaping over D-Bus',
    brokenControl.ActivateWindow('Broken') === false);

print(`\n${passed} geçti, ${failed} kaldı`);
if (failed)
    imports.system.exit(1);
