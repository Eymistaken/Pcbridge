"""A desktop notification: notify-send on Linux, a toast on Windows.

The Windows toast goes through Windows PowerShell's WinRT projection, shown
under PowerShell's app id (a desktop app needs a registered id for its own).
The title and the message reach the script as environment variables, never
as part of the script text, so nothing in them is ever run.
"""

from __future__ import annotations

import os
import subprocess
import sys

_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$null = $texts.Item(0).AppendChild($template.CreateTextNode($env:PCBRIDGE_NOTIFY_TITLE))
$null = $texts.Item(1).AppendChild($template.CreateTextNode($env:PCBRIDGE_NOTIFY_MESSAGE))
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
"""


def send(title: str, message: str) -> str | None:
    """Show the notification; None on success, else why it failed."""
    if sys.platform == "win32":
        env = {**os.environ, "PCBRIDGE_NOTIFY_TITLE": title, "PCBRIDGE_NOTIFY_MESSAGE": message}
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _TOAST],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=20, env=env, creationflags=0x08000000)
        if result.returncode != 0:
            return (result.stderr or result.stdout).strip()[:300] or f"PowerShell exited with {result.returncode}"
        return None
    subprocess.run(["notify-send", "-a", "pcbridge", title, message], timeout=10, capture_output=True)
    return None
