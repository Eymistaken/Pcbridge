---
name: computer-use
description: Drive this Linux desktop by looking at the screen. Take a screenshot, read the PNG, click and type, check the result. For GUI tasks — windows, menus, buttons, forms. The only way into applications that accessibility cannot see (Electron: Vesktop, VS Code, Discord).
---

# Driving the computer by looking at the screen

You have an eye: you can read PNG files. Two commands on this machine connect
you to the screen.

## The loop

```bash
pcb-shot --monitor 2                 # 1. look
```
```
Read /run/user/1000/pcbridge/shots/...png      # 2. really see it
```
```bash
pcb-do '[{"a":"click","x":840,"y":312,"shot":"m2-a1b2c3"}]'   # 3. act
pcb-shot --monitor 2                                           # 4. CHECK THE RESULT
```

Step four is not optional. Never move on before you have seen that an action
worked.

## Facts about the screen

- **Learn the layout first.** `pcb-shot` prints every monitor it captured: its
  number, connector, size, offset and whether it is the primary one. Monitors
  are numbered by position, left to right (then top to bottom), starting at 1.
  Do not assume a layout; read it.
- **GNOME's panel menus and the `Super` overview open on the primary monitor.**
  `pcb-shot` names it. If you press `Super` and look for the overview on another
  monitor, you will think it "did not work".
- **Do not convert coordinates — carry the id.** `pcb-shot` writes a
  `shot: m2-a1b2c3` line next to every picture. Give the pixel you see **as
  is** and add that id; pcbridge applies the offset and the scale itself:
  `{"a":"click","x":<picture_x>,"y":<picture_y>,"shot":"m2-a1b2c3"}`.
  Without an id a coordinate counts as a **global canvas** coordinate (top left
  `(0,0)`). If you forget the id and send a picture coordinate, `pcb-do`
  notices and **refuses** — it asks which one you meant instead of guessing. If
  you really mean a global or per-monitor coordinate, add `"monitor": 1` (or
  the right number) and it goes through.
- Keyboard layouts do not matter: `type` goes through the clipboard.
- **After `Super` the clipboard is blocked**; to type while the overview is
  open use `{"a":"type","text":"...","raw":true}` (ASCII only).

## GROUP your actions

Every `pcb-do` call is a separate process and sets the virtual keyboard and
pointer up from scratch. Measured:

| | time |
|---|---|
| device setup per process | **1.4 s** |
| an actual key press | 0.03 s |

Sending ten actions one by one wastes ~14 seconds. Send them in one list:

```bash
pcb-do '[{"a":"click","x":840,"y":900,"shot":"m2-a1b2c3"},
         {"a":"wait","ms":300},
         {"a":"type","text":"hello"},
         {"a":"key","keys":"Return"}]'
```

Screenshots are not free either: one costs roughly 1200–1900 input tokens on
Claude (other models up to 20–30 times more). Check after every action, but
do not read the same screen twice.

`pcb-shot` scales pictures down (the **same** setting as `screen_capture`,
`[desktop] screenshot_scale_long_edge`), so the pixel you see is smaller than
the one on screen. You do not need to think about it — give the coordinate as
you see it and add the `shot` id.

**Do not read coordinates off `--scale 0`.** Full resolution (1920) is larger
than what you actually see: the image pipeline scales it down to 1568 itself,
so your pixel and the recorded scale disagree and a `shot` coordinate is off
by ~1.22 times. `pcb-shot` prints a warning in that case. Full resolution is
only for **looking** — when you cannot make out small text.

## Actions

```
{"a":"key",          "keys":"ctrl+s"}          a key or combination
{"a":"hold",         "keys":"shift"}           HOLD DOWN until release
{"a":"release",      "keys":"shift"}           let go
{"a":"type",         "text":"...", "raw":false} type text
{"a":"wait",         "ms":400}                  wait (at most 30000)
{"a":"move",         "x":.., "y":..}            move the pointer
{"a":"click",        "x":.., "y":..}            left click (in place without x/y)
{"a":"double_click", "x":.., "y":..}
{"a":"triple_click", "x":.., "y":..}            selects a whole line
{"a":"right_click",  "x":.., "y":..}
{"a":"middle_click", "x":.., "y":..}
{"a":"mouse_down",   "button":"left", "x":.., "y":..}   HOLD DOWN
{"a":"mouse_up",     "button":"left"}                    let go
{"a":"drag",         "x":.., "y":.., "to_x":.., "to_y":.., "button":"left"}
{"a":"scroll",       "amount":-3, "horizontal":false}   negative = down / left
{"a":"launch",       "app":"Vesktop"}           start an application
{"a":"focus",        "window":"Text Editor"}    raise a window (extension: ms; search fallback: ~6.7 s)
{"a":"ui_click",     "id":"90e6"}               an accessibility node
{"a":"ui_set_text",  "id":"1b72", "text":"..."} fill a text field directly
```

