"""Offline contract tests for desktop provider migrations."""

import os

# The contracts describe GNOME unless a test says otherwise (the Plasma tests
# patch `session.desktop_kind`). Without this, running them inside a Plasma
# session would turn every GNOME expectation into a KDE one.
os.environ["XDG_CURRENT_DESKTOP"] = "GNOME"

# The terminal UI asks GitHub for a newer release at start; no test does
# that (tests/contracts/test_upgrade.py turns it back on with a fake opener).
os.environ["PCBRIDGE_NO_UPGRADE_CHECK"] = "1"
