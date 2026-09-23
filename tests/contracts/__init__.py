"""Offline contract tests for desktop provider migrations."""

import os

# The contracts describe GNOME unless a test says otherwise (the Plasma tests
# patch `session.desktop_kind`). Without this, running them inside a Plasma
# session would turn every GNOME expectation into a KDE one.
os.environ["XDG_CURRENT_DESKTOP"] = "GNOME"
