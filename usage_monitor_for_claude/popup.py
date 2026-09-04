"""
Popup Window
=============

Dark-themed HTML popup window showing account info and usage bars.
Uses GTK3 with a WebKit2 web view for smooth CSS transitions and
flexible layout.

The popup assets (``popup.html``/``popup.js``/``popup.css``) come from
the Windows original: they talk to a ``window.pywebview.api`` bridge,
which an injected document-start user script recreates on top of
WebKit's script message handler.  The Linux port diverges from upstream
in one behavior: the header bar is always a drag handle - holding the
mouse down on it moves the popup, no pin required (upstream gates
dragging behind the pin).
"""
from __future__ import annotations

import json
import math
import threading
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__, state
from .claude_cli import CHANGELOG_URL, find_installations
from .formatting import divider_positions, elapsed_pct, expand_popup_fields, field_period, format_credits, popup_label, time_until
from .i18n import T
from .settings import (
    BAR_BG, BAR_COLOR_LEVELS, BAR_DIVIDER, BAR_FG, BAR_FG_WARN, BAR_MARKER, BG,
    COMPACT_HIDE, FG, FG_DIM, FG_HEADING, FG_LINK, POPUP_FIELDS,
)

try:
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    gi.require_version('WebKit2', '4.1')
    from gi.repository import Gdk, GLib, Gtk, WebKit2
except Exception:  # bindings or display stack unavailable (headless import)
    Gdk = None
    GLib = None
    Gtk = None
    WebKit2 = None

_POPUP_DIR = Path(__file__).parent / 'popup'

# GDK keyval for the Escape key (kept as a literal so tests need no Gdk).
_KEY_ESCAPE = 0xFF1B

# Margin between the popup and the work-area edges, logical pixels.
_MARGIN = 12


def _read_css_scale() -> float:
    """Return how many logical pixels WebKit renders one CSS pixel as.

    WebKit sizes a CSS pixel by ``window.devicePixelRatio``, which is the
    monitor's GDK scale factor multiplied by the desktop's Xft DPI over 96
    (a 110 dpi desktop renders at 110/96).  ``Gtk.Window.resize`` works in
    logical pixels, which already carry the GDK scale factor, so only the
    DPI part is left to apply - without it the window is sized in CSS
    pixels and ends up shorter than the page it has to show.

    Returns
    -------
    float
        The factor, or ``1.0`` when the DPI is unset or unreadable
        (headless import, no display yet, 96 dpi desktop).
    """
    if Gtk is None:
        return 1.0
    try:
        settings = Gtk.Settings.get_default()
        xft_dpi = settings.get_property('gtk-xft-dpi') if settings is not None else -1
    except Exception:
        return 1.0
    # gtk-xft-dpi is the DPI in 1024ths, and -1 when the desktop sets none.
    if not isinstance(xft_dpi, int) or xft_dpi <= 0:
        return 1.0
    return (xft_dpi / 1024) / 96


# Bridge methods JavaScript may invoke; anything else is ignored.
_BRIDGE_METHODS = frozenset({'close', 'open_url', 'set_pinned', 'begin_drag', 'drag', 'end_drag', 'report_height'})

# Injected at document-start so upstream popup.js finds the pywebview
# bridge it was written against.  Calls post a JSON envelope to the
# 'bridge' message handler and return a Promise settled from Python.
_BRIDGE_SCRIPT = """
(function () {
    if (window.pywebview) { return; }
    var nextId = 1;
    var pending = {};
    function invoke(method, args) {
        return new Promise(function (resolve, reject) {
            var id = nextId++;
            pending[id] = { resolve: resolve, reject: reject };
            window.webkit.messageHandlers.bridge.postMessage(
                JSON.stringify({ id: id, method: method, args: args }));
        });
    }
    window.__bridgeSettle = function (id, ok, value) {
        var entry = pending[id];
        if (!entry) { return; }
        delete pending[id];
        (ok ? entry.resolve : entry.reject)(value);
    };
    var api = {};
    ['close', 'open_url', 'set_pinned', 'begin_drag', 'drag', 'end_drag',
     'report_height'].forEach(function (name) {
        api[name] = function () {
            return invoke(name, Array.prototype.slice.call(arguments));
        };
    });
    window.pywebview = { api: api };
})();
"""

