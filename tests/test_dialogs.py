"""
Dialogs Tests
==============

Unit tests for the GTK dialog helpers and their graceful degradation
without a display.  GTK itself is always mocked - the suite must not
open real dialogs.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import MagicMock, patch

import usage_monitor_for_claude.dialogs as dialogs


def _fake_gtk(response: object = None) -> MagicMock:
    """Return a mock Gtk module whose MessageDialog.run() returns *response*."""
    gtk = MagicMock()
    gtk.ResponseType.YES = -8
    gtk.ResponseType.NO = -9
    if response is not None:
        gtk.MessageDialog.return_value.run.return_value = response
    return gtk


class TestShowError(unittest.TestCase):
    """Tests for show_error()."""

    def test_without_gtk_prints_to_stderr(self):
        """Without GTK the error goes to stderr instead of being lost."""
        stderr = io.StringIO()
        with patch.object(dialogs, '_get_gtk', return_value=None), redirect_stderr(stderr):
            dialogs.show_error('My Title', 'the details')
        self.assertIn('My Title', stderr.getvalue())
        self.assertIn('the details', stderr.getvalue())

    def test_with_gtk_shows_dialog(self):
        """With GTK a MessageDialog is created, run, and destroyed."""
        gtk = _fake_gtk()
        stderr = io.StringIO()
        with patch.object(dialogs, '_get_gtk', return_value=gtk), redirect_stderr(stderr):
            dialogs.show_error('My Title', 'the details')

        gtk.MessageDialog.assert_called_once()
        self.assertEqual(gtk.MessageDialog.call_args.kwargs['text'], 'the details')
        dialog = gtk.MessageDialog.return_value
        dialog.set_title.assert_called_once_with('My Title')
        dialog.run.assert_called_once()
        dialog.destroy.assert_called_once()
        self.assertEqual(stderr.getvalue(), '')

    def test_gtk_failure_falls_back_to_stderr(self):
        """A GTK error mid-dialog still surfaces the message on stderr."""
        gtk = _fake_gtk()
        gtk.MessageDialog.side_effect = RuntimeError('display gone')
        stderr = io.StringIO()
        with patch.object(dialogs, '_get_gtk', return_value=gtk), redirect_stderr(stderr):
            dialogs.show_error('My Title', 'the details')
        self.assertIn('the details', stderr.getvalue())


class TestAskYesNo(unittest.TestCase):
    """Tests for ask_yes_no()."""

    def test_without_gtk_returns_false(self):
        """Without a display the safe answer to a destructive question is No."""
        with patch.object(dialogs, '_get_gtk', return_value=None):
            self.assertFalse(dialogs.ask_yes_no('Title', 'Replace?'))

    def test_yes_returns_true(self):
        gtk = _fake_gtk()
        gtk.MessageDialog.return_value.run.return_value = gtk.ResponseType.YES
        with patch.object(dialogs, '_get_gtk', return_value=gtk):
            self.assertTrue(dialogs.ask_yes_no('Title', 'Replace?'))
        gtk.MessageDialog.return_value.destroy.assert_called_once()

    def test_no_returns_false(self):
        gtk = _fake_gtk()
        gtk.MessageDialog.return_value.run.return_value = gtk.ResponseType.NO
        with patch.object(dialogs, '_get_gtk', return_value=gtk):
            self.assertFalse(dialogs.ask_yes_no('Title', 'Replace?'))

    def test_close_returns_false(self):
        """Closing the dialog (DELETE_EVENT) counts as No."""
        gtk = _fake_gtk()
        gtk.MessageDialog.return_value.run.return_value = -4  # Gtk.ResponseType.DELETE_EVENT
        with patch.object(dialogs, '_get_gtk', return_value=gtk):
            self.assertFalse(dialogs.ask_yes_no('Title', 'Replace?'))

    def test_gtk_failure_returns_false(self):
        gtk = _fake_gtk()
        gtk.MessageDialog.side_effect = RuntimeError('display gone')
        with patch.object(dialogs, '_get_gtk', return_value=gtk):
            self.assertFalse(dialogs.ask_yes_no('Title', 'Replace?'))

    def test_message_and_title_passed_through(self):
        gtk = _fake_gtk()
        gtk.MessageDialog.return_value.run.return_value = gtk.ResponseType.NO
        with patch.object(dialogs, '_get_gtk', return_value=gtk):
            dialogs.ask_yes_no('The Title v1.2.3', 'Replace the running instance?')
        self.assertEqual(gtk.MessageDialog.call_args.kwargs['text'], 'Replace the running instance?')
        gtk.MessageDialog.return_value.set_title.assert_called_once_with('The Title v1.2.3')


class TestGetGtkProbeCache(unittest.TestCase):
    """Tests for the one-time GTK probe cache."""

    def test_cached_result_is_returned(self):
        """A cached probe result is returned without re-probing."""
        sentinel = MagicMock()
        with patch.object(dialogs, '_gtk_probe', (sentinel,)):
            self.assertIs(dialogs._get_gtk(), sentinel)

    def test_cached_unavailable_is_returned(self):
        """A cached negative probe stays negative without re-probing."""
        with patch.object(dialogs, '_gtk_probe', (None,)):
            self.assertIsNone(dialogs._get_gtk())

    def test_failed_probe_is_cached(self):
        """A failing GTK import is probed once, then served from the cache."""
        with patch.object(dialogs, '_gtk_probe', None), \
             patch.dict('sys.modules', {'gi': None}):
            # Importing a module whose sys.modules entry is None raises
            # ImportError, simulating a system without PyGObject.
            self.assertIsNone(dialogs._get_gtk())
            self.assertEqual(dialogs._gtk_probe, (None,))
            self.assertIsNone(dialogs._get_gtk())


if __name__ == '__main__':
    unittest.main()
