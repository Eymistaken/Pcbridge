#!/usr/bin/env -S gjs -m
/* GNOME Shell gerektirmeyen pencere etkinleştirme sözleşmeleri. */

import {
    MAX_FIELD_LENGTH,
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

print('\nFocusedWindow');

function focusWindow({title = '', wmClass = '', appId = ''} = {}) {
    return {
        get_title: () => title,
        get_wm_class: () => wmClass,
        get_gtk_application_id: () => appId,
    };
}

const game = focusWindow({title: 'Minecraft* 26.3', wmClass: 'Minecraft* 26.3'});
const focusState = new FakeState(true);
const focusControl = new WindowControl(focusState, {
    listWindows: () => {
        throw new Error('FocusedWindow must not list windows');
    },
    focusedWindow: () => game,
});
check('the focused window is named while the grant is current',
    JSON.stringify(focusControl.FocusedWindow()) ===
        JSON.stringify([true, 'Minecraft* 26.3', '', 'Minecraft* 26.3']));
check('the grant is reread for every focus read', focusState.refreshes === 1);
check('it never lists windows to answer', focusControl.FocusedWindow()[0] === true);

const closedFocus = new WindowControl(new FakeState(false), {
    focusedWindow: () => game,
});
check('a closed grant names nothing',
    JSON.stringify(closedFocus.FocusedWindow()) === JSON.stringify([false, '', '', '']));

const noFocus = new WindowControl(new FakeState(true), {focusedWindow: () => null});
check('no focused window is not found, not an error',
    noFocus.FocusedWindow()[0] === false);

const gtkApp = focusWindow({
    title: 'Belge', wmClass: 'org.gnome.TextEditor', appId: 'org.gnome.TextEditor',
});
check('the GTK application id is passed on',
    new WindowControl(new FakeState(true), {focusedWindow: () => gtkApp})
        .FocusedWindow()[2] === 'org.gnome.TextEditor');

const longTitle = focusWindow({title: 'x'.repeat(5000), wmClass: 'long'});
check('a very long title is cut to the field limit',
    new WindowControl(new FakeState(true), {focusedWindow: () => longTitle})
        .FocusedWindow()[3].length === MAX_FIELD_LENGTH);

const throwing = {
    get_title: () => {
        throw new Error('gone');
    },
    get_wm_class: () => 'still-here',
    get_gtk_application_id: () => null,
};
const halfRead = new WindowControl(new FakeState(true), {focusedWindow: () => throwing})
    .FocusedWindow();
check('a field that throws is empty, the rest is still returned',
    halfRead[0] === true && halfRead[1] === 'still-here' && halfRead[2] === '' &&
        halfRead[3] === '');

const brokenFocus = new WindowControl(
    {refresh() {
        throw new Error('state gone');
    }},
    {focusedWindow: () => game},
);
check('errors return not-found instead of escaping over D-Bus',
    brokenFocus.FocusedWindow()[0] === false);

print(`\n${passed} geçti, ${failed} kaldı`);
if (failed)
    imports.system.exit(1);
