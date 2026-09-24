"""Tests for color_extractor module."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest
from PIL import Image

from custom_components.chameleon.color_extractor import (
    _normalize_palette,
    _sync_white_fraction,
    balance_mostly_white_palette,
    clamp_rgb_color,
    normalize_palette_brightness,
    select_interesting_colors,
    extract_color_palette_bytes,
    generate_gradient_path,
    rgb_to_hs,
)


def test_normalize_palette_clamps_rgb_channels():
    """Extractor quirks cannot send invalid channel values to lights."""
    assert _normalize_palette([(256, -1, 128)]) == [(255, 0, 128)]
    assert clamp_rgb_color((-10, 300, 128.9)) == (0, 255, 128)


def test_normalize_palette_brightness_preserves_hue_and_equalizes_value():
    """Dark and muted image colors become bright LED RGB values."""
    colors = [(32, 4, 4), (40, 80, 120), (180, 170, 160)]
    result = normalize_palette_brightness(colors)
    assert all(max(color) == 255 for color in result)
    assert result[0][0] == 255 and result[0][1] < 100
    assert result[1][2] == 255 and result[1][0] < result[1][1]
    assert result[2][0] == 255 and result[2][1] < result[2][0]


def test_normalize_palette_brightness_keeps_neutral_colors_neutral():
    """Black and gray have no usable hue and should not turn into red."""
    assert normalize_palette_brightness([(0, 0, 0), (50, 50, 50)]) == [
        (255, 255, 255),
        (255, 255, 255),
    ]


def test_normalize_animated_gradient_keeps_intermediate_colors_bright():
    """RGB interpolation should not reintroduce dim color values."""
    gradient = generate_gradient_path([(255, 0, 0), (0, 255, 0)], steps_between=10)
    normalized = normalize_palette_brightness(gradient)
    assert len(normalized) == len(gradient)
    assert all(max(color) == 255 for color in normalized)


@pytest.mark.asyncio
async def test_extract_color_palette_bytes_uses_in_memory_image():
    """Album art can be processed without writing its bytes or token to disk."""
    image = Image.new("RGB", (40, 20), color=(220, 30, 40))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    hass = MagicMock()

    async def run_in_executor(func, *args):
        return func(*args)

    hass.async_add_executor_job = run_in_executor
    palette = await extract_color_palette_bytes(hass, buffer.getvalue(), color_count=3, quality=1)

    assert palette
    assert palette[0][0] > palette[0][1]
    assert palette[0][0] > palette[0][2]


class TestGenerateGradientPath:
    """Tests for generate_gradient_path function."""

    def test_empty_colors(self):
        """Test with empty color list."""
        result = generate_gradient_path([])
        assert result == []

    def test_single_color(self):
        """Test with single color."""
        colors = [(255, 0, 0)]
        result = generate_gradient_path(colors)
        assert result == colors

    def test_two_colors_default_steps(self):
        """Test gradient between two colors with default steps."""
        colors = [(255, 0, 0), (0, 255, 0)]  # Red to Green
        result = generate_gradient_path(colors, steps_between=10)

        # Should have 20 colors (10 steps between each pair, 2 pairs for loop back)
        assert len(result) == 20

        # First color should be red
        assert result[0] == (255, 0, 0)

        # Colors should transition smoothly
        # Check that red decreases and green increases in first half
        for i in range(1, 10):
            assert result[i][0] < result[i - 1][0] or result[i][0] == 0  # Red decreases
            assert result[i][1] > result[i - 1][1] or result[i][1] == 255  # Green increases

    def test_three_colors(self):
        """Test gradient with three colors."""
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        result = generate_gradient_path(colors, steps_between=5)

        # Should have 15 colors (5 steps * 3 pairs)
        assert len(result) == 15

    def test_steps_between_one(self):
        """Test with minimal steps (essentially no interpolation)."""
        colors = [(255, 0, 0), (0, 255, 0)]
        result = generate_gradient_path(colors, steps_between=1)

        # Should just have the starting colors (1 step per transition)
        assert len(result) == 2
        assert result[0] == (255, 0, 0)
        assert result[1] == (0, 255, 0)

    def test_gradient_values_in_range(self):
        """Test that all gradient values are valid RGB."""
        colors = [(255, 128, 0), (0, 64, 255)]
        result = generate_gradient_path(colors, steps_between=20)

        for r, g, b in result:
            assert 0 <= r <= 255
            assert 0 <= g <= 255
            assert 0 <= b <= 255


class TestRgbToHs:
    """Tests for rgb_to_hs function."""

    def test_red(self):
        """Test pure red."""
        hue, sat = rgb_to_hs((255, 0, 0))
        assert hue == pytest.approx(0, abs=1)
        assert sat == pytest.approx(100, abs=1)

    def test_green(self):
        """Test pure green."""
        hue, sat = rgb_to_hs((0, 255, 0))
        assert hue == pytest.approx(120, abs=1)
        assert sat == pytest.approx(100, abs=1)

    def test_blue(self):
        """Test pure blue."""
        hue, sat = rgb_to_hs((0, 0, 255))
        assert hue == pytest.approx(240, abs=1)
        assert sat == pytest.approx(100, abs=1)

    def test_yellow(self):
        """Test yellow (red + green)."""
        hue, sat = rgb_to_hs((255, 255, 0))
        assert hue == pytest.approx(60, abs=1)
        assert sat == pytest.approx(100, abs=1)

    def test_cyan(self):
        """Test cyan (green + blue)."""
        hue, sat = rgb_to_hs((0, 255, 255))
        assert hue == pytest.approx(180, abs=1)
        assert sat == pytest.approx(100, abs=1)

    def test_magenta(self):
        """Test magenta (red + blue)."""
        hue, sat = rgb_to_hs((255, 0, 255))
        assert hue == pytest.approx(300, abs=1)
        assert sat == pytest.approx(100, abs=1)

    def test_white(self):
        """Test white (no saturation)."""
        _hue, sat = rgb_to_hs((255, 255, 255))
        assert sat == pytest.approx(0, abs=1)
        # Hue is undefined for white, but should be 0

    def test_black(self):
        """Test black (no saturation)."""
        _hue, sat = rgb_to_hs((0, 0, 0))
        assert sat == pytest.approx(0, abs=1)

    def test_gray(self):
        """Test gray (50% brightness, no saturation)."""
        _hue, sat = rgb_to_hs((128, 128, 128))
        assert sat == pytest.approx(0, abs=1)

    def test_half_saturation(self):
        """Test color with 50% saturation."""
        # Light red (pink-ish)
        hue, sat = rgb_to_hs((255, 128, 128))
        assert hue == pytest.approx(0, abs=1)  # Still red hue
        assert 40 < sat < 60  # Approximately 50% saturation


def test_interesting_colors_reject_neutrals_dark_and_pale_swatches():
    colors = [(255, 255, 255), (0, 0, 0), (35, 12, 12),
              (180, 178, 170), (255, 220, 220), (210, 40, 90), (35, 100, 170)]
    assert select_interesting_colors(colors) == [(210, 40, 90), (35, 100, 170)]
    assert select_interesting_colors([(255, 255, 255)]) == [(255, 255, 255)]


def test_interesting_colors_allows_white_for_neutral_or_mostly_white_palettes():
    assert select_interesting_colors([(12, 12, 12), (242, 243, 241), (90, 90, 90)]) == [(242, 243, 241)]
    assert select_interesting_colors([(250, 250, 250), (30, 100, 180), (18, 18, 18)]) == [
        (250, 250, 250), (30, 100, 180)
    ]
    assert select_interesting_colors([(255, 255, 255), (220, 40, 80), (30, 100, 180)]) == [
        (220, 40, 80), (30, 100, 180)
    ]


def test_mostly_white_artwork_assigns_white_to_most_lights():
    image = Image.new("RGB", (100, 100), "white")
    for x in range(10):
        for y in range(100):
            image.putpixel((x, y), (225, 25, 40))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    white_fraction = _sync_white_fraction(buffer.getvalue())
    assert white_fraction >= 0.85
    colors = balance_mostly_white_palette(select_interesting_colors([(225, 25, 40)]), white_fraction, 7)
    assert colors == [(255, 255, 255)] * 5 + [(225, 25, 40)] * 2
    assert balance_mostly_white_palette([(225, 25, 40)], white_fraction, 1) == [(255, 255, 255)]
    assert balance_mostly_white_palette([(225, 25, 40)], 0.4, 7) == [(225, 25, 40)]


def test_interesting_colors_keeps_pale_blue_without_repeating_coral():
    """The Squeezebox cover's dominant blue survives while coral shades collapse."""
    colors = [(201, 228, 244), (23, 25, 27), (231, 161, 147),
              (119, 90, 84), (234, 173, 157), (156, 122, 110), (121, 135, 143)]
    assert select_interesting_colors(colors) == [(201, 228, 244), (231, 161, 147)]
    bright = normalize_palette_brightness(select_interesting_colors(colors))
    assert bright[0][2] == 255 and bright[0][0] < bright[0][2]


def test_interesting_colors_suppresses_skin_tones_but_keeps_bright_orange():
    colors = [(220, 180, 150), (190, 135, 100), (160, 110, 85),
              (40, 110, 180), (255, 150, 20)]
    assert select_interesting_colors(colors) == [(40, 110, 180), (255, 150, 20)]
