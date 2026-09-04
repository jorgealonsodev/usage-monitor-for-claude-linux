"""
Tray Icon Tests
================

Unit tests for tray icon rendering and theme detection.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from PIL import Image, ImageDraw

import usage_monitor_for_claude.tray_icon as tray_icon_mod


def setUpModule():
    # Pin the default icon style and unset color levels so a local
    # usage-monitor-settings.json with 'icon_style' or 'icon_color_levels'
    # cannot flip the classic-style rendering tests.
    for attr, value in (('ICON_STYLE', 'number+bars'), ('ICON_COLOR_LEVELS', None)):
        patcher = patch.object(tray_icon_mod, attr, value)
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


class TestWatchThemeChange(unittest.TestCase):
    """Tests for watch_theme_change() - the Gtk.Settings-based theme watcher."""

    @patch.object(tray_icon_mod, '_gtk_settings')
    def test_connects_both_theme_signals(self, mock_settings_fn):
        """The watcher subscribes to theme-name and prefer-dark notifications."""
        settings = MagicMock()
        mock_settings_fn.return_value = settings

        tray_icon_mod.watch_theme_change(MagicMock())

        connected = [call_args[0][0] for call_args in settings.connect.call_args_list]
        self.assertEqual(connected, ['notify::gtk-theme-name', 'notify::gtk-application-prefer-dark-theme'])

    @patch.object(tray_icon_mod, '_gtk_settings')
    def test_callback_exception_does_not_propagate(self, mock_settings_fn):
        """A transient callback failure (e.g. re-render error during a panel
        restart) must not raise out of the GTK signal emission."""
        settings = MagicMock()
        mock_settings_fn.return_value = settings

        calls = []

        def callback():
            calls.append(1)
            raise RuntimeError('transient render failure')

        tray_icon_mod.watch_theme_change(callback)

        handler = settings.connect.call_args_list[0][0][1]
        handler(settings, MagicMock())  # must not raise
        handler(settings, MagicMock())
        self.assertEqual(len(calls), 2)

    @patch.object(tray_icon_mod, '_gtk_settings')
    def test_signal_fires_callback(self, mock_settings_fn):
        """Each property notification invokes the callback once."""
        settings = MagicMock()
        mock_settings_fn.return_value = settings
        callback = MagicMock()

        tray_icon_mod.watch_theme_change(callback)

        for call_args in settings.connect.call_args_list:
            call_args[0][1](settings, MagicMock())
        self.assertEqual(callback.call_count, 2)

    @patch.object(tray_icon_mod, '_gtk_settings', return_value=None)
    def test_missing_gtk_leaves_theme_unwatched(self, _mock_settings_fn):
        """Without a GTK stack the watcher returns without connecting anything."""
        callback = MagicMock()
        tray_icon_mod.watch_theme_change(callback)

        callback.assert_not_called()


class TestOverageBarEndState(unittest.TestCase):
    """Tests for the overage bar when the elapsed time is clamped to 100%."""

    _FG = (255, 255, 255, 255)
    _FG_HALF = (255, 255, 255, 80)
    _FG_WARN = (224, 80, 80, 255)

    def _draw_bar(self, pct, time_pct):
        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        tray_icon_mod._draw_usage_bar(draw, 0, pct, 'overage', time_pct, self._FG, self._FG_HALF, self._FG_WARN)
        return img

    def test_stale_window_below_limit_shows_empty_bar(self):
        """With a stale resets_at the elapsed time clamps to 100%; usage below the
        limit must keep the overage reading (empty bar) instead of jumping to a
        linear utilization fill until the confirming poll arrives."""
        img = self._draw_bar(80.0, 100.0)
        self.assertEqual(img.getpixel((32, 4)), self._FG_HALF)

    def test_stale_window_exhausted_shows_full_bar(self):
        """An exhausted quota keeps a full bar in the stale-window end state."""
        img = self._draw_bar(100.0, 100.0)
        self.assertEqual(img.getpixel((32, 4)), self._FG)

    def test_active_window_overage_fill_unchanged(self):
        """The regular overage fill (time_pct < 100) is unaffected."""
        # 75% used at 50% elapsed: overage 25 of remaining 50 -> half filled.
        img = self._draw_bar(75.0, 50.0)
        self.assertEqual(img.getpixel((16, 4)), self._FG)
        self.assertEqual(img.getpixel((48, 4)), self._FG_HALF)


class TestAddIconMargin(unittest.TestCase):
    """Tests for the transparent margin around the finished tray icon.

    The icon is drawn edge to edge, so in the panel its bars run into the
    neighbouring tray icons and read as one continuous strip.  The margin
    is presentation, not drawing: it insets the finished image, leaving
    the icon geometry and its colors exactly as create_icon_image made
    them.
    """

    def _margin_px(self, percent):
        return round(tray_icon_mod.ICON_SIZE * percent / 100)

    def _opaque(self):
        """A fully painted canvas - any inset shows up as a transparent frame."""
        return Image.new('RGBA', (tray_icon_mod.ICON_SIZE, tray_icon_mod.ICON_SIZE), (255, 0, 0, 255))

    def test_content_is_inset_on_every_side(self):
        with patch.object(tray_icon_mod, 'ICON_MARGIN', 10):
            bbox = tray_icon_mod.add_icon_margin(self._opaque()).getbbox()

        margin = self._margin_px(10)
        self.assertEqual(bbox, (margin, margin, tray_icon_mod.ICON_SIZE - margin, tray_icon_mod.ICON_SIZE - margin))

    def test_canvas_size_is_unchanged(self):
        """The margin insets the drawing, it does not grow the canvas."""
        with patch.object(tray_icon_mod, 'ICON_MARGIN', 10):
            framed = tray_icon_mod.add_icon_margin(self._opaque())

        self.assertEqual(framed.size, (tray_icon_mod.ICON_SIZE, tray_icon_mod.ICON_SIZE))

    def test_zero_margin_returns_the_image_untouched(self):
        """Zero restores the edge-to-edge icon with no resampling at all."""
        original = self._opaque()
        with patch.object(tray_icon_mod, 'ICON_MARGIN', 0):
            self.assertIs(tray_icon_mod.add_icon_margin(original), original)

    def test_larger_margin_leaves_less_content(self):
        with patch.object(tray_icon_mod, 'ICON_MARGIN', 20):
            wide = tray_icon_mod.add_icon_margin(self._opaque()).getbbox()
        with patch.object(tray_icon_mod, 'ICON_MARGIN', 5):
            narrow = tray_icon_mod.add_icon_margin(self._opaque()).getbbox()

        self.assertGreater(wide[0], narrow[0])

    def test_drawn_icon_keeps_its_shape_inside_the_frame(self):
        """A real icon still fills the inset box edge to edge."""
        with patch.object(tray_icon_mod, 'ICON_MARGIN', 10):
            bbox = tray_icon_mod.add_icon_margin(tray_icon_mod.create_icon_image(6, 3)).getbbox()

        margin = self._margin_px(10)
        self.assertEqual(bbox, (margin, margin, tray_icon_mod.ICON_SIZE - margin, tray_icon_mod.ICON_SIZE - margin))


class TestIconGlyphNearExhaustion(unittest.TestCase):
    """Tests for the percentage glyph just below 100% utilization."""

    def test_99_5_to_99_99_renders_like_99(self):
        """Utilization in [99.5, 100) must not round up to a three-digit '100'
        that overflows the 64 px canvas (and reads as exhausted) - it renders
        exactly like 99%."""
        reference = tray_icon_mod.create_icon_image(99.0, 10.0)
        for pct in (99.5, 99.9, 99.99):
            with self.subTest(pct=pct):
                img = tray_icon_mod.create_icon_image(pct, 10.0)
                self.assertEqual(img.tobytes(), reference.tobytes())

    def test_100_renders_exhausted_glyph(self):
        """At exactly 100% the exhausted glyph replaces the number."""
        img = tray_icon_mod.create_icon_image(100.0, 10.0)
        reference = tray_icon_mod.create_icon_image(99.0, 10.0)
        self.assertNotEqual(img.tobytes(), reference.tobytes())


class TestLoadFont(unittest.TestCase):
    """Tests for load_font()."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.object(tray_icon_mod, '_fc_match', return_value='/fonts/DejaVuSans-Bold.ttf')
    def test_loads_fc_match_result_for_normal_text(self, mock_fc, mock_image_font):
        """Default call loads the fontconfig match for the bold text face."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        result = tray_icon_mod.load_font(42)

        self.assertIs(result, mock_font)
        mock_fc.assert_called_once_with(tray_icon_mod._FONT_PATTERN)
        mock_image_font.truetype.assert_called_once_with('/fonts/DejaVuSans-Bold.ttf', 42)

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.object(tray_icon_mod, '_fc_match', return_value='/fonts/NotoSansSymbols2-Regular.ttf')
    def test_loads_symbol_pattern_for_symbol_text(self, mock_fc, mock_image_font):
        """symbol=True resolves the symbol font pattern."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        result = tray_icon_mod.load_font(36, symbol=True)

        self.assertIs(result, mock_font)
        mock_fc.assert_called_once_with(tray_icon_mod._SYMBOL_PATTERN)
        mock_image_font.truetype.assert_called_once_with('/fonts/NotoSansSymbols2-Regular.ttf', 36)

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.object(tray_icon_mod, '_fc_match', return_value=None)
    def test_falls_back_to_default_when_all_fail(self, _mock_fc, mock_image_font):
        """Falls back to load_default() when no TrueType font can be opened."""
        mock_image_font.truetype.side_effect = OSError
        mock_default = MagicMock()
        mock_image_font.load_default.return_value = mock_default

        result = tray_icon_mod.load_font(42)

        self.assertIs(result, mock_default)
        mock_image_font.load_default.assert_called_once()

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.object(tray_icon_mod, '_fc_match', return_value='/fonts/missing.ttf')
    def test_tries_fallback_paths_on_failure(self, _mock_fc, mock_image_font):
        """Tries the known fallback paths when the fc-match file fails to open."""
        mock_font = MagicMock()
        mock_image_font.truetype.side_effect = [OSError, mock_font]

        result = tray_icon_mod.load_font(42)

        self.assertIs(result, mock_font)
        self.assertEqual(mock_image_font.truetype.call_count, 2)
        mock_image_font.truetype.assert_called_with(tray_icon_mod._FONT_FALLBACK_PATHS[0], 42)

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.object(tray_icon_mod, '_fc_match', return_value=None)
    def test_fc_match_failure_uses_fallback_paths(self, _mock_fc, mock_image_font):
        """A failing fc-match run goes straight to the direct fallback paths."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        result = tray_icon_mod.load_font(42)

        self.assertIs(result, mock_font)
        mock_image_font.truetype.assert_called_once_with(tray_icon_mod._FONT_FALLBACK_PATHS[0], 42)

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.object(tray_icon_mod, '_fc_match', return_value='/fonts/DejaVuSans-Bold.ttf')
    def test_lru_cache_returns_same_instance(self, _mock_fc, mock_image_font):
        """Cached: same size returns same font object without second truetype call."""
        mock_font = MagicMock()
        mock_image_font.truetype.return_value = mock_font

        first = tray_icon_mod.load_font(42)
        second = tray_icon_mod.load_font(42)

        self.assertIs(first, second)
        mock_image_font.truetype.assert_called_once()

    @patch.object(tray_icon_mod, 'ImageFont')
    @patch.object(tray_icon_mod, '_fc_match', return_value='/fonts/DejaVuSans-Bold.ttf')
    def test_different_sizes_cached_separately(self, _mock_fc, mock_image_font):
        """Different sizes produce separate cache entries."""
        mock_image_font.truetype.return_value = MagicMock()

        tray_icon_mod.load_font(36)
        tray_icon_mod.load_font(42)

        self.assertEqual(mock_image_font.truetype.call_count, 2)


class TestFcMatch(unittest.TestCase):
    """Tests for _fc_match() - the fontconfig resolver seam."""

    @patch.object(tray_icon_mod, 'subprocess')
    def test_returns_stdout_path(self, mock_subprocess):
        """A successful fc-match run yields the printed file path."""
        mock_subprocess.run.return_value = MagicMock(returncode=0, stdout='/fonts/x.ttf\n')

        self.assertEqual(tray_icon_mod._fc_match('DejaVu Sans:bold'), '/fonts/x.ttf')

    @patch.object(tray_icon_mod, 'subprocess')
    def test_nonzero_exit_returns_none(self, mock_subprocess):
        mock_subprocess.run.return_value = MagicMock(returncode=1, stdout='')

        self.assertIsNone(tray_icon_mod._fc_match('DejaVu Sans:bold'))

    @patch.object(tray_icon_mod, 'subprocess')
    def test_missing_binary_returns_none(self, mock_subprocess):
        mock_subprocess.run.side_effect = FileNotFoundError

        self.assertIsNone(tray_icon_mod._fc_match('DejaVu Sans:bold'))

    @patch.object(tray_icon_mod, 'subprocess')
    def test_empty_output_returns_none(self, mock_subprocess):
        mock_subprocess.run.return_value = MagicMock(returncode=0, stdout='')

        self.assertIsNone(tray_icon_mod._fc_match('DejaVu Sans:bold'))


class TestTaskbarUsesLightTheme(unittest.TestCase):
    """Tests for taskbar_uses_light_theme()."""

    def _settings(self, prefer_dark, theme_name):
        settings = MagicMock()
        values = {
            'gtk-application-prefer-dark-theme': prefer_dark,
            'gtk-theme-name': theme_name,
        }
        settings.get_property.side_effect = lambda name: values[name]
        return settings

    @patch.object(tray_icon_mod, '_gtk_settings')
    def test_returns_true_for_light_theme(self, mock_settings_fn):
        """A non-dark theme without the dark preference is light."""
        mock_settings_fn.return_value = self._settings(False, 'Adwaita')

        self.assertTrue(tray_icon_mod.taskbar_uses_light_theme())

    @patch.object(tray_icon_mod, '_gtk_settings')
    def test_returns_false_when_prefer_dark_set(self, mock_settings_fn):
        """The prefer-dark flag wins regardless of the theme name."""
        mock_settings_fn.return_value = self._settings(True, 'Adwaita')

        self.assertFalse(tray_icon_mod.taskbar_uses_light_theme())

    @patch.object(tray_icon_mod, '_gtk_settings')
    def test_returns_false_for_dark_theme_name(self, mock_settings_fn):
        """A theme whose name carries 'dark' is treated as dark."""
        mock_settings_fn.return_value = self._settings(False, 'Mint-Y-Dark-Aqua')

        self.assertFalse(tray_icon_mod.taskbar_uses_light_theme())

    @patch.object(tray_icon_mod, '_gtk_settings')
    def test_returns_true_for_empty_theme_name(self, mock_settings_fn):
        """A missing theme name without the dark preference stays light."""
        mock_settings_fn.return_value = self._settings(False, None)

        self.assertTrue(tray_icon_mod.taskbar_uses_light_theme())

    @patch.object(tray_icon_mod, '_gtk_settings', return_value=None)
    def test_returns_false_without_gtk(self, _mock_settings_fn):
        """Without a GTK stack the theme defaults to dark."""
        self.assertFalse(tray_icon_mod.taskbar_uses_light_theme())

    @patch.object(tray_icon_mod, '_gtk_settings')
    def test_returns_false_on_property_error(self, mock_settings_fn):
        """A failing property read defaults to dark."""
        settings = MagicMock()
        settings.get_property.side_effect = TypeError
        mock_settings_fn.return_value = settings

        self.assertFalse(tray_icon_mod.taskbar_uses_light_theme())


def _real_font():
    """Return a real PIL font for rendering tests."""
    from PIL import ImageFont

    try:
        return ImageFont.truetype('arial.ttf', 20)
    except OSError:
        return ImageFont.load_default()


class TestCreateIconImage(unittest.TestCase):
    """Tests for create_icon_image()."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    def test_returns_64x64_rgba_image(self):
        """Icon is always 64x64 RGBA."""
        img = tray_icon_mod.create_icon_image(0, 0)

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    def test_low_usage_renders_without_error(self):
        """Usage <= 50% renders successfully."""
        img = tray_icon_mod.create_icon_image(30, 20)

        self.assertEqual(img.size, (64, 64))

    def test_high_usage_renders_without_error(self):
        """Usage > 50% renders successfully."""
        img = tray_icon_mod.create_icon_image(75, 20)

        self.assertEqual(img.size, (64, 64))

    def test_full_usage_renders_without_error(self):
        """Usage >= 100% renders successfully."""
        img = tray_icon_mod.create_icon_image(100, 20)

        self.assertEqual(img.size, (64, 64))

    def test_dark_and_light_taskbar_produce_different_images(self):
        """Dark vs light taskbar produces different pixel data."""
        img_dark = tray_icon_mod.create_icon_image(50, 50, light_taskbar=False)
        img_light = tray_icon_mod.create_icon_image(50, 50, light_taskbar=True)

        self.assertEqual(img_dark.size, (64, 64))
        self.assertEqual(img_light.size, (64, 64))
        self.assertNotEqual(img_dark.tobytes(), img_light.tobytes())

    def test_zero_usage_no_bar_fill(self):
        """Zero usage has no filled bar pixels beyond the half-tone background."""
        img = tray_icon_mod.create_icon_image(0, 0)

        self.assertEqual(img.size, (64, 64))

    def test_full_bar_fill_at_100_percent(self):
        """100% usage fills the entire bar width."""
        img_full = tray_icon_mod.create_icon_image(100, 100)
        img_zero = tray_icon_mod.create_icon_image(0, 0)

        # The bar area pixels should differ between 0% and 100%
        self.assertNotEqual(img_full.tobytes(), img_zero.tobytes())

    def test_boundary_zero_differs_from_one(self):
        """0% (shows 'C') and 1% (shows percentage) produce different icons."""
        img_zero = tray_icon_mod.create_icon_image(0, 0)
        img_one = tray_icon_mod.create_icon_image(1, 0)

        self.assertNotEqual(img_zero.tobytes(), img_one.tobytes())

    @patch.object(tray_icon_mod, 'load_font')
    def test_zero_usage_calls_font_size_42(self, mock_font):
        """Usage of 0% requests size 42 font for 'C' letter."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(0, 0)

        mock_font.assert_any_call(42)

    @patch.object(tray_icon_mod, 'load_font')
    def test_nonzero_usage_calls_font_size_40(self, mock_font):
        """Any usage > 0% requests size 40 font for percentage."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(30, 20)

        mock_font.assert_any_call(40)

    @patch.object(tray_icon_mod, 'load_font')
    def test_full_usage_calls_symbol_font(self, mock_font):
        """Usage >= 100% requests size 36 symbol font for cross."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 20)

        mock_font.assert_any_call(36, symbol=True)

    @patch.object(tray_icon_mod, 'load_font')
    def test_bottom_bar_at_100_also_triggers_cross(self, mock_font):
        """Bottom bar at 100% triggers the cross glyph even when top bar is low."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(20, 100)

        mock_font.assert_any_call(36, symbol=True)

    @patch.object(tray_icon_mod, 'load_font')
    def test_extra_usage_available_shows_dollar_when_exhausted(self, mock_font):
        """When a quota is exhausted but paid extra-usage is available, show '$' instead of '✕'."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 20, extra_usage_available=True)

        # Dollar sign uses the regular size-42 font, not the symbol font
        mock_font.assert_any_call(42)
        self.assertNotIn(call(36, symbol=True), mock_font.call_args_list)

    @patch.object(tray_icon_mod, 'load_font')
    def test_extra_usage_available_irrelevant_when_no_quota_exhausted(self, mock_font):
        """extra_usage_available has no effect while every quota is below 100%."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(75, 20, extra_usage_available=True)

        # Still shows the percentage, not '$'
        mock_font.assert_any_call(40)

    def test_dollar_and_cross_states_produce_different_images(self):
        """'$' (extra usage available) and '✕' (fully blocked) render differently."""
        img_cross = tray_icon_mod.create_icon_image(100, 20, extra_usage_available=False)
        img_dollar = tray_icon_mod.create_icon_image(100, 20, extra_usage_available=True)

        self.assertNotEqual(img_cross.tobytes(), img_dollar.tobytes())