__all__ = ['UsagePopup']

if TYPE_CHECKING:
    from .app import UsageMonitorForClaude
    from .cache import CacheSnapshot


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _bar_fill_color(pct: float) -> str | None:
    """Return the ``bar_color_levels`` CSS fill color for *pct*, or None.

    None when no levels are configured - the popup then keeps the exact
    ``bar_fg``/``bar_fg_warn`` behavior.  With levels configured every
    bar gets an explicit color (the base ``bar_fg`` below the lowest
    threshold), superseding the warn fill switch - the levels are a
    superset of that signal.
    """
    if not BAR_COLOR_LEVELS:
        return None

    color = BAR_FG
    for threshold, level_color in BAR_COLOR_LEVELS:  # sorted by threshold
        if pct >= threshold:
            color = level_color
    return color


def _usage_entries(usage: dict[str, Any]) -> list[tuple[str, dict[str, Any] | None, int | None, str]]:
    """Return ``(label, data, period, field)`` tuples from the given usage data.

    The raw *field* name is included so the popup can hide individual bars
    by field name when the pinned compact view is configured.
    """
    fields = expand_popup_fields(POPUP_FIELDS, usage)
    return [(popup_label(key), usage.get(key), field_period(key), key) for key in fields]


def _snapshot_to_dict(
    snap: CacheSnapshot, installations: list[dict[str, str]] | None = None, next_poll_time: float | None = None,
) -> dict[str, Any]:
    """Convert a CacheSnapshot to a JSON-serializable dict for the popup JS.

    Parameters
    ----------
    snap : CacheSnapshot
        Immutable snapshot of the cache state.
    installations : list or None
        Pre-computed installation list, or None to detect now.
    next_poll_time : float or None
        Unix timestamp of the next scheduled API poll.
    """
    # Profile - truthiness check (not `is not None`): hides the account section when the API
    # returns an empty or incomplete response, instead of rendering empty Email/Plan fields.
    profile = None
    if snap.profile:
        account = snap.profile.get('account') or {}
        org = snap.profile.get('organization') or {}
        profile = {
            'email': account.get('email', ''),
            'plan': org.get('organization_type', '').replace('_', ' ').title(),
        }

    # Usage bars
    usage = []
    if snap.usage:
        for label, entry, period, field in _usage_entries(snap.usage):
            if not entry or entry.get('utilization') is None:
                continue
            pct = entry.get('utilization', 0) or 0
            resets_at = entry.get('resets_at', '')
            time_pct = elapsed_pct(resets_at, period) if period else None
            warn = pct >= 100 or (time_pct is not None and pct > time_pct)
            marker_rel = max(0.0, min(1.0, time_pct / 100)) if time_pct is not None else None

            usage.append({
                'key': field,
                'label': label,
                'pct_text': f'{pct:.0f}%',
                'fill_pct': max(0.0, min(1.0, pct / 100)),
                'warn': warn,
                'fill_color': _bar_fill_color(pct),
                'reset_text': time_until(resets_at) if resets_at else '',
                'dividers': divider_positions(resets_at, period) if period else [],
                'marker_rel': marker_rel,
            })

    # Extra usage
    extra = None
    if snap.usage:
        extra_data = snap.usage.get('extra_usage')
        if extra_data and extra_data.get('is_enabled'):
            used = extra_data.get('used_credits')
            if used is not None:
                limit = extra_data.get('monthly_limit', 0) or 0
                currency = extra_data.get('currency')
                decimal_places = extra_data.get('decimal_places')
                if limit > 0:
                    pct = used / limit * 100
                    extra = {
                        'has_limit': True,
                        'pct_text': f'{pct:.0f}%',
                        'fill_pct': max(0.0, min(1.0, pct / 100)),
                        'fill_color': _bar_fill_color(pct),
                        'spent_text': T['extra_usage_spent'].format(
                            used=format_credits(used, currency, decimal_places),
                            limit=format_credits(limit, currency, decimal_places),
                        ),
                    }
                else:
                    # No monthly cap (e.g. uncapped pay-as-you-go credits) - show
                    # what has been spent without a percentage bar to imply a limit.
                    extra = {
                        'has_limit': False,
                        'pct_text': '',
                        'fill_pct': 0.0,
                        'fill_color': None,
                        'spent_text': T['extra_usage_spent_no_limit'].format(
                            used=format_credits(used, currency, decimal_places),
                        ),
                    }

    # Installations
    if installations is None:
        installations = [{'name': i.name, 'version': i.version} for i in find_installations()]

    # Status - pass raw timestamps for JS live timer; fallback text for initial load
    if not snap.usage:
        if snap.last_error:
            status: dict[str, Any] = {'text': snap.last_error[:120], 'is_error': True}
        else:
            status = {'text': T['status_refreshing'], 'is_error': False, 'refreshing': True}
    else:
        status = {
            'last_success_time': snap.last_success_time,
            'next_poll_time': next_poll_time,
            'refreshing': snap.refreshing,
            'error': snap.last_error[:120] if snap.last_error else None,
        }

    return {
        'profile': profile,
        'usage': usage,
        'extra': extra,
        'installations': installations,
        'status': status,
    }


