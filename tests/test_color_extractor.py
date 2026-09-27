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
    distribute_palette_by_coverage,
    extract_color_palette_bytes,
    generate_gradient_path,
    normalize_palette_brightness,
    rgb_to_hs,
    select_interesting_colors,
)


def test_normalize_palette_clamps_rgb_channels():
    """Extractor quirks cannot send invalid channel values to lights."""
    assert _normalize_palette([(256, -1, 128)]) == [(255, 0, 128)]
    assert clamp_rgb_color((-10, 300, 128.9)) == (0, 255, 128)


def test_gentle_normalization_preserves_bright_and_muted_swatches():
    assert normalize_palette_brightness([(32, 4, 4), (40, 80, 120), (180, 170, 160), (255, 200, 0)]) == [
        (204, 26, 26), (68, 136, 204), (204, 193, 181), (255, 200, 0),
    ]


def test_gray_cover_clouds_and_logo_shadows_are_not_exaggerated():
    assert normalize_palette_brightness([(17, 20, 22), (140, 132, 136), (132, 132, 140), (68, 76, 76)]) == [
        (204, 204, 204), (204, 204, 204), (204, 204, 204), (204, 204, 204),
    ]
    assert normalize_palette_brightness([(221, 219, 231), (43, 22, 23)]) == [(221, 219, 231), (204, 104, 109)]


def test_gentle_neutral_floor_and_visible_gradient():
    assert normalize_palette_brightness([(0, 0, 0), (50, 50, 50)]) == [(204, 204, 204)] * 2
    gradient = generate_gradient_path([(255, 0, 0), (0, 255, 0)], steps_between=10)
    assert all(max(c) >= 204 for c in normalize_palette_brightness(gradient))


@pytest.mark.parametrize("color", [(13, 27, 20), (1, 0, 0), (20, 30, 55)])
def test_dark_chromatic_saturation_is_preserved(color):
    result = normalize_palette_brightness([color])[0]
    assert max(result) == 204
    assert rgb_to_hs(result)[0] == pytest.approx(rgb_to_hs(color)[0], abs=1)
    assert rgb_to_hs(result)[1] == pytest.approx(rgb_to_hs(color)[1], abs=1)


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


@pytest.mark.parametrize("color", [(0, 1, 0), (3, 12, 5), (220, 180, 150), (255, 255, 255)])
def test_interesting_colors_retains_white_and_real_dark_hues(color):
    assert select_interesting_colors([(0, 0, 0), color]) == [color]
    assert select_interesting_colors([(0, 0, 0)]) == []


