"""
Settings
=========

Centralizes all user-tunable constants.  Structural constants (API URLs,
file paths) remain in their respective modules.

Loads an optional ``usage-monitor-settings.json`` to let users override
any constant.  Search order:

1. ``$CLAUDE_CONFIG_DIR/usage-monitor-settings.json`` (if set and different from ``~/.claude/``)
2. Next to the executable (frozen) or project root (source) - kept for
   tarball/portable installs
3. ``$XDG_CONFIG_HOME/usage-monitor-for-claude/usage-monitor-settings.json``
   (default ``~/.config/usage-monitor-for-claude/``)
4. ``~/.claude/usage-monitor-settings.json``

The app never creates this file - users place it manually.
"""
from __future__ import annotations

import json
import locale as _locale
import os
import sys
from pathlib import Path

from . import dialogs
from .instance_id import effective_config_dir, is_default_config_dir

__all__ = [
    'ALERT_EXTRA_USAGE_SPENT', 'ALERT_TIME_AWARE', 'ALERT_TIME_AWARE_BELOW',
    'BAR_BG', 'BAR_COLOR_LEVELS', 'BAR_DIVIDER', 'BAR_FG', 'BAR_FG_WARN', 'BAR_MARKER', 'BG',
    'CLI_COMMAND', 'COMPACT_HIDE', 'CURRENCY_SYMBOL',
    'FG', 'FG_DIM', 'FG_HEADING', 'FG_LINK',
    'ICON_COLOR_LEVELS', 'ICON_DARK', 'ICON_FIELDS', 'ICON_LIGHT', 'ICON_MARGIN', 'ICON_STYLE',
    'IDLE_PAUSE',
    'LANGUAGE', 'MAX_BACKOFF', 'NOTIFY_CLAUDE_UPDATE',
    'ON_DOUBLE_CLICK_COMMAND', 'ON_RESET_COMMAND', 'ON_STARTUP_COMMAND', 'ON_THRESHOLD_COMMAND',
    'POLL_ERROR', 'POLL_FAST', 'POLL_FAST_EXTRA', 'POLL_INTERVAL',
    'POPUP_FIELDS', 'SETTINGS_FILENAME', 'TIME_FORMAT', 'TOOLTIP_FIELDS',
    'get_alert_thresholds',
]

SETTINGS_FILENAME = 'usage-monitor-settings.json'

_NUMERIC_BOUNDS: dict[str, int] = {
    'poll_interval': 1,
    'poll_fast': 1,
    'poll_fast_extra': 1,
    'poll_error': 1,
    'max_backoff': 1,
    'idle_pause': 0,
}
_COLOR_KEYS = frozenset({'bg', 'fg', 'fg_dim', 'fg_heading', 'fg_link', 'bar_bg', 'bar_fg', 'bar_fg_warn', 'bar_divider', 'bar_marker'})
_ICON_KEYS = frozenset({'icon_light', 'icon_dark'})
_THRESHOLD_KEY_PREFIX = 'alert_thresholds_'
_PERCENT_KEYS = frozenset({'alert_time_aware_below'})
_STRING_KEYS = frozenset({'currency_symbol', 'language'})
_VALID_TIME_FORMATS = frozenset({'24h', '12h'})
_VALID_ICON_STYLES = frozenset({'number+bars', 'numbers'})
# Largest icon margin, percent of the icon size per side.  Past this the
# glyph has no room left to stay readable in the tray.
_MAX_ICON_MARGIN = 25
_COMMAND_KEYS = frozenset({'on_double_click_command', 'on_reset_command', 'on_startup_command', 'on_threshold_command'})
_BOOL_KEYS = frozenset({'alert_time_aware', 'notify_claude_update'})
_STRING_LIST_KEYS = frozenset({'tooltip_fields', 'compact_hide'})
_WILDCARD_STRING_LIST_KEYS = frozenset({'popup_fields'})
_VALID_BAR_MODES = frozenset({'utilization', 'overage'})


