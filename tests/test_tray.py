"""
Tray Adapter Tests
===================

Unit tests for the AppIndicator tray adapter: icon-refresh filename
strategy, menu mapping, notification routing, and lifecycle.  All gi
bindings are replaced with fakes so the suite runs headless.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

import usage_monitor_for_claude.tray as tray_mod
from usage_monitor_for_claude.tray import SEPARATOR, MenuItem, TrayIcon


# ---------------------------------------------------------------------------
# GTK fakes (just enough surface for the adapter)
# ---------------------------------------------------------------------------

class _FakeMenu:
    def __init__(self):
        self.children = []
        self.signals = {}
        self.shown = False

    def append(self, item):
        self.children.append(item)

    def connect(self, signal, handler):
        self.signals[signal] = handler

    def show_all(self):
        self.shown = True

    def get_children(self):
        return list(self.children)


class _FakeMenuItem:
    def __init__(self, label=''):
        self.label = label
        self.sensitive = True
        self.hidden = False
        self.no_show_all = False
        self._submenu = None
        self._handlers = {}
        self._next_handler_id = 0
        self._blocked = set()

    def connect(self, signal, handler):
        self._next_handler_id += 1
        self._handlers[self._next_handler_id] = (signal, handler)
        return self._next_handler_id

    def handler_block(self, handler_id):
        self._blocked.add(handler_id)

    def handler_unblock(self, handler_id):
        self._blocked.discard(handler_id)

    def set_sensitive(self, value):
        self.sensitive = bool(value)

    def set_no_show_all(self, value):
        self.no_show_all = bool(value)

    def hide(self):
        self.hidden = True

    def set_submenu(self, submenu):
        self._submenu = submenu

    def get_submenu(self):
        return self._submenu

    def emit_activate(self):
        for handler_id, (signal, handler) in list(self._handlers.items()):
            if signal == 'activate' and handler_id not in self._blocked:
                handler(self)


class _FakeCheckMenuItem(_FakeMenuItem):
    def __init__(self, label=''):
        super().__init__(label)
        self._active = False

    def set_active(self, value):
        value = bool(value)
        if value == self._active:
            return
        self._active = value
        # GTK emits 'activate' for programmatic toggles too - the adapter
        # must block the callback handler while syncing state.
        self.emit_activate()

    def get_active(self):
        return self._active


class _FakeSeparator:
    def __init__(self):
        self.label = None


def _fake_gtk():
    gtk = MagicMock()
    gtk.Menu = _FakeMenu
    gtk.MenuItem = _FakeMenuItem
    gtk.CheckMenuItem = _FakeCheckMenuItem
    gtk.SeparatorMenuItem = _FakeSeparator
    gtk.main_quit = MagicMock()
    return gtk


def _fake_indicator_module():
    module = MagicMock()
    module.Indicator.new.return_value = MagicMock()
    module.IndicatorStatus.ACTIVE = 'ACTIVE'
    module.IndicatorStatus.PASSIVE = 'PASSIVE'
    return module


def _fake_glib():
    glib = MagicMock()
    # Run marshalled calls immediately so effects are observable in tests.
    glib.idle_add.side_effect = lambda func, *args: func(*args)
    return glib


class _TrayTestCase(unittest.TestCase):
    """Base fixture patching the gi seams and the icon directory."""

    def setUp(self):
        self.gtk = _fake_gtk()
        self.indicator_mod = _fake_indicator_module()
        self.glib = _fake_glib()
        self.icon_dir = tempfile.mkdtemp(prefix='tray-test-')

        patchers = [
            patch.object(tray_mod, 'Gtk', self.gtk),
            patch.object(tray_mod, 'AyatanaAppIndicator3', self.indicator_mod),
            patch.object(tray_mod, 'GLib', self.glib),
            patch.object(TrayIcon, '_make_icon_dir', staticmethod(lambda name: self.icon_dir)),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    @property
    def indicator(self):
        return self.indicator_mod.Indicator.new.return_value

    @staticmethod
    def _image(color=(255, 0, 0, 255)):
        return Image.new('RGBA', (4, 4), color)


# ---------------------------------------------------------------------------
# Construction & lifecycle
# ---------------------------------------------------------------------------

class TestConstruction(_TrayTestCase):
    """Tests for indicator setup and lifecycle."""

    def test_creates_active_indicator_with_name(self):
        TrayIcon('usage_monitor', title='hello')

        name = self.indicator_mod.Indicator.new.call_args[0][0]
        self.assertEqual(name, 'usage_monitor')
        self.indicator.set_status.assert_called_with('ACTIVE')
        self.indicator.set_title.assert_called_with('hello')

    def test_initial_icon_written_and_applied(self):
        tray = TrayIcon('usage_monitor', icon=self._image())

        path = self.indicator.set_icon_full.call_args[0][0]
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(path.startswith(self.icon_dir))
        self.indicator.set_icon_theme_path.assert_called_with(self.icon_dir)
        self.assertIs(tray.icon is not None, True)

    def test_run_invokes_setup_with_adapter(self):
        tray = TrayIcon('usage_monitor')
        setup = MagicMock()

        tray.run(setup=setup)

        setup.assert_called_once_with(tray)

    def test_stop_hides_indicator_and_quits_main_loop(self):
        tray = TrayIcon('usage_monitor')

        tray.stop()

        self.indicator.set_status.assert_called_with('PASSIVE')
        self.gtk.main_quit.assert_called_once()

    def test_visible_setter_toggles_status(self):
        tray = TrayIcon('usage_monitor')

        tray.visible = False
        self.indicator.set_status.assert_called_with('PASSIVE')

        tray.visible = True
        self.indicator.set_status.assert_called_with('ACTIVE')

    def test_title_setter_updates_indicator(self):
        tray = TrayIcon('usage_monitor')

        tray.title = 'Usage: 42%'

        self.assertEqual(tray.title, 'Usage: 42%')
        self.indicator.set_title.assert_called_with('Usage: 42%')


# ---------------------------------------------------------------------------
# Icon refresh strategy
# ---------------------------------------------------------------------------

class TestIconRefresh(_TrayTestCase):
    """Tests for the fresh-filename icon update strategy.

    AppIndicator caches icons by path, so every update must land in a
    new monotonically-suffixed file for the panel to pick it up.
    """

    def test_each_update_uses_a_fresh_filename(self):
        tray = TrayIcon('usage_monitor')

        tray.icon = self._image((255, 0, 0, 255))
        first = self.indicator.set_icon_full.call_args[0][0]
        tray.icon = self._image((0, 255, 0, 255))
        second = self.indicator.set_icon_full.call_args[0][0]

        self.assertNotEqual(first, second)

    def test_filenames_are_monotonically_suffixed(self):
        tray = TrayIcon('usage_monitor')

        tray.icon = self._image()
        tray.icon = self._image()
        tray.icon = self._image()
        path = self.indicator.set_icon_full.call_args[0][0]

        self.assertTrue(path.endswith('icon-3.png'))

    def test_previous_icon_file_is_removed(self):
        tray = TrayIcon('usage_monitor')

        tray.icon = self._image()
        first = self.indicator.set_icon_full.call_args[0][0]
        tray.icon = self._image()
        second = self.indicator.set_icon_full.call_args[0][0]

        self.assertFalse(os.path.exists(first))
        self.assertTrue(os.path.isfile(second))

    def test_icon_update_marshalled_through_glib(self):
        tray = TrayIcon('usage_monitor')

        tray.icon = self._image()

        self.assertTrue(self.glib.idle_add.called)

    def test_none_icon_is_stored_without_applying(self):
        tray = TrayIcon('usage_monitor')

        tray.icon = None

        self.assertIsNone(tray.icon)
        self.indicator.set_icon_full.assert_not_called()


# ---------------------------------------------------------------------------
# Menu mapping
# ---------------------------------------------------------------------------

class TestMenuMapping(_TrayTestCase):
    """Tests for mapping the MenuItem structure onto GTK menu widgets."""

    def _menu_children(self):
        return self.indicator.set_menu.call_args[0][0].get_children()

    def test_labels_and_separators_mapped_in_order(self):
        TrayIcon('usage_monitor', menu=[
            MenuItem('Show', MagicMock(), default=True),
            SEPARATOR,
            MenuItem('Quit', MagicMock()),
        ])

        children = self._menu_children()
        self.assertEqual(len(children), 3)
        self.assertEqual(children[0].label, 'Show')
        self.assertIsInstance(children[1], _FakeSeparator)
        self.assertEqual(children[2].label, 'Quit')

    def test_activation_runs_callback_without_arguments(self):
        callback = MagicMock()
        TrayIcon('usage_monitor', menu=[MenuItem('Show', callback)])

        self._menu_children()[0].emit_activate()

        callback.assert_called_once_with()

    def test_default_item_becomes_secondary_activate_target(self):
        """Middle-click on the indicator triggers the default entry."""
        TrayIcon('usage_monitor', menu=[
            MenuItem('Show', MagicMock(), default=True),
            MenuItem('Quit', MagicMock()),
        ])

        target = self.indicator.set_secondary_activate_target.call_args[0][0]
        self.assertEqual(target.label, 'Show')

    def test_disabled_item_is_insensitive(self):
        TrayIcon('usage_monitor', menu=[MenuItem('Test', MagicMock(), enabled=False)])

        self.assertFalse(self._menu_children()[0].sensitive)

    def test_invisible_item_is_hidden(self):
        TrayIcon('usage_monitor', menu=[
            MenuItem('Hidden', MagicMock(), visible=False),
            MenuItem('Shown', MagicMock()),
        ])

        children = self._menu_children()
        self.assertTrue(children[0].hidden)
        self.assertFalse(children[1].hidden)

    def test_submenu_mapped_recursively(self):
        TrayIcon('usage_monitor', menu=[
            MenuItem('Tests', submenu=[
                MenuItem('Reset', MagicMock()),
                MenuItem('Threshold', MagicMock(), enabled=False),
            ]),
        ])

        submenu = self._menu_children()[0].get_submenu()
        labels = [child.label for child in submenu.get_children()]
        self.assertEqual(labels, ['Reset', 'Threshold'])
        self.assertFalse(submenu.get_children()[1].sensitive)

    def test_checked_item_reflects_probe_state(self):
        TrayIcon('usage_monitor', menu=[MenuItem('Autostart', MagicMock(), checked=lambda: True)])

        item = self._menu_children()[0]
        self.assertIsInstance(item, _FakeCheckMenuItem)
        self.assertTrue(item.get_active())

    def test_check_state_refreshed_on_menu_show_without_firing_callback(self):
        """Opening the menu re-reads the checked probe; the programmatic
        state sync must not invoke the item's activation callback."""
        state = {'enabled': False}
        callback = MagicMock()
        TrayIcon('usage_monitor', menu=[MenuItem('Autostart', callback, checked=lambda: state['enabled'])])

        menu = self.indicator.set_menu.call_args[0][0]
        item = menu.get_children()[0]
        self.assertFalse(item.get_active())

        state['enabled'] = True
        menu.signals['show'](menu)

        self.assertTrue(item.get_active())
        callback.assert_not_called()

    def test_user_toggle_still_fires_callback(self):
        callback = MagicMock()
        TrayIcon('usage_monitor', menu=[MenuItem('Autostart', callback, checked=lambda: False)])

        self._menu_children()[0].emit_activate()

        callback.assert_called_once_with()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class TestNotifyRouting(_TrayTestCase):
    """Tests for tray notification routing into the notifications module."""

    def test_notify_routes_with_title_first(self):
        """The adapter keeps pystray's (message, title) argument order and
        maps it onto notifications.notify(title, message)."""
        tray = TrayIcon('usage_monitor')

        with patch.object(tray_mod.notifications, 'notify') as mock_notify:
            tray.notify('quota reset', 'Usage Monitor')

        mock_notify.assert_called_once_with('Usage Monitor', 'quota reset')

    def test_notify_without_title(self):
        tray = TrayIcon('usage_monitor')

        with patch.object(tray_mod.notifications, 'notify') as mock_notify:
            tray.notify('message only')

        mock_notify.assert_called_once_with('', 'message only')


