"""
Instance Identity Tests
========================

Unit tests for --config-dir parsing and per-instance name suffixes.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from usage_monitor_for_claude.instance_id import config_dir_suffix, effective_config_dir, is_default_config_dir, parse_config_dir


class TestParseConfigDir(unittest.TestCase):
    """Tests for parse_config_dir()."""

    def test_equals_form(self):
        self.assertEqual(parse_config_dir(['app', '--config-dir=/opt/claude']), '/opt/claude')

    def test_space_form(self):
        self.assertEqual(parse_config_dir(['app', '--config-dir', '/opt/claude']), '/opt/claude')

    def test_absent_flag_returns_none(self):
        self.assertIsNone(parse_config_dir(['app', '--verbose']))

    def test_flag_without_value_returns_none(self):
        self.assertIsNone(parse_config_dir(['app', '--config-dir']))

    def test_empty_value_returns_none(self):
        self.assertIsNone(parse_config_dir(['app', '--config-dir=']))

    def test_strips_surrounding_double_quotes(self):
        self.assertEqual(parse_config_dir(['app', '--config-dir="/opt/claude"']), '/opt/claude')

    def test_strips_surrounding_single_quotes(self):
        self.assertEqual(parse_config_dir(['app', "--config-dir='/opt/claude'"]), '/opt/claude')

    def test_strips_trailing_slash(self):
        self.assertEqual(parse_config_dir(['app', '--config-dir=/opt/claude/']), '/opt/claude')

    def test_whitespace_only_value_returns_none(self):
        self.assertIsNone(parse_config_dir(['app', '--config-dir=   ']))

    def test_expands_environment_variables(self):
        """$VAR syntax works even from launchers that do not expand it."""
        with patch.dict('os.environ', {'CLAUDE_TEST_BASE': '/srv/claude'}):
            result = parse_config_dir(['app', '--config-dir=$CLAUDE_TEST_BASE/accounts/work'])
        self.assertEqual(result, '/srv/claude/accounts/work')

    def test_expands_tilde(self):
        with patch.dict('os.environ', {'HOME': '/home/test'}):
            result = parse_config_dir(['app', '--config-dir=~/.claude-second'])
        self.assertEqual(result, '/home/test/.claude-second')

    def test_last_occurrence_wins(self):
        argv = ['app', '--config-dir=/opt/first', '--config-dir=/opt/second']
        self.assertEqual(parse_config_dir(argv), '/opt/second')

    def test_filesystem_root_keeps_separator(self):
        """The filesystem root must stay a root, not collapse to an empty value."""
        self.assertEqual(parse_config_dir(['app', '--config-dir=/']), '/')


class TestConfigDirSuffix(unittest.TestCase):
    """Tests for config_dir_suffix() and is_default_config_dir()."""

    def test_default_when_env_unset(self):
        with patch.dict('os.environ', {}, clear=False):
            os.environ.pop('CLAUDE_CONFIG_DIR', None)
            self.assertTrue(is_default_config_dir())
            self.assertEqual(config_dir_suffix(), '')

    def test_default_when_env_points_to_home_claude(self):
        with TemporaryDirectory() as home_tmp:
            claude_dir = Path(home_tmp) / '.claude'
            claude_dir.mkdir()
            with patch.object(Path, 'home', return_value=Path(home_tmp)), \
                 patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': str(claude_dir)}):
                self.assertTrue(is_default_config_dir())
                self.assertEqual(config_dir_suffix(), '')

    def test_default_when_env_is_symlink_to_home_claude(self):
        """A symlink to ~/.claude is still the default dir (realpath comparison)."""
        with TemporaryDirectory() as home_tmp:
            claude_dir = Path(home_tmp) / '.claude'
            claude_dir.mkdir()
            link = Path(home_tmp) / 'claude-link'
            link.symlink_to(claude_dir)
            with patch.object(Path, 'home', return_value=Path(home_tmp)), \
                 patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': str(link)}):
                self.assertTrue(is_default_config_dir())
                self.assertEqual(config_dir_suffix(), '')

    def test_custom_dir_produces_suffix(self):
        with TemporaryDirectory() as config_tmp:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp}):
                self.assertFalse(is_default_config_dir())
                suffix = config_dir_suffix()
        self.assertTrue(suffix.startswith('_'))
        self.assertEqual(len(suffix), 13)

    def test_suffix_stable_across_trailing_slash(self):
        with TemporaryDirectory() as config_tmp:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp}):
                suffix_plain = config_dir_suffix()
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp + '/'}):
                suffix_variant = config_dir_suffix()
        self.assertEqual(suffix_plain, suffix_variant)

    def test_suffix_stable_across_symlink(self):
        """A symlink and its target hash to the same suffix (resolved path)."""
        with TemporaryDirectory() as base_tmp:
            target = Path(base_tmp) / 'real-config'
            target.mkdir()
            link = Path(base_tmp) / 'link-config'
            link.symlink_to(target)
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': str(target)}):
                suffix_target = config_dir_suffix()
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': str(link)}):
                suffix_link = config_dir_suffix()
        self.assertEqual(suffix_target, suffix_link)

    def test_different_dirs_produce_different_suffixes(self):
        with TemporaryDirectory() as dir_a, TemporaryDirectory() as dir_b:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': dir_a}):
                suffix_a = config_dir_suffix()
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': dir_b}):
                suffix_b = config_dir_suffix()
        self.assertNotEqual(suffix_a, suffix_b)

    def test_effective_config_dir_resolves_env_value(self):
        with TemporaryDirectory() as config_tmp:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': config_tmp}):
                self.assertEqual(effective_config_dir(), Path(config_tmp).resolve())


if __name__ == '__main__':
    unittest.main()