def _load_settings() -> dict:
    """Read the first ``usage-monitor-settings.json`` found, or return ``{}``."""
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).parent
    else:
        app_dir = Path(__file__).resolve().parent.parent

    home_claude = Path.home() / '.claude'

    xdg_config_home = os.environ.get('XDG_CONFIG_HOME')
    xdg_base = Path(xdg_config_home) if xdg_config_home else Path.home() / '.config'

    # A custom config dir takes precedence over the exe-adjacent file so
    # each instance (one per Claude account) can have its own settings.
    search_paths = []
    if not is_default_config_dir():
        search_paths.append(effective_config_dir() / SETTINGS_FILENAME)
    search_paths.append(app_dir / SETTINGS_FILENAME)
    search_paths.append(xdg_base / 'usage-monitor-for-claude' / SETTINGS_FILENAME)
    search_paths.append(home_claude / SETTINGS_FILENAME)

    for path in search_paths:
        if path.is_file():
            try:
                # utf-8-sig reads BOM-less UTF-8 identically and strips a BOM
                # when present (written by e.g. an editor on another platform).
                text = path.read_text(encoding='utf-8-sig').strip()
                if not text:
                    return {}
                data = json.loads(text)
                if not isinstance(data, dict):
                    raise ValueError(f'Expected a JSON object, got {type(data).__name__}')
                return _validate(data, path)
            except (json.JSONDecodeError, ValueError) as exc:
                dialogs.show_error(
                    'Usage Monitor for Claude - Settings Error',
                    f'Invalid JSON in settings file:\n{path}\n\n{exc}',
                )
                return {}
            except OSError:
                return {}

    return {}


def _valid_rgba(value: object) -> bool:
    """Return True if *value* is a list of exactly 4 integers in 0\u2013255."""
    return (
        isinstance(value, list) and len(value) == 4
        and all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in value)
    )


def _parse_level_color(value: object) -> tuple[int, int, int, int] | None:
    """Normalize an ``icon_color_levels`` color to an RGBA tuple, or None.

    Accepts a ``#rgb``/``#rrggbb``/``#rrggbbaa`` hex string or an
    ``[R, G, B, A]`` array like the ``icon_light``/``icon_dark`` values.
    """
    if isinstance(value, str):
        digits = value[1:] if value.startswith('#') else None
        if digits is None:
            return None
        try:
            if len(digits) == 3:
                r, g, b = (int(c * 2, 16) for c in digits)
                return (r, g, b, 255)
            if len(digits) == 6:
                return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), 255)
            if len(digits) == 8:
                return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), int(digits[6:8], 16))
        except ValueError:
            return None
        return None

    if _valid_rgba(value):
        return tuple(value)  # type: ignore[return-value]
    return None


def _valid_color_level(pair: object) -> bool:
    """Return True if *pair* is a valid ``[threshold, color]`` level entry."""
    return (
        isinstance(pair, list) and len(pair) == 2
        and not isinstance(pair[0], bool) and isinstance(pair[0], (int, float))
        and 0 <= pair[0] <= 100
        and _parse_level_color(pair[1]) is not None
    )