def _init_config(snap: CacheSnapshot, next_poll_time: float | None = None) -> dict[str, Any]:
    """Build the config object passed to JS ``init()`` after the page loads."""
    return {
        'colors': {
            'bg': BG, 'fg': FG, 'fg_dim': FG_DIM, 'fg_heading': FG_HEADING, 'fg_link': FG_LINK,
            'bar_bg': BAR_BG, 'bar_fg': BAR_FG, 'bar_fg_warn': BAR_FG_WARN, 'bar_divider': BAR_DIVIDER, 'bar_marker': BAR_MARKER,
        },
        't': {
            'title': T['popup_title'], 'account': T['account'], 'email': T['email'], 'plan': T['plan'],
            'usage': T['usage'], 'extra_usage': T['extra_usage'],
            'claude_code': T['claude_code'], 'changelog': T['changelog'],
            'pin_popup': T['pin_popup'], 'unpin_popup': T['unpin_popup'],
            'status_updated_s': T['status_updated_s'], 'status_updated': T['status_updated'],
            'status_next_update': T['status_next_update'], 'status_refreshing': T['status_refreshing'],
            'duration_hm': T['duration_hm'], 'duration_m': T['duration_m'], 'duration_s': T['duration_s'],
        },
        'app_version': __version__,
        'compact_hide': COMPACT_HIDE,
        'data': _snapshot_to_dict(snap, next_poll_time=next_poll_time),
    }


# ---------------------------------------------------------------------------
# JS-callable API
# ---------------------------------------------------------------------------

class _PopupApi:
    """Methods exposed to JavaScript via the injected pywebview-style bridge."""

    def __init__(self, popup: UsagePopup) -> None:
        self._popup = popup

    def close(self) -> None:
        self._popup._close()

    def open_url(self) -> None:
        webbrowser.open(CHANGELOG_URL)

    def set_pinned(self, pinned: bool) -> bool:
        return self._popup._set_pinned(pinned)

    def begin_drag(self) -> bool:
        return self._popup._begin_drag()

    def drag(self) -> bool:
        return self._popup._drag()

    def end_drag(self) -> None:
        self._popup._end_drag()

    def report_height(self, height: int) -> None:
        """Called by JS ResizeObserver when content height changes.

        ``height`` is in CSS pixels; it is converted to the logical pixels
        GTK sizes windows in before any geometry work, so everything below
        this point speaks a single unit.

        Bridge calls are serialized on the GTK main loop in production,
        but the geometry lock still guards the whole check-resize-show
        sequence so the contract (no interleaved stale resize, single
        show) holds for any caller thread.
        """
        if not height:
            return

        popup = self._popup
        window_height = popup._to_window_pixels(height)
        with popup._geometry_lock:
            if window_height == popup._last_height:
                return
            popup._last_height = window_height
            popup._resize_and_position(window_height)
            if not popup._shown:
                popup._show_window()


