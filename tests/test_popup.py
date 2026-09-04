"""
Popup Tests
=============

Unit tests for popup data helpers (_usage_entries, _snapshot_to_dict,
_init_config) and the GTK window layer (pin/drag state, report_height,
dismissal, update loop, positioning) with all gi seams mocked.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from usage_monitor_for_claude.cache import CacheSnapshot
from usage_monitor_for_claude.popup import (
    UsagePopup, _CSS_BASE_FONT_PX, _KEY_ESCAPE, _MARGIN, _PopupApi, _init_config, _read_css_scale,
    _read_font_zoom, _snapshot_to_dict, _usage_entries,
)


def _snap(
    usage=None, profile=None, last_success_time=None,
    refreshing=False, last_error=None, version=1,
) -> CacheSnapshot:
    """Build a CacheSnapshot with convenient defaults."""
    return CacheSnapshot(
        usage=usage or {},
        profile=profile,
        last_success_time=last_success_time,
        refreshing=refreshing,
        last_error=last_error,
        version=version,
    )


# ---------------------------------------------------------------------------
# _usage_entries
# ---------------------------------------------------------------------------

class TestUsageEntries(unittest.TestCase):
    """Tests for _usage_entries - extracts labelled tuples from usage dict."""

    def test_returns_entries_for_active_fields(self):
        """Returns entries only for non-null fields with utilization."""
        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T00:00:00Z'},
            'seven_day': {'utilization': 10, 'resets_at': '2026-01-07T00:00:00Z'},
            'seven_day_sonnet': None,
        }
        entries = _usage_entries(usage)
        self.assertEqual(len(entries), 2)

    def test_labels_use_popup_label(self):
        """Each entry's label is generated via popup_label."""
        from usage_monitor_for_claude.formatting import popup_label

        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T00:00:00Z'},
            'seven_day': {'utilization': 10, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        entries = _usage_entries(usage)
        labels = [e[0] for e in entries]
        self.assertEqual(labels, [popup_label('five_hour'), popup_label('seven_day')])

    def test_periods_derived_from_field_name(self):
        """Period is derived from the field name via field_period."""
        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T00:00:00Z'},
            'seven_day': {'utilization': 10, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        entries = _usage_entries(usage)
        periods = [e[2] for e in entries]
        self.assertEqual(periods, [5 * 3600, 7 * 24 * 3600])

    def test_data_extraction(self):
        """Entry data is pulled from the correct usage dict keys."""
        five_hour = {'utilization': 42, 'resets_at': '2026-01-01T00:00:00Z'}
        seven_day = {'utilization': 10, 'resets_at': '2026-01-07T00:00:00Z'}
        usage = {'five_hour': five_hour, 'seven_day': seven_day}

        entries = _usage_entries(usage)
        self.assertEqual(len(entries), 2)
        self.assertIs(entries[0][1], five_hour)
        self.assertIs(entries[1][1], seven_day)

    def test_entry_includes_field_key(self):
        """Each entry's 4th element is the raw API field name."""
        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T00:00:00Z'},
            'seven_day_opus': {'utilization': 10, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        entries = _usage_entries(usage)
        keys = [e[3] for e in entries]
        self.assertEqual(keys, ['five_hour', 'seven_day_opus'])

    def test_empty_usage_returns_empty(self):
        """Empty usage dict returns no entries."""
        self.assertEqual(_usage_entries({}), [])

    def test_all_null_fields_returns_empty(self):
        """All-null fields return no entries."""
        usage = {'five_hour': None, 'seven_day': None, 'seven_day_sonnet': None}
        self.assertEqual(_usage_entries(usage), [])

    def test_null_utilization_skipped(self):
        """Fields with utilization None are skipped."""
        usage = {
            'five_hour': {'utilization': None, 'resets_at': '2026-01-01T05:00:00Z'},
            'seven_day': {'utilization': 20, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        entries = _usage_entries(usage)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1]['utilization'], 20)

    @patch('usage_monitor_for_claude.popup.POPUP_FIELDS', ['fve_hour', 'seven_day'])
    def test_misspelled_popup_field_skipped(self):
        """Misspelled popup_fields entry is skipped, valid one shown."""
        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T05:00:00Z'},
            'seven_day': {'utilization': 20, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        entries = _usage_entries(usage)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1]['utilization'], 20)

    @patch('usage_monitor_for_claude.popup.POPUP_FIELDS', ['seven_day_sonnet'])
    def test_popup_field_pointing_to_null_skipped(self):
        """popup_fields entry pointing to a null field produces no entries."""
        usage = {'seven_day_sonnet': None, 'five_hour': {'utilization': 42, 'resets_at': ''}}
        entries = _usage_entries(usage)
        self.assertEqual(entries, [])

    def test_non_dict_values_in_usage_ignored(self):
        """Non-dict values (like error strings) in usage are ignored."""
        usage = {
            'error': 'server down',
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T05:00:00Z'},
        }
        entries = _usage_entries(usage)
        self.assertEqual(len(entries), 1)

    def test_extra_usage_not_shown_as_bar(self):
        """extra_usage is excluded from dynamic bars (different structure)."""
        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T05:00:00Z'},
            'extra_usage': {'is_enabled': True, 'monthly_limit': 1000, 'used_credits': 500, 'utilization': 50},
        }
        entries = _usage_entries(usage)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1]['utilization'], 42)


# ---------------------------------------------------------------------------
# _snapshot_to_dict
# ---------------------------------------------------------------------------