def _validate(data: dict, path: Path) -> dict:
    """Drop entries with invalid types or values and show an error dialog listing errors."""
    errors: list[str] = []
    drop: list[str] = []

    for key, value in data.items():
        if key in _NUMERIC_BOUNDS:
            min_val = _NUMERIC_BOUNDS[key]
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f'  {key}: expected an integer, got {type(value).__name__}')
                drop.append(key)
            elif value < min_val:
                errors.append(f'  {key}: must be >= {min_val}, got {value}')
                drop.append(key)

        elif key in _COLOR_KEYS:
            if not isinstance(value, str):
                errors.append(f'  {key}: expected a color string, got {type(value).__name__}')
                drop.append(key)

        elif key.startswith(_THRESHOLD_KEY_PREFIX):
            if not isinstance(value, list):
                errors.append(f'  {key}: expected an array, got {type(value).__name__}')
                drop.append(key)
            else:
                bad = [v for v in value if isinstance(v, bool) or not isinstance(v, (int, float)) or not (1 <= v <= 100)]
                if bad:
                    errors.append(f'  {key}: all values must be numbers between 1 and 100')
                    drop.append(key)
                else:
                    data[key] = sorted(set(value))

        elif key == 'alert_extra_usage_spent':
            if not isinstance(value, list):
                errors.append(f'  {key}: expected an array, got {type(value).__name__}')
                drop.append(key)
            else:
                bad = [v for v in value if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0]
                if bad:
                    errors.append(f'  {key}: all values must be numbers greater than 0')
                    drop.append(key)
                else:
                    data[key] = sorted(set(value))

        elif key in _PERCENT_KEYS:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f'  {key}: expected a number, got {type(value).__name__}')
                drop.append(key)
            elif not (1 <= value <= 100):
                errors.append(f'  {key}: must be between 1 and 100, got {value}')
                drop.append(key)

        elif key in _STRING_KEYS:
            if not isinstance(value, str):
                errors.append(f'  {key}: expected a string, got {type(value).__name__}')
                drop.append(key)

        elif key == 'time_format':
            if value not in _VALID_TIME_FORMATS:
                errors.append(f'  {key}: must be "24h" or "12h", got {value!r}')
                drop.append(key)

        elif key == 'icon_margin':
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f'  {key}: expected a number, got {type(value).__name__}')
                drop.append(key)
            elif not (0 <= value <= _MAX_ICON_MARGIN):
                errors.append(f'  {key}: must be between 0 and {_MAX_ICON_MARGIN}, got {value}')
                drop.append(key)

        elif key == 'icon_style':
            if value not in _VALID_ICON_STYLES:
                errors.append(f'  {key}: must be "number+bars" or "numbers", got {value!r}')
                drop.append(key)

        elif key in _COMMAND_KEYS:
            if isinstance(value, str):
                # An empty or whitespace-only string means "not set" (like [])
                # so it never activates the command machinery, e.g. the
                # double-click handler with its deferred single click.
                data[key] = [value] if value.strip() else []
            elif isinstance(value, list):
                if any(not isinstance(item, str) or not item.strip() for item in value):
                    errors.append(f'  {key}: all items must be non-empty strings')
                    drop.append(key)
            else:
                errors.append(f'  {key}: expected a string or array of strings, got {type(value).__name__}')
                drop.append(key)

        elif key in _BOOL_KEYS:
            if not isinstance(value, bool):
                errors.append(f'  {key}: expected true or false, got {type(value).__name__}')
                drop.append(key)

        elif key in _STRING_LIST_KEYS:
            if not isinstance(value, list):
                errors.append(f'  {key}: expected an array, got {type(value).__name__}')
                drop.append(key)
            elif any(not isinstance(item, str) or not item for item in value):
                errors.append(f'  {key}: all entries must be non-empty strings')
                drop.append(key)
            else:
                seen: set[str] = set()
                deduped: list[str] = []
                for item in value:
                    if item not in seen:
                        seen.add(item)
                        deduped.append(item)
                data[key] = deduped

        elif key in _WILDCARD_STRING_LIST_KEYS:
            if not isinstance(value, list):
                errors.append(f'  {key}: expected an array, got {type(value).__name__}')
                drop.append(key)
            elif any(not isinstance(item, str) or not item for item in value):
                errors.append(f'  {key}: all entries must be non-empty strings')
                drop.append(key)
            elif value.count('*') > 1:
                errors.append(f'  {key}: "*" may appear at most once')
                drop.append(key)
            else:
                seen_wc: set[str] = set()
                deduped_wc: list[str] = []
                for item in value:
                    if item == '*' or item not in seen_wc:
                        seen_wc.add(item)
                        deduped_wc.append(item)
                data[key] = deduped_wc

        elif key == 'icon_fields':
            if not isinstance(value, list):
                errors.append(f'  {key}: expected an array, got {type(value).__name__}')
                drop.append(key)
            elif len(value) != 2:
                errors.append(f'  {key}: expected exactly 2 entries, got {len(value)}')
                drop.append(key)
            elif any(not isinstance(item, str) or not item for item in value):
                errors.append(f'  {key}: all entries must be non-empty strings')
                drop.append(key)
            else:
                invalid_modes = [
                    item for item in value
                    if ':' in item and item.split(':', 1)[1] not in _VALID_BAR_MODES
                ]
                if invalid_modes:
                    errors.append(
                        f'  {key}: unknown bar mode in: {", ".join(invalid_modes)}'
                        f' (valid: {", ".join(sorted(_VALID_BAR_MODES))})'
                    )
                    drop.append(key)

        elif key in ('icon_color_levels', 'bar_color_levels'):
            if not isinstance(value, list):
                errors.append(f'  {key}: expected an array of [threshold, color] pairs, got {type(value).__name__}')
                drop.append(key)
            elif any(not _valid_color_level(pair) for pair in value):
                errors.append(
                    f'  {key}: each entry must be a [threshold, color] pair with a number 0–100'
                    ' and a "#rgb"/"#rrggbb"/"#rrggbbaa" string or [R, G, B, A] array'
                )
                drop.append(key)

        elif key in _ICON_KEYS:
            if not isinstance(value, dict):
                errors.append(f'  {key}: expected an object, got {type(value).__name__}')
                drop.append(key)
            else:
                bad = [k for k, v in value.items() if not _valid_rgba(v)]
                for k in bad:
                    errors.append(f'  {key}.{k}: expected [R, G, B, A] with integers 0\u2013255')
                    del value[k]

        elif key == 'cli_command':
            # An empty object is valid and means "not set" - the native CLI
            # auto-detection stays active.
            if not isinstance(value, dict):
                errors.append(f'  {key}: expected an object mapping a name to a command array, got {type(value).__name__}')
                drop.append(key)
            else:
                invalid = False
                for name, command in value.items():
                    if not name.strip():
                        errors.append(f'  {key}: names must be non-empty strings')
                        invalid = True
                        break
                    if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item.strip() for item in command):
                        errors.append(f'  {key}.{name}: expected a non-empty array of non-empty strings')
                        invalid = True
                        break
                if invalid:
                    drop.append(key)

    for key in drop:
        del data[key]

    if errors:
        dialogs.show_error(
            'Usage Monitor for Claude - Settings Error',
            f'Invalid values in settings file:\n{path}\n\n' + '\n'.join(errors),
        )

    return data


