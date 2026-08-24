"""
Tray Icon
==========

Creates system tray icons and detects the desktop panel theme.
"""
from __future__ import annotations

import functools
import subprocess
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from .settings import ICON_COLOR_LEVELS, ICON_DARK, ICON_LIGHT, ICON_STYLE

__all__ = ['load_font', 'taskbar_uses_light_theme', 'watch_theme_change', 'create_icon_image', 'create_status_image']

TRANSPARENT = (0, 0, 0, 0)

# Fontconfig patterns and direct fallback paths per font role.  The symbol
# font must cover U+2715 (the exhausted-quota cross), which the common text
# faces lack.
_FONT_PATTERN = 'DejaVu Sans:bold'
_FONT_FALLBACK_PATHS = (
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
)
_SYMBOL_PATTERN = 'Noto Sans Symbols 2'
_SYMBOL_FALLBACK_PATHS = (
    '/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
)

# Icon canvas and bar geometry (pixels)
ICON_SIZE = 64
BAR_HEIGHT = 9
BAR_GAP = 3
MARKER_WIDTH = 4

# Row height for the 'numbers' icon style - two rows split the canvas evenly.
NUMBER_ROW_HEIGHT = 32


def _fc_match(pattern: str) -> str | None:
    """Resolve a fontconfig pattern to a font file path, or None on failure."""
    try:
        proc = subprocess.run(
            ['fc-match', '-f', '%{file}', pattern],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None

    path = proc.stdout.strip() if proc.returncode == 0 else ''
    return path or None


@functools.lru_cache(maxsize=None)
def load_font(size: int, symbol: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load font at given size. Use symbol=True for Unicode glyphs not in the text face."""
    if symbol:
        pattern, fallbacks = _SYMBOL_PATTERN, _SYMBOL_FALLBACK_PATHS
    else:
        pattern, fallbacks = _FONT_PATTERN, _FONT_FALLBACK_PATHS

    matched = _fc_match(pattern)
    candidates = (matched, *fallbacks) if matched else fallbacks
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue

    return ImageFont.load_default()


def _level_color(pct: float, fg: tuple) -> tuple:
    """Return the ``icon_color_levels`` color active for *pct*, or *fg*.

    The active color is the configured pair with the highest threshold
    at or below *pct*; a *pct* below the lowest threshold (and any *pct*
    when no levels are configured) keeps the base *fg*.  The same levels
    apply on light and dark panels - configured colors are absolute.
    """
    if not ICON_COLOR_LEVELS:
        return fg

    color = fg
    for threshold, level_color in ICON_COLOR_LEVELS:  # sorted by threshold
        if pct >= threshold:
            color = level_color
    return color


def _gtk_settings():
    """Return the default ``Gtk.Settings``, or None when GTK is unavailable."""
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk

        return Gtk.Settings.get_default()
    except Exception:
        return None


def taskbar_uses_light_theme() -> bool:
    """Return True if the desktop panel/theme is light.

    Reads the GTK theme preference (``gtk-application-prefer-dark-theme``
    and a ``dark`` suffix in ``gtk-theme-name``).  Returns False (dark)
    when the theme cannot be determined - light icons on a light panel
    are still legible, the inverse is not.
    """
    settings = _gtk_settings()
    if settings is None:
        return False

    try:
        if settings.get_property('gtk-application-prefer-dark-theme'):
            return False
        theme_name = settings.get_property('gtk-theme-name') or ''
        return 'dark' not in theme_name.lower()
    except Exception:
        return False


def watch_theme_change(callback: Callable[[], None]) -> None:
    """Call *callback* whenever the GTK theme changes.

    Connects to the ``Gtk.Settings`` property-change notifications and
    returns immediately - no thread or polling involved.  A missing GTK
    stack simply leaves the theme unwatched.
    """
    settings = _gtk_settings()
    if settings is None:
        return

    def _on_change(*_args) -> None:
        try:
            callback()
        except Exception:
            # A transient callback failure (icon re-render during a panel
            # restart) must not end theme watching for the session.
            pass

    settings.connect('notify::gtk-theme-name', _on_change)
    settings.connect('notify::gtk-application-prefer-dark-theme', _on_change)


def create_icon_image(
    pct_top: float, pct_bottom: float, light_taskbar: bool = False,
    *, mode_top: str = 'utilization', mode_bottom: str = 'utilization',
    time_pct_top: float | None = None, time_pct_bottom: float | None = None,
    extra_usage_available: bool = False,
) -> Image.Image:
    """Create tray icon: 'C' letter + two usage bars.

    With ``ICON_STYLE`` set to ``'numbers'`` the icon instead shows the two
    utilization percentages as stacked rows without bars; the mode and
    elapsed-time parameters have no effect in that style.

    Parameters
    ----------
    pct_top : float
        Utilization percentage (0-100) for the upper bar.
    pct_bottom : float
        Utilization percentage (0-100) for the lower bar.
    light_taskbar : bool
        Use dark-on-light colors for a light taskbar.
    mode_top : str
        Display mode for the upper bar: ``'utilization'`` (linear fill)
        or ``'overage'`` (fills as usage exceeds the time marker).
    mode_bottom : str
        Display mode for the lower bar.  Same semantics as *mode_top*.
    time_pct_top : float or None
        Elapsed-time percentage for the upper bar.  Draws the reset-time
        marker in ``utilization`` mode; required for ``overage`` mode.
    time_pct_bottom : float or None
        Elapsed-time percentage for the lower bar.  Same semantics as
        *time_pct_top*.
    extra_usage_available : bool
        True if the account has paid extra-usage credits still available.
        When a quota is fully exhausted, this decides whether to show ``$``
        (continuing costs money) or ``✕`` (fully blocked).
    """
    colors = ICON_DARK if light_taskbar else ICON_LIGHT
    fg, fg_half, fg_warn = colors['fg'], colors['fg_half'], colors['fg_warn']

    S = ICON_SIZE
    img = Image.new('RGBA', (S, S), TRANSPARENT)
    draw = ImageDraw.Draw(img)

    if ICON_STYLE == 'numbers':
        # Two states collapse both rows into one full-size glyph: idle shows
        # the single 'C', and both quotas exhausted shows one large '✕'/'$' -
        # extra_usage_available applies account-wide, so the two rows would
        # only repeat the same symbol twice at half size.
        if pct_top >= 100 and pct_bottom >= 100 and not extra_usage_available:
            _draw_centered_text(draw, '\u2715', load_font(36, symbol=True), 2, fg)
        elif pct_top >= 100 and pct_bottom >= 100:
            _draw_centered_text(draw, '$', load_font(42), 2, fg)
        elif pct_top <= 0 and pct_bottom <= 0:
            _draw_centered_text(draw, 'C', load_font(42), 0, fg)
        else:
            _draw_number_row(draw, 0, pct_top, extra_usage_available, fg)
            _draw_number_row(draw, NUMBER_ROW_HEIGHT, pct_bottom, extra_usage_available, fg)
        return img

    # Top glyph: "✕" when any quota exhausted and no extra credits left,
    # "$" when exhausted but paid extra-usage still available,
    # "C" while usage is still zero, otherwise the percentage.  Only the
    # percentage digits follow the configured color levels - the error and
    # status glyphs keep the base fg.
    stroke_width = 0
    glyph_color = fg
    any_exhausted = pct_top >= 100 or pct_bottom >= 100
    if any_exhausted and not extra_usage_available:
        text, font = '\u2715', load_font(36, symbol=True)
        stroke_width = 2
    elif any_exhausted:
        text, font = '$', load_font(42)
        stroke_width = 2
    elif pct_top > 0:
        # Clamp to 99: values in [99.5, 100) would round to a three-digit
        # '100' that overflows the canvas and reads as exhausted.
        text, font = f'{min(pct_top, 99):.0f}', load_font(40)
        glyph_color = _level_color(pct_top, fg)
    else:
        text, font = 'C', load_font(42)

    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    tw = bbox[2] - bbox[0]
    draw.text(((S - tw) / 2 - bbox[0], -bbox[1]), text, fill=glyph_color, font=font, stroke_width=stroke_width, stroke_fill=glyph_color)

    # Progress bars - full width, flush to bottom
    bar2_y = S - BAR_HEIGHT
    bar1_y = bar2_y - BAR_GAP - BAR_HEIGHT

    _draw_usage_bar(draw, bar1_y, pct_top, mode_top, time_pct_top, fg, fg_half, fg_warn)
    _draw_usage_bar(draw, bar2_y, pct_bottom, mode_bottom, time_pct_bottom, fg, fg_half, fg_warn)

    return img


def _draw_centered_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, stroke_width: int, fg: tuple,
        box_top: int = 0, box_height: int = ICON_SIZE) -> None:
    """Draw *text* horizontally centered, vertically centered within the given box."""
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (ICON_SIZE - tw) / 2 - bbox[0]
    y = box_top + (box_height - th) / 2 - bbox[1]
    draw.text((x, y), text, fill=fg, font=font, stroke_width=stroke_width, stroke_fill=fg)


def _draw_number_row(draw: ImageDraw.ImageDraw, row_top: int, pct: float, extra_usage_available: bool, fg: tuple) -> None:
    """Draw one row of the ``'numbers'`` icon style at vertical offset *row_top*.

    The row shows the percentage clamped to 99, or ``✕``/``$`` when the
    quota is exhausted (following the same extra-usage rule as the classic
    top glyph).  The percentage digits follow the configured color levels
    for this row's own *pct*; the exhausted glyphs (and unconfigured
    levels) keep the base *fg*, matching the classic glyph.
    """
    stroke_width = 0
    color = fg
    if pct >= 100 and not extra_usage_available:
        text, font = '\u2715', load_font(34, symbol=True)
        stroke_width = 2
    elif pct >= 100:
        # 32 is the ceiling for '$': its ascender and descender already span
        # 30 of the 32 row pixels, larger sizes would touch the next row.
        text, font = '$', load_font(32)
        stroke_width = 1
    else:
        # Clamp to 99: values in [99.5, 100) would round to a three-digit
        # '100' that overflows the canvas and reads as exhausted.
        text, font = f'{min(pct, 99):.0f}', load_font(40)
        color = _level_color(pct, fg)

    _draw_centered_text(draw, text, font, stroke_width, color, row_top, NUMBER_ROW_HEIGHT)


def _draw_usage_bar(draw: ImageDraw.ImageDraw, y: int, pct: float, mode: str, time_pct: float | None, fg: tuple, fg_half: tuple, fg_warn: tuple) -> None:
    """Draw one full-width usage bar at vertical offset *y*.

    In ``utilization`` mode the bar fills linearly with *pct* and shows a
    reset-time marker in *fg* at the *time_pct* position; the fill switches
    to *fg_warn* when usage is ahead of the elapsed time or fully exhausted,
    mirroring the popup's warning fill.  In ``overage`` mode the bar fills
    as *pct* exceeds *time_pct* and no marker is drawn - elapsed time is
    already encoded in the fill.

    When ``icon_color_levels`` is configured, every fill is tinted with the
    active level color for this bar's own *pct* instead - the levels are a
    superset of the ahead-of-time signal, so they take precedence over the
    *fg_warn* switch.  The half-tone track and the marker keep their base
    colors either way.
    """
    draw.rectangle([0, y, ICON_SIZE - 1, y + BAR_HEIGHT - 1], fill=fg_half)

    if mode == 'overage' and time_pct is not None:
        fill = _level_color(pct, fg)
        if time_pct >= 100:
            # End state for a stale window (elapsed time clamped to 100%,
            # e.g. between a reset and the confirming poll): usage below the
            # limit stayed within budget (empty bar), an exhausted quota
            # keeps the bar full - never the linear utilization fill.
            if pct >= 100:
                draw.rectangle([0, y, ICON_SIZE - 1, y + BAR_HEIGHT - 1], fill=fill)
            return

        overage = max(0.0, pct - time_pct)
        fill_ratio = min(1.0, overage / (100 - time_pct))
        fill_w = max(0, int(ICON_SIZE * fill_ratio))
        if fill_w > 0:
            draw.rectangle([0, y, fill_w - 1, y + BAR_HEIGHT - 1], fill=fill)
        return

    fill_w = max(0, min(ICON_SIZE, int(ICON_SIZE * pct / 100)))
    if fill_w > 0:
        if ICON_COLOR_LEVELS:
            fill = _level_color(pct, fg)
        else:
            warn = mode == 'utilization' and (pct >= 100 or (time_pct is not None and pct > time_pct))
            fill = fg_warn if warn else fg
        draw.rectangle([0, y, fill_w - 1, y + BAR_HEIGHT - 1], fill=fill)

    if mode != 'utilization' or time_pct is None:
        return

    marker_x = min(ICON_SIZE - MARKER_WIDTH, max(0, int(ICON_SIZE * time_pct / 100) - MARKER_WIDTH // 2))
    marker_end = marker_x + MARKER_WIDTH - 1
    draw.rectangle([marker_x, y, marker_end, y + BAR_HEIGHT - 1], fill=fg)


def create_status_image(text: str, light_taskbar: bool = False) -> Image.Image:
    """Create monochrome centered-text icon for error/status states."""
    fg_dim = (ICON_DARK if light_taskbar else ICON_LIGHT)['fg_dim']

    S = 64
    img = Image.new('RGBA', (S, S), TRANSPARENT)
    draw = ImageDraw.Draw(img)
    font = load_font(46)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((S - tw) / 2 - bbox[0], (S - th) / 2 - bbox[1]), text, fill=fg_dim, font=font)

    return img
