# pcbridge Cinnamon applet

The panel item for pcbridge on Cinnamon (X11). It shows whether desktop
control is granted, with the minutes left, and its menu has **"Lock desktop
control now"**, which runs `pcbridge lock` (the kill switch; it works with
the daemon down).

It only **reads** pcbridge's grant file
(`~/.local/state/pcbridge/desktop_unlock.json`) and `status.json`; it never
writes either. The frame around the screens is not drawn here: on X11 the
native helper draws it (`docs/security.md`, "X11 (Cinnamon and Xfce)").

## Install

`pcbridge setup` copies this directory to
`~/.local/share/cinnamon/applets/pcbridge@eymistaken/` and adds the applet
to the panel; Cinnamon loads it at once, no new login needed. The `.deb`
installs it under `/usr/share/cinnamon/applets/`. `pcbridge uninstall`
takes it off the panel and moves its files to the backup.

By hand: copy the directory there and add it from System Settings >
Applets. To remove it, right-click it and choose "Remove from panel".

Publishing it on Cinnamon Spices needs a pull request to their repository;
the source stays here.
