"""
Idle Detection
===============

Detects user inactivity and session lock state on Linux.

Idle time comes from the X11 XScreenSaver extension via ctypes
(``libXss``), lock state from systemd-logind over D-Bus via Gio -
no extra dependencies required.

Both probes are best effort: without an X display (e.g. Wayland) or
without logind they degrade to "not idle" / "not locked", and the
unavailable result is cached so the expensive probe never reruns.
"""
from __future__ import annotations

import ctypes

__all__ = ['get_idle_seconds', 'is_workstation_locked']


class _XScreenSaverInfo(ctypes.Structure):
    _fields_ = [
        ('window', ctypes.c_ulong),
        ('state', ctypes.c_int),
        ('kind', ctypes.c_int),
        ('til_or_since', ctypes.c_ulong),
        ('idle', ctypes.c_ulong),  # milliseconds since last input
        ('eventMask', ctypes.c_ulong),
    ]


# X11 connection state, initialized lazily on first use:
# (xlib, xss, display, root_window, info_ptr) once open, None before.
# _x11_unavailable latches a failed probe so a missing display or
# library is not re-probed on every poll.
_x11: tuple | None = None
_x11_unavailable = False


def _init_x11() -> None:
    """Open the X display and allocate the XScreenSaver info struct once."""
    global _x11, _x11_unavailable
    if _x11 is not None or _x11_unavailable:
        return

    try:
        xlib = ctypes.CDLL('libX11.so.6')
        xss = ctypes.CDLL('libXss.so.1')

        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_XScreenSaverInfo)
        xss.XScreenSaverQueryInfo.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XScreenSaverInfo),
        ]
        xss.XScreenSaverQueryInfo.restype = ctypes.c_int

        display = xlib.XOpenDisplay(None)
        if not display:
            raise OSError('no X display')

        root = xlib.XDefaultRootWindow(display)
        info = xss.XScreenSaverAllocInfo()
        if not info:
            raise OSError('XScreenSaverAllocInfo failed')

        _x11 = (xlib, xss, display, root, info)
    except Exception:
        _x11_unavailable = True


def get_idle_seconds() -> float:
    """Return seconds since the last keyboard or mouse input.

    Returns
    -------
    float
        Idle duration in seconds.  Returns 0.0 on failure (no X display,
        Wayland session, missing XScreenSaver extension).
    """
    _init_x11()
    if _x11 is None:
        return 0.0

    _xlib, xss, display, root, info = _x11
    try:
        if not xss.XScreenSaverQueryInfo(display, root, info):
            return 0.0
        return info.contents.idle / 1000.0
    except Exception:
        return 0.0


# systemd-logind D-Bus state: the Gio system bus connection once open.
# _logind_unavailable latches any failure (no D-Bus, no logind, no
# session) so the probe never reruns.
_logind_bus = None
_logind_unavailable = False


def _get_logind_bus():
    """Return the Gio system bus connection, or ``None`` when unavailable."""
    global _logind_bus, _logind_unavailable
    if _logind_bus is not None or _logind_unavailable:
        return _logind_bus

    try:
        from gi.repository import Gio
        _logind_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        if _logind_bus is None:
            _logind_unavailable = True
    except Exception:
        _logind_unavailable = True

    return _logind_bus


def is_workstation_locked() -> bool:
    """Return True if the current login session is locked.

    Reads the ``LockedHint`` property of the caller's logind session
    (``/org/freedesktop/login1/session/auto``).  Best effort: without
    logind or a session the answer is ``False``.

    Returns
    -------
    bool
        True if the session appears to be locked.
    """
    global _logind_unavailable

    bus = _get_logind_bus()
    if bus is None or _logind_unavailable:
        return False

    try:
        from gi.repository import GLib
        result = bus.call_sync(
            'org.freedesktop.login1',
            '/org/freedesktop/login1/session/auto',
            'org.freedesktop.DBus.Properties',
            'Get',
            GLib.Variant('(ss)', ('org.freedesktop.login1.Session', 'LockedHint')),
            GLib.VariantType('(v)'),
            0,  # Gio.DBusCallFlags.NONE
            1000,  # ms timeout - lock state is polled, never worth blocking on
            None,
        )
        return bool(result[0])
    except Exception:
        # No session for this process, logind absent, or a D-Bus error:
        # latch the failure so the round trip is not repeated every poll.
        _logind_unavailable = True
        return False
