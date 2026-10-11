#!/usr/bin/env python3
"""Stands in for systemd-inhibit / gnome-session-inhibit in the tests.

    fake_inhibitor.py MODE PIDFILE REAL-ARGV...

MODE "hold": write our pid to PIDFILE, then become the last argument (`cat`),
as the real tools run their command while they hold the lock.
MODE "deny": fail the way logind does without polkit.
"""

import os
import sys

mode, pidfile, *argv = sys.argv[1:]
if mode == "deny":
    print("Failed to inhibit: Access denied", file=sys.stderr)
    sys.exit(1)
with open(pidfile, "a", encoding="utf-8") as fh:
    fh.write(f"{os.getpid()} {' '.join(argv)}\n")
os.execvp(argv[-1], [argv[-1]])