class TestSnapshotToDict(unittest.TestCase):
    """Tests for _snapshot_to_dict - converts CacheSnapshot to popup JSON."""

    # -- profile --

    def test_no_profile(self):
        """Profile is None when snapshot has no profile."""
        result = _snapshot_to_dict(_snap(), installations=[])
        self.assertIsNone(result['profile'])

    def test_profile_extraction(self):
        """Email and plan are extracted from nested account/organization dicts."""
        profile = {
            'account': {'email': 'test@example.com'},
            'organization': {'organization_type': 'pro_team'},
        }
        result = _snapshot_to_dict(_snap(profile=profile), installations=[])
        self.assertEqual(result['profile']['email'], 'test@example.com')
        self.assertEqual(result['profile']['plan'], 'Pro Team')

    def test_empty_profile_hidden(self):
        """Empty profile dict from API is treated as absent (no broken UI)."""
        result = _snapshot_to_dict(_snap(profile={}), installations=[])
        self.assertIsNone(result['profile'])

    def test_profile_missing_nested_keys(self):
        """Present but incomplete profile defaults missing fields to empty strings."""
        result = _snapshot_to_dict(_snap(profile={'account': {}}), installations=[])
        self.assertEqual(result['profile']['email'], '')
        self.assertEqual(result['profile']['plan'], '')

    def test_profile_with_null_account_and_organization(self):
        """A profile carrying account/organization as null must not crash the popup."""
        result = _snapshot_to_dict(_snap(profile={'account': None, 'organization': None}), installations=[])
        self.assertEqual(result['profile']['email'], '')
        self.assertEqual(result['profile']['plan'], '')

    # -- usage bars --

    def test_no_usage_data(self):
        """Empty usage dict produces empty usage list."""
        result = _snapshot_to_dict(_snap(), installations=[])
        self.assertEqual(result['usage'], [])

    def test_skips_entries_without_utilization(self):
        """Entries with None utilization are omitted."""
        usage = {'five_hour': {'utilization': None}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(result['usage'], [])

    def test_skips_missing_entries(self):
        """Missing usage keys produce no bar entries."""
        usage = {'five_hour': None}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(result['usage'], [])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='5h 0m')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_usage_bar_fields(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Each usage bar dict has all required fields with correct types."""
        usage = {'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        self.assertEqual(len(result['usage']), 1)
        bar = result['usage'][0]
        self.assertEqual(bar['pct_text'], '42%')
        self.assertAlmostEqual(bar['fill_pct'], 0.42)
        self.assertFalse(bar['warn'])
        self.assertIsNone(bar['marker_rel'])
        self.assertEqual(bar['reset_text'], '5h 0m')
        self.assertEqual(bar['dividers'], [])

    def test_field_with_null_resets_at(self):
        """An inactive scoped limit (resets_at None) renders a 0% bar with no reset text."""
        usage = {'seven_day_fable': {'utilization': 0.0, 'resets_at': None}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        self.assertEqual(len(result['usage']), 1)
        bar = result['usage'][0]
        self.assertEqual(bar['key'], 'seven_day_fable')
        self.assertEqual(bar['pct_text'], '0%')
        self.assertEqual(bar['fill_pct'], 0.0)
        self.assertEqual(bar['reset_text'], '')
        self.assertEqual(bar['dividers'], [])
        self.assertIsNone(bar['marker_rel'])
        self.assertFalse(bar['warn'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=30.0)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='3h 30m')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[0.5])
    def test_warn_when_usage_ahead_of_time(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Bar is marked warn when utilization exceeds elapsed percentage."""
        usage = {'five_hour': {'utilization': 60, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        bar = result['usage'][0]
        self.assertTrue(bar['warn'])
        self.assertAlmostEqual(bar['marker_rel'], 0.3)

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=80.0)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='1h 0m')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_no_warn_when_usage_behind_time(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Bar is not warn when utilization is below elapsed percentage."""
        usage = {'five_hour': {'utilization': 40, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        bar = result['usage'][0]
        self.assertFalse(bar['warn'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=50.0)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='2h 30m')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_no_warn_when_equal(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Exactly equal usage and elapsed is not a warning (strictly greater)."""
        usage = {'five_hour': {'utilization': 50, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertFalse(result['usage'][0]['warn'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_warn_at_100_without_time_period(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Bar at 100% is warn even when no time period (time_pct is None)."""
        usage = {'five_hour': {'utilization': 100, 'resets_at': ''}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertTrue(result['usage'][0]['warn'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=100.0)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_warn_at_100_when_time_also_100(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Bar at 100% is warn even when elapsed time is also 100% (strict > would miss this)."""
        usage = {'five_hour': {'utilization': 100, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertTrue(result['usage'][0]['warn'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_fill_pct_clamped_to_0_1(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Fill percentage is clamped between 0.0 and 1.0, and over-quota is always warn."""
        usage = {'five_hour': {'utilization': 150, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(result['usage'][0]['fill_pct'], 1.0)
        self.assertTrue(result['usage'][0]['warn'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_zero_utilization(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Zero utilization produces 0% text and 0.0 fill."""
        usage = {'five_hour': {'utilization': 0, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        # utilization 0 is falsy, so `or 0` kicks in - entry is still shown
        bar = result['usage'][0]
        self.assertEqual(bar['pct_text'], '0%')
        self.assertAlmostEqual(bar['fill_pct'], 0.0)

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_multiple_usage_entries(self, _mock_dividers, _mock_time_until, _mock_elapsed):
        """Multiple usage types each produce a bar entry."""
        usage = {
            'five_hour': {'utilization': 10, 'resets_at': '2026-01-01T05:00:00Z'},
            'seven_day': {'utilization': 20, 'resets_at': '2026-01-07T00:00:00Z'},
            'seven_day_sonnet': {'utilization': 30, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(len(result['usage']), 3)
        pcts = [b['pct_text'] for b in result['usage']]
        self.assertEqual(pcts, ['10%', '20%', '30%'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_usage_bar_includes_field_key(self, _mock_div, _mock_tu, _mock_ep):
        """Each usage bar dict carries its API field name for compact hiding."""
        usage = {
            'five_hour': {'utilization': 10, 'resets_at': '2026-01-01T05:00:00Z'},
            'seven_day_opus': {'utilization': 30, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        keys = [bar['key'] for bar in result['usage']]
        self.assertEqual(keys, ['five_hour', 'seven_day_opus'])

    @patch('usage_monitor_for_claude.popup.POPUP_FIELDS', ['typo_field', 'seven_day'])
    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_misspelled_popup_field_skipped_in_dict(self, _mock_div, _mock_tu, _mock_ep):
        """Misspelled popup_fields entry produces no bar, valid one shown."""
        usage = {
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T05:00:00Z'},
            'seven_day': {'utilization': 20, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(len(result['usage']), 1)
        self.assertEqual(result['usage'][0]['pct_text'], '20%')

    def test_all_null_fields_no_bars(self):
        """All-null quota fields produce no usage bars."""
        usage = {'five_hour': None, 'seven_day': None, 'seven_day_sonnet': None}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(result['usage'], [])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_non_dict_values_in_response_ignored(self, _mock_div, _mock_tu, _mock_ep):
        """Non-dict values in the API response are not shown as bars."""
        usage = {
            'error': 'temporary',
            'rate_limited': True,
            'five_hour': {'utilization': 42, 'resets_at': '2026-01-01T05:00:00Z'},
        }
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(len(result['usage']), 1)
        self.assertEqual(result['usage'][0]['pct_text'], '42%')

    # -- extra usage --

    def test_no_extra_usage(self):
        """Extra is None when no extra_usage key in usage dict."""
        result = _snapshot_to_dict(_snap(), installations=[])
        self.assertIsNone(result['extra'])

    def test_extra_usage_disabled(self):
        """Extra is None when extra usage is not enabled."""
        usage = {'extra_usage': {'is_enabled': False, 'monthly_limit': 1000, 'used_credits': 500}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertIsNone(result['extra'])

    def test_extra_usage_enabled_no_used_credits_key(self):
        """Extra is None when used_credits is absent, even if enabled."""
        usage = {'extra_usage': {'is_enabled': True, 'monthly_limit': 1000}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertIsNone(result['extra'])

    @patch('usage_monitor_for_claude.popup.format_credits', side_effect=lambda c, *_: f'${c / 100:.2f}')
    def test_extra_usage_zero_limit_shows_no_cap_variant(self, _mock_credits):
        """A zero monthly limit shows the no-cap spent text instead of hiding the section."""
        usage = {'extra_usage': {'is_enabled': True, 'monthly_limit': 0, 'used_credits': 0}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        extra = result['extra']
        self.assertIsNotNone(extra)
        self.assertFalse(extra['has_limit'])
        self.assertEqual(extra['pct_text'], '')
        self.assertIn('$0.00', extra['spent_text'])

    @patch('usage_monitor_for_claude.popup.format_credits', side_effect=lambda c, *_: f'${c / 100:.2f}')
    def test_extra_usage_null_limit_shows_no_cap_variant(self, _mock_credits):
        """A null monthly_limit (uncapped pay-as-you-go credits) shows what has been spent."""
        usage = {'extra_usage': {'is_enabled': True, 'monthly_limit': None, 'used_credits': 2981}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        extra = result['extra']
        self.assertIsNotNone(extra)
        self.assertFalse(extra['has_limit'])
        self.assertIn('$29.81', extra['spent_text'])

    @patch('usage_monitor_for_claude.popup.format_credits', side_effect=lambda c, *_: f'${c / 100:.2f}')
    def test_extra_usage_calculation(self, _mock_credits):
        """Extra usage computes percentage and formatted text correctly."""
        usage = {'extra_usage': {'is_enabled': True, 'monthly_limit': 10000, 'used_credits': 2500}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        extra = result['extra']
        self.assertIsNotNone(extra)
        self.assertTrue(extra['has_limit'])
        self.assertEqual(extra['pct_text'], '25%')
        self.assertAlmostEqual(extra['fill_pct'], 0.25)
        self.assertIn('$25.00', extra['spent_text'])
        self.assertIn('$100.00', extra['spent_text'])

    @patch('usage_monitor_for_claude.popup.format_credits', side_effect=lambda c, *_: f'${c / 100:.2f}')
    def test_extra_usage_fill_clamped(self, _mock_credits):
        """Extra usage fill is clamped to 1.0 when over limit."""
        usage = {'extra_usage': {'is_enabled': True, 'monthly_limit': 1000, 'used_credits': 2000}}
        result = _snapshot_to_dict(_snap(usage=usage), installations=[])
        self.assertEqual(result['extra']['fill_pct'], 1.0)

    # -- installations --

    def test_installations_passthrough(self):
        """Pre-computed installations list is passed through unchanged."""
        installs = [{'name': 'VS Code', 'version': '1.0.0'}]
        result = _snapshot_to_dict(_snap(), installations=installs)
        self.assertEqual(result['installations'], installs)

    @patch('usage_monitor_for_claude.popup.find_installations')
    def test_installations_auto_detected(self, mock_find):
        """When installations is None, find_installations() is called."""
        inst = MagicMock()
        inst.name = 'Cursor'
        inst.version = '2.0.0'
        mock_find.return_value = [inst]

        result = _snapshot_to_dict(_snap(), installations=None)
        mock_find.assert_called_once()
        self.assertEqual(result['installations'], [{'name': 'Cursor', 'version': '2.0.0'}])

    # -- status --

    def test_status_error_when_no_usage(self):
        """Shows error text when there's no usage data but there's an error."""
        result = _snapshot_to_dict(_snap(usage={}, last_error='Connection failed'), installations=[])
        self.assertEqual(result['status']['text'], 'Connection failed')
        self.assertTrue(result['status']['is_error'])

    def test_status_error_truncated(self):
        """Error messages are truncated to 120 characters."""
        long_error = 'x' * 200
        result = _snapshot_to_dict(_snap(usage={}, last_error=long_error), installations=[])
        self.assertEqual(len(result['status']['text']), 120)

    def test_status_refreshing_when_no_usage_no_error(self):
        """Shows refreshing status when no usage data and no error."""
        from usage_monitor_for_claude.i18n import T

        result = _snapshot_to_dict(_snap(usage={}, last_error=None), installations=[])
        self.assertEqual(result['status']['text'], T['status_refreshing'])
        self.assertFalse(result['status']['is_error'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_status_live_mode_keys(self, _mock_div, _mock_tu, _mock_ep):
        """Live mode status contains all required keys for the JS timer."""
        usage = {'five_hour': {'utilization': 50, 'resets_at': '2026-01-01T05:00:00Z'}}
        result = _snapshot_to_dict(
            _snap(usage=usage, last_success_time=1000.0, refreshing=True, last_error='Server down'),
            installations=[], next_poll_time=1180.0,
        )
        self.assertEqual(set(result['status'].keys()), {'last_success_time', 'next_poll_time', 'refreshing', 'error'})

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_status_error_truncated_in_live_mode(self, _mock_div, _mock_tu, _mock_ep):
        """Error messages are truncated to 120 characters in live mode."""
        usage = {'five_hour': {'utilization': 50, 'resets_at': '2026-01-01T05:00:00Z'}}
        long_error = 'x' * 200
        result = _snapshot_to_dict(
            _snap(usage=usage, last_error=long_error),
            installations=[],
        )
        self.assertEqual(len(result['status']['error']), 120)

    # -- top-level dict structure --

    def test_all_top_level_keys_present(self):
        """Result always has profile, usage, extra, installations, status."""
        result = _snapshot_to_dict(_snap(), installations=[])
        self.assertEqual(set(result.keys()), {'profile', 'usage', 'extra', 'installations', 'status'})


# ---------------------------------------------------------------------------
# _init_config
# ---------------------------------------------------------------------------

class TestBarColorLevels(unittest.TestCase):
    """Tests for bar_color_levels tinting of the popup usage bars."""

    _LEVELS = [(0.0, '#1ea03c'), (70.0, '#e6aa28'), (90.0, '#c81e1e')]

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_each_bar_colored_by_its_own_pct(self, _mock_div, _mock_tu, _mock_ep):
        """Every bar's fill_color follows the level for that bar's own pct."""
        usage = {
            'five_hour': {'utilization': 75, 'resets_at': '2026-01-01T05:00:00Z'},
            'seven_day': {'utilization': 20, 'resets_at': '2026-01-07T00:00:00Z'},
            'seven_day_sonnet': {'utilization': 95, 'resets_at': '2026-01-07T00:00:00Z'},
        }
        with patch('usage_monitor_for_claude.popup.BAR_COLOR_LEVELS', self._LEVELS):
            result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        colors = [bar['fill_color'] for bar in result['usage']]
        self.assertEqual(colors, ['#e6aa28', '#1ea03c', '#c81e1e'])

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=30.0)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_levels_supersede_warn_fill(self, _mock_div, _mock_tu, _mock_ep):
        """A bar ahead of the elapsed time gets its level color, not the warn red."""
        usage = {'five_hour': {'utilization': 60, 'resets_at': '2026-01-01T05:00:00Z'}}
        with patch('usage_monitor_for_claude.popup.BAR_COLOR_LEVELS', self._LEVELS):
            result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        bar = result['usage'][0]
        self.assertTrue(bar['warn'])  # data flag still reported
        self.assertEqual(bar['fill_color'], '#1ea03c')  # level for pct=60 wins

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_below_lowest_threshold_uses_base_bar_fg(self, _mock_div, _mock_tu, _mock_ep):
        """A pct below the lowest threshold falls back to the base bar_fg."""
        from usage_monitor_for_claude.settings import BAR_FG

        usage = {'five_hour': {'utilization': 20, 'resets_at': '2026-01-01T05:00:00Z'}}
        with patch('usage_monitor_for_claude.popup.BAR_COLOR_LEVELS', [(50.0, '#c81e1e')]):
            result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        self.assertEqual(result['usage'][0]['fill_color'], BAR_FG)

    @patch('usage_monitor_for_claude.popup.elapsed_pct', return_value=None)
    @patch('usage_monitor_for_claude.popup.time_until', return_value='')
    @patch('usage_monitor_for_claude.popup.divider_positions', return_value=[])
    def test_unset_levels_leave_fill_color_none(self, _mock_div, _mock_tu, _mock_ep):
        """Without configured levels fill_color is None - JS keeps bar_fg/warn."""
        usage = {'five_hour': {'utilization': 95, 'resets_at': '2026-01-01T05:00:00Z'}}
        with patch('usage_monitor_for_claude.popup.BAR_COLOR_LEVELS', None):
            result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        self.assertIsNone(result['usage'][0]['fill_color'])

    @patch('usage_monitor_for_claude.popup.format_credits', side_effect=lambda c, *_: f'${c / 100:.2f}')
    def test_extra_usage_bar_colored_by_its_pct(self, _mock_credits):
        """The extra-usage bar follows the same levels for its own pct."""
        usage = {'extra_usage': {'is_enabled': True, 'monthly_limit': 10000, 'used_credits': 9500}}
        with patch('usage_monitor_for_claude.popup.BAR_COLOR_LEVELS', self._LEVELS):
            result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        self.assertEqual(result['extra']['fill_color'], '#c81e1e')

    @patch('usage_monitor_for_claude.popup.format_credits', side_effect=lambda c, *_: f'${c / 100:.2f}')
    def test_uncapped_extra_usage_has_no_fill_color(self, _mock_credits):
        """The no-limit extra variant shows no bar, so no level color either."""
        usage = {'extra_usage': {'is_enabled': True, 'monthly_limit': None, 'used_credits': 2981}}
        with patch('usage_monitor_for_claude.popup.BAR_COLOR_LEVELS', self._LEVELS):
            result = _snapshot_to_dict(_snap(usage=usage), installations=[])

        self.assertIsNone(result['extra']['fill_color'])


class TestInitConfig(unittest.TestCase):
    """Tests for _init_config - builds the JS init() config object."""

    def test_top_level_keys(self):
        """Config has colors, t (translations), app_version, compact_hide, and data."""
        config = _init_config(_snap())
        self.assertEqual(set(config.keys()), {'colors', 't', 'app_version', 'compact_hide', 'data'})

    @patch('usage_monitor_for_claude.popup.COMPACT_HIDE', ['account', 'seven_day_opus'])
    def test_compact_hide_from_settings(self):
        """compact_hide is taken from the COMPACT_HIDE setting."""
        config = _init_config(_snap())
        self.assertEqual(config['compact_hide'], ['account', 'seven_day_opus'])

    def test_colors_from_settings(self):
        """Color values come from settings module constants."""
        from usage_monitor_for_claude.settings import BAR_BG, BAR_DIVIDER, BAR_FG, BAR_FG_WARN, BAR_MARKER, BG, FG, FG_DIM, FG_HEADING, FG_LINK

        config = _init_config(_snap())
        colors = config['colors']
        self.assertEqual(colors['bg'], BG)
        self.assertEqual(colors['fg'], FG)
        self.assertEqual(colors['fg_dim'], FG_DIM)
        self.assertEqual(colors['fg_heading'], FG_HEADING)
        self.assertEqual(colors['fg_link'], FG_LINK)
        self.assertEqual(colors['bar_bg'], BAR_BG)
        self.assertEqual(colors['bar_fg'], BAR_FG)
        self.assertEqual(colors['bar_fg_warn'], BAR_FG_WARN)
        self.assertEqual(colors['bar_divider'], BAR_DIVIDER)
        self.assertEqual(colors['bar_marker'], BAR_MARKER)

    def test_translations_from_i18n(self):
        """Translation values come from the T dict."""
        from usage_monitor_for_claude.i18n import T

        config = _init_config(_snap())
        t = config['t']
        self.assertEqual(t['title'], T['popup_title'])
        self.assertEqual(t['account'], T['account'])
        self.assertEqual(t['email'], T['email'])
        self.assertEqual(t['plan'], T['plan'])
        self.assertEqual(t['usage'], T['usage'])
        self.assertEqual(t['extra_usage'], T['extra_usage'])
        self.assertEqual(t['claude_code'], T['claude_code'])
        self.assertEqual(t['changelog'], T['changelog'])
        self.assertEqual(t['pin_popup'], T['pin_popup'])
        self.assertEqual(t['unpin_popup'], T['unpin_popup'])
        self.assertEqual(t['status_updated_s'], T['status_updated_s'])
        self.assertEqual(t['status_updated'], T['status_updated'])
        self.assertEqual(t['status_refreshing'], T['status_refreshing'])
        self.assertEqual(t['status_next_update'], T['status_next_update'])
        self.assertEqual(t['duration_hm'], T['duration_hm'])
        self.assertEqual(t['duration_m'], T['duration_m'])
        self.assertEqual(t['duration_s'], T['duration_s'])

    def test_app_version(self):
        """app_version matches the package version."""
        from usage_monitor_for_claude import __version__

        config = _init_config(_snap())
        self.assertEqual(config['app_version'], __version__)

    def test_data_is_snapshot_to_dict_output(self):
        """The data key contains the output of _snapshot_to_dict."""
        snap = _snap(profile={'account': {'email': 'a@b.com'}, 'organization': {}})
        config = _init_config(snap)
        self.assertEqual(config['data']['profile']['email'], 'a@b.com')
        self.assertEqual(set(config['data'].keys()), {'profile', 'usage', 'extra', 'installations', 'status'})


# ---------------------------------------------------------------------------
# Pin state
# ---------------------------------------------------------------------------

class TestPinState(unittest.TestCase):
    """Tests for UsagePopup pin and drag state."""

    def test_set_pinned_updates_state(self):
        popup = object.__new__(UsagePopup)
        popup._pinned = False

        self.assertTrue(popup._set_pinned(True))
        self.assertTrue(popup._pinned)

        self.assertFalse(popup._set_pinned(False))
        self.assertFalse(popup._pinned)

    def test_unpinning_keeps_dragged_position_flag(self):
        """Unpinning must not clear the moved flag - a dragged popup stays
        where the user put it regardless of the pin state."""
        popup = object.__new__(UsagePopup)
        popup._pinned = True
        popup._moved_by_drag = True

        popup._set_pinned(False)

        self.assertTrue(popup._moved_by_drag)

    def test_begin_drag_works_without_pin(self):
        """Dragging needs no pin - a header hold on an unpinned popup drags."""
        popup = object.__new__(UsagePopup)
        popup._pinned = False
        popup._dragging = False
        popup._window = MagicMock()
        popup._window.get_position.return_value = (460, 360)
        popup._pointer_position = MagicMock(return_value=(500, 400))

        self.assertTrue(popup._begin_drag())
        self.assertTrue(popup._dragging)
        self.assertEqual(popup._drag_offset, (40, 40))

    def test_begin_drag_ignored_without_window(self):
        popup = object.__new__(UsagePopup)
        popup._pinned = True
        popup._window = None
        popup._dragging = False

        self.assertFalse(popup._begin_drag())
        self.assertFalse(popup._dragging)

    def test_begin_drag_anchors_pointer_offset(self):
        popup = object.__new__(UsagePopup)
        popup._pinned = True
        popup._dragging = False
        popup._window = MagicMock()
        popup._window.get_position.return_value = (460, 360)
        popup._pointer_position = MagicMock(return_value=(500, 400))

        self.assertTrue(popup._begin_drag())

        self.assertTrue(popup._dragging)
        self.assertEqual(popup._drag_offset, (40, 40))

    def test_drag_ignored_without_window(self):
        popup = object.__new__(UsagePopup)
        popup._pinned = True
        popup._dragging = True
        popup._window = None

        self.assertFalse(popup._drag())

    def test_drag_self_heals_when_begin_message_was_lost(self):
        """A drag step arriving without a prior begin_drag anchors the grab
        at the current pointer, so a lost/late begin_drag bridge message
        cannot leave the gesture dead."""
        popup = object.__new__(UsagePopup)
        popup._pinned = True
        popup._dragging = False
        popup._window = MagicMock()
        popup._window.get_position.return_value = (460, 360)
        popup._moved_by_drag = False
        popup._pointer_position = MagicMock(return_value=(500, 400))

        self.assertTrue(popup._drag())

        self.assertTrue(popup._dragging)
        self.assertEqual(popup._drag_offset, (40, 40))
        # The implicit begin anchors at the current pointer: no jump.
        popup._window.move.assert_called_once_with(460, 360)
        self.assertTrue(popup._moved_by_drag)

    def test_drag_moves_popup_with_pointer(self):
        popup = object.__new__(UsagePopup)
        popup._pinned = True
        popup._dragging = True
        popup._window = MagicMock()
        popup._drag_offset = (40, 40)
        popup._moved_by_drag = False
        popup._pointer_position = MagicMock(return_value=(700, 620))

        self.assertTrue(popup._drag())

        popup._window.move.assert_called_once_with(660, 580)
        self.assertTrue(popup._moved_by_drag)

    def test_drag_moves_unpinned_popup(self):
        """An unpinned popup follows the pointer just like a pinned one."""
        popup = object.__new__(UsagePopup)
        popup._pinned = False
        popup._dragging = True
        popup._window = MagicMock()
        popup._drag_offset = (40, 40)
        popup._moved_by_drag = False
        popup._pointer_position = MagicMock(return_value=(700, 620))

        self.assertTrue(popup._drag())

        popup._window.move.assert_called_once_with(660, 580)
        self.assertTrue(popup._moved_by_drag)

    def test_drag_converges_from_absolute_pointer_position(self):
        """Each step derives the position from the current pointer, so
        out-of-order calls converge instead of accumulating drift."""
        popup = object.__new__(UsagePopup)
        popup._pinned = True
        popup._dragging = True
        popup._window = MagicMock()
        popup._drag_offset = (10, 10)
        popup._moved_by_drag = False
        popup._pointer_position = MagicMock(side_effect=[(200, 200), (150, 150), (200, 200)])

        popup._drag()
        popup._drag()
        popup._drag()

        self.assertEqual(popup._window.move.call_args_list[-1][0], (190, 190))

    def test_end_drag_clears_dragging(self):
        popup = object.__new__(UsagePopup)
        popup._dragging = True
        popup._last_drag_target = None

        popup._end_drag()

        self.assertFalse(popup._dragging)

    @patch('usage_monitor_for_claude.popup.state')
    def test_end_drag_saves_last_dragged_position(self, mock_state):
        """Ending a drag persists the position the drag itself commanded.

        get_position() cannot be trusted right after a move (the WM
        confirms it late), so the saved value must be the _drag() target,
        never a window readback.
        """
        popup = object.__new__(UsagePopup)
        popup._pinned = True
        popup._dragging = True
        popup._moved_by_drag = False
        popup._drag_offset = (40, 40)
        popup._last_drag_target = None
        popup._window = MagicMock()
        # A stale readback must not leak into the save.
        popup._window.get_position.return_value = (1932, 12)
        popup._pointer_position = MagicMock(return_value=(700, 620))

        popup._drag()
        popup._end_drag()

        self.assertFalse(popup._dragging)
        mock_state.save_popup_position.assert_called_once_with(660, 580)
        self.assertIsNone(popup._last_drag_target)

    @patch('usage_monitor_for_claude.popup.state')
    def test_end_drag_without_prior_drag_saves_nothing(self, mock_state):
        """A stray end_drag bridge call (no drag in progress) must not save."""
        popup = object.__new__(UsagePopup)
        popup._dragging = False
        popup._last_drag_target = None

        popup._end_drag()

        mock_state.save_popup_position.assert_not_called()

    @patch('usage_monitor_for_claude.popup.state')
    def test_end_drag_after_motionless_click_saves_nothing(self, mock_state):
        """A header click that never moved the popup must not pin the saved
        position to wherever the popup happened to be."""
        popup = object.__new__(UsagePopup)
        popup._pinned = True
        popup._dragging = False
        popup._window = MagicMock()
        popup._window.get_position.return_value = (460, 360)
        popup._pointer_position = MagicMock(return_value=(500, 400))
        popup._last_drag_target = (111, 222)  # leftover from an older drag

        popup._begin_drag()   # resets the target
        popup._end_drag()     # no _drag() in between

        self.assertFalse(popup._dragging)
        mock_state.save_popup_position.assert_not_called()


# ---------------------------------------------------------------------------
# report_height / first show
# ---------------------------------------------------------------------------

class TestReportHeight(unittest.TestCase):
    """Tests for _PopupApi.report_height - the first report must always show the window."""

    def _build_popup(self):
        """Run the real UsagePopup.__init__ with GLib mocked, return (popup, api).

        __init__ blocks on _closed.wait(), so it runs on a worker thread; the
        popup instance is captured from the ``_build_window`` bound method
        handed to ``GLib.idle_add`` (which the mock never executes, so no
        real window is created).
        """
        mock_glib = patch('usage_monitor_for_claude.popup.GLib').start()
        # Keep the constructor's saved-position lookup off the real state file.
        patch('usage_monitor_for_claude.popup.state').start()
        self.addCleanup(patch.stopall)

        captured = {}

        def capture_idle(func, *args):
            captured.setdefault('popup', getattr(func, '__self__', None))

        mock_glib.idle_add.side_effect = capture_idle

        app = MagicMock()
        thread = threading.Thread(target=lambda: UsagePopup(app), daemon=True)
        thread.start()

        deadline = time.time() + 2.0
        while 'popup' not in captured and time.time() < deadline:
            time.sleep(0.01)
        self.assertIn('popup', captured)

        popup = captured['popup']
        api = popup._api
        self.addCleanup(popup._closed.set)

        popup._css_scale = 1.0
        popup._resize_and_position = MagicMock()
        popup._show_window = MagicMock()
        return popup, api

    def test_first_report_at_initial_window_height_shows_popup(self):
        """A first content height equal to the initial window height must still show the window."""
        popup, api = self._build_popup()
        initial_window_height = mock_height = 400

        api.report_height(mock_height)

        popup._resize_and_position.assert_called_once_with(initial_window_height)
        popup._show_window.assert_called_once()

    def test_first_report_at_other_height_shows_popup(self):
        """A first content height different from the window height shows the window."""
        popup, api = self._build_popup()

        api.report_height(523)

        popup._resize_and_position.assert_called_once_with(523)
        popup._show_window.assert_called_once()

    def test_repeated_report_with_same_height_is_deduplicated(self):
        """A second report with an unchanged height must not resize again."""
        popup, api = self._build_popup()

        api.report_height(523)
        api.report_height(523)

        popup._resize_and_position.assert_called_once_with(523)

    def test_zero_height_ignored(self):
        """A zero height report is ignored entirely."""
        popup, api = self._build_popup()

        api.report_height(0)

        popup._resize_and_position.assert_not_called()
        popup._show_window.assert_not_called()

    def test_stale_height_report_cannot_overwrite_newer_resize(self):
        """pywebview dispatches each bridge call on a fresh thread; two rapid
        height reports must not interleave so that the earlier resize is
        applied after (and overwrites) the later one."""
        popup, api = self._build_popup()

        first_entered = threading.Event()
        release_first = threading.Event()
        applied = []

        def resize(height):
            if height == 400:
                first_entered.set()
                release_first.wait(2)
            applied.append(height)

        popup._resize_and_position = MagicMock(side_effect=resize)

        first = threading.Thread(target=lambda: api.report_height(400), daemon=True)
        first.start()
        self.assertTrue(first_entered.wait(2))

        second = threading.Thread(target=lambda: api.report_height(523), daemon=True)
        second.start()
        time.sleep(0.1)
        release_first.set()
        first.join(2)
        second.join(2)

        # The window size (last applied resize) must match the tracked height.
        self.assertEqual(applied[-1], popup._last_height)

    def test_concurrent_first_reports_start_show_only_once(self):
        """Two pre-show reports racing each other must not both run _show_window
        (which would start two update-push loops for one popup)."""
        popup, api = self._build_popup()

        show_entered = threading.Event()
        release_show = threading.Event()
        show_calls = []

        def show():
            show_calls.append(1)
            show_entered.set()
            release_show.wait(2)
            popup._shown = True

        popup._show_window = MagicMock(side_effect=show)

        first = threading.Thread(target=lambda: api.report_height(400), daemon=True)
        first.start()
        self.assertTrue(show_entered.wait(2))

        second = threading.Thread(target=lambda: api.report_height(523), daemon=True)
        second.start()
        time.sleep(0.1)
        release_show.set()
        first.join(2)
        second.join(2)

        self.assertEqual(len(show_calls), 1)


# ---------------------------------------------------------------------------
# JS bridge contract
# ---------------------------------------------------------------------------

class TestBridgeContract(unittest.TestCase):
    """Tests for the pywebview-compatible bridge dispatch.

    The popup.js comes from the Windows original and
    calls ``pywebview.api.<method>(...)``; an injected user script posts
    ``{id, method, args}`` JSON envelopes to Python, which must dispatch
    to _PopupApi and settle the JS Promise.
    """

    def _popup(self):
        popup = object.__new__(UsagePopup)
        popup._pinned = False
        popup._moved_by_drag = False
        popup._css_scale = 1.0
        popup._window = MagicMock()
        popup._webview = MagicMock()
        popup._api = _PopupApi(popup)
        popup._evaluate_js = MagicMock()
        return popup

    def _message(self, text):
        message = MagicMock()
        message.get_js_value.return_value.to_string.return_value = text
        return message

    def test_dispatches_method_with_args_and_settles_promise(self):
        popup = self._popup()

        popup._on_bridge_message(MagicMock(), self._message('{"id": 7, "method": "set_pinned", "args": [true]}'))

        self.assertTrue(popup._pinned)
        script = popup._evaluate_js.call_args[0][0]
        self.assertIn('window.__bridgeSettle(7, true, true)', script)

    def test_returns_result_value_to_js(self):
        """set_pinned's boolean result reaches the Promise resolution."""
        popup = self._popup()
        popup._pinned = True

        popup._on_bridge_message(MagicMock(), self._message('{"id": 3, "method": "set_pinned", "args": [false]}'))

        script = popup._evaluate_js.call_args[0][0]
        self.assertIn('window.__bridgeSettle(3, true, false)', script)

    def test_unknown_method_is_not_dispatched(self):
        """Only the declared bridge methods are callable from page JS."""
        popup = self._popup()

        popup._on_bridge_message(MagicMock(), self._message('{"id": 1, "method": "_close", "args": []}'))

        script = popup._evaluate_js.call_args[0][0]
        self.assertIn('window.__bridgeSettle(1, true, null)', script)

    def test_failing_method_rejects_promise(self):
        popup = self._popup()
        with patch.object(popup, '_set_pinned', side_effect=RuntimeError('boom')):
            popup._on_bridge_message(MagicMock(), self._message('{"id": 9, "method": "set_pinned", "args": [true]}'))

        script = popup._evaluate_js.call_args[0][0]
        self.assertIn('window.__bridgeSettle(9, false, null)', script)

    def test_malformed_json_is_ignored(self):
        popup = self._popup()

        popup._on_bridge_message(MagicMock(), self._message('not json'))

        popup._evaluate_js.assert_not_called()

    def test_message_without_id_is_not_settled(self):
        popup = self._popup()

        popup._on_bridge_message(MagicMock(), self._message('{"method": "end_drag", "args": []}'))

        popup._evaluate_js.assert_not_called()

    def test_report_height_reaches_geometry_path(self):
        popup = self._popup()
        popup._geometry_lock = threading.Lock()
        popup._last_height = 0
        popup._shown = True
        popup._resize_and_position = MagicMock()

        popup._on_bridge_message(MagicMock(), self._message('{"id": 2, "method": "report_height", "args": [523]}'))

        popup._resize_and_position.assert_called_once_with(523)

    def test_bridge_method_names_cover_popup_js_api(self):
        """Every pywebview.api method upstream popup.js calls must be bridged."""
        from usage_monitor_for_claude.popup import _BRIDGE_METHODS, _BRIDGE_SCRIPT

        expected = {'close', 'open_url', 'set_pinned', 'begin_drag', 'drag', 'end_drag', 'report_height'}
        self.assertEqual(_BRIDGE_METHODS, frozenset(expected))
        for name in expected:
            self.assertIn(f"'{name}'", _BRIDGE_SCRIPT)


# ---------------------------------------------------------------------------
# Dismissal (focus loss, Escape, close)
# ---------------------------------------------------------------------------

class TestDismissal(unittest.TestCase):
    """Tests for the GTK dismissal handlers replacing the Win32 hook pump."""

    def _popup(self, pinned=False, shown=True, dragging=False):
        popup = object.__new__(UsagePopup)
        popup._running = True
        popup._pinned = pinned
        popup._shown = shown
        popup._dragging = dragging
        popup._closed = threading.Event()
        popup._window = MagicMock()
        popup._webview = MagicMock()
        return popup

    def test_focus_out_closes_unpinned_popup(self):
        popup = self._popup()
        with patch.object(popup, '_close') as mock_close:
            popup._on_focus_out(MagicMock(), MagicMock())
        mock_close.assert_called_once()

    def test_focus_out_keeps_pinned_popup(self):
        popup = self._popup(pinned=True)
        with patch.object(popup, '_close') as mock_close:
            popup._on_focus_out(MagicMock(), MagicMock())
        mock_close.assert_not_called()

    def test_focus_out_before_shown_is_ignored(self):
        """The invisible layout phase must not be dismissed by focus churn."""
        popup = self._popup(shown=False)
        with patch.object(popup, '_close') as mock_close:
            popup._on_focus_out(MagicMock(), MagicMock())
        mock_close.assert_not_called()

    def test_focus_out_during_drag_is_ignored(self):
        popup = self._popup(dragging=True)
        with patch.object(popup, '_close') as mock_close:
            popup._on_focus_out(MagicMock(), MagicMock())
        mock_close.assert_not_called()

    def test_escape_closes_unpinned_popup(self):
        popup = self._popup()
        event = MagicMock(keyval=_KEY_ESCAPE)
        with patch.object(popup, '_close') as mock_close:
            handled = popup._on_key_press(MagicMock(), event)
        mock_close.assert_called_once()
        self.assertTrue(handled)

    def test_escape_keeps_pinned_popup(self):
        popup = self._popup(pinned=True)
        event = MagicMock(keyval=_KEY_ESCAPE)
        with patch.object(popup, '_close') as mock_close:
            popup._on_key_press(MagicMock(), event)
        mock_close.assert_not_called()

    def test_other_key_is_ignored(self):
        popup = self._popup()
        event = MagicMock(keyval=0x20)  # space
        with patch.object(popup, '_close') as mock_close:
            handled = popup._on_key_press(MagicMock(), event)
        mock_close.assert_not_called()
        self.assertFalse(handled)

    @patch('usage_monitor_for_claude.popup.GLib')
    def test_close_releases_blocked_constructor(self, mock_glib):
        """_close() sets the event the constructor blocks on and schedules destruction."""
        popup = self._popup(pinned=True)
        popup._close()

        self.assertFalse(popup._running)
        self.assertTrue(popup._closed.is_set())
        mock_glib.idle_add.assert_called_once()

    def test_window_destroyed_event_releases_constructor(self):
        """A destroy from the window manager ends the popup even while pinned."""
        popup = self._popup(pinned=True)
        popup._on_window_destroyed()

        self.assertFalse(popup._running)
        self.assertTrue(popup._closed.is_set())
        self.assertIsNone(popup._window)


# ---------------------------------------------------------------------------
# _update_loop resilience
# ---------------------------------------------------------------------------

class TestUpdateLoopResilience(unittest.TestCase):
    """Tests that a transient failure does not end the popup's update stream."""

    def test_transient_failure_does_not_end_update_loop(self):
        """One failing JS push (or snapshot conversion) must not stop updates -
        a pinned popup can live for days and would show stale bars forever."""
        popup = object.__new__(UsagePopup)
        popup._running = True
        popup._last_version = 0
        popup._evaluate_js = MagicMock()

        class FakeCache:
            def __init__(self):
                self.version_counter = 0

            @property
            def snapshot(self):
                self.version_counter += 1
                snap = MagicMock()
                snap.version = self.version_counter
                return snap

        popup.app = MagicMock()
        popup.app.cache = FakeCache()
        popup.app._next_poll_time = 100.0

        def eval_js(_script):
            if popup._evaluate_js.call_count == 1:
                raise RuntimeError('transient WebKit hiccup')
            popup._running = False

        popup._evaluate_js.side_effect = eval_js

        iterations = [0]

        def guarded_sleep(_seconds):
            iterations[0] += 1
            if iterations[0] > 10:
                popup._running = False

        with patch('usage_monitor_for_claude.popup.time.sleep', side_effect=guarded_sleep), \
             patch('usage_monitor_for_claude.popup.find_installations', return_value=[]), \
             patch('usage_monitor_for_claude.popup._snapshot_to_dict', return_value={}):
            popup._update_loop()

        self.assertEqual(popup._evaluate_js.call_count, 2)

    def test_failed_update_is_retried_on_next_tick(self):
        """An update that failed to push is retried even when the data did not
        change again - the version marker advances only on success."""
        popup = object.__new__(UsagePopup)
        popup._running = True
        popup._last_version = 0
        popup._evaluate_js = MagicMock()

        snap = MagicMock()
        snap.version = 1
        popup.app = MagicMock()
        popup.app.cache.snapshot = snap
        popup.app._next_poll_time = 100.0

        def eval_js(_script):
            if popup._evaluate_js.call_count == 1:
                raise RuntimeError('transient WebKit hiccup')
            popup._running = False

        popup._evaluate_js.side_effect = eval_js

        iterations = [0]

        def guarded_sleep(_seconds):
            iterations[0] += 1
            if iterations[0] > 10:
                popup._running = False

        with patch('usage_monitor_for_claude.popup.time.sleep', side_effect=guarded_sleep), \
             patch('usage_monitor_for_claude.popup.find_installations', return_value=[]), \
             patch('usage_monitor_for_claude.popup._snapshot_to_dict', return_value={}):
            popup._update_loop()

        self.assertEqual(popup._evaluate_js.call_count, 2)
        self.assertEqual(popup._last_version, 1)


# ---------------------------------------------------------------------------
# _tray_position
# ---------------------------------------------------------------------------

class TestTrayPosition(unittest.TestCase):
    """Tests for UsagePopup._tray_position - popup placement near the tray.

    There is no portable tray-window handle on Linux, so the popup lands
    in the work-area corner nearest the current pointer (which sits next
    to the tray right after a tray interaction).  The XFCE panel is
    excluded from the reported work area.
    """

    def _call(self, pointer, workarea, width=340, height=400):
        """Call _tray_position without constructing a full UsagePopup."""
        popup = object.__new__(UsagePopup)
        popup._pointer_and_workarea = MagicMock(return_value=(pointer, workarea))
        return popup._tray_position(width, height)

    def test_pointer_bottom_right_lands_bottom_right(self):
        """Pointer near a bottom panel tray places the popup bottom-right."""
        x, y = self._call(pointer=(1900, 1030), workarea=(0, 0, 1920, 1040))
        self.assertEqual(x, 1920 - 340 - _MARGIN)
        self.assertEqual(y, 1040 - 400 - _MARGIN)

    def test_pointer_top_right_lands_top_right(self):
        """Pointer near a top panel tray places the popup top-right."""
        x, y = self._call(pointer=(1900, 5), workarea=(0, 30, 1920, 1050))
        self.assertEqual(x, 1920 - 340 - _MARGIN)
        self.assertEqual(y, 30 + _MARGIN)

    def test_pointer_bottom_left_lands_bottom_left(self):
        x, y = self._call(pointer=(10, 1030), workarea=(0, 0, 1920, 1040))
        self.assertEqual(x, _MARGIN)
        self.assertEqual(y, 1040 - 400 - _MARGIN)

    def test_pointer_top_left_lands_top_left(self):
        x, y = self._call(pointer=(10, 5), workarea=(0, 40, 1920, 1040))
        self.assertEqual(x, _MARGIN)
        self.assertEqual(y, 40 + _MARGIN)

    def test_workarea_offset_monitor(self):
        """A monitor not at virtual (0, 0) keeps the popup on that monitor."""
        x, y = self._call(pointer=(3800, 1030), workarea=(1920, 0, 1920, 1040))
        self.assertEqual(x, 1920 + 1920 - 340 - _MARGIN)
        self.assertEqual(y, 1040 - 400 - _MARGIN)

    def test_popup_fits_within_work_area(self):
        """The popup extent never exceeds the work area."""
        x, y = self._call(pointer=(1900, 1030), workarea=(0, 0, 1920, 1040))
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + 340, 1920)
        self.assertLessEqual(y + 400, 1040)


# ---------------------------------------------------------------------------
# _resize_and_position
# ---------------------------------------------------------------------------

class TestResizeAndPosition(unittest.TestCase):
    """Tests for UsagePopup._resize_and_position - logical-pixel geometry."""

    def _popup(self, pinned=False, moved=False):
        popup = object.__new__(UsagePopup)
        popup._pinned = pinned
        popup._moved_by_drag = moved
        popup._shown = False
        popup._saved_position = None
        popup._restored = False
        popup._css_scale = 1.0
        popup._window = MagicMock()
        popup._pointer_and_workarea = MagicMock(return_value=((1900, 1030), (0, 0, 1920, 1040)))
        return popup

    def test_resize_uses_logical_pixels(self):
        """At 96 dpi one CSS pixel is one logical pixel - the height passes through."""
        popup = self._popup()
        popup._resize_and_position(500)
        popup._window.resize.assert_called_once_with(340, 500)

    def test_move_targets_tray_corner(self):
        popup = self._popup()
        popup._resize_and_position(500)
        popup._window.move.assert_called_once_with(1920 - 340 - _MARGIN, 1040 - 500 - _MARGIN)

    def test_window_fits_within_work_area(self):
        popup = self._popup()
        popup._resize_and_position(500)
        resize_w, resize_h = popup._window.resize.call_args[0]
        move_x, move_y = popup._window.move.call_args[0]
        self.assertLessEqual(move_x + resize_w, 1920)
        self.assertLessEqual(move_y + resize_h, 1040)

    def test_dragged_popup_resizes_without_snapping_to_tray(self):
        """A dragged popup keeps its position when content height changes,
        pinned or not - the pin only controls dismissal."""
        for pinned in (True, False):
            with self.subTest(pinned=pinned):
                popup = self._popup(pinned=pinned, moved=True)
                popup._resize_and_position(500)

                popup._window.resize.assert_called_once_with(340, 500)
                popup._window.move.assert_not_called()

    def test_unmoved_popup_still_snaps_to_tray(self):
        """A popup that was never dragged keeps following the tray corner."""
        for pinned in (True, False):
            with self.subTest(pinned=pinned):
                popup = self._popup(pinned=pinned, moved=False)
                popup._resize_and_position(500)

                popup._window.move.assert_called_once()


# ---------------------------------------------------------------------------
# CSS pixel scale (Xft DPI)
# ---------------------------------------------------------------------------

class TestReadCssScale(unittest.TestCase):
    """Tests for _read_css_scale - the CSS pixel to logical pixel factor.

    WebKit renders one CSS pixel as ``devicePixelRatio`` physical pixels,
    and that ratio is the monitor's GDK scale factor multiplied by the
    desktop's Xft DPI over 96 (a 110 dpi desktop renders at 110/96).
    ``Gtk.Window.resize`` works in logical pixels, which already carry the
    GDK scale factor, so only the DPI part belongs here.
    """

    def _settings(self, xft_dpi):
        settings = MagicMock()
        settings.get_property.return_value = xft_dpi
        return settings

    def test_follows_xft_dpi(self):
        """A 110 dpi desktop renders CSS pixels 110/96 larger."""
        with patch('usage_monitor_for_claude.popup.Gtk') as mock_gtk:
            mock_gtk.Settings.get_default.return_value = self._settings(110 * 1024)
            self.assertAlmostEqual(_read_css_scale(), 110 / 96)

    def test_default_dpi_is_neutral(self):
        """At 96 dpi one CSS pixel is one logical pixel."""
        with patch('usage_monitor_for_claude.popup.Gtk') as mock_gtk:
            mock_gtk.Settings.get_default.return_value = self._settings(96 * 1024)
            self.assertEqual(_read_css_scale(), 1.0)

    def test_unset_xft_dpi_is_neutral(self):
        """gtk-xft-dpi is -1 when the desktop sets no DPI - fall back to 96."""
        with patch('usage_monitor_for_claude.popup.Gtk') as mock_gtk:
            mock_gtk.Settings.get_default.return_value = self._settings(-1)
            self.assertEqual(_read_css_scale(), 1.0)

    def test_missing_settings_is_neutral(self):
        """No default settings (no display yet) must not break sizing."""
        with patch('usage_monitor_for_claude.popup.Gtk') as mock_gtk:
            mock_gtk.Settings.get_default.return_value = None
            self.assertEqual(_read_css_scale(), 1.0)

    def test_missing_gtk_is_neutral(self):
        """A headless import leaves Gtk as None."""
        with patch('usage_monitor_for_claude.popup.Gtk', None):
            self.assertEqual(_read_css_scale(), 1.0)

    def test_settings_failure_is_neutral(self):
        """A failing settings lookup must not stop the popup from opening."""
        with patch('usage_monitor_for_claude.popup.Gtk') as mock_gtk:
            mock_gtk.Settings.get_default.side_effect = RuntimeError('no display')
            self.assertEqual(_read_css_scale(), 1.0)


class TestReadFontZoom(unittest.TestCase):
    """Tests for _read_font_zoom - following the desktop font size.

    popup.css is written against a 13 px base font.  The desktop font size
    is a separate setting from the DPI (XFCE exposes both), so a user who
    raises it must still get a proportionally larger popup: the zoom is
    the ratio between their font and that 13 px baseline.
    """

    def _gtk(self, mock_gtk, font_name):
        settings = MagicMock()
        settings.get_property.return_value = font_name
        mock_gtk.Settings.get_default.return_value = settings

    def _zoom(self, font_name):
        with patch('usage_monitor_for_claude.popup.Gtk') as mock_gtk:
            self._gtk(mock_gtk, font_name)
            return _read_font_zoom()

    def test_point_size_becomes_css_pixels(self):
        """A point is 1/72 inch and a CSS pixel 1/96, so 12 pt is 16 px."""
        self.assertAlmostEqual(self._zoom('Sans 12'), 16 / _CSS_BASE_FONT_PX)

    def test_baseline_font_is_neutral(self):
        """The font popup.css was drawn against needs no zoom."""
        self.assertAlmostEqual(self._zoom(f'Sans {_CSS_BASE_FONT_PX * 72 / 96}'), 1.0)

    def test_style_keywords_are_ignored(self):
        """Pango font descriptions carry style words before the size."""
        self.assertAlmostEqual(self._zoom('DejaVu Sans Bold Italic 12'), 16 / _CSS_BASE_FONT_PX)

    def test_fractional_size(self):
        self.assertAlmostEqual(self._zoom('Cantarell 10.5'), (10.5 * 96 / 72) / _CSS_BASE_FONT_PX)

    def test_absolute_pixel_size(self):
        """Pango also accepts an absolute size in pixels."""
        self.assertAlmostEqual(self._zoom('Sans 16px'), 16 / _CSS_BASE_FONT_PX)

    def test_font_without_a_size_is_neutral(self):
        self.assertEqual(self._zoom('Sans'), 1.0)

    def test_unparsable_font_is_neutral(self):
        self.assertEqual(self._zoom(''), 1.0)
        self.assertEqual(self._zoom(None), 1.0)

    def test_absurd_sizes_are_clamped(self):
        """A broken font setting must not produce an unusable popup."""
        self.assertEqual(self._zoom('Sans 2'), 0.5)
        self.assertEqual(self._zoom('Sans 400'), 3.0)

    def test_missing_settings_is_neutral(self):
        with patch('usage_monitor_for_claude.popup.Gtk') as mock_gtk:
            mock_gtk.Settings.get_default.return_value = None
            self.assertEqual(_read_font_zoom(), 1.0)

    def test_missing_gtk_is_neutral(self):
        with patch('usage_monitor_for_claude.popup.Gtk', None):
            self.assertEqual(_read_font_zoom(), 1.0)

    def test_settings_failure_is_neutral(self):
        with patch('usage_monitor_for_claude.popup.Gtk') as mock_gtk:
            mock_gtk.Settings.get_default.side_effect = RuntimeError('no display')
            self.assertEqual(_read_font_zoom(), 1.0)


class TestCssScaleComposition(unittest.TestCase):
    """The popup scales with the DPI and with the desktop font size.

    WebKit folds the page zoom into devicePixelRatio, so one CSS pixel
    becomes ``dpi factor * zoom`` logical pixels and both settings have to
    reach the window geometry through the same number.
    """

    def test_scale_multiplies_dpi_by_font_zoom(self):
        # GLib as None makes __init__ return instead of blocking on the
        # window it would otherwise build on the main loop.
        with patch('usage_monitor_for_claude.popup.GLib', None), \
                patch('usage_monitor_for_claude.popup.state'), \
                patch('usage_monitor_for_claude.popup._read_css_scale', return_value=1.25), \
                patch('usage_monitor_for_claude.popup._read_font_zoom', return_value=1.5):
            popup = object.__new__(UsagePopup)
            UsagePopup.__init__(popup, MagicMock())

        self.assertEqual(popup._zoom, 1.5)
        self.assertAlmostEqual(popup._css_scale, 1.25 * 1.5)


class TestScaledGeometry(unittest.TestCase):
    """Tests that the reported CSS height reaches GTK as logical pixels.

    A height passed through unscaled makes the window shorter than its own
    content on every desktop whose DPI is not 96, clipping the last rows.
    """

    _SCALE = 110 / 96

    def _popup(self, scale):
        popup = object.__new__(UsagePopup)
        popup._pinned = False
        popup._moved_by_drag = False
        popup._shown = True
        popup._saved_position = None
        popup._restored = False
        popup._css_scale = scale
        popup._last_height = 0
        popup._geometry_lock = threading.Lock()
        popup._window = MagicMock()
        popup._pointer_and_workarea = MagicMock(return_value=((1900, 1030), (0, 0, 1920, 1040)))
        popup._api = _PopupApi(popup)
        return popup

    def test_reported_height_is_scaled_for_gtk(self):
        """529 CSS pixels are 607 logical pixels on a 110 dpi desktop."""
        popup = self._popup(self._SCALE)

        popup._api.report_height(529)

        self.assertEqual(popup._window.resize.call_args[0][1], 607)

    def test_scaled_height_is_rounded_up(self):
        """Rounding down would clip the last row - always round up."""
        popup = self._popup(self._SCALE)

        popup._api.report_height(100)

        # 100 * 110/96 = 114.58
        self.assertEqual(popup._window.resize.call_args[0][1], 115)

    def test_width_is_scaled_too(self):
        """The 340 CSS pixel design width must keep its proportions at any DPI."""
        popup = self._popup(self._SCALE)

        popup._api.report_height(529)

        # 340 * 110/96 = 389.58
        self.assertEqual(popup._window.resize.call_args[0][0], 390)

    def test_deduplication_compares_logical_pixels(self):
        """A repeated report still resizes only once after scaling."""
        popup = self._popup(self._SCALE)

        popup._api.report_height(529)
        popup._api.report_height(529)

        popup._window.resize.assert_called_once()

    def test_position_accounts_for_the_scaled_size(self):
        """The tray corner placement must use the scaled window box."""
        popup = self._popup(self._SCALE)

        popup._api.report_height(529)

        self.assertEqual(popup._window.move.call_args[0], (1920 - 390 - _MARGIN, 1040 - 607 - _MARGIN))

    def test_neutral_scale_leaves_the_height_untouched(self):
        """At 96 dpi nothing changes - the reported height is used as-is."""
        popup = self._popup(1.0)

        popup._api.report_height(529)

        popup._window.resize.assert_called_once_with(340, 529)


# ---------------------------------------------------------------------------
# Saved popup position (restore on open, anchored growth)
# ---------------------------------------------------------------------------

class TestSavedPositionRestore(unittest.TestCase):
    """Tests for restoring the persisted popup position on open.

    The first placement prefers the position saved when a previous popup
    was dragged, clamped into its monitor's work area; an off-screen saved
    position falls back to the corner nearest the pointer.  After a
    restored placement, later height reports keep the popup anchored
    where it is instead of snapping back.
    """

    _WORKAREA = (0, 0, 1920, 1040)

    def _popup(self, saved=None, shown=False, restored=False, workareas=None):
        popup = object.__new__(UsagePopup)
        popup._pinned = False
        popup._moved_by_drag = False
        popup._shown = shown
        popup._saved_position = saved
        popup._restored = restored
        popup._css_scale = 1.0
        popup._window = MagicMock()
        popup._pointer_and_workarea = MagicMock(return_value=((1900, 1030), self._WORKAREA))
        popup._monitor_workareas = MagicMock(return_value=workareas if workareas is not None else [self._WORKAREA])
        return popup

    def test_saved_position_inside_workarea_is_used(self):
        """A saved position that fits the work area is restored as-is."""
        popup = self._popup(saved=(600, 300))

        popup._resize_and_position(500)

        popup._window.move.assert_called_once_with(600, 300)
        self.assertTrue(popup._restored)
        popup._pointer_and_workarea.assert_not_called()

    def test_saved_position_clamped_into_workarea(self):
        """A partially visible saved position is clamped fully inside."""
        # 1800 + 340 > 1920 and 900 + 500 > 1040 -> clamp both axes
        popup = self._popup(saved=(1800, 900))

        popup._resize_and_position(500)

        popup._window.move.assert_called_once_with(1920 - 340, 1040 - 500)
        self.assertTrue(popup._restored)

    def test_saved_position_negative_clamped_to_origin(self):
        """A saved position hanging off the top-left is pulled to the edge."""
        popup = self._popup(saved=(-100, -50))

        popup._resize_and_position(500)

        popup._window.move.assert_called_once_with(0, 0)

    def test_offscreen_saved_position_falls_back_to_corner(self):
        """A saved position touching no monitor uses the corner placement."""
        popup = self._popup(saved=(5000, 5000))

        popup._resize_and_position(500)

        popup._window.move.assert_called_once_with(1920 - 340 - _MARGIN, 1040 - 500 - _MARGIN)
        self.assertFalse(popup._restored)

    def test_saved_position_on_second_monitor_is_used(self):
        """A saved position on another monitor restores onto that monitor."""
        popup = self._popup(saved=(2500, 300), workareas=[(0, 0, 1920, 1040), (1920, 0, 1920, 1040)])

        popup._resize_and_position(500)

        popup._window.move.assert_called_once_with(2500, 300)

    def test_no_saved_position_uses_corner(self):
        """Without a saved position the corner placement is unchanged."""
        popup = self._popup(saved=None)

        popup._resize_and_position(500)

        popup._window.move.assert_called_once_with(1920 - 340 - _MARGIN, 1040 - 500 - _MARGIN)

    def test_monitor_query_failure_falls_back_to_corner(self):
        """A failing Gdk monitor query silently uses the corner placement."""
        popup = self._popup(saved=(600, 300))
        popup._monitor_workareas.side_effect = RuntimeError('no display')

        popup._resize_and_position(500)

        popup._window.move.assert_called_once_with(1920 - 340 - _MARGIN, 1040 - 500 - _MARGIN)

    def test_height_growth_keeps_restored_popup_anchored(self):
        """Mid-session height growth must not reposition to the saved point."""
        popup = self._popup(saved=(50, 50), shown=True, restored=True)
        popup._window.get_position.return_value = (600, 300)

        popup._resize_and_position(700)

        popup._window.resize.assert_called_once_with(340, 700)
        popup._window.move.assert_not_called()

    def test_height_growth_clamps_restored_popup_at_workarea_edge(self):
        """Growth that would overflow the work area pulls the popup back in."""
        popup = self._popup(saved=(600, 900), shown=True, restored=True)
        popup._window.get_position.return_value = (600, 900)

        popup._resize_and_position(500)  # 900 + 500 > 1040

        popup._window.move.assert_called_once_with(600, 1040 - 500)

    def test_restored_popup_dragged_keeps_position(self):
        """The drag override still wins over every placement path."""
        popup = self._popup(saved=(600, 300), shown=True, restored=True)
        popup._moved_by_drag = True

        popup._resize_and_position(700)

        popup._window.resize.assert_called_once_with(340, 700)
        popup._window.move.assert_not_called()

    def test_unrestored_popup_keeps_corner_growth_behavior(self):
        """Without a restore, each height report still re-snaps to the corner
        (bottom placement stays anchored to the bottom edge while growing)."""
        popup = self._popup(saved=None, shown=True, restored=False)

        popup._resize_and_position(700)

        popup._window.move.assert_called_once_with(1920 - 340 - _MARGIN, 1040 - 700 - _MARGIN)


if __name__ == '__main__':
    unittest.main()