def _icon_colors(key: str, defaults: dict[str, tuple]) -> dict[str, tuple]:
    """Merge icon color overrides from settings, converting JSON arrays to tuples."""
    overrides = _S.get(key, {})
    return {k: tuple(overrides[k]) if k in overrides else v for k, v in defaults.items()}


_S = _load_settings()

# Polling intervals (seconds)
POLL_INTERVAL = _S.get('poll_interval', 180)
POLL_FAST = _S.get('poll_fast', 120)
POLL_FAST_EXTRA = _S.get('poll_fast_extra', 2)
POLL_ERROR = _S.get('poll_error', 30)
MAX_BACKOFF = _S.get('max_backoff', 900)
IDLE_PAUSE = _S.get('idle_pause', 300)

def _css_color(rgba: tuple[int, int, int, int]) -> str:
    """Render a normalized RGBA tuple as a CSS color string."""
    r, g, b, a = rgba
    if a == 255:
        return f'#{r:02x}{g:02x}{b:02x}'
    return f'rgba({r}, {g}, {b}, {round(a / 255, 3)})'


def _parse_bar_color_levels(raw: object) -> list[tuple[float, str]] | None:
    """Normalize validated ``bar_color_levels`` pairs to CSS colors, sorted by threshold."""
    if not isinstance(raw, list):
        return None
    levels = []
    for pair in raw:
        color = _parse_level_color(pair[1])
        if color is None:  # unreachable after validation - defensive
            continue
        levels.append((float(pair[0]), _css_color(color)))
    levels.sort(key=lambda level: level[0])
    return levels


