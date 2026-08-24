"""
Persistent State
=================

Tiny JSON state store for UI conveniences that should survive restarts -
currently only the popup position the user last dragged the pinned popup
to.  The file lives at ``$XDG_CONFIG_HOME/usage-monitor-for-claude/
state<suffix>.json`` (default ``~/.config/...``), where ``<suffix>`` is
the same per-config-dir instance suffix used for the lock file and the
autostart entry, so each instance (one per Claude account) keeps its own
state.

State is a convenience, never a requirement: every failure here is
silent - no dialog, no exception escaping.  Writes are atomic (temp file
plus ``os.replace``) so a crash can never leave a half-written file.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .instance_id import config_dir_suffix

__all__ = ['load_popup_position', 'save_popup_position']

_POPUP_POSITION_KEY = 'popup_position'


def _state_path() -> Path:
    """Return the per-instance state file path."""
    xdg_config_home = os.environ.get('XDG_CONFIG_HOME')
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / '.config'
    return base / 'usage-monitor-for-claude' / f'state{config_dir_suffix()}.json'


def _load() -> dict:
    """Read the state file, or return ``{}`` on any failure."""
    try:
        data = json.loads(_state_path().read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_popup_position() -> tuple[int, int] | None:
    """Return the saved popup position as ``(x, y)``, or None."""
    position = _load().get(_POPUP_POSITION_KEY)
    if (
        isinstance(position, list) and len(position) == 2
        and all(isinstance(c, int) and not isinstance(c, bool) for c in position)
    ):
        return position[0], position[1]
    return None


def save_popup_position(x: int, y: int) -> None:
    """Persist the popup position, silently ignoring any failure."""
    try:
        data = _load()
        data[_POPUP_POSITION_KEY] = [int(x), int(y)]

        path = _state_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix='.state-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as tmp_file:
                json.dump(data, tmp_file)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception:
        pass
