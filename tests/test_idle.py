"""
Idle Detection Tests
=====================

Unit tests for X11 idle time and logind lock detection.  The X11 and
D-Bus seams are mocked - the suite must not depend on a live display
or session bus.
"""
from __future__ import annotations

import ctypes
import unittest
from unittest.mock import MagicMock, patch

import usage_monitor_for_claude.idle as idle


def _fake_x11(idle_ms: int, query_ok: bool = True) -> tuple:
    """Build a fake initialized X11 state reporting *idle_ms* of idle time."""
    info = idle._XScreenSaverInfo()
    info.idle = idle_ms
    info_ptr = ctypes.pointer(info)
    xss = MagicMock()
    xss.XScreenSaverQueryInfo.return_value = 1 if query_ok else 0
    return (MagicMock(), xss, 1234, 5678, info_ptr)


class TestGetIdleSeconds(unittest.TestCase):
    """Tests for get_idle_seconds()."""

    def test_converts_milliseconds_to_seconds(self):
        with patch.object(idle, '_x11', _fake_x11(1500)):
            self.assertEqual(idle.get_idle_seconds(), 1.5)

    def test_zero_idle(self):
        with patch.object(idle, '_x11', _fake_x11(0)):
            self.assertEqual(idle.get_idle_seconds(), 0.0)

    def test_large_idle_values(self):
        """Hours of idle time survive the conversion without overflow."""
        with patch.object(idle, '_x11', _fake_x11(7_200_000)):
            self.assertEqual(idle.get_idle_seconds(), 7200.0)

    def test_query_failure_returns_zero(self):
        """A failed XScreenSaverQueryInfo reports 'not idle', not garbage."""
        with patch.object(idle, '_x11', _fake_x11(9999, query_ok=False)):
            self.assertEqual(idle.get_idle_seconds(), 0.0)

    def test_unavailable_returns_zero(self):
        """Without X11 (Wayland, headless) idle time degrades to 0."""
        with patch.object(idle, '_x11', None), \
             patch.object(idle, '_x11_unavailable', True):
            self.assertEqual(idle.get_idle_seconds(), 0.0)

    def test_query_call_uses_display_and_root(self):
        """The query runs against the opened display's root window."""
        fake = _fake_x11(1000)
        with patch.object(idle, '_x11', fake):
            idle.get_idle_seconds()
        fake[1].XScreenSaverQueryInfo.assert_called_once_with(1234, 5678, fake[4])

    def test_failed_probe_is_cached(self):
        """A missing library is probed once, later calls skip the dlopen."""
        with patch.object(idle, '_x11', None), \
             patch.object(idle, '_x11_unavailable', False), \
             patch.object(idle.ctypes, 'CDLL', side_effect=OSError('libXss not found')) as mock_cdll:
            self.assertEqual(idle.get_idle_seconds(), 0.0)
            self.assertEqual(idle.get_idle_seconds(), 0.0)
            self.assertEqual(mock_cdll.call_count, 1)

    def test_no_display_is_cached(self):
        """XOpenDisplay returning NULL latches the unavailable state."""
        xlib = MagicMock()
        xlib.XOpenDisplay.return_value = None
        with patch.object(idle, '_x11', None), \
             patch.object(idle, '_x11_unavailable', False), \
             patch.object(idle.ctypes, 'CDLL', return_value=xlib):
            self.assertEqual(idle.get_idle_seconds(), 0.0)
            self.assertTrue(idle._x11_unavailable)


class TestIsWorkstationLocked(unittest.TestCase):
    """Tests for is_workstation_locked()."""

    def test_unavailable_returns_false(self):
        """Without logind the session counts as unlocked."""
        with patch.object(idle, '_logind_bus', None), \
             patch.object(idle, '_logind_unavailable', True):
            self.assertFalse(idle.is_workstation_locked())

    def test_locked_hint_true(self):
        bus = MagicMock()
        bus.call_sync.return_value = (True,)
        with patch.object(idle, '_logind_bus', bus), \
             patch.object(idle, '_logind_unavailable', False):
            self.assertTrue(idle.is_workstation_locked())

    def test_locked_hint_false(self):
        bus = MagicMock()
        bus.call_sync.return_value = (False,)
        with patch.object(idle, '_logind_bus', bus), \
             patch.object(idle, '_logind_unavailable', False):
            self.assertFalse(idle.is_workstation_locked())

    def test_queries_logind_session_locked_hint(self):
        """The D-Bus call targets the caller's logind session's LockedHint."""
        bus = MagicMock()
        bus.call_sync.return_value = (False,)
        with patch.object(idle, '_logind_bus', bus), \
             patch.object(idle, '_logind_unavailable', False):
            idle.is_workstation_locked()

        args = bus.call_sync.call_args[0]
        self.assertEqual(args[0], 'org.freedesktop.login1')
        self.assertEqual(args[1], '/org/freedesktop/login1/session/auto')
        self.assertEqual(args[2], 'org.freedesktop.DBus.Properties')
        self.assertEqual(args[3], 'Get')

    def test_dbus_error_latches_unavailable(self):
        """A failing D-Bus call answers False and is never retried."""
        bus = MagicMock()
        bus.call_sync.side_effect = RuntimeError('no such session')
        with patch.object(idle, '_logind_bus', bus), \
             patch.object(idle, '_logind_unavailable', False):
            self.assertFalse(idle.is_workstation_locked())
            self.assertFalse(idle.is_workstation_locked())
            self.assertEqual(bus.call_sync.call_count, 1)

    def test_bus_probe_failure_cached(self):
        """A failing system-bus connection is probed once and latched."""
        with patch.object(idle, '_logind_bus', None), \
             patch.object(idle, '_logind_unavailable', False), \
             patch('gi.repository.Gio.bus_get_sync', side_effect=RuntimeError('no bus')) as mock_bus:
            self.assertFalse(idle.is_workstation_locked())
            self.assertFalse(idle.is_workstation_locked())
            self.assertEqual(mock_bus.call_count, 1)
            self.assertTrue(idle._logind_unavailable)


if __name__ == '__main__':
    unittest.main()
