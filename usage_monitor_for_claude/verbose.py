"""
Verbose Diagnostics
====================

Collects and prints system and runtime diagnostics when the app is
launched with ``--verbose``.  Helps users diagnose startup failures
without needing to instrument a Python installation.
"""
from __future__ import annotations

import importlib.metadata
import locale
import os
import platform
import sys
from pathlib import Path

__all__ = ['setup_console', 'print_startup_diagnostics', 'print_runtime_diagnostics']

_OS_RELEASE_PATH = Path('/etc/os-release')


def setup_console() -> bool:
    """Ensure diagnostics are visible.

    On Linux stdout/stderr are already attached to the launching
    terminal (or the session log), so this is a no-op kept for API
    compatibility with the entry point.
    """
    return True


def _section(title: str) -> None:
    """Print a section header."""
    print(f'\n  {title}')
    print(f'  {"-" * len(title)}')


def _row(label: str, value: str, indent: int = 4) -> None:
    """Print a key-value row with aligned columns."""
    print(f'{" " * indent}{label + ":":<22s} {value}')


def _package_version(name: str) -> str:
    """Get installed package version, or 'not found'."""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return 'not found'


def _distro() -> str:
    """Read the distribution name from /etc/os-release."""
    try:
        for line in _OS_RELEASE_PATH.read_text(encoding='utf-8').splitlines():
            if line.startswith('PRETTY_NAME='):
                return line.split('=', 1)[1].strip().strip('"')
    except OSError:
        pass
    return 'unknown'


def _gi_versions() -> list[tuple[str, str]]:
    """Collect GTK / WebKit2GTK / libnotify versions via GObject introspection."""
    rows: list[tuple[str, str]] = []
    try:
        import gi
    except ImportError:
        return [('PyGObject', 'not found')]

    rows.append(('PyGObject', getattr(gi, '__version__', 'unknown')))

    try:
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
        rows.append(('GTK', f'{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}'))
    except Exception:
        rows.append(('GTK', 'not found'))

    try:
        gi.require_version('WebKit2', '4.1')
        from gi.repository import WebKit2
        rows.append(('WebKit2GTK', f'{WebKit2.get_major_version()}.{WebKit2.get_minor_version()}.{WebKit2.get_micro_version()}'))
    except Exception:
        rows.append(('WebKit2GTK', 'not found'))

    try:
        gi.require_version('Notify', '0.7')
        from gi.repository import Notify  # noqa: F401
        rows.append(('Notify (typelib)', '0.7'))
    except Exception:
        rows.append(('Notify (typelib)', 'not found'))

    return rows


def _redact_home(path_str: str) -> str:
    """Replace the user's home directory with ``~`` to avoid exposing the username.

    Boundary-aware, so a sibling directory whose name merely starts with
    the home path is not partially redacted.
    """
    home = str(Path.home())

    if path_str == home:
        return '~'
    if path_str.startswith(home + os.sep):
        return '~' + path_str[len(home):]

    return path_str


def _credentials_status() -> str:
    """Check if the credentials file exists (never reads its content)."""
    config_dir = Path(os.environ.get('CLAUDE_CONFIG_DIR', '')) if os.environ.get('CLAUDE_CONFIG_DIR') else Path.home() / '.claude'
    cred_path = config_dir / '.credentials.json'
    display_path = _redact_home(str(cred_path))

    if cred_path.exists():
        return f'found ({display_path})'

    return f'NOT FOUND ({display_path})'


def _cli_discovery() -> str:
    """Report the discovered Claude CLI path and whether it exists."""
    try:
        from .claude_cli import CLAUDE_CLI_PATH
    except Exception as exc:
        return f'error: {exc}'

    status = 'found' if CLAUDE_CLI_PATH.is_file() else 'NOT FOUND'
    return f'{status} ({_redact_home(str(CLAUDE_CLI_PATH))})'


def print_startup_diagnostics() -> None:
    """Print system and environment diagnostics before the UI starts."""
    from . import __version__

    print(f'\n  Usage Monitor for Claude v{__version__} - Verbose Mode')
    print(f'  {"=" * 48}')

    # System
    _section('System')
    _row('Distribution', _distro())
    _row('Kernel', f'{platform.system()} {platform.release()}')
    _row('Architecture', platform.machine())
    _row('Session type', os.environ.get('XDG_SESSION_TYPE', '(not set)'))
    _row('Desktop', os.environ.get('XDG_CURRENT_DESKTOP', '(not set)'))

    # Python
    _section('Python')
    _row('Version', sys.version.split()[0])
    _row('Executable', _redact_home(sys.executable))
    frozen = getattr(sys, 'frozen', False)
    _row('Frozen', str(frozen))
    if frozen:
        _row('Bundle dir', _redact_home(str(getattr(sys, '_MEIPASS', 'unknown'))))

    # Locale
    _section('Locale')
    sys_locale = locale.getlocale()
    _row('System locale', f'{sys_locale[0]}, {sys_locale[1]}' if sys_locale[0] else 'not set')
    _row('Filesystem encoding', sys.getfilesystemencoding())
    _row('Default encoding', sys.getdefaultencoding())
    _row('CLAUDE_CONFIG_DIR', _redact_home(os.environ.get('CLAUDE_CONFIG_DIR', '')) or '(not set)')

    # Runtimes
    _section('Runtimes')
    for label, version in _gi_versions():
        _row(label, version)

    # Dependencies
    _section('Dependencies')
    for pkg in ('Pillow', 'requests'):
        _row(pkg, _package_version(pkg))

    # Credentials
    _section('Credentials')
    _row('File', _credentials_status())

    # Claude CLI
    _section('Claude CLI')
    _row('Binary', _cli_discovery())

    print()


def print_runtime_diagnostics() -> None:
    """Print diagnostics that are only available after the GUI stack has loaded."""
    _section('Runtime (post-init)')

    try:
        import gi
        gi.require_version('Gdk', '3.0')
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        _row('Display', display.get_name() if display else 'none')
        if display:
            _row('Monitors', str(display.get_n_monitors()))
    except Exception as exc:
        _row('Display', f'error: {exc}')

    print()