class TestCreateIconImageOverageMode(unittest.TestCase):
    """Tests for create_icon_image() overage-mode bars.

    Overage mode shows how far usage has gone into the over-budget zone.
    The bar is empty when pct <= time_pct (on pace or ahead), and full when
    pct reaches 100%. Formula: fill_ratio = clamp((pct - time_pct) / (100 - time_pct), 0, 1)
    """

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    def test_overage_mode_returns_64x64_rgba(self):
        """Overage mode still produces a 64x64 RGBA image."""
        img = tray_icon_mod.create_icon_image(80, 80, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    def test_overage_mode_time_pct_at_100_keeps_overage_reading(self):
        """time_pct=100 (stale window right after a reset) keeps the overage
        reading - usage below the limit stays an empty bar instead of jumping
        to the linear utilization fill until the confirming poll arrives."""
        img_end_state = tray_icon_mod.create_icon_image(50, 50, mode_top='overage', mode_bottom='overage', time_pct_top=100, time_pct_bottom=100)
        img_on_pace = tray_icon_mod.create_icon_image(50, 50, mode_top='overage', mode_bottom='overage', time_pct_top=50, time_pct_bottom=50)

        self.assertEqual(img_end_state.tobytes(), img_on_pace.tobytes())

    def test_on_pace_produces_empty_bar(self):
        """Usage exactly at time_pct means on pace - bar pixels are not fully opaque (no fill)."""
        # pct=60, time_pct=60 -> overage=0 -> fill_ratio=0 -> no fill
        img = tray_icon_mod.create_icon_image(60, 60, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        S = 64
        bar_h = 9
        gap = 3
        bar2_y = S - bar_h
        bar1_y = bar2_y - gap - bar_h
        pixels = img.load()
        for bar_y in (bar1_y, bar2_y):
            mid_y = bar_y + bar_h // 2
            # No pixel in the bar should be fully opaque (fill_w=0)
            self.assertNotEqual(pixels[0, mid_y][3], 255, f'Expected no fill at x=0, y={mid_y}')

    def test_below_pace_produces_empty_bar(self):
        """Usage below time_pct (ahead of schedule) also produces an empty bar."""
        # pct=40 < time_pct=60 -> overage=0 -> no fill; same result as pct=60
        S = 64
        bar_h = 9
        gap = 3
        bar2_y = S - bar_h
        bar1_y = bar2_y - gap - bar_h

        img_ahead = tray_icon_mod.create_icon_image(40, 40, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)
        pixels = img_ahead.load()
        for bar_y in (bar1_y, bar2_y):
            mid_y = bar_y + bar_h // 2
            self.assertNotEqual(pixels[0, mid_y][3], 255, f'Expected no fill at x=0, y={mid_y}')

    def test_half_fill_at_midpoint_of_over_budget_range(self):
        """pct halfway between time_pct and 100% produces a half-filled bar."""
        # time_pct=60, pct=80 -> (80-60)/(100-60) = 0.5 -> fill_w = 32px
        img = tray_icon_mod.create_icon_image(80, 80, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        S = 64
        bar_h = 9
        gap = 3
        bar2_y = S - bar_h
        bar1_y = bar2_y - gap - bar_h
        pixels = img.load()
        for bar_y in (bar1_y, bar2_y):
            mid_y = bar_y + bar_h // 2
            # x=31 (last pixel of left half) should be filled (fg, alpha=255)
            self.assertEqual(pixels[31, mid_y][3], 255, f'Expected filled pixel at x=31, y={mid_y}')
            # x=32 (first pixel of right half) should not be filled (bg, alpha<255)
            self.assertNotEqual(pixels[32, mid_y][3], 255, f'Expected unfilled pixel at x=32, y={mid_y}')

    def test_full_bar_at_100_percent_usage(self):
        """100% usage fills the entire bar regardless of time_pct."""
        # time_pct=60, pct=100 -> (100-60)/(100-60) = 1.0 -> full bar
        img = tray_icon_mod.create_icon_image(100, 100, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        S = 64
        bar_h = 9
        gap = 3
        bar2_y = S - bar_h
        bar1_y = bar2_y - gap - bar_h
        pixels = img.load()
        for bar_y in (bar1_y, bar2_y):
            mid_y = bar_y + bar_h // 2
            self.assertEqual(pixels[S - 1, mid_y][3], 255, f'Expected fully filled bar at y={mid_y}')

    def test_mixed_modes_top_overage_bottom_utilization(self):
        """Top bar in overage mode, bottom bar in utilization mode produces valid image."""
        img = tray_icon_mod.create_icon_image(80, 50, mode_top='overage', mode_bottom='utilization', time_pct_top=60, time_pct_bottom=None)

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')


class TestCreateIconImageTimeMarker(unittest.TestCase):
    """Tests for the reset-time marker and warning fill on utilization-mode bars.

    The marker is a MARKER_WIDTH-wide vertical line in the icon foreground
    color, centered at the elapsed-time position, clamped to the icon bounds,
    and drawn only in utilization mode. The bar fill switches to the warning
    color (fg_warn) when usage is ahead of the elapsed time or fully
    exhausted, mirroring the popup's warning fill.
    """

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    @staticmethod
    def _bar_mid_rows():
        """Return the vertical center row of each bar."""
        bar2_y = tray_icon_mod.ICON_SIZE - tray_icon_mod.BAR_HEIGHT
        bar1_y = bar2_y - tray_icon_mod.BAR_GAP - tray_icon_mod.BAR_HEIGHT
        return (bar1_y + tray_icon_mod.BAR_HEIGHT // 2, bar2_y + tray_icon_mod.BAR_HEIGHT // 2)

    def test_marker_solid_on_unfilled_track(self):
        """Marker ahead of the fill is drawn in solid fg on the track."""
        # pct=20 -> fill ends at x=12; time_pct=50 -> marker at x=30..33
        img = tray_icon_mod.create_icon_image(20, 10, time_pct_top=50, time_pct_bottom=50)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[32, mid_y], fg, f'Expected solid marker pixel at x=32, y={mid_y}')

    def test_fill_plain_when_on_pace(self):
        """Usage at or below the elapsed time keeps the plain fg fill."""
        # pct=20 <= time_pct=50 -> no warning
        img = tray_icon_mod.create_icon_image(20, 20, time_pct_top=50, time_pct_bottom=50)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], fg, f'Expected plain fill pixel at x=5, y={mid_y}')

    def test_fill_warns_when_usage_ahead(self):
        """Usage ahead of the elapsed time switches the fill to fg_warn, marker stays fg."""
        # pct=70 -> fill ends at x=43; time_pct=40 -> marker at x=23..26 inside the fill
        img = tray_icon_mod.create_icon_image(70, 70, time_pct_top=40, time_pct_bottom=40)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        fg_half = tray_icon_mod.ICON_LIGHT['fg_half']
        fg_warn = tray_icon_mod.ICON_LIGHT['fg_warn']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], fg_warn, f'Expected warn fill pixel at x=5, y={mid_y}')
            self.assertEqual(pixels[24, mid_y], fg, f'Expected marker pixel inside fill at x=24, y={mid_y}')
            self.assertEqual(pixels[35, mid_y], fg_warn, f'Expected warn fill pixel at x=35, y={mid_y}')
            self.assertEqual(pixels[50, mid_y], fg_half, f'Expected track pixel at x=50, y={mid_y}')

    def test_fill_warns_at_full_usage(self):
        """100% usage warns even when the elapsed time is also at 100%."""
        # pct=100, time_pct=100 -> warn via the >=100 rule; marker at x=60..63
        img = tray_icon_mod.create_icon_image(100, 100, time_pct_top=100, time_pct_bottom=100)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        fg_warn = tray_icon_mod.ICON_LIGHT['fg_warn']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], fg_warn, f'Expected warn fill pixel at x=5, y={mid_y}')
            self.assertEqual(pixels[63, mid_y], fg, f'Expected marker pixel at x=63, y={mid_y}')

    def test_fill_warns_at_full_usage_without_time_pct(self):
        """100% usage warns even when no elapsed time is known (no marker drawn)."""
        img = tray_icon_mod.create_icon_image(100, 100)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        fg_warn = tray_icon_mod.ICON_LIGHT['fg_warn']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], fg_warn, f'Expected warn fill pixel at x=5, y={mid_y}')
            for x in range(64):
                self.assertNotEqual(pixels[x, mid_y], fg, f'Unexpected marker pixel at x={x}, y={mid_y}')

    def test_marker_at_fill_edge_stays_solid(self):
        """Usage exactly at the elapsed time keeps a plain fill with a solid fg marker."""
        # pct=50 -> fill ends at x=32; time_pct=50 -> marker at x=30..33; no warning (strictly greater)
        img = tray_icon_mod.create_icon_image(50, 50, time_pct_top=50, time_pct_bottom=50)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], fg, f'Expected plain fill pixel at x=5, y={mid_y}')
            for x in range(30, 34):
                self.assertEqual(pixels[x, mid_y], fg, f'Expected solid marker pixel at x={x}, y={mid_y}')

    def test_no_marker_without_time_pct(self):
        """time_pct=None leaves the unfilled track translucent everywhere."""
        # pct=20 -> fill ends at x=12; everything beyond must stay fg_half
        img = tray_icon_mod.create_icon_image(20, 10)

        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            for x in range(13, 64):
                self.assertNotEqual(pixels[x, mid_y][3], 255, f'Unexpected solid pixel at x={x}, y={mid_y}')

    def test_marker_clamped_at_period_start(self):
        """time_pct=0 keeps the marker inside the left icon edge."""
        img = tray_icon_mod.create_icon_image(0, 0, time_pct_top=0, time_pct_bottom=0)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[0, mid_y], fg, f'Expected marker pixel at x=0, y={mid_y}')

    def test_marker_clamped_at_period_end(self):
        """time_pct=100 keeps the marker inside the right icon edge."""
        img = tray_icon_mod.create_icon_image(0, 0, time_pct_top=100, time_pct_bottom=100)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[63, mid_y], fg, f'Expected marker pixel at x=63, y={mid_y}')

    def test_overage_mode_draws_no_marker_and_no_warn(self):
        """Overage mode encodes pace in the fill itself - no marker, no warning color."""
        # pct=80, time_pct=50 -> overage fill ends at x=38; a marker would sit at x=30..33
        img = tray_icon_mod.create_icon_image(80, 80, mode_top='overage', mode_bottom='overage', time_pct_top=50, time_pct_bottom=50)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            for x in range(30, 34):
                self.assertEqual(pixels[x, mid_y], fg, f'Expected plain fill pixel at x={x}, y={mid_y}')

    def test_marker_uses_light_taskbar_palette(self):
        """Light taskbar draws the marker with the ICON_DARK palette."""
        img = tray_icon_mod.create_icon_image(20, 10, light_taskbar=True, time_pct_top=50, time_pct_bottom=50)

        fg = tray_icon_mod.ICON_DARK['fg']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[32, mid_y], fg, f'Expected marker pixel at x=32, y={mid_y}')

    def test_fill_warns_on_light_taskbar(self):
        """Light taskbar uses the ICON_DARK palette: warn fill with the fg marker on top."""
        # pct=100 -> full fill in fg_warn; time_pct=50 -> marker at x=30..33 in fg
        img = tray_icon_mod.create_icon_image(100, 100, light_taskbar=True, time_pct_top=50, time_pct_bottom=50)

        fg = tray_icon_mod.ICON_DARK['fg']
        fg_warn = tray_icon_mod.ICON_DARK['fg_warn']
        pixels = img.load()
        for mid_y in self._bar_mid_rows():
            self.assertEqual(pixels[32, mid_y], fg, f'Expected marker pixel at x=32, y={mid_y}')
            self.assertEqual(pixels[5, mid_y], fg_warn, f'Expected warn fill pixel at x=5, y={mid_y}')


