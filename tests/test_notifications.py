"""
Notifications Tests
====================

Unit tests for the libnotify wrapper and its fallback chain.  The gi
bindings and notify-send are always mocked - the suite must not show
real notifications.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import MagicMock, patch

import usage_monitor_for_claude.notifications as notifications


def _gi_modules(notify_mod: MagicMock) -> dict:
    """Build a sys.modules patch dict exposing *notify_mod* as gi.repository.Notify."""
    gi = MagicMock()
    repository = MagicMock()
    repository.Notify = notify_mod
    gi.repository = repository
    return {'gi': gi, 'gi.repository': repository}


class TestInit(unittest.TestCase):
    """Tests for init()."""

    def tearDown(self):
        notifications._notify_mod = None
        notifications._app_name = notifications._DEFAULT_APP_NAME

    def test_init_success(self):
        """A successful libnotify init stores the module and app name."""
        notify_mod = MagicMock()
        notify_mod.is_initted.return_value = False
        notify_mod.init.return_value = True
        with patch.dict('sys.modules', _gi_modules(notify_mod)):
            self.assertTrue(notifications.init('My App'))
        self.assertIs(notifications._notify_mod, notify_mod)
        notify_mod.init.assert_called_once_with('My App')

    def test_init_already_initted(self):
        """A second init() does not re-initialize libnotify."""
        notify_mod = MagicMock()
        notify_mod.is_initted.return_value = True
        with patch.dict('sys.modules', _gi_modules(notify_mod)):
            self.assertTrue(notifications.init('My App'))
        notify_mod.init.assert_not_called()

    def test_init_failure_returns_false(self):
        """libnotify refusing to init leaves the fallback chain active."""
        notify_mod = MagicMock()
        notify_mod.is_initted.return_value = False
        notify_mod.init.return_value = False
        with patch.dict('sys.modules', _gi_modules(notify_mod)):
            self.assertFalse(notifications.init('My App'))
        self.assertIsNone(notifications._notify_mod)

    def test_init_without_gi_returns_false(self):
        """A missing gi module degrades instead of raising."""
        with patch.dict('sys.modules', {'gi': None}):
            self.assertFalse(notifications.init('My App'))
        self.assertIsNone(notifications._notify_mod)


class TestNotify(unittest.TestCase):
    """Tests for notify() and its fallback chain."""

    def tearDown(self):
        notifications._notify_mod = None
        notifications._app_name = notifications._DEFAULT_APP_NAME

    def test_notify_via_libnotify(self):
        """With libnotify initialized, the notification is shown through it."""
        notify_mod = MagicMock()
        with patch.object(notifications, '_notify_mod', notify_mod), \
             patch.object(notifications, 'subprocess') as mock_subprocess:
            notifications.notify('Title', 'Body')

        args = notify_mod.Notification.new.call_args[0]
        self.assertEqual(args[0], 'Title')
        self.assertEqual(args[1], 'Body')
        notify_mod.Notification.new.return_value.show.assert_called_once()
        mock_subprocess.run.assert_not_called()

    def test_notify_uses_packaged_icon(self):
        """The packaged notification logo is passed as the icon."""
        notify_mod = MagicMock()
        with patch.object(notifications, '_notify_mod', notify_mod):
            notifications.notify('Title', 'Body')

        icon = notify_mod.Notification.new.call_args[0][2]
        self.assertIsNotNone(icon)
        self.assertTrue(icon.endswith('notification_logo.png'))

    def test_notify_falls_back_to_notify_send(self):
        """Without libnotify, notify-send is invoked with app name, title, and body."""
        with patch.object(notifications, '_notify_mod', None), \
             patch.object(notifications.subprocess, 'run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            notifications.notify('Title', 'Body')

        command = mock_run.call_args[0][0]
        self.assertEqual(command[0], 'notify-send')
        self.assertIn('Title', command)
        self.assertIn('Body', command)

    def test_libnotify_error_falls_back_to_notify_send(self):
        """A libnotify failure mid-show falls through to notify-send."""
        notify_mod = MagicMock()
        notify_mod.Notification.new.side_effect = RuntimeError('daemon gone')
        with patch.object(notifications, '_notify_mod', notify_mod), \
             patch.object(notifications.subprocess, 'run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            notifications.notify('Title', 'Body')

        mock_run.assert_called_once()

    def test_notify_send_missing_falls_back_to_stderr(self):
        """When even notify-send fails, the notification lands on stderr."""
        stderr = io.StringIO()
        with patch.object(notifications, '_notify_mod', None), \
             patch.object(notifications.subprocess, 'run', side_effect=FileNotFoundError('notify-send')), \
             redirect_stderr(stderr):
            notifications.notify('Title', 'Body')

        self.assertIn('Title', stderr.getvalue())
        self.assertIn('Body', stderr.getvalue())

    def test_notify_send_nonzero_exit_falls_back_to_stderr(self):
        """A failing notify-send (no daemon) still surfaces the message."""
        stderr = io.StringIO()
        with patch.object(notifications, '_notify_mod', None), \
             patch.object(notifications.subprocess, 'run') as mock_run, \
             redirect_stderr(stderr):
            mock_run.return_value = MagicMock(returncode=1)
            notifications.notify('Title', 'Body')

        self.assertIn('Title', stderr.getvalue())

    def test_notify_send_uses_app_name(self):
        """The app name from init() is forwarded to notify-send."""
        notify_mod = MagicMock()
        notify_mod.is_initted.return_value = False
        notify_mod.init.return_value = False
        with patch.dict('sys.modules', _gi_modules(notify_mod)):
            notifications.init('Custom Name')

        with patch.object(notifications.subprocess, 'run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            notifications.notify('Title', 'Body')

        command = mock_run.call_args[0][0]
        self.assertIn('Custom Name', command)


if __name__ == '__main__':
    unittest.main()
