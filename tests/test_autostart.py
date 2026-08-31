"""
Autostart Tests
================

Unit tests for the XDG autostart desktop entry management.  The
autostart directory is a per-test temp dir via XDG_CONFIG_HOME - no
real autostart entries are touched.
"""
from __future__ import annotations

import os
import stat
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import usage_monitor_for_claude.autostart as autostart

MODULE = 'usage_monitor_for_claude.autostart'


class AutostartDirTestCase(unittest.TestCase):
    """Base: isolated XDG_CONFIG_HOME and default config dir."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._env = patch.dict('os.environ', {'XDG_CONFIG_HOME': self._tmp.name})
        self._env.start()
        os.environ.pop('CLAUDE_CONFIG_DIR', None)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    @property
    def autostart_dir(self) -> Path:
        return Path(self._tmp.name) / 'autostart'


class TestAutostartFilePath(AutostartDirTestCase):
    """Tests for the desktop file location."""

    def test_lives_in_xdg_autostart(self):
        self.assertEqual(
            autostart.autostart_file_path(),
            self.autostart_dir / 'usage-monitor-for-claude.desktop',
        )

    def test_defaults_to_home_config(self):
        with TemporaryDirectory() as home_tmp:
            with patch.dict('os.environ', {}, clear=False), \
                 patch.object(Path, 'home', return_value=Path(home_tmp)):
                os.environ.pop('XDG_CONFIG_HOME', None)
                expected = Path(home_tmp) / '.config' / 'autostart' / 'usage-monitor-for-claude.desktop'
                self.assertEqual(autostart.autostart_file_path(), expected)

    def test_carries_config_dir_suffix(self):
        with patch(f'{MODULE}.config_dir_suffix', return_value='_abc123'):
            self.assertTrue(autostart.autostart_file_path().name.endswith('_abc123.desktop'))


class TestIsAutostartEnabled(AutostartDirTestCase):
    """Tests for is_autostart_enabled()."""

    def test_disabled_when_no_file(self):
        self.assertFalse(autostart.is_autostart_enabled())

    def test_enabled_when_file_exists(self):
        autostart.set_autostart(True)
        self.assertTrue(autostart.is_autostart_enabled())

    def test_disabled_when_hidden(self):
        """Hidden=true (written by DE session settings) counts as disabled."""
        autostart.set_autostart(True)
        path = autostart.autostart_file_path()
        path.write_text(path.read_text(encoding='utf-8') + 'Hidden=true\n', encoding='utf-8')
        self.assertFalse(autostart.is_autostart_enabled())

    def test_hidden_case_insensitive(self):
        autostart.set_autostart(True)
        path = autostart.autostart_file_path()
        path.write_text(path.read_text(encoding='utf-8') + 'Hidden = True\n', encoding='utf-8')
        self.assertFalse(autostart.is_autostart_enabled())


class TestSetAutostart(AutostartDirTestCase):
    """Tests for set_autostart()."""

    def test_enable_writes_desktop_entry(self):
        autostart.set_autostart(True)
        content = autostart.autostart_file_path().read_text(encoding='utf-8')
        self.assertIn('[Desktop Entry]', content)
        self.assertIn('Type=Application', content)
        self.assertIn('Name=Usage Monitor for Claude', content)
        self.assertIn(f'Exec={autostart._autostart_command()}', content)

    def test_enable_creates_missing_autostart_dir(self):
        self.assertFalse(self.autostart_dir.exists())
        autostart.set_autostart(True)
        self.assertTrue(autostart.autostart_file_path().is_file())

    def test_disable_removes_file(self):
        autostart.set_autostart(True)
        autostart.set_autostart(False)
        self.assertFalse(autostart.autostart_file_path().exists())

    def test_disable_without_file_is_noop(self):
        autostart.set_autostart(False)  # must not raise
        self.assertFalse(autostart.autostart_file_path().exists())

    def test_custom_config_dir_stored_in_exec(self):
        """A non-default config dir instance stores --config-dir in the command."""
        with TemporaryDirectory() as config_tmp:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp}):
                autostart.set_autostart(True)
                path = autostart.autostart_file_path()
                self.assertNotEqual(path.name, 'usage-monitor-for-claude.desktop')
                content = path.read_text(encoding='utf-8')
        resolved = Path(config_tmp).resolve()
        self.assertIn(f'--config-dir="{resolved}"', content)

    def test_default_config_dir_has_no_config_flag(self):
        autostart.set_autostart(True)
        content = autostart.autostart_file_path().read_text(encoding='utf-8')
        self.assertNotIn('--config-dir', content)


class TestLauncher(AutostartDirTestCase):
    """Tests for the launcher command resolution."""

    def test_frozen_uses_executable(self):
        with patch.object(autostart.sys, 'frozen', True, create=True), \
             patch.object(autostart.sys, 'executable', '/opt/usage-monitor/app'):
            self.assertEqual(autostart._launcher(), '"/opt/usage-monitor/app"')

    def test_installed_console_script_used_directly(self):
        """An executable launcher script (installed entry point) is stored as-is."""
        with TemporaryDirectory() as bin_tmp:
            script = Path(bin_tmp) / 'usage-monitor-for-claude'
            script.write_text('#!/usr/bin/python3\n', encoding='utf-8')
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
            with patch.object(autostart.sys, 'argv', [str(script)]):
                self.assertEqual(autostart._launcher(), f'"{script.resolve()}"')

    def test_installed_layout_uses_wrapper_script(self):
        """A <prefix>/lib/<app> install resolves the <prefix>/bin launcher.

        The .deb and tarball installs run the app as ``python3 -m`` with
        PYTHONPATH pointing at the application root, so argv[0] is
        ``__main__.py``.  Storing ``python3 -m`` would drop PYTHONPATH and
        the autostart entry would fail to import the package.
        """
        with TemporaryDirectory() as prefix:
            app_root = Path(prefix) / 'lib' / autostart.AUTOSTART_BASE_NAME
            package = app_root / 'usage_monitor_for_claude'
            package.mkdir(parents=True)
            wrapper = Path(prefix) / 'bin' / autostart.AUTOSTART_BASE_NAME
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text('#!/bin/sh\n', encoding='utf-8')
            wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

            with patch.object(autostart, '__file__', str(package / 'autostart.py')), \
                 patch.object(autostart.sys, 'argv', [str(package / '__main__.py')]):
                self.assertEqual(autostart._launcher(), f'"{wrapper.resolve()}"')

    def test_installed_layout_without_wrapper_uses_python_dash_m(self):
        """Without a sibling bin/ launcher the python -m fallback stands."""
        with TemporaryDirectory() as prefix:
            package = Path(prefix) / 'lib' / autostart.AUTOSTART_BASE_NAME / 'usage_monitor_for_claude'
            package.mkdir(parents=True)

            with patch.object(autostart, '__file__', str(package / 'autostart.py')), \
                 patch.object(autostart.sys, 'argv', [str(package / '__main__.py')]):
                self.assertEqual(autostart._launcher(), f'"{sys.executable}" -m usage_monitor_for_claude')

    def test_source_run_uses_python_dash_m(self):
        """Running from source (argv[0] is a .py file) stores python -m."""
        with patch.object(autostart.sys, 'argv', ['/somewhere/usage_monitor_for_claude/__main__.py']):
            self.assertEqual(autostart._launcher(), f'"{sys.executable}" -m usage_monitor_for_claude')

    def test_empty_argv_uses_python_dash_m(self):
        with patch.object(autostart.sys, 'argv', []):
            self.assertEqual(autostart._launcher(), f'"{sys.executable}" -m usage_monitor_for_claude')


class TestSyncAutostartPath(AutostartDirTestCase):
    """Tests for sync_autostart_path() self-healing."""

    def test_noop_when_not_registered(self):
        autostart.sync_autostart_path()
        self.assertFalse(autostart.autostart_file_path().exists())

    def test_rewrites_when_exec_changed(self):
        """A moved launcher is healed by rewriting the desktop entry."""
        path = autostart.autostart_file_path()
        path.parent.mkdir(parents=True)
        path.write_text(
            '[Desktop Entry]\nType=Application\nName=Usage Monitor for Claude\n'
            'Exec="/old/location/app"\n',
            encoding='utf-8',
        )
        autostart.sync_autostart_path()
        content = path.read_text(encoding='utf-8')
        self.assertIn(f'Exec={autostart._autostart_command()}', content)
        self.assertNotIn('/old/location/app', content)

    def test_unchanged_exec_left_alone(self):
        autostart.set_autostart(True)
        path = autostart.autostart_file_path()
        before = path.read_text(encoding='utf-8')
        with patch(f'{MODULE}.set_autostart') as mock_set:
            autostart.sync_autostart_path()
        mock_set.assert_not_called()
        self.assertEqual(path.read_text(encoding='utf-8'), before)

    def test_hidden_entry_not_reenabled(self):
        """A user-disabled (Hidden=true) entry is never rewritten by sync."""
        path = autostart.autostart_file_path()
        path.parent.mkdir(parents=True)
        path.write_text(
            '[Desktop Entry]\nType=Application\nExec="/old/location/app"\nHidden=true\n',
            encoding='utf-8',
        )
        autostart.sync_autostart_path()
        content = path.read_text(encoding='utf-8')
        self.assertIn('Hidden=true', content)
        self.assertIn('/old/location/app', content)


if __name__ == '__main__':
    unittest.main()
