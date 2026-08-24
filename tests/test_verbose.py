"""
Verbose Diagnostics Tests
==========================

Unit tests for the --verbose diagnostics: distro detection, home
redaction, credentials status, and the full startup dump.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import usage_monitor_for_claude.verbose as verbose


class TestSetupConsole(unittest.TestCase):
    """Tests for setup_console()."""

    def test_returns_true(self):
        """On Linux stdout is already attached - the call is a successful no-op."""
        self.assertTrue(verbose.setup_console())


class TestDistro(unittest.TestCase):
    """Tests for _distro() /etc/os-release parsing."""

    def _distro_from(self, content: str | None) -> str:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'os-release'
            if content is not None:
                path.write_text(content, encoding='utf-8')
            with patch.object(verbose, '_OS_RELEASE_PATH', path):
                return verbose._distro()

    def test_parses_pretty_name(self):
        content = 'NAME="Test Linux"\nPRETTY_NAME="Test Linux 1.0 (Testing)"\nID=test\n'
        self.assertEqual(self._distro_from(content), 'Test Linux 1.0 (Testing)')

    def test_unquoted_pretty_name(self):
        self.assertEqual(self._distro_from('PRETTY_NAME=Plain\n'), 'Plain')

    def test_missing_file_returns_unknown(self):
        self.assertEqual(self._distro_from(None), 'unknown')

    def test_missing_pretty_name_returns_unknown(self):
        self.assertEqual(self._distro_from('NAME="Test"\nID=test\n'), 'unknown')


class TestRedactHome(unittest.TestCase):
    """Tests for _redact_home()."""

    def _redact(self, path_str: str, home: str = '/home/test') -> str:
        with patch.object(Path, 'home', return_value=Path(home)):
            return verbose._redact_home(path_str)

    def test_home_itself(self):
        self.assertEqual(self._redact('/home/test'), '~')

    def test_path_under_home(self):
        self.assertEqual(self._redact('/home/test/.claude/settings.json'), '~/.claude/settings.json')

    def test_path_outside_home_untouched(self):
        self.assertEqual(self._redact('/opt/claude'), '/opt/claude')

    def test_sibling_prefix_not_redacted(self):
        """A sibling dir merely starting with the home path stays intact."""
        self.assertEqual(self._redact('/home/testuser/file'), '/home/testuser/file')


class TestCredentialsStatus(unittest.TestCase):
    """Tests for _credentials_status() (must never read the file content)."""

    def test_found(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / '.credentials.json').write_text('{}', encoding='utf-8')
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': tmp}):
                status = verbose._credentials_status()
        self.assertTrue(status.startswith('found'))

    def test_not_found(self):
        with TemporaryDirectory() as tmp:
            with patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': tmp}):
                status = verbose._credentials_status()
        self.assertTrue(status.startswith('NOT FOUND'))


class TestCliDiscovery(unittest.TestCase):
    """Tests for the CLI discovery row."""

    def test_reports_discovered_path(self):
        status = verbose._cli_discovery()
        self.assertIn('claude', status)
        self.assertTrue(status.startswith(('found', 'NOT FOUND')))


class TestStartupDiagnostics(unittest.TestCase):
    """Tests for print_startup_diagnostics()."""

    def test_prints_all_sections(self):
        """The dump runs to completion and contains every section."""
        out = io.StringIO()
        with redirect_stdout(out):
            verbose.print_startup_diagnostics()
        text = out.getvalue()
        self.assertIn('Verbose Mode', text)
        for section in ('System', 'Python', 'Locale', 'Runtimes', 'Dependencies', 'Credentials', 'Claude CLI'):
            self.assertIn(section, text)
        for row in ('Distribution', 'Kernel', 'Session type', 'Desktop', 'CLAUDE_CONFIG_DIR'):
            self.assertIn(row, text)

    def test_does_not_leak_home_path(self):
        """Paths under the home directory are redacted to ~ in the dump."""
        out = io.StringIO()
        with redirect_stdout(out):
            verbose.print_startup_diagnostics()
        self.assertNotIn(str(Path.home() / '.claude'), out.getvalue())


class TestRuntimeDiagnostics(unittest.TestCase):
    """Tests for print_runtime_diagnostics()."""

    def test_runs_without_crashing(self):
        out = io.StringIO()
        with redirect_stdout(out):
            verbose.print_runtime_diagnostics()
        self.assertIn('Runtime (post-init)', out.getvalue())


if __name__ == '__main__':
    unittest.main()