# ---------------------------------------------------------------------------
# Headless degradation
# ---------------------------------------------------------------------------

class TestHeadlessDegradation(unittest.TestCase):
    """Without the gi stack the adapter must stay import- and state-safe."""

    def setUp(self):
        patchers = [
            patch.object(tray_mod, 'Gtk', None),
            patch.object(tray_mod, 'AyatanaAppIndicator3', None),
            patch.object(tray_mod, 'GLib', None),
            patch.object(TrayIcon, '_make_icon_dir', staticmethod(lambda name: tempfile.mkdtemp(prefix='tray-test-'))),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_constructor_and_setters_do_not_crash(self):
        tray = TrayIcon('usage_monitor', icon=None, title='t', menu=[MenuItem('Show', MagicMock())])

        tray.title = 'new title'
        tray.icon = Image.new('RGBA', (4, 4))
        tray.visible = False
        tray.run(setup=None)
        tray.stop()

        self.assertEqual(tray.title, 'new title')

    def test_notify_still_routes(self):
        tray = TrayIcon('usage_monitor')

        with patch.object(tray_mod.notifications, 'notify') as mock_notify:
            tray.notify('msg', 'title')

        mock_notify.assert_called_once_with('title', 'msg')


if __name__ == '__main__':
    unittest.main()