class TestCreateIconImageNumbersStyle(unittest.TestCase):
    """Tests for create_icon_image() with the 'numbers' icon style.

    The style replaces the big-number-plus-bars layout with two stacked
    percentage rows: row 1 shows pct_top, row 2 shows pct_bottom.  Each row
    applies the classic glyph rules per row (✕/$ when exhausted, clamp to
    99) and is always drawn in fg, like the classic glyph.
    """

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()
        patcher = patch.object(tray_icon_mod, 'ICON_STYLE', 'numbers')
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    @staticmethod
    def _row_ranges():
        """Return the y ranges of the top and bottom number rows."""
        row_h = tray_icon_mod.NUMBER_ROW_HEIGHT
        return (range(0, row_h), range(row_h, 2 * row_h))

    @staticmethod
    def _region_has_color(img, y_range, color):
        """Return True if any pixel in the given rows matches *color* exactly."""
        pixels = img.load()
        for y in y_range:
            for x in range(tray_icon_mod.ICON_SIZE):
                if pixels[x, y] == color:
                    return True
        return False

    def test_returns_64x64_rgba_image(self):
        """Numbers style still produces a 64x64 RGBA image."""
        img = tray_icon_mod.create_icon_image(47, 82)

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    def test_no_bar_track_drawn(self):
        """The bar zones stay transparent - no fg_half track is drawn."""
        img = tray_icon_mod.create_icon_image(50, 50)

        pixels = img.load()
        for y in (48, 59):
            self.assertEqual(pixels[0, y][3], 0, f'Expected transparent pixel at x=0, y={y}')

    @patch.object(tray_icon_mod, 'load_font')
    def test_rows_use_font_40(self, mock_font):
        """Both rows request the size 40 digit font - the same size as the classic single number."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(30, 20)

        mock_font.assert_any_call(40)

    @patch.object(tray_icon_mod, 'load_font')
    def test_both_rows_zero_shows_single_c(self, mock_font):
        """Both fields at 0% collapse to the single idle 'C' (size 42)."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(0, 0)

        mock_font.assert_any_call(42)
        self.assertNotIn(call(40), mock_font.call_args_list)

    @patch.object(tray_icon_mod, 'load_font')
    def test_zero_row_beside_nonzero_shows_zero_digit(self, mock_font):
        """A single zero row renders '0' - only both-zero collapses to 'C'."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(0, 50)

        mock_font.assert_any_call(40)
        self.assertNotIn(call(42), mock_font.call_args_list)

    @patch.object(tray_icon_mod, 'load_font')
    def test_fractional_usage_shows_rows_not_idle_c(self, mock_font):
        """Usage in (0, 0.5) renders two '0' rows - only exactly zero collapses to 'C'."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(0.3, 0.3)

        mock_font.assert_any_call(40)
        self.assertNotIn(call(42), mock_font.call_args_list)

    @patch.object(tray_icon_mod, 'load_font')
    def test_exhausted_row_uses_symbol_font(self, mock_font):
        """An exhausted row without extra credits requests the size 34 symbol font for '✕'."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 20)

        mock_font.assert_any_call(34, symbol=True)

    @patch.object(tray_icon_mod, 'load_font')
    def test_exhausted_row_with_extra_usage_shows_dollar(self, mock_font):
        """With paid extra usage available the exhausted row shows '$' instead of '✕'."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 20, extra_usage_available=True)

        mock_font.assert_any_call(32)
        self.assertNotIn(call(34, symbol=True), mock_font.call_args_list)

    @patch.object(tray_icon_mod, 'load_font')
    def test_both_rows_exhausted_shows_single_large_cross(self, mock_font):
        """Both quotas exhausted collapse to one full-size '✕' instead of two half-size ones."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 100)

        mock_font.assert_any_call(36, symbol=True)
        self.assertNotIn(call(34, symbol=True), mock_font.call_args_list)

    @patch.object(tray_icon_mod, 'load_font')
    def test_both_rows_exhausted_with_extra_usage_shows_single_large_dollar(self, mock_font):
        """Both quotas exhausted with extra usage collapse to one full-size '$'."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_icon_image(100, 100, extra_usage_available=True)

        mock_font.assert_any_call(42)
        self.assertNotIn(call(32), mock_font.call_args_list)

    def test_top_row_unaffected_by_bottom_exhaustion(self):
        """Exhaustion applies per row - the top row keeps its number when only the bottom is exhausted."""
        img_exhausted = tray_icon_mod.create_icon_image(20, 100)
        img_normal = tray_icon_mod.create_icon_image(20, 5)

        row_h = tray_icon_mod.NUMBER_ROW_HEIGHT
        top_exhausted = img_exhausted.crop((0, 0, 64, row_h)).tobytes()
        top_normal = img_normal.crop((0, 0, 64, row_h)).tobytes()
        self.assertEqual(top_exhausted, top_normal)

    def test_rows_stay_fg_when_ahead_of_time(self):
        """Rows are always drawn in fg - being ahead of the elapsed time does not recolor them."""
        # top: 70% used at 40% elapsed - would warn on a bar, but not here
        img = tray_icon_mod.create_icon_image(70, 20, time_pct_top=40, time_pct_bottom=40)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        fg_warn = tray_icon_mod.ICON_LIGHT['fg_warn']
        top_rows, bottom_rows = self._row_ranges()
        self.assertTrue(self._region_has_color(img, top_rows, fg), 'Expected fg digits in the top row')
        self.assertTrue(self._region_has_color(img, bottom_rows, fg), 'Expected fg digits in the bottom row')
        self.assertFalse(self._region_has_color(img, range(0, 64), fg_warn), 'Unexpected fg_warn pixels in numbers style')

    def test_exhausted_glyph_drawn_in_fg(self):
        """The exhausted '✕' is drawn in fg, matching the classic glyph."""
        img = tray_icon_mod.create_icon_image(100, 20)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        fg_warn = tray_icon_mod.ICON_LIGHT['fg_warn']
        top_rows, _bottom_rows = self._row_ranges()
        self.assertTrue(self._region_has_color(img, top_rows, fg), 'Expected fg glyph in the exhausted top row')
        self.assertFalse(self._region_has_color(img, range(0, 64), fg_warn), 'Unexpected fg_warn pixels in numbers style')

    def test_time_pct_has_no_effect(self):
        """Elapsed-time values do not change the numbers-style rendering."""
        img_with_time = tray_icon_mod.create_icon_image(70, 20, time_pct_top=40, time_pct_bottom=40)
        img_without_time = tray_icon_mod.create_icon_image(70, 20)

        self.assertEqual(img_with_time.tobytes(), img_without_time.tobytes())

    def test_99_5_renders_like_99_per_row(self):
        """Utilization in [99.5, 100) clamps to '99' in both rows."""
        reference_top = tray_icon_mod.create_icon_image(99.0, 10.0)
        reference_bottom = tray_icon_mod.create_icon_image(10.0, 99.0)
        for pct in (99.5, 99.9, 99.99):
            with self.subTest(pct=pct):
                self.assertEqual(tray_icon_mod.create_icon_image(pct, 10.0).tobytes(), reference_top.tobytes())
                self.assertEqual(tray_icon_mod.create_icon_image(10.0, pct).tobytes(), reference_bottom.tobytes())

    def test_overage_mode_suffix_ignored(self):
        """The overage bar mode has no effect in numbers style."""
        img_overage = tray_icon_mod.create_icon_image(70, 20, mode_top='overage', time_pct_top=40, time_pct_bottom=40)
        img_plain = tray_icon_mod.create_icon_image(70, 20, time_pct_top=40, time_pct_bottom=40)

        self.assertEqual(img_overage.tobytes(), img_plain.tobytes())

    def test_light_taskbar_uses_dark_palette(self):
        """Light taskbar draws the digits with the ICON_DARK palette."""
        img = tray_icon_mod.create_icon_image(50, 50, light_taskbar=True)

        fg = tray_icon_mod.ICON_DARK['fg']
        top_rows, bottom_rows = self._row_ranges()
        self.assertTrue(self._region_has_color(img, top_rows, fg), 'Expected ICON_DARK fg digits in the top row')
        self.assertTrue(self._region_has_color(img, bottom_rows, fg), 'Expected ICON_DARK fg digits in the bottom row')