# ---------------------------------------------------------------------------
# Popup window
# ---------------------------------------------------------------------------

class UsagePopup:
    """Dark-themed HTML popup window showing account info and usage bars."""

    # Design width and placeholder height, both in CSS pixels - see
    # _to_window_pixels for the conversion to the logical pixels GTK uses.
    WIDTH = 340
    _CHECK_MS = 2000
    _INITIAL_HEIGHT = 400

    def __init__(self, app: UsageMonitorForClaude) -> None:
        """Create and display a popup window with usage details.

        Blocks the calling thread until the window is closed.  Requires
        the GTK main loop to be running on the main thread; the window
        itself is built there via ``GLib.idle_add``.

        Parameters
        ----------
        app : UsageMonitorForClaude
            Parent application providing ``cache`` for data access.
        """
        self.app = app
        self._running = True
        self._pinned = False
        # True once a header drag moved the popup this session - the popup
        # then stays where the user put it instead of following the tray
        # corner on height changes.
        self._moved_by_drag = False
        self._dragging = False
        self._drag_offset = (0, 0)
        # Last position commanded by _drag() - saved on _end_drag.  The
        # window's own get_position() cannot be trusted right after a move:
        # the WM delivers the confirming configure event late, so reading it
        # on mouseup can return the pre-drag position and would persist the
        # spot the user just dragged away from.
        self._last_drag_target: tuple[int, int] | None = None
        self._closed = threading.Event()
        # Logical pixels per CSS pixel, read once: the desktop DPI does not
        # change while a popup is open.
        self._css_scale = _read_css_scale()
        # Serializes the resize/show geometry path.
        self._geometry_lock = threading.Lock()
        # 0 means "no height reported yet": the first ResizeObserver report
        # must always count as a change so the window gets resized,
        # positioned, and shown even when the content is exactly
        # _INITIAL_HEIGHT tall.
        self._last_height = 0
        self._shown = False
        # Position saved when a previous popup was dragged; restored on the
        # first placement when it still touches a monitor's work area.
        self._saved_position = state.load_popup_position()
        self._restored = False
        self._window: Any = None
        self._webview: Any = None
        snap = app.cache.snapshot
        self._last_version = snap.version

        self._api = _PopupApi(self)

        if GLib is None:
            self._closed.set()
            return

        GLib.idle_add(self._build_window)
        self._closed.wait()

    # Window construction (GTK thread)

    def _build_window(self) -> bool:
        """Create the GTK window and WebKit view on the main loop."""
        try:
            content_manager = WebKit2.UserContentManager()
            content_manager.add_script(WebKit2.UserScript.new(
                _BRIDGE_SCRIPT,
                WebKit2.UserContentInjectedFrames.TOP_FRAME,
                WebKit2.UserScriptInjectionTime.START,
                None, None,
            ))
            content_manager.register_script_message_handler('bridge')
            content_manager.connect('script-message-received::bridge', self._on_bridge_message)

            webview = WebKit2.WebView.new_with_user_content_manager(content_manager)
            background = Gdk.RGBA()
            if background.parse(BG):
                webview.set_background_color(background)
            webview.connect('load-changed', self._on_load_changed)

            window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            window.set_decorated(False)
            window.set_skip_taskbar_hint(True)
            window.set_skip_pager_hint(True)
            window.set_keep_above(True)
            window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            window.set_default_size(self._window_width(), self._to_window_pixels(self._INITIAL_HEIGHT))
            window.add(webview)
            window.connect('focus-out-event', self._on_focus_out)
            window.connect('key-press-event', self._on_key_press)
            window.connect('destroy', self._on_window_destroyed)

            self._webview = webview
            self._window = window

            # Show invisibly so the page can lay out and report its real
            # height; the first report resizes, positions and reveals it.
            window.set_opacity(0.0)
            window.show_all()
            webview.load_uri((_POPUP_DIR / 'popup.html').as_uri())
        except Exception:
            self._close()

        return False

    def _on_load_changed(self, _webview: Any, load_event: Any) -> None:
        """Inject the init config once the page finished loading."""
        if WebKit2 is not None and load_event != WebKit2.LoadEvent.FINISHED:
            return

        config = _init_config(self.app.cache.snapshot, next_poll_time=self.app._next_poll_time)
        try:
            self._evaluate_js(f'init({json.dumps(config)})')
        except Exception:
            pass

    # Bridge plumbing

    def _on_bridge_message(self, _content_manager: Any, message: Any) -> None:
        """Dispatch one JS bridge call and settle its Promise."""
        try:
            payload = json.loads(self._message_text(message))
            method = payload.get('method', '')
            args = payload.get('args') or []
            call_id = payload.get('id')
        except Exception:
            return

        ok = True
        result: Any = None
        try:
            if method in _BRIDGE_METHODS:
                result = getattr(self._api, method)(*args)
        except Exception:
            ok = False

        if call_id is None:
            return
        try:
            self._evaluate_js(f'window.__bridgeSettle({json.dumps(call_id)}, {json.dumps(ok)}, {json.dumps(result)})')
        except Exception:
            pass

    @staticmethod
    def _message_text(message: Any) -> str:
        """Extract the JSON string from a script-message payload."""
        get_js_value = getattr(message, 'get_js_value', None)
        if get_js_value is not None:
            return get_js_value().to_string()
        return message.to_string()

    def _evaluate_js(self, script: str) -> None:
        """Schedule *script* on the popup page (any thread).

        Raises when the window is already gone so callers with retry
        logic (the update loop) can react.
        """
        if self._window is None or GLib is None:
            raise RuntimeError('popup window is not available')
        GLib.idle_add(self._run_js, script)

    def _run_js(self, script: str) -> bool:
        webview = self._webview
        if webview is None:
            return False
        try:
            webview.evaluate_javascript(script, -1, None, None, None, None, None)
        except AttributeError:
            # WebKitGTK before 2.40 exposes only the deprecated call.
            webview.run_javascript(script, None, None, None)
        except Exception:
            pass
        return False

    # Dismissal

    def _on_focus_out(self, _widget: Any = None, _event: Any = None) -> bool:
        """Close on focus loss unless pinned (or mid-drag on the header)."""
        if self._shown and not self._pinned and not self._dragging:
            self._close()
        return False

    def _on_key_press(self, _widget: Any, event: Any) -> bool:
        """Close on Escape unless pinned."""
        if getattr(event, 'keyval', None) == _KEY_ESCAPE and self._shown and not self._pinned:
            self._close()
            return True
        return False

    def _on_window_destroyed(self, _widget: Any = None) -> None:
        self._window = None
        self._webview = None
        self._running = False
        self._closed.set()

    def _close(self) -> None:
        self._running = False
        if GLib is not None and self._window is not None:
            GLib.idle_add(self._destroy_window)
        self._closed.set()

    def _destroy_window(self) -> bool:
        window, self._window = self._window, None
        self._webview = None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
        return False

    # Pin & drag

    def _set_pinned(self, pinned: bool) -> bool:
        self._pinned = bool(pinned)
        return self._pinned

    def _begin_drag(self) -> bool:
        """Anchor the pointer to the window for a header drag.

        Dragging needs no pin - holding the mouse down on the header bar
        always moves the popup.  Records the offset between the pointer
        and the window's top-left corner; each drag step then computes
        the absolute position from the current pointer, so out-of-order
        calls converge instead of accumulating drift.
        """
        if self._window is None:
            return False

        pointer_x, pointer_y = self._pointer_position()
        win_x, win_y = self._window.get_position()
        self._drag_offset = (pointer_x - win_x, pointer_y - win_y)
        self._last_drag_target = None
        self._dragging = True
        return True

    def _drag(self) -> bool:
        """Reposition the popup so the pointer keeps its initial grab offset."""
        if self._window is None:
            return False
        # JS enables dragging optimistically on mousedown and may deliver a
        # drag step whose begin_drag message was lost or not yet settled;
        # anchor the grab at the current pointer so the gesture self-heals
        # instead of dying silently.
        if not self._dragging and not self._begin_drag():
            return False

        pointer_x, pointer_y = self._pointer_position()
        target = (pointer_x - self._drag_offset[0], pointer_y - self._drag_offset[1])
        self._window.move(*target)
        self._last_drag_target = target
        self._moved_by_drag = True
        return True

    def _end_drag(self) -> None:
        """End a header drag and remember where the popup was dropped.

        Persists the last position _drag() itself commanded, so the next
        popup opens where the user left this one.  A drag that never moved
        (a plain header click) saves nothing.  Saving is best-effort - the
        state store is silent on failure.
        """
        was_dragging, self._dragging = self._dragging, False
        target, self._last_drag_target = self._last_drag_target, None
        if not was_dragging or target is None:
            return
        state.save_popup_position(*target)

    # Geometry

    @staticmethod
    def _pointer_position() -> tuple[int, int]:
        """Return the current pointer position in logical screen coordinates."""
        display = Gdk.Display.get_default()
        pointer = display.get_default_seat().get_pointer()
        _screen, x, y = pointer.get_position()
        return x, y

    def _pointer_and_workarea(self) -> tuple[tuple[int, int], tuple[int, int, int, int]]:
        """Return the pointer position and its monitor's work area.

        The work area excludes docked panels (e.g. the XFCE panel), so a
        corner of it sits right next to the tray.
        """
        display = Gdk.Display.get_default()
        pointer = display.get_default_seat().get_pointer()
        _screen, pointer_x, pointer_y = pointer.get_position()
        monitor = display.get_monitor_at_point(pointer_x, pointer_y)
        workarea = monitor.get_workarea()
        return (pointer_x, pointer_y), (workarea.x, workarea.y, workarea.width, workarea.height)

    def _tray_position(self, width: int, height: int) -> tuple[int, int]:
        """Calculate the popup position near the system tray.

        There is no portable way to locate the tray icon itself, so the
        popup lands in the work-area corner nearest the pointer - which,
        right after a tray click, is the corner next to the tray.

        Parameters
        ----------
        width : int
            Window width in logical pixels.
        height : int
            Window height in logical pixels.

        Returns
        -------
        tuple[int, int]
            Logical (x, y) coordinates for ``Gtk.Window.move``.
        """
        (pointer_x, pointer_y), (area_x, area_y, area_w, area_h) = self._pointer_and_workarea()

        if pointer_x < area_x + area_w / 2:
            x = area_x + _MARGIN
        else:
            x = area_x + area_w - width - _MARGIN

        if pointer_y < area_y + area_h / 2:
            y = area_y + _MARGIN
        else:
            y = area_y + area_h - height - _MARGIN

        return int(x), int(y)

    @staticmethod
    def _monitor_workareas() -> list[tuple[int, int, int, int]]:
        """Return every monitor's work area as ``(x, y, width, height)``."""
        display = Gdk.Display.get_default()
        return [
            (area.x, area.y, area.width, area.height)
            for area in (display.get_monitor(index).get_workarea() for index in range(display.get_n_monitors()))
        ]

    def _clamp_into_workarea(self, x: int, y: int, width: int, height: int) -> tuple[int, int] | None:
        """Clamp the popup rectangle fully into the work area it overlaps most.

        Returns None when the rectangle touches no monitor's work area
        (e.g. a position saved on a since-disconnected monitor) or the
        monitor layout cannot be read - the caller then falls back to
        the corner placement.
        """
        best = None
        best_overlap = 0
        try:
            for area_x, area_y, area_w, area_h in self._monitor_workareas():
                overlap_w = min(x + width, area_x + area_w) - max(x, area_x)
                overlap_h = min(y + height, area_y + area_h) - max(y, area_y)
                if overlap_w > 0 and overlap_h > 0 and overlap_w * overlap_h > best_overlap:
                    best_overlap = overlap_w * overlap_h
                    best = (area_x, area_y, area_w, area_h)
        except Exception:
            return None

        if best is None:
            return None
        area_x, area_y, area_w, area_h = best
        return (
            int(max(area_x, min(x, area_x + area_w - width))),
            int(max(area_y, min(y, area_y + area_h - height))),
        )

    def _to_window_pixels(self, css_pixels: int) -> int:
        """Convert a CSS-pixel length into the logical pixels GTK sizes in.

        Rounded up: a window one pixel short of its content clips the last
        row of text, while one pixel of spare space is invisible.
        """
        return max(1, math.ceil(css_pixels * self._css_scale))

    def _window_width(self) -> int:
        """Return the popup width in logical pixels."""
        return self._to_window_pixels(self.WIDTH)

    def _resize_and_position(self, height: int) -> None:
        """Resize the window and reposition it near the system tray.

        *height* is already in logical pixels - the CSS height reported by
        JavaScript is converted in ``_PopupApi.report_height``.

        The first call happens while the window is still invisible
        (opacity 0), so separate resize/move calls cause no visible jump.

        The first placement prefers the position saved when a previous
        popup was dragged, clamped into its monitor's work area; a saved
        position that touches no monitor falls back to the corner nearest
        the pointer.  A popup the user dragged this session stays exactly
        where it was dropped.  After a restored placement, later height
        reports keep the popup anchored where it is (re-clamping only
        when the new height would overflow the work area) instead of
        snapping to a corner; the corner placement keeps its existing
        per-report behavior.
        """
        width = self._window_width()
        self._window.resize(width, height)
        if self._moved_by_drag:
            return

        if not self._shown and self._saved_position is not None:
            placed = self._clamp_into_workarea(*self._saved_position, width, height)
            if placed is not None:
                self._restored = True
                self._window.move(*placed)
                return
            self._saved_position = None  # off-screen: corner fallback for this open

        if self._restored:
            # Keep the popup anchored where the user (or the restore) put
            # it - only pull it back when the new height overflows.
            try:
                x, y = self._window.get_position()
            except Exception:
                return
            clamped = self._clamp_into_workarea(x, y, width, height)
            if clamped is not None and clamped != (x, y):
                self._window.move(*clamped)
            return

        x, y = self._tray_position(width, height)
        self._window.move(x, y)

    def _show_window(self) -> None:
        """Make the popup visible after the first resize positioned it correctly."""
        self._shown = True
        if GLib is not None:
            GLib.idle_add(self._present_window)
        threading.Thread(target=self._update_loop, daemon=True).start()

    def _present_window(self) -> bool:
        window = self._window
        if window is not None:
            try:
                window.set_opacity(1.0)
                window.present()
            except Exception:
                pass
        return False

    # Live updates

    def _update_loop(self) -> None:
        """Poll for data changes and push updates to the popup."""
        cached_installations = [{'name': i.name, 'version': i.version} for i in find_installations()]
        last_next_poll_time = self.app._next_poll_time
        while self._running:
            time.sleep(self._CHECK_MS / 1000)
            if not self._running:
                break
            try:
                snap = self.app.cache.snapshot
                next_poll_time = self.app._next_poll_time
                if snap.version == self._last_version and next_poll_time == last_next_poll_time:
                    continue
                if snap.version != self._last_version:
                    cached_installations = [{'name': i.name, 'version': i.version} for i in find_installations()]
                data = _snapshot_to_dict(snap, installations=cached_installations, next_poll_time=next_poll_time)
                self._evaluate_js(f'updateData({json.dumps(data)})')
                # Commit the markers only after a successful push, so a failed
                # update is retried on the next tick instead of being skipped
                # by the dedup check until the next data change.
                self._last_version = snap.version
                last_next_poll_time = next_poll_time
            except Exception:
                # A transient failure (snapshot conversion, filesystem scan,
                # one-off evaluate_js hiccup) must not end the update stream -
                # a pinned popup can live for days.  The destroyed-window
                # case exits via the _running flag on the next iteration.
                continue
