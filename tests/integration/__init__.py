"""Integration tests for cross-language native behavior."""

import os

# Offline fixtures describe GNOME; live capture keeps its actual session.
if os.environ.get("PCBRIDGE_TEST_CAPTURE") != "1":
    os.environ["XDG_CURRENT_DESKTOP"] = "GNOME"
    os.environ.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
