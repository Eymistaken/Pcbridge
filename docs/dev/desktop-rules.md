# Desktop tool rules

How the desktop tools must behave, and where each rule lives. Condensed from
the former `KURALLAR.md`; the section and item numbers are kept because code
and tests cite them ("desktop-rules §4 item 5").

## 1. Rule classes

Every rule is one of three kinds; writing down which one ends the same
discussion before it starts again.

| Class | Where it lives | If the agent ignores it |
|---|---|---|
| **A: Automatic** | The code handles it | The agent does not need to know |
| **G: Gate** | `SafetyGate` or the tool's entry | Refused, with the reason |
| **H: Hint** | `INSTRUCTIONS` and docstrings | Nothing. Hope. |

A hint alone is not a rule. Anything critical is automatic or a gate; hints
are for preferences ("prefer `ui_dump` to a screenshot").

## 2. The grant closes itself: the sliding lease (A)

An agent has no "I am done" event, so it cannot be trusted to call
`desktop_lock`. The grant is therefore tied to the last action, not only to a
deadline:

- `desktop_unlock` writes `hard_until` (the ceiling, default 15 min, at most
  `unlock_max_minutes`) and `until`.
- Every admitted action moves `until` to `min(hard_until, now +
  unlock_idle_seconds)` (default 90 s). With no action, the grant lapses
  90 s after the last one.
- The GNOME extension reads only `until` and arms a timer for it, so the
  frame fades with the grant without any extension change. `Gio.FileMonitor`'s
  800 ms rate limit merges the frequent writes.
- `computer_task` keeps the lease alive while its local agent works.

Measured (2026-09-20): in a real task with reading and thinking between
actions, 90 s meant reopening the grant four times. The value stays; the
agent re-unlocks, which needs no human (invariant: `desktop_unlock` never
asks a person).

## 3. Launching GUI programs from the shell (H + optional G)

A GUI program started with `shell_run` lives in pcbridge's cgroup and dies
with it; `window_focus` starts the application in its own scope instead. The
docstrings say so. The old hard block is optional:
`[desktop] block_gui_launch_in_shell` (default `false`); when `true` and the
blocklist matches, `shell_run` refuses. Handing a URL to an already running
browser is a normal, deterministic shell action.

## 4. The content gates

| # | Rule | Class | Where |
|---|---|---|---|
| 1 | A stale screenshot's coordinates are refused | A | `agent_shot_max_age_seconds` (60 s) |
| 2 | A key held and never released is released | A | `hold_max_seconds` (120 s) |
| 3 | Text goes through the clipboard, not raw keys | A | `input.py` (`tr+intl` breaks ASCII keycodes) |
| 4 | Check the result after a click | H | `ui_click` reports it; `mouse` cannot |
| 5 | Closing a window / quitting without saving needs `confirm_close` | G | A closing shortcut is refused unless confirmed; in `computer_batch` the whole list is refused at parse time |
| 6 | Three clicks in a row on the same node stop | G | `[desktop] repeat_click_limit = 3`; the third click is never sent |
| 7 | Never type into a password field (`role = password text`) | G | Cannot be overridden with `force` |

The decision table is `pcbridge/desktop/policy.py`, pure and without I/O;
its contract is `tests/contracts/test_desktop_gates.py`.

Known limits, on purpose:

- **Item 5 is narrow**: it looks only at the shortcut the caller sends. A
  window's close *button* is out of scope: AT-SPI has no "close" role, and
  matching labels would depend on the language ("Close"/"Kapat") and catch
  harmless buttons that close a tooltip.
- **Item 6 counts within one sequence.** An agent looping over separate
  `ui_click` calls is not caught; a cross-process counter would need the
  lease's file-and-lock machinery.

## 5. After an action, assume the focus moved

- A click that moves the focus stops the rest of a `computer_batch` /
  `pcb-do` list (`batch_check_focus`), unless the caller declared the
  expected window (`expect_focus`); a move anywhere else still stops it.
- A coordinate without `shot=` or `monitor=` that falls inside a recent
  downscaled screenshot is refused as ambiguous
  (`ambiguous_coord_guard`): the caller says which space it meant.

## 6. What the gate never does

- It never asks a human. `desktop_unlock` is the agent's own call; the
  person's controls are `[desktop] enabled`, the lock screen, the kill
  switch (`pcbridge lock`, `bridgekilit`, the panel menu) and the audit log.
- It never guesses. An unreadable screen lock, unknown user activity or an
  unknown monitor layout refuse the action with a typed error
  (`LOCK_STATE_UNKNOWN`, `ACTIVITY_UNKNOWN`, `DISPLAY_MAPPING_UNKNOWN`).
- It never writes content to the audit log: what was done (command, path,
  text length), never the content (output, file body, the text itself).