# Popup theme
BG = _S.get('bg', '#1e1e1e')
FG = _S.get('fg', '#cccccc')
FG_DIM = _S.get('fg_dim', '#888888')
FG_HEADING = _S.get('fg_heading', '#ffffff')
FG_LINK = _S.get('fg_link', '#4a9eff')
BAR_BG = _S.get('bar_bg', '#333333')
BAR_FG = _S.get('bar_fg', '#4a9eff')
BAR_FG_WARN = _S.get('bar_fg_warn', '#e05050')
BAR_DIVIDER = _S.get('bar_divider', '#000c')
BAR_MARKER = _S.get('bar_marker', '#fffc')

# Popup bar color levels: optional [threshold, color] pairs recoloring the
# popup usage bars by usage (semaphore).  None when unset - the bars then
# keep the exact bar_fg / bar_fg_warn behavior.
BAR_COLOR_LEVELS: list[tuple[float, str]] | None = _parse_bar_color_levels(_S.get('bar_color_levels'))

# Tray icon colors
ICON_LIGHT = _icon_colors('icon_light', {
    'fg': (255, 255, 255, 255),
    'fg_half': (255, 255, 255, 80),
    'fg_dim': (255, 255, 255, 140),
    'fg_warn': (224, 80, 80, 255),
})
ICON_DARK = _icon_colors('icon_dark', {
    'fg': (0, 0, 0, 255),
    'fg_half': (0, 0, 0, 80),
    'fg_dim': (0, 0, 0, 140),
    'fg_warn': (224, 80, 80, 255),
})

# Tray icon color levels: optional [threshold, color] pairs recoloring the
# icon bars and percentage digits by usage (semaphore).  None when unset -
# rendering then keeps the exact upstream fg/fg_warn behavior.


def _parse_icon_color_levels(raw: object) -> list[tuple[float, tuple[int, int, int, int]]] | None:
    """Normalize validated ``icon_color_levels`` pairs, sorted by threshold."""
    if not isinstance(raw, list):
        return None
    levels = []
    for pair in raw:
        color = _parse_level_color(pair[1])
        if color is None:  # unreachable after validation - defensive
            continue
        levels.append((float(pair[0]), color))
    levels.sort(key=lambda level: level[0])
    return levels


ICON_COLOR_LEVELS: list[tuple[float, tuple[int, int, int, int]]] | None = _parse_icon_color_levels(_S.get('icon_color_levels'))

# Tray icon fields
ICON_FIELDS: list[str] = _S.get('icon_fields', ['five_hour', 'seven_day'])

# Tray icon layout: 'number+bars' shows the top field's percentage above two
# usage bars, 'numbers' shows both fields as two stacked percentages
ICON_STYLE: str = _S.get('icon_style', 'number+bars')

# Transparent margin around the tray icon, percent of the icon size per
# side.  Drawn edge to edge the bars run into the neighbouring tray icons
# and read as one continuous strip.
ICON_MARGIN: float = _S.get('icon_margin', 10)

# Tooltip fields
TOOLTIP_FIELDS: list[str] = _S.get('tooltip_fields', ['five_hour', 'seven_day'])

# Popup fields
POPUP_FIELDS: list[str] = _S.get('popup_fields', ['*'])

# Sections and usage bars hidden while the popup is pinned (compact view)
COMPACT_HIDE: list[str] = _S.get('compact_hide', [])

# Alert thresholds
ALERT_TIME_AWARE: bool = _S.get('alert_time_aware', True)
ALERT_TIME_AWARE_BELOW: float = _S.get('alert_time_aware_below', 90)

# Notify when a background token refresh installs a new Claude CLI version
NOTIFY_CLAUDE_UPDATE: bool = _S.get('notify_claude_update', True)

# Currency

