"""
State Tests
============

Unit tests for the persistent JSON state store (popup position).
"""
from __future__ import annotations

import json
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import usage_monitor_for_claude.state as state_mod
from usage_monitor_for_claude.instance_id import config_dir_suffix


class _StateEnv:
    """Context manager pinning XDG_CONFIG_HOME (and clearing CLAUDE_CONFIG_DIR)."""

    def __init__(self, config_home: Path, claude_config_dir: str | None = None):
        env = {'XDG_CONFIG_HOME': str(config_home)}
        if claude_config_dir is not None:
            env['CLAUDE_CONFIG_DIR'] = claude_config_dir
        self._patcher = patch.dict('os.environ', env)
        self._clear_claude = claude_config_dir is None

    def __enter__(self):
        self._patcher.start()
        if self._clear_claude:
            os.environ.pop('CLAUDE_CONFIG_DIR', None)
        return self

    def __exit__(self, *exc):
        self._patcher.stop()
        return False


class TestPopupPositionRoundTrip(unittest.TestCase):
    """Tests for save_popup_position / load_popup_position."""

    def test_round_trip(self):
        """A saved position is loaded back as an (x, y) tuple."""
        with TemporaryDirectory() as tmp, _StateEnv(Path(tmp)):
            state_mod.save_popup_position(120, 340)
            self.assertEqual(state_mod.load_popup_position(), (120, 340))

    def test_default_instance_file_has_no_suffix(self):
        """The default ~/.claude instance writes plain state.json."""
        with TemporaryDirectory() as tmp, _StateEnv(Path(tmp)):
            state_mod.save_popup_position(1, 2)
            self.assertTrue((Path(tmp) / 'usage-monitor-for-claude' / 'state.json').is_file())

    def test_custom_config_dir_uses_suffixed_file(self):
        """A non-default CLAUDE_CONFIG_DIR gets its own state<suffix>.json."""
        with TemporaryDirectory() as tmp, TemporaryDirectory() as claude_dir:
            with _StateEnv(Path(tmp), claude_config_dir=claude_dir):
                suffix = config_dir_suffix()
                self.assertNotEqual(suffix, '')
                state_mod.save_popup_position(7, 8)
                expected = Path(tmp) / 'usage-monitor-for-claude' / f'state{suffix}.json'
                self.assertTrue(expected.is_file())
                self.assertEqual(state_mod.load_popup_position(), (7, 8))
            # The default-instance file was never created.
            self.assertFalse((Path(tmp) / 'usage-monitor-for-claude' / 'state.json').exists())

    def test_save_creates_dir_with_private_permissions(self):
        """The state directory is created on demand with 0o700 permissions."""
        with TemporaryDirectory() as tmp, _StateEnv(Path(tmp)):
            state_mod.save_popup_position(1, 2)
            mode = stat.S_IMODE((Path(tmp) / 'usage-monitor-for-claude').stat().st_mode)
            self.assertEqual(mode, 0o700)

    def test_save_leaves_no_temp_file_behind(self):
        """The atomic write replaces the temp file instead of leaving it."""
        with TemporaryDirectory() as tmp, _StateEnv(Path(tmp)):
            state_mod.save_popup_position(1, 2)
            names = os.listdir(Path(tmp) / 'usage-monitor-for-claude')
            self.assertEqual(names, ['state.json'])

    def test_save_preserves_other_state_keys(self):
        """Saving the position merges into existing state instead of replacing it."""
        with TemporaryDirectory() as tmp, _StateEnv(Path(tmp)):
            path = Path(tmp) / 'usage-monitor-for-claude' / 'state.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'future_key': 42}), encoding='utf-8')

            state_mod.save_popup_position(5, 6)

            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['future_key'], 42)
            self.assertEqual(data['popup_position'], [5, 6])

    def test_save_coerces_floats_to_ints(self):
        """Float coordinates (e.g. from Gdk) are stored as integers."""
        with TemporaryDirectory() as tmp, _StateEnv(Path(tmp)):
            state_mod.save_popup_position(10.7, 20.2)
            self.assertEqual(state_mod.load_popup_position(), (10, 20))


class TestPopupPositionFailures(unittest.TestCase):
    """All state failures are silent - state is a convenience, never a dialog."""

    def test_missing_file_returns_none(self):
        with TemporaryDirectory() as tmp, _StateEnv(Path(tmp)):
            self.assertIsNone(state_mod.load_popup_position())

    def test_corrupt_file_returns_none(self):
        """Unparseable JSON loads as no saved position, without raising."""
        with TemporaryDirectory() as tmp, _StateEnv(Path(tmp)):
            path = Path(tmp) / 'usage-monitor-for-claude' / 'state.json'
            path.parent.mkdir(parents=True)
            path.write_text('{not json', encoding='utf-8')
            self.assertIsNone(state_mod.load_popup_position())

    def test_non_dict_json_returns_none(self):
        with TemporaryDirectory() as tmp, _StateEnv(Path(tmp)):
            path = Path(tmp) / 'usage-monitor-for-claude' / 'state.json'
            path.parent.mkdir(parents=True)
            path.write_text('[1, 2]', encoding='utf-8')
            self.assertIsNone(state_mod.load_popup_position())

    def test_malformed_position_returns_none(self):
        """A position that is not exactly two integers loads as None."""
        for bad in ([1], [1, 2, 3], ['a', 'b'], [True, False], 'x', None, {'x': 1}):
            with self.subTest(position=bad), TemporaryDirectory() as tmp, _StateEnv(Path(tmp)):
                path = Path(tmp) / 'usage-monitor-for-claude' / 'state.json'
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({'popup_position': bad}), encoding='utf-8')
                self.assertIsNone(state_mod.load_popup_position())

    def test_unwritable_dir_is_silent_noop(self):
        """A read-only config home makes save a silent no-op."""
        with TemporaryDirectory() as tmp, _StateEnv(Path(tmp)):
            os.chmod(tmp, 0o500)
            try:
                state_mod.save_popup_position(1, 2)  # must not raise
                self.assertIsNone(state_mod.load_popup_position())
            finally:
                os.chmod(tmp, 0o700)

    def test_unwritable_state_dir_is_silent_noop(self):
        """A read-only state directory makes the atomic write a silent no-op."""
        with TemporaryDirectory() as tmp, _StateEnv(Path(tmp)):
            state_dir = Path(tmp) / 'usage-monitor-for-claude'
            state_dir.mkdir(parents=True)
            os.chmod(state_dir, 0o500)
            try:
                state_mod.save_popup_position(1, 2)  # must not raise
                self.assertIsNone(state_mod.load_popup_position())
                self.assertEqual(os.listdir(state_dir), [])
            finally:
                os.chmod(state_dir, 0o700)


if __name__ == '__main__':
    unittest.main()
