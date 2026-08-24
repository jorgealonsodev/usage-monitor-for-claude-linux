"""
Autostart
==========

Manages autostart via an XDG autostart ``.desktop`` entry in
``$XDG_CONFIG_HOME/autostart`` (default ``~/.config/autostart``).
Each monitor instance (one per Claude config directory) uses its own
desktop file name and stores its ``--config-dir`` in the command.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .instance_id import config_dir_suffix, effective_config_dir, is_default_config_dir

__all__ = ['AUTOSTART_BASE_NAME', 'autostart_file_path', 'is_autostart_enabled', 'set_autostart', 'sync_autostart_path']

AUTOSTART_BASE_NAME = 'usage-monitor-for-claude'


def _autostart_dir() -> Path:
    """Return the XDG autostart directory."""
    xdg_config_home = os.environ.get('XDG_CONFIG_HOME')
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / '.config'
    return base / 'autostart'


def autostart_file_path() -> Path:
    """Return the per-instance desktop file path."""
    return _autostart_dir() / f'{AUTOSTART_BASE_NAME}{config_dir_suffix()}.desktop'


def _launcher() -> str:
    """Return the (quoted) launcher command for this installation.

    An installed console script (e.g. ``/usr/bin/usage-monitor-for-claude``)
    is used directly; running from source (``python3 -m
    usage_monitor_for_claude``) leaves ``sys.argv[0]`` pointing at the
    package's ``__main__.py``, in which case the interpreter with ``-m``
    is stored instead.
    """
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'

    argv0 = sys.argv[0] if sys.argv else ''
    if argv0:
        launcher = Path(argv0).resolve()
        if launcher.is_file() and os.access(launcher, os.X_OK) and launcher.suffix != '.py':
            return f'"{launcher}"'

    return f'"{sys.executable}" -m usage_monitor_for_claude'


def _autostart_command() -> str:
    """Return the ``Exec=`` command line for this instance."""
    command = _launcher()
    if not is_default_config_dir():
        command += f' --config-dir="{effective_config_dir()}"'
    return command


def _desktop_entry() -> str:
    """Return the full desktop entry content for this instance."""
    return (
        '[Desktop Entry]\n'
        'Type=Application\n'
        'Name=Usage Monitor for Claude\n'
        f'Exec={_autostart_command()}\n'
        'X-GNOME-Autostart-enabled=true\n'
    )


def _stored_exec(path: Path) -> str | None:
    """Return the ``Exec=`` value stored in *path*, or ``None``."""
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.startswith('Exec='):
                return line[len('Exec='):]
    except OSError:
        pass
    return None


def is_autostart_enabled() -> bool:
    """Check whether the app is registered to start with the session.

    Returns
    -------
    bool
        ``True`` if a matching desktop file exists and is not disabled
        via ``Hidden=true``.
    """
    path = autostart_file_path()
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return False

    for line in text.splitlines():
        if line.strip().lower().replace(' ', '') == 'hidden=true':
            return False
    return True


def set_autostart(enable: bool) -> None:
    """Create or remove the autostart desktop file.

    Parameters
    ----------
    enable : bool
        ``True`` to register autostart, ``False`` to remove it.
    """
    path = autostart_file_path()
    if enable:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_desktop_entry(), encoding='utf-8')
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def sync_autostart_path() -> None:
    """Update the autostart command if the launcher has been moved.

    Compares the stored ``Exec=`` line with the current expected one and
    silently rewrites the desktop file when they differ.
    """
    path = autostart_file_path()
    if not path.is_file():
        return

    # A file disabled via Hidden=true (e.g. by the desktop environment's
    # session settings) reflects a user choice - rewriting it would
    # silently re-enable autostart.
    if not is_autostart_enabled():
        return

    if _stored_exec(path) != _autostart_command():
        set_autostart(True)