# Level palette for the icon_color_levels tests - deliberately distinct from
# the default fg (white), fg_half, and fg_warn values.
_GREEN = (30, 160, 60, 255)
_AMBER = (230, 170, 40, 255)
_RED = (200, 30, 30, 255)
_LEVELS = [(0.0, _GREEN), (70.0, _AMBER), (90.0, _RED)]


def _bar_mid_rows():
    """Return the vertical center row of each classic-style bar."""
    bar2_y = tray_icon_mod.ICON_SIZE - tray_icon_mod.BAR_HEIGHT
    bar1_y = bar2_y - tray_icon_mod.BAR_GAP - tray_icon_mod.BAR_HEIGHT
    return (bar1_y + tray_icon_mod.BAR_HEIGHT // 2, bar2_y + tray_icon_mod.BAR_HEIGHT // 2)


def _region_has_color(img, y_range, color):
    """Return True if any pixel in the given rows matches *color* exactly."""
    pixels = img.load()
    for y in y_range:
        for x in range(tray_icon_mod.ICON_SIZE):
            if pixels[x, y] == color:
                return True
    return False


class TestLevelColor(unittest.TestCase):
    """Tests for _level_color() - the icon_color_levels selection rule.

    The active color is the pair with the highest threshold <= pct; a pct
    below the lowest threshold, or no configured levels at all, keeps the
    base fg.
    """

    _FG = (255, 255, 255, 255)

    def test_unset_levels_return_fg(self):
        """With no levels configured every pct keeps the base fg."""
        with patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', None):
            for pct in (0, 50, 100):
                self.assertEqual(tray_icon_mod._level_color(pct, self._FG), self._FG)

    def test_empty_levels_return_fg(self):
        """An empty (but configured) level list behaves like unset."""
        with patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', []):
            self.assertEqual(tray_icon_mod._level_color(50, self._FG), self._FG)

    def test_below_lowest_threshold_returns_fg(self):
        """A pct below the lowest threshold keeps the base fg."""
        with patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', [(10.0, _GREEN), (70.0, _AMBER)]):
            self.assertEqual(tray_icon_mod._level_color(9.9, self._FG), self._FG)
            self.assertEqual(tray_icon_mod._level_color(0, self._FG), self._FG)

    def test_exact_threshold_activates_level(self):
        """A pct exactly at a threshold selects that level's color."""
        with patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', _LEVELS):
            self.assertEqual(tray_icon_mod._level_color(0, self._FG), _GREEN)
            self.assertEqual(tray_icon_mod._level_color(70, self._FG), _AMBER)
            self.assertEqual(tray_icon_mod._level_color(90, self._FG), _RED)

    def test_between_thresholds_uses_lower_level(self):
        """A pct between two thresholds selects the lower pair's color."""
        with patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', _LEVELS):
            self.assertEqual(tray_icon_mod._level_color(35, self._FG), _GREEN)
            self.assertEqual(tray_icon_mod._level_color(89.9, self._FG), _AMBER)

    def test_above_highest_threshold_uses_highest_level(self):
        """Any pct at or above the highest threshold keeps the last color."""
        with patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', _LEVELS):
            self.assertEqual(tray_icon_mod._level_color(95, self._FG), _RED)
            self.assertEqual(tray_icon_mod._level_color(100, self._FG), _RED)
            self.assertEqual(tray_icon_mod._level_color(150, self._FG), _RED)


class TestIconColorLevelsRendering(unittest.TestCase):
    """Tests for icon_color_levels tinting in the classic 'number+bars' style."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()
        patcher = patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', _LEVELS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    def test_each_bar_tinted_by_its_own_pct(self):
        """The fill of every bar follows the level for that bar's own pct."""
        img = tray_icon_mod.create_icon_image(75, 20)

        pixels = img.load()
        mid1, mid2 = _bar_mid_rows()
        self.assertEqual(pixels[5, mid1], _AMBER, 'Expected level color for pct=75 in the top bar')
        self.assertEqual(pixels[5, mid2], _GREEN, 'Expected level color for pct=20 in the bottom bar')

    def test_track_beyond_fill_keeps_fg_half(self):
        """The half-tone background track keeps its base color."""
        img = tray_icon_mod.create_icon_image(20, 20)

        fg_half = tray_icon_mod.ICON_LIGHT['fg_half']
        pixels = img.load()
        for mid_y in _bar_mid_rows():
            self.assertEqual(pixels[50, mid_y], fg_half)

    def test_levels_take_precedence_over_fg_warn(self):
        """Usage ahead of the elapsed time uses the level color, not fg_warn."""
        # pct=70 ahead of time_pct=40 - fg_warn would apply without levels
        img = tray_icon_mod.create_icon_image(70, 70, time_pct_top=40, time_pct_bottom=40)

        fg_warn = tray_icon_mod.ICON_LIGHT['fg_warn']
        pixels = img.load()
        for mid_y in _bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], _AMBER, 'Expected level color instead of fg_warn')
        self.assertFalse(_region_has_color(img, range(64), fg_warn), 'Unexpected fg_warn pixels with levels configured')

    def test_levels_take_precedence_over_fg_warn_at_exhaustion(self):
        """A fully exhausted bar uses the level color, not fg_warn."""
        img = tray_icon_mod.create_icon_image(100, 100, time_pct_top=50, time_pct_bottom=50)

        pixels = img.load()
        for mid_y in _bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], _RED)

    def test_marker_stays_fg_over_tinted_fill(self):
        """The reset-time marker keeps the base fg on top of a tinted fill."""
        # pct=70 -> fill ends at x=43; time_pct=40 -> marker at x=23..26
        img = tray_icon_mod.create_icon_image(70, 70, time_pct_top=40, time_pct_bottom=40)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        pixels = img.load()
        for mid_y in _bar_mid_rows():
            self.assertEqual(pixels[24, mid_y], fg, 'Expected fg marker pixel inside the tinted fill')

    def test_glyph_tinted_by_top_pct(self):
        """The percentage glyph follows the level color for the top field's pct."""
        img = tray_icon_mod.create_icon_image(75, 20)

        glyph_rows = range(0, 40)  # bars start at y=43
        self.assertTrue(_region_has_color(img, glyph_rows, _AMBER), 'Expected tinted digits in the glyph area')

    def test_exhausted_glyph_stays_fg(self):
        """The exhausted '✕' keeps the base fg - only digits are tinted."""
        img = tray_icon_mod.create_icon_image(100, 20)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        glyph_rows = range(0, 40)
        self.assertTrue(_region_has_color(img, glyph_rows, fg), 'Expected fg exhausted glyph')
        self.assertFalse(_region_has_color(img, glyph_rows, _RED), 'Exhausted glyph must not be tinted')

    def test_idle_c_glyph_stays_fg(self):
        """The idle 'C' keeps the base fg even with a threshold-0 level."""
        img = tray_icon_mod.create_icon_image(0, 0)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        glyph_rows = range(0, 40)
        self.assertTrue(_region_has_color(img, glyph_rows, fg), 'Expected fg idle glyph')
        self.assertFalse(_region_has_color(img, glyph_rows, _GREEN), 'Idle glyph must not be tinted')

    def test_same_levels_apply_on_light_taskbar(self):
        """Configured colors are absolute - the light panel uses them too."""
        img = tray_icon_mod.create_icon_image(75, 20, light_taskbar=True)

        pixels = img.load()
        mid1, mid2 = _bar_mid_rows()
        self.assertEqual(pixels[5, mid1], _AMBER)
        self.assertEqual(pixels[5, mid2], _GREEN)

    def test_overage_bar_colored_by_pct(self):
        """Overage-mode fills use the level color for the same pct value."""
        # time_pct=60, pct=80 -> half-filled bar, tinted for pct=80
        img = tray_icon_mod.create_icon_image(80, 80, mode_top='overage', mode_bottom='overage', time_pct_top=60, time_pct_bottom=60)

        pixels = img.load()
        for mid_y in _bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], _AMBER)

    def test_overage_stale_window_full_bar_colored_by_pct(self):
        """The stale-window exhausted full bar is tinted for pct=100."""
        img = tray_icon_mod.create_icon_image(100, 100, mode_top='overage', mode_bottom='overage', time_pct_top=100, time_pct_bottom=100)

        pixels = img.load()
        for mid_y in _bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], _RED)

    def test_hex_and_rgba_settings_forms_render_identically(self):
        """'#rrggbb' strings and [R, G, B, A] arrays normalize to the same levels."""
        import usage_monitor_for_claude.settings as settings_mod

        hex_levels = settings_mod._parse_icon_color_levels([[0, '#1ea03c'], [70, '#e6aa28'], [90, '#c81e1e']])
        rgba_levels = settings_mod._parse_icon_color_levels(
            [[0, [30, 160, 60, 255]], [70, [230, 170, 40, 255]], [90, [200, 30, 30, 255]]]
        )
        self.assertEqual(hex_levels, rgba_levels)

        with patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', hex_levels):
            img_hex = tray_icon_mod.create_icon_image(75, 20)
        with patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', rgba_levels):
            img_rgba = tray_icon_mod.create_icon_image(75, 20)
        self.assertEqual(img_hex.tobytes(), img_rgba.tobytes())

    def test_short_hex_and_alpha_hex_parse(self):
        """'#rgb' expands per digit and '#rrggbbaa' carries its alpha."""
        import usage_monitor_for_claude.settings as settings_mod

        levels = settings_mod._parse_icon_color_levels([[0, '#f00'], [50, '#11223344']])
        self.assertEqual(levels, [(0.0, (255, 0, 0, 255)), (50.0, (17, 34, 51, 68))])

        with patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', levels):
            img = tray_icon_mod.create_icon_image(20, 20)
        pixels = img.load()
        for mid_y in _bar_mid_rows():
            self.assertEqual(pixels[5, mid_y], (255, 0, 0, 255))


