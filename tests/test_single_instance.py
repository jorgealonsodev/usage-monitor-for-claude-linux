"""
Single-Instance Tests
======================

Unit tests for the single-instance guard: lock file round-trip,
ensure_single_instance control flow, and release_instance_lock.

The lock files are real (in a per-test temp dir used as
XDG_RUNTIME_DIR); only dialogs and process termination are mocked.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import usage_monitor_for_claude.single_instance as si
from usage_monitor_for_claude import __version__
from usage_monitor_for_claude.i18n import T

MODULE = 'usage_monitor_for_claude.single_instance'


class LockDirTestCase(unittest.TestCase):
    """Base: isolated lock directory and suffix, clean module state."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._env = patch.dict('os.environ', {'XDG_RUNTIME_DIR': self._tmp.name})
        self._env.start()
        self._suffix = patch(f'{MODULE}.config_dir_suffix', return_value='_test')
        self._suffix.start()
        si._lock_file = None
        self._external_holder = None

    def tearDown(self):
        si.release_instance_lock()
        self._release_external_holder()
        self._suffix.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _hold_lock_externally(self, pid: int = 12345, version: str | None = '9.9.9') -> None:
        """Simulate another running instance by flocking the lock file.

        flock conflicts apply between open file descriptions, so a second
        open of the same file conflicts even within one process.
        """
        path = si._lock_path()
        f = open(path, 'w', encoding='utf-8')
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        record: dict = {'pid': pid}
        if version is not None:
            record['version'] = version
        json.dump(record, f)
        f.flush()
        self._external_holder = f

    def _release_external_holder(self) -> None:
        if self._external_holder is not None:
            try:
                fcntl.flock(self._external_holder.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self._external_holder.close()
            self._external_holder = None


class TestLockPath(LockDirTestCase):
    """Tests for the lock file location."""

    def test_uses_xdg_runtime_dir(self):
        self.assertEqual(si._lock_path(), Path(self._tmp.name) / 'usage-monitor-for-claude_test.lock')

    def test_falls_back_to_tmp(self):
        with patch.dict('os.environ', {}, clear=False):
            os.environ.pop('XDG_RUNTIME_DIR', None)
            self.assertEqual(si._lock_path(), Path('/tmp') / 'usage-monitor-for-claude_test.lock')

    def test_carries_config_dir_suffix(self):
        with patch(f'{MODULE}.config_dir_suffix', return_value='_abc123'):
            self.assertTrue(si._lock_path().name.endswith('_abc123.lock'))


class TestAcquireAndHolderInfo(LockDirTestCase):
    """Tests for the lock round-trip and holder record."""

    def test_acquire_succeeds_when_free(self):
        self.assertTrue(si._try_acquire())
        self.assertIsNotNone(si._lock_file)

    def test_acquire_writes_pid_and_version(self):
        si._try_acquire()
        pid, version = si._read_holder_info()
        self.assertEqual(pid, os.getpid())
        self.assertEqual(version, __version__)

    def test_acquire_fails_when_held(self):
        self._hold_lock_externally()
        self.assertFalse(si._try_acquire())
        self.assertIsNone(si._lock_file)

    def test_read_holder_info_missing_file(self):
        self.assertEqual(si._read_holder_info(), (None, None))

    def test_read_holder_info_corrupt_file(self):
        si._lock_path().write_text('not json', encoding='utf-8')
        self.assertEqual(si._read_holder_info(), (None, None))

    def test_read_holder_info_invalid_pid(self):
        si._lock_path().write_text(json.dumps({'pid': 'x', 'version': '1.0.0'}), encoding='utf-8')
        pid, version = si._read_holder_info()
        self.assertIsNone(pid)
        self.assertEqual(version, '1.0.0')

    def test_release_frees_the_lock(self):
        self.assertTrue(si._try_acquire())
        si.release_instance_lock()
        self.assertIsNone(si._lock_file)
        # A fresh open file description can now take the lock.
        self._hold_lock_externally()  # would raise BlockingIOError if still held


class TestEnsureSingleInstance(LockDirTestCase):
    """Tests for ensure_single_instance() control flow."""

    def test_first_instance_proceeds_without_dialog(self):
        with patch(f'{MODULE}.dialogs') as mock_dialogs:
            self.assertTrue(si.ensure_single_instance())
        mock_dialogs.ask_yes_no.assert_not_called()
        mock_dialogs.show_error.assert_not_called()

    def test_conflict_user_declines(self):
        """When the user answers No, this instance exits and the holder stays."""
        self._hold_lock_externally(version='9.9.9')
        with patch(f'{MODULE}.dialogs') as mock_dialogs:
            mock_dialogs.ask_yes_no.return_value = False
            self.assertFalse(si.ensure_single_instance())
        mock_dialogs.ask_yes_no.assert_called_once()

    def test_conflict_dialog_title_includes_version(self):
        """The dialog title carries the running instance's version."""
        self._hold_lock_externally(version='9.9.9')
        with patch(f'{MODULE}.dialogs') as mock_dialogs:
            mock_dialogs.ask_yes_no.return_value = False
            si.ensure_single_instance()
        title, message = mock_dialogs.ask_yes_no.call_args[0]
        self.assertEqual(title, T['popup_title'] + ' v9.9.9')
        self.assertEqual(message, T['already_running'].format(running_version='9.9.9'))

    def test_conflict_unknown_version_uses_placeholder(self):
        """A holder without a version record gets '?' in the message."""
        self._hold_lock_externally(version=None)
        with patch(f'{MODULE}.dialogs') as mock_dialogs:
            mock_dialogs.ask_yes_no.return_value = False
            si.ensure_single_instance()
        title, message = mock_dialogs.ask_yes_no.call_args[0]
        self.assertEqual(title, T['popup_title'])
        self.assertEqual(message, T['already_running'].format(running_version='?'))

    def test_conflict_user_accepts_and_holder_dies(self):
        """After Yes, the holder is terminated and the lock is taken over."""
        self._hold_lock_externally(pid=12345)
        with patch(f'{MODULE}.dialogs') as mock_dialogs, \
             patch(f'{MODULE}._terminate_pid', side_effect=lambda pid: self._release_external_holder()) as mock_term:
            mock_dialogs.ask_yes_no.return_value = True
            self.assertTrue(si.ensure_single_instance())
        mock_term.assert_called_once_with(12345)
        # The new instance stored its own holder record.
        pid, version = si._read_holder_info()
        self.assertEqual(pid, os.getpid())
        self.assertEqual(version, __version__)

    def test_conflict_holder_survives_reports_failure(self):
        """If the holder never releases the lock, the replacement fails closed."""
        self._hold_lock_externally(pid=12345)
        with patch(f'{MODULE}.dialogs') as mock_dialogs, \
             patch(f'{MODULE}._terminate_pid'), \
             patch.object(si, '_REPLACE_TIMEOUT', 0.2):
            mock_dialogs.ask_yes_no.return_value = True
            self.assertFalse(si.ensure_single_instance())
        mock_dialogs.show_error.assert_called_once()
        self.assertEqual(mock_dialogs.show_error.call_args[0][1], T['replace_failed'])

    def test_holder_pid_reread_before_terminate(self):
        """A holder record that changed after the dialog is not terminated.

        The dialog can stay open for a long time; PIDs recycle, so the
        snapshotted PID is only trusted when a re-read confirms it.
        """
        self._hold_lock_externally(pid=12345)
        with patch(f'{MODULE}.dialogs') as mock_dialogs, \
             patch(f'{MODULE}._read_holder_info', side_effect=[(12345, '9.9.9'), (67890, '9.9.9')]), \
             patch(f'{MODULE}._terminate_pid') as mock_term, \
             patch.object(si, '_REPLACE_TIMEOUT', 0.2):
            mock_dialogs.ask_yes_no.return_value = True
            si.ensure_single_instance()
        mock_term.assert_not_called()

    def test_unwritable_lock_dir_fails_closed(self):
        """An unopenable lock file shows an error and refuses to run unguarded."""
        with patch.dict('os.environ', {'XDG_RUNTIME_DIR': str(Path(self._tmp.name) / 'missing' / 'nested')}), \
             patch(f'{MODULE}.dialogs') as mock_dialogs:
            self.assertFalse(si.ensure_single_instance())
        mock_dialogs.show_error.assert_called_once()
        mock_dialogs.ask_yes_no.assert_not_called()


class TestTerminatePid(unittest.TestCase):
    """Tests for _terminate_pid() against a real child process."""

    def test_terminates_child_process(self):
        child = subprocess.Popen(['sleep', '30'])
        # Reap the child as soon as it dies: an unreaped zombie still
        # answers os.kill(pid, 0), which would stall the liveness loop.
        reaper = threading.Thread(target=child.wait, daemon=True)
        reaper.start()
        try:
            si._terminate_pid(child.pid)
            reaper.join(timeout=5)
            # By the time _terminate_pid returns, the child must be dead.
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_nonexistent_pid_is_ignored(self):
        """Signaling a dead PID must not raise."""
        child = subprocess.Popen(['true'])
        child.wait()
        si._terminate_pid(child.pid)  # PID already reaped


if __name__ == '__main__':
    unittest.main()