def _detect_currency_symbol() -> str:
    """Detect the system locale currency symbol for monetary formatting."""
    try:
        _locale.setlocale(_locale.LC_MONETARY, '')
        return _locale.localeconv().get('currency_symbol', '') or ''
    except _locale.Error:
        return ''


_SYSTEM_CURRENCY_SYMBOL = _detect_currency_symbol()
# None when the user set no override: presence must be explicit, because an
# override that happens to equal the system symbol still has to win over the
# API billing currency.
CURRENCY_SYMBOL: str | None = _S.get('currency_symbol')

# Language override
LANGUAGE: str = _S.get('language', '')

# Clock format for reset times: '24h' (e.g. 14:30) or '12h' (e.g. 2:30 PM)

def _detect_system_time_format() -> str:
    """Detect whether the system clock uses a 24-hour or 12-hour format.

    Inspects the locale's time format string (``T_FMT``): a format that
    renders an AM/PM marker (``%p``, or the ``%r`` 12-hour shortcut) means
    a 12-hour clock.  Falls back to ``'24h'`` if the query fails.
    """
    try:
        # Adopt the user's LC_TIME from the environment first - Python
        # starts in the C locale, whose T_FMT is always 24-hour.
        _locale.setlocale(_locale.LC_TIME, '')
        fmt = _locale.nl_langinfo(_locale.T_FMT)
        return '12h' if ('%p' in fmt or '%r' in fmt) else '24h'
    except Exception:
        return '24h'


_SYSTEM_TIME_FORMAT = _detect_system_time_format()
TIME_FORMAT: str = _S.get('time_format', _SYSTEM_TIME_FORMAT)

# Extra Claude CLI command(s) to report a version for - name -> base command
# (e.g. run the version check inside WSL).  Display only: these are listed in
# addition to the auto-detected native binary and the IDE extensions, and never
# take part in authentication (see claude_cli.py).
CLI_COMMAND: dict[str, list[str]] = _S.get('cli_command', {})

# Event commands
ON_DOUBLE_CLICK_COMMAND: list[str] = _S.get('on_double_click_command', [])
ON_RESET_COMMAND: list[str] = _S.get('on_reset_command', [])
ON_STARTUP_COMMAND: list[str] = _S.get('on_startup_command', [])
ON_THRESHOLD_COMMAND: list[str] = _S.get('on_threshold_command', [])

_ALERT_THRESHOLDS: dict[str, list[float]] = {
    'five_hour': [50, 80, 95],
    'seven_day': [95],
    'extra_usage': [50, 80, 95],
}

# Absolute extra-usage spending amounts (in major currency units, e.g. dollars)
# that trigger a notification.  Complements the percentage thresholds and is
# the only alert that can fire when extra usage has no monthly limit.  Empty
# by default - sensible amounts depend on the account's currency and budget.
ALERT_EXTRA_USAGE_SPENT: list[float] = _S.get('alert_extra_usage_spent', [])


def get_alert_thresholds(variant_key: str) -> list[float]:
    """Return the alert thresholds for a usage variant.

    Uses a fallback chain: exact user override, built-in default for
    the exact key, user override for the base period, built-in default
    for the base period, then empty list (alerts disabled).

    Parameters
    ----------
    variant_key : str
        API variant key, e.g. ``'five_hour'``, ``'seven_day_sonnet'``,
        or ``'extra_usage'``.
    """
    exact_settings_key = f'{_THRESHOLD_KEY_PREFIX}{variant_key}'
    if exact_settings_key in _S:
        return _S[exact_settings_key]

    if variant_key in _ALERT_THRESHOLDS:
        return _ALERT_THRESHOLDS[variant_key]

    # Fallback to base period (strip variant suffix)
    parts = variant_key.split('_', 2)
    if len(parts) >= 3:
        base_key = f'{parts[0]}_{parts[1]}'
        base_settings_key = f'{_THRESHOLD_KEY_PREFIX}{base_key}'
        if base_settings_key in _S:
            return _S[base_settings_key]
        if base_key in _ALERT_THRESHOLDS:
            return _ALERT_THRESHOLDS[base_key]

    return []
