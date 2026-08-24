"""
Instance Identity
==================

Derives a per-instance identifier from the effective Claude config
directory so multiple monitor instances (one per Claude account) can
coexist, each guarding its own single-instance lock and autostart
entry.

This module must stay free of imports from ``api`` or ``settings`` -
it is used before ``CLAUDE_CONFIG_DIR`` is finalized in ``__main__``.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

__all__ = ['config_dir_suffix', 'effective_config_dir', 'is_default_config_dir', 'parse_config_dir']


def parse_config_dir(argv: list[str]) -> str | None:
    """Extract the ``--config-dir`` value from command-line arguments.

    Supports both ``--config-dir=PATH`` and ``--config-dir PATH`` forms.
    Surrounding quotes are stripped.  Environment variables (``$HOME``)
    and a leading ``~`` are expanded, so the flag works the same from a
    shell, a desktop launcher, and an autostart entry.

    Parameters
    ----------
    argv : list[str]
        Argument list, typically ``sys.argv``.

    Returns
    -------
    str | None
        The cleaned path value, or ``None`` if the flag is absent or
        has no value.
    """
    value = None
    for index, arg in enumerate(argv):
        if arg.startswith('--config-dir='):
            value = arg.split('=', 1)[1]
        elif arg == '--config-dir' and index + 1 < len(argv):
            value = argv[index + 1]

    if value is None:
        return None

    value = value.strip().strip('"').strip("'")
    if value != '/':
        value = value.rstrip('/')
    if not value:
        return None

    return str(Path(os.path.expandvars(value)).expanduser())


def effective_config_dir() -> Path:
    """Return the resolved Claude config directory currently in effect."""
    custom = os.environ.get('CLAUDE_CONFIG_DIR')
    base = Path(custom) if custom else Path.home() / '.claude'
    return base.resolve()


def is_default_config_dir() -> bool:
    """Return True when the effective config dir is the default ``~/.claude``."""
    default = os.path.realpath(str(Path.home() / '.claude'))
    return os.path.realpath(str(effective_config_dir())) == default


def config_dir_suffix() -> str:
    """Return a per-instance suffix for lock file and autostart entry names.

    Empty for the default ``~/.claude`` directory (preserving the legacy
    names so older versions are still detected), otherwise an underscore
    plus a short hash of the resolved directory path.  Hashing keeps the
    names short and free of path separators.
    """
    if is_default_config_dir():
        return ''

    normalized = str(effective_config_dir())
    return '_' + hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12]