**The pointer does not jump**; it travels through intermediate points (~5000
px/s, 0.4 s across one screen). A `move` does not return instantly; repeating
it because it "hung" stacks two movements.

**`hold` / `mouse_down` carry over to later actions.** A drag with stops on
the way — a slider, a selection rectangle, dropping a file on a folder — is
`mouse_down`, `move`, `move`, `mouse_up`. `drag` is the one-shot version.

Make sure you let go. If a sequence stops half way (error, budget, focus
moved), held input is released automatically; if it finishes normally it is
**not** — "hold, click in the next call" is a legitimate use. The report says
which one happened. As a last resort the server releases everything after a
while (default 120 s), but until then the user cannot use the machine.

`ui_click` / `ui_set_text` need no coordinates and are **far more reliable** —
but they only work in GTK/GNOME applications. In Electron applications
(Vesktop, VS Code, Discord) the accessibility tree is **empty**; there you have
to work with your eyes. That is why this skill exists.

## Say so before you click into another window

When a click moves the focus, `pcb-do` **stops** and does not send the
remaining keys. The reason is the accident below; if you do it on purpose,
declare it first:

```bash
pcb-do --expect-focus "Text Editor" \
       '[{"a":"click","x":900,"y":500},
         {"a":"wait","ms":300},
         {"a":"type","text":"hello"}]'
```

If the focus goes to the window you **expected**, the sequence goes on. If it
goes **anywhere else**, it still stops — the guard stays, your intent becomes
its yardstick.

Read the window name off the title bar in the `pcb-shot` picture; a part of
it is enough (`"Editor"` works too).

Without it a click that moves the focus returns code 2, and the message names
the window it went to — learn from it and try again.

## Exit codes

| code | meaning | what to do |
|---|---|---|
| 0 | everything done | go on |
| 2 | **partly** done | look with `pcb-shot`, see where it stopped |
| 3 | the safety gate refused | stop and tell the user |
| 4 | malformed JSON | fix it, check with `--dry-run` |

`pcb-do --dry-run '<json>'` parses your list without running anything. Use it
when unsure.

After code 3 do not retry. The user may have closed the grant, the screen may
be locked or the time may be up; getting past that is not your job and you
cannot.

## When one execution path is closed

A permission or backend error does not change the user's task. Keep track of
which scope is closed: the pcbridge grant, screen capture, pointer, keyboard
and accessibility are separate permissions. Do not repeat the same call
blindly and do not ask for broader access on your own.

If another path finishes the task with the permissions you already have, use
it. With the pointer closed but accessibility open, run the visible node with
`ui_click`; when a running browser only needs a URL, use a deterministic
shell handoff. Switching paths must not restart or widen the target
application, the user's intent or the open grant.

## Your screenshot goes stale

After reading a coordinate off a picture, click **right away**. Do not write
code, pause to think or do other work in between. The user may have switched
windows meanwhile, and your coordinate may now be on top of something else.

`pcb-do` **refuses** a coordinate action when the picture is older than 60
seconds (code 3). With `shot` the yardstick is **that capture's own age**, not
the newest PNG in the folder — taking a new picture meanwhile does not make an
old id fresh. This is a net, not a rule; keep the loop tight instead of
relying on it:

```
pcb-shot  →  Read  →  pcb-do        ← nothing else in between
```

This also comes from an accident: on 3 August 2026 a click was based on a
69-second-old picture. Where Vesktop was expected there was another
application, and the click landed there. The focus guard did **not** fire,
because the focus was already on that application — nothing changed. The
guard below does not catch this case; a tight loop does.

## No blind clicks

**Never click a point without having seen what is there.**

This rule comes from an accident. 2 August 2026, 21:13: during a measurement a
`move(920, 520)` + `click` was sent and the text editor window was **assumed
to be there, not checked**. The click landed on the desktop. The focus moved
there. The `ctrl+a` + `Delete` sent afterwards to clean up **moved 23 items
from the desktop to the trash**.

The lesson went into the code: `pcb-do` now checks after mouse clicks whether
the focus changed and **stops** if it did (exit code 2). This net catches you,
but do not skip steps because of it.

In practice: really `Read` the `pcb-shot` output before a click. Show yourself
where the target is in the picture. Then click.

## If you get stuck

Do not guess. If three attempts made no progress, stop and tell the user what
you see, what you tried and what did not happen. Clicking on in the wrong
place is always the worst option.

The same goes for ambiguity: when you cannot be sure from the screen ("which
chat window", "which file"), ask.