def test_dark_green_normalization_preserves_hue():
    colors = [(0, 1, 0), (3, 12, 5), (13, 27, 20)]
    normalized = normalize_palette_brightness(select_interesting_colors(colors))
    for source, result in zip(colors, normalized, strict=True):
        assert max(result) == 204
        assert result[1] > result[0] and result[1] > result[2]
        assert rgb_to_hs(result)[0] == pytest.approx(rgb_to_hs(source)[0], abs=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("color,accepted", [((0, 0, 0), False), ((0, 1, 0), True), ((3, 12, 5), True)])
async def test_black_artwork_detection_precedes_quantization(color, accepted):
    image = Image.new("RGB", (40, 40), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    hass = MagicMock()

    async def run_in_executor(func, *args):
        return func(*args)

    hass.async_add_executor_job = run_in_executor
    palette = await extract_color_palette_bytes(hass, buffer.getvalue(), color_count=3, quality=1)
    assert bool(select_interesting_colors(palette)) == accepted


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
    assert colors == [(255, 255, 255)] * 6 + [(225, 25, 40)]
    assert balance_mostly_white_palette([(225, 25, 40)], white_fraction, 1) == [(255, 255, 255)]
    assert balance_mostly_white_palette([(225, 25, 40)], 0.4, 7) == [(225, 25, 40)]


def test_interesting_colors_ranks_muted_dark_hues_above_skin_tones():
    """All non-black shades remain available, including muted and repeated hues."""
    colors = [(201, 228, 244), (23, 25, 27), (231, 161, 147),
              (119, 90, 84), (234, 173, 157), (156, 122, 110), (121, 135, 143)]
    assert select_interesting_colors(colors) == [colors[0], colors[6]]
    bright = normalize_palette_brightness(select_interesting_colors(colors))
    assert bright[0][2] == 244 and bright[0][0] < bright[0][2]


def test_interesting_colors_prefers_blue_and_vivid_orange_to_skin_tones():
    colors = [(220, 180, 150), (190, 135, 100), (160, 110, 85),
              (40, 110, 180), (255, 150, 20)]
    assert select_interesting_colors(colors) == [colors[3], colors[4]]


@pytest.mark.parametrize("fails", [False, True])
def test_artwork_extractor_closes_image_and_stream_even_on_failure(fails):
    from unittest.mock import patch

    from custom_components.chameleon.color_extractor import _sync_extract_palette_bytes
    thief = MagicMock()
    thief.get_palette.return_value = [(255, 0, 0)]
    if fails:
        thief.get_palette.side_effect = ValueError("invalid image")
    with patch("colorthief.ColorThief", return_value=thief) as factory:
        if fails:
            with pytest.raises(ValueError):
                _sync_extract_palette_bytes(b"image", 3, 1)
        else:
            assert _sync_extract_palette_bytes(b"image", 3, 1) == [(255, 0, 0)]
    thief.image.close.assert_called_once()
    assert factory.call_args.args[0].closed


def test_tigallerro_dark_green_palette_is_accepted_and_brightened():
    """The observed dominant background keeps its 150-degree green hue."""
    source = [(13, 27, 20)]
    assert select_interesting_colors(source) == source
    assert normalize_palette_brightness(select_interesting_colors(source)) == [(98, 204, 151)]


def test_normalization_does_not_invent_hues_from_neutral_quantization_noise():
    source = [(116, 115, 116), (116, 116, 115), (115, 116, 116), (218, 218, 218)]
    assert normalize_palette_brightness(source) == [(204, 204, 204)] * 3 + [(218, 218, 218)]
    assert normalize_palette_brightness([(13, 27, 20), (1, 0, 0)]) == [(98, 204, 151), (204, 0, 0)]


def test_black_white_artwork_quantized_palette_retains_neutral_levels():
    # The observed black/white cover's real ColorThief palette includes gray
    # (116, 115, 116); it must not become a bright magenta accent.
    palette = [(218, 218, 218), (4, 4, 4), (87, 87, 87), (132, 132, 132),
               (124, 124, 124), (116, 115, 116), (60, 60, 60)]
    assert select_interesting_colors(palette) == [(218, 218, 218)]
    assert normalize_palette_brightness(select_interesting_colors(palette)) == [(218, 218, 218)]


@pytest.mark.parametrize("color", [(0, 0, 0), (4, 4, 4), (12, 12, 12), (60, 60, 60), (132, 132, 132), (116, 115, 116)])
def test_neutral_grays_are_low_ranked_fallbacks_not_selected_over_color(color):
    assert select_interesting_colors([color, (10, 180, 240)]) == [(10, 180, 240)]
    assert select_interesting_colors([color]) == ([] if color == (0, 0, 0) else [color])


def test_colorful_palette_selects_saturated_hues_over_white_gray_dark_and_skin():
    source = [(230, 230, 230), (80, 80, 80), (13, 27, 20), (220, 180, 150), (20, 200, 240), (240, 20, 180)]
    assert select_interesting_colors(source, white_fraction=0.25) == [source[4], source[5]]


def test_mostly_white_palette_ranks_white_high_and_retains_a_dark_accent():
    source = [(13, 27, 20), (220, 180, 150), (230, 230, 230), (80, 80, 80)]
    selected = select_interesting_colors(source, white_fraction=0.85)
    assert selected == [source[2], source[0]]
    assert balance_mostly_white_palette(selected, 0.85, 7) == [(255, 255, 255)] * 6 + [source[0]]


def test_dark_chromatic_palette_outranks_skin_without_brightness_rejection():
    source = [(220, 180, 150), (13, 27, 20), (3, 5, 12), (119, 90, 84)]
    assert select_interesting_colors(source) == [source[1], source[2]]


def test_skin_tones_remain_available_when_no_stronger_candidate_exists():
    source = [(80, 80, 80), (220, 180, 150), (119, 90, 84)]
    assert select_interesting_colors(source) == source[1:]


def test_ranking_keeps_low_ranked_candidates_and_original_order_within_tiers():
    from custom_components.chameleon.color_extractor import rank_palette_colors

    source = [(80, 80, 80), (220, 180, 150), (13, 27, 20), (230, 230, 230), (20, 200, 240), (240, 20, 180)]
    assert rank_palette_colors(source) == [(source[4], 90), (source[5], 90), (source[2], 60), (source[3], 50), (source[1], 30), (source[0], 10)]


def test_mostly_white_cover_distributes_multiple_high_ranked_accents():
    source = [(240, 20, 180), (20, 200, 240), (230, 230, 230)]
    selected = select_interesting_colors(source, white_fraction=0.7)
    assert selected[0] == source[2]
    assert balance_mostly_white_palette(selected, 0.7, 7) == [(255, 255, 255)] * 5 + source[:2]


def test_saturated_color_priority_uses_measured_pixel_coverage():
    from custom_components.chameleon.color_extractor import _sync_palette_coverage

    red, blue, skin = (240, 20, 50), (20, 100, 240), (220, 180, 150)
    image = Image.new("RGB", (100, 10), red)
    for x in range(80, 100):
        for y in range(10):
            image.putpixel((x, y), blue)
    source = BytesIO()
    image.save(source, format="PNG")
    colors = [blue, skin, red]
    coverage = _sync_palette_coverage(source.getvalue(), colors)
    assert coverage == pytest.approx([0.2, 0.0, 0.8])
    assert select_interesting_colors(colors, coverage=coverage) == [red, blue]


def test_coverage_cannot_promote_skin_over_real_dark_hues():
    skin, dark = (220, 180, 150), (13, 27, 20)
    assert select_interesting_colors([skin, dark], coverage=[0.95, 0.05]) == [dark]


def test_equal_rank_and_coverage_retains_original_order():
    first, second = (240, 20, 50), (20, 100, 240)
    assert select_interesting_colors([first, second], coverage=[0.5, 0.5]) == [first, second]


def test_sample_dark_blue_is_brightened_without_saturation_boost():
    assert normalize_palette_brightness([(38, 49, 64)]) == [(121, 156, 204)]
    source = (133, 148, 197)
    result = normalize_palette_brightness([source])[0]
    assert result == (138, 153, 204)
    assert rgb_to_hs(result)[0] == pytest.approx(rgb_to_hs(source)[0], abs=1)
    assert rgb_to_hs(result)[1] == pytest.approx(rgb_to_hs(source)[1], abs=1)


def test_bright_swatches_remain_unchanged():
    source = [(221, 219, 231), (240, 150, 20), (255, 200, 0)]
    assert normalize_palette_brightness(source) == source


def test_commodores_sky_outweighs_near_black_logo_shadow():
    colors = [(133, 148, 197), (43, 22, 23), (221, 219, 231),
              (79, 52, 50), (108, 88, 79), (137, 125, 122), (194, 205, 229)]
    selected = select_interesting_colors(colors)
    assert selected == [colors[0], colors[6], colors[2]]
    assert normalize_palette_brightness(selected) == [(138, 153, 204), (194, 205, 229), (221, 219, 231)]


def test_dark_only_artwork_keeps_its_colors_and_brightness_boost():
    source = [(13, 27, 20), (3, 5, 12)]
    assert select_interesting_colors(source) == source
    assert select_interesting_colors([(38, 49, 64)]) == [(38, 49, 64)]
    assert normalize_palette_brightness([(38, 49, 64)]) == [(121, 156, 204)]


def test_bright_neutral_alone_does_not_remove_genuine_dark_accent():
    assert select_interesting_colors([(13, 27, 20), (230, 230, 230)]) == [(13, 27, 20), (230, 230, 230)]


def test_short_n_sweet_assigns_more_lights_to_large_patches():
    blue, brown, red = (44, 81, 151), (89, 49, 30), (164, 60, 36)
    assert distribute_palette_by_coverage([blue, brown, red], [blue, brown, red],
                                          [0.46655, 0.22198, 0.05975], 7) == [blue] * 4 + [brown] * 2 + [red]


def test_tiny_detail_is_not_guaranteed_a_light():
    blue, red = (40, 80, 160), (160, 40, 40)
    assert distribute_palette_by_coverage([blue, red], [blue, red], [0.99, 0.01], 7) == [blue] * 7
    assert distribute_palette_by_coverage([red, blue], [red, blue], [0.01, 0.99], 1) == [blue]


def test_prom_keeps_substantial_dark_green_background():
    dark, green, pale, blue = (31, 40, 36), (82, 111, 85), (154, 170, 155), (135, 149, 161)
    source = [dark, green, pale, blue]
    coverage = [0.40460, 0.14648, 0.08276, 0.04114]
    selected = select_interesting_colors(source, coverage=coverage)
    assert dark in selected
    allocated = distribute_palette_by_coverage(selected, source, coverage, 7)
    assert allocated.count(dark) == 4
    assert len(allocated) == 7


@pytest.mark.parametrize("coverage", [None, [], [0, 0], [-1, 1], [float("nan"), 1], [float("inf"), 1]])
def test_unavailable_or_invalid_coverage_retains_palette(coverage):
    source = [(40, 80, 160), (160, 40, 40)]
    assert distribute_palette_by_coverage(source, source, coverage, 7) == source


def test_duplicate_source_swatches_combine_coverage_without_duplicate_quota():
    blue, red = (40, 80, 160), (160, 40, 40)
    assert distribute_palette_by_coverage([blue, blue, red], [blue, blue, red], [0.4, 0.4, 0.2], 5) == [blue] * 4 + [red]
