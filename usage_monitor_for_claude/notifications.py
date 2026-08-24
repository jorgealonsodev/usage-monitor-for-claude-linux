"""
Notifications
==============

Desktop notifications via libnotify (Gio introspection), with graceful
fallbacks: the ``notify-send`` command when the bindings are missing,
stderr when even that fails.

Notifications carry the packaged logo instead of the live tray icon.
The UI phase routes tray notifications through this module.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

__all__ = ['init', 'notify']

_ICON_PATH = Path(__file__).resolve().parent / 'notification_logo.png'

_DEFAULT_APP_NAME = 'Usage Monitor for Claude'

# The Notify module once init() succeeded, else None.
_notify_mod = None
_app_name = _DEFAULT_APP_NAME


def _icon() -> str | None:
    """Return the packaged notification icon path, or ``None`` if missing."""
    return str(_ICON_PATH) if _ICON_PATH.is_file() else None


def init(app_name: str) -> bool:
    """Initialize libnotify under *app_name*.

    Safe to call more than once.  Failure is not fatal - ``notify``
    falls back to ``notify-send`` and stderr.

    Returns
    -------
    bool
        True when libnotify is available and initialized.
    """
    global _notify_mod, _app_name
    _app_name = app_name or _DEFAULT_APP_NAME

    try:
        import gi
        gi.require_version('Notify', '0.7')
        from gi.repository import Notify

        if Notify.is_initted() or Notify.init(_app_name):
            _notify_mod = Notify
            return True
    except Exception:
        pass

    _notify_mod = None
    return False


def notify(title: str, message: str) -> None:
    """Show a desktop notification, degrading gracefully on failure."""
    if _notify_mod is not None:
        try:
            notification = _notify_mod.Notification.new(title, message, _icon())
            notification.show()
            return
        except Exception:
            pass

    if _notify_send(title, message):
        return

    print(f'[notification] {title}: {message}', file=sys.stderr)


def _notify_send(title: str, message: str) -> bool:
    """Fall back to the ``notify-send`` command.  Returns True on success."""
    command = ['notify-send', '--app-name', _app_name]
    icon = _icon()
    if icon:
        command += ['--icon', icon]
    command += ['--', title, message]

    try:
        proc = subprocess.run(
            command, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return proc.returncode == 0
    except Exception:
        return False