class TestIconColorLevelsUnsetParity(unittest.TestCase):
    """Unset icon_color_levels must render byte-identical to the pre-feature code."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()
        patcher = patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    @staticmethod
    def _reference_classic_icon(pct_top, pct_bottom, time_pct_top, time_pct_bottom):
        """Re-implementation of the pre-feature create_icon_image drawing.

        Copies the upstream glyph and bar algorithm exactly as it was
        before icon_color_levels existed, so the rendering contract is
        checked against the original behavior rather than against the
        new code itself.
        """
        colors = tray_icon_mod.ICON_LIGHT
        fg, fg_half, fg_warn = colors['fg'], colors['fg_half'], colors['fg_warn']

        S = tray_icon_mod.ICON_SIZE
        img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Glyph (pct_top in (0, 100) renders the percentage)
        text, font = f'{min(pct_top, 99):.0f}', tray_icon_mod.load_font(40)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=0)
        tw = bbox[2] - bbox[0]
        draw.text(((S - tw) / 2 - bbox[0], -bbox[1]), text, fill=fg, font=font, stroke_width=0, stroke_fill=fg)

        # Bars (utilization mode with warn switch and marker)
        bar2_y = S - tray_icon_mod.BAR_HEIGHT
        bar1_y = bar2_y - tray_icon_mod.BAR_GAP - tray_icon_mod.BAR_HEIGHT
        for y, pct, time_pct in ((bar1_y, pct_top, time_pct_top), (bar2_y, pct_bottom, time_pct_bottom)):
            draw.rectangle([0, y, S - 1, y + tray_icon_mod.BAR_HEIGHT - 1], fill=fg_half)
            fill_w = max(0, min(S, int(S * pct / 100)))
            if fill_w > 0:
                warn = pct >= 100 or (time_pct is not None and pct > time_pct)
                draw.rectangle([0, y, fill_w - 1, y + tray_icon_mod.BAR_HEIGHT - 1], fill=fg_warn if warn else fg)
            if time_pct is not None:
                marker_x = min(S - tray_icon_mod.MARKER_WIDTH, max(0, int(S * time_pct / 100) - tray_icon_mod.MARKER_WIDTH // 2))
                draw.rectangle([marker_x, y, marker_x + tray_icon_mod.MARKER_WIDTH - 1, y + tray_icon_mod.BAR_HEIGHT - 1], fill=fg)

        return img

    def test_unset_levels_render_byte_identical_plain_fill(self):
        """No levels + on-pace usage matches the pre-feature rendering exactly."""
        img = tray_icon_mod.create_icon_image(20, 10, time_pct_top=50, time_pct_bottom=50)
        reference = self._reference_classic_icon(20, 10, 50, 50)
        self.assertEqual(img.tobytes(), reference.tobytes())

    def test_unset_levels_render_byte_identical_warn_fill(self):
        """No levels + ahead-of-time usage keeps the fg_warn fill byte-identical."""
        img = tray_icon_mod.create_icon_image(70, 20, time_pct_top=40, time_pct_bottom=50)
        reference = self._reference_classic_icon(70, 20, 40, 50)
        self.assertEqual(img.tobytes(), reference.tobytes())

    def test_unset_levels_render_byte_identical_without_time(self):
        """No levels and no elapsed time matches the pre-feature rendering."""
        img = tray_icon_mod.create_icon_image(30, 60)
        reference = self._reference_classic_icon(30, 60, None, None)
        self.assertEqual(img.tobytes(), reference.tobytes())


class TestIconColorLevelsNumbersStyle(unittest.TestCase):
    """Tests for icon_color_levels in the 'numbers' icon style."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()
        for attr, value in (('ICON_STYLE', 'numbers'), ('ICON_COLOR_LEVELS', _LEVELS)):
            patcher = patch.object(tray_icon_mod, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    @staticmethod
    def _row_ranges():
        row_h = tray_icon_mod.NUMBER_ROW_HEIGHT
        return (range(0, row_h), range(row_h, 2 * row_h))

    def test_rows_tinted_independently(self):
        """Each stacked number follows the level for its own field's pct."""
        img = tray_icon_mod.create_icon_image(95, 20)

        top_rows, bottom_rows = self._row_ranges()
        self.assertTrue(_region_has_color(img, top_rows, _RED), 'Expected pct=95 level color in the top row')
        self.assertTrue(_region_has_color(img, bottom_rows, _GREEN), 'Expected pct=20 level color in the bottom row')
        self.assertFalse(_region_has_color(img, top_rows, _GREEN), 'Top row must not use the bottom row color')
        self.assertFalse(_region_has_color(img, bottom_rows, _RED), 'Bottom row must not use the top row color')

    def test_exhausted_row_glyph_stays_fg(self):
        """An exhausted row's '✕' keeps the base fg while the other row is tinted."""
        img = tray_icon_mod.create_icon_image(100, 20)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        top_rows, bottom_rows = self._row_ranges()
        self.assertTrue(_region_has_color(img, top_rows, fg), 'Expected fg exhausted glyph in the top row')
        self.assertFalse(_region_has_color(img, top_rows, _RED), 'Exhausted glyph must not be tinted')
        self.assertTrue(_region_has_color(img, bottom_rows, _GREEN), 'Expected tinted digits in the bottom row')

    def test_unset_levels_render_byte_identical(self):
        """Unset levels keep the numbers style byte-identical to base fg rows."""
        with patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', None):
            img_unset = tray_icon_mod.create_icon_image(75, 20)
        with patch.object(tray_icon_mod, 'ICON_COLOR_LEVELS', []):
            img_empty = tray_icon_mod.create_icon_image(75, 20)

        fg = tray_icon_mod.ICON_LIGHT['fg']
        self.assertEqual(img_unset.tobytes(), img_empty.tobytes())
        top_rows, bottom_rows = self._row_ranges()
        self.assertTrue(_region_has_color(img_unset, top_rows, fg))
        self.assertTrue(_region_has_color(img_unset, bottom_rows, fg))


class TestCreateStatusImage(unittest.TestCase):
    """Tests for create_status_image()."""

    def setUp(self):
        tray_icon_mod.load_font.cache_clear()

    def tearDown(self):
        tray_icon_mod.load_font.cache_clear()

    def test_returns_64x64_rgba_image(self):
        """Status icon is always 64x64 RGBA."""
        img = tray_icon_mod.create_status_image('!')

        self.assertEqual(img.size, (64, 64))
        self.assertEqual(img.mode, 'RGBA')

    @patch.object(tray_icon_mod, 'load_font')
    def test_uses_size_46_font(self, mock_font):
        """Status text uses size 46 font."""
        mock_font.return_value = _real_font()

        tray_icon_mod.create_status_image('?')

        mock_font.assert_called_with(46)

    def test_light_taskbar_variant(self):
        """Light taskbar produces a valid image."""
        img = tray_icon_mod.create_status_image('!', light_taskbar=True)

        self.assertEqual(img.size, (64, 64))


if __name__ == '__main__':
    unittest.main()
