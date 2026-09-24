"""Color extraction logic for Chameleon integration."""

from __future__ import annotations

import colorsys
import logging
from io import BytesIO
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import DEFAULT_COLOR_COUNT, DEFAULT_QUALITY

_LOGGER = logging.getLogger(__name__)

# RGB color type
type RGBColor = tuple[int, int, int]


def clamp_rgb_color(color: RGBColor) -> RGBColor:
    """Clamp every channel to the 8-bit RGB range accepted by light services."""
    return (
        max(0, min(255, int(color[0]))),
        max(0, min(255, int(color[1]))),
        max(0, min(255, int(color[2]))),
    )


def _normalize_color(color: RGBColor) -> RGBColor:
    """Normalize one extracted color."""
    return clamp_rgb_color(color)


def _normalize_palette(colors: list[RGBColor]) -> list[RGBColor]:
    """Normalize every color in a palette."""
    return [clamp_rgb_color(color) for color in colors]


def select_interesting_colors(colors: list[RGBColor]) -> list[RGBColor]:
    """Keep distinct, visible hues in their original dominance order.

    Score source swatches before brightness enhancement. A pale but distinctly
    blue background has a usable hue; a near-white highlight or gray does not.
    Muted warm skin tones and repeated shades of one hue should not crowd out
    the rest of an album cover's palette.
    """
    selected: list[tuple[int, RGBColor]] = []
    selected_hues: list[float] = []
    white_candidate: tuple[int, RGBColor] | None = None
    for index, color in enumerate(colors):
        r, g, b = clamp_rgb_color(color)
        hue, saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        hue_degrees = hue * 360
        if white_candidate is None and value >= 0.8 and saturation < 0.1:
            white_candidate = (index, (r, g, b))
        if value < 0.18 or saturation < 0.15 or max(r, g, b) - min(r, g, b) < 30:
            continue
        # Typical muted tan/peach face colors are rarely useful LED accents.
        # Saturated oranges and yellows outside this range remain available.
        if 15 <= hue_degrees <= 50 and 0.15 <= saturation <= 0.6 and 0.3 <= value <= 0.95:
            continue
        if any(min(abs(hue_degrees - chosen), 360 - abs(hue_degrees - chosen)) < 25 for chosen in selected_hues):
            continue
        selected.append((index, (r, g, b)))
        selected_hues.append(hue_degrees)
    # An achromatic cover can otherwise leave every light unchanged. A leading
    # white swatch with at most one useful hue also represents a mostly white
    # image; several distinct hues keep the usual white exclusion in place.
    if white_candidate is not None and (not selected or (white_candidate[0] <= 1 and len(selected) <= 1)):
        selected.append(white_candidate)
        selected.sort(key=lambda item: item[0])
    return [color for _, color in selected]


def balance_mostly_white_palette(colors: list[RGBColor], white_fraction: float, light_count: int) -> list[RGBColor]:
    """Give a mostly white image white lights while retaining a small color accent."""
    if white_fraction < 0.7 or light_count < 1:
        return colors
    accents = [color for color in colors if not _is_bright_neutral(color)]
    if len(accents) > 1:
        return colors
    if not accents or light_count == 1:
        return [(255, 255, 255)] * light_count
    accent_count = min(2, max(1, light_count - 1))
    return [(255, 255, 255)] * (light_count - accent_count) + [accents[0]] * accent_count


def _is_bright_neutral(color: RGBColor) -> bool:
    r, g, b = clamp_rgb_color(color)
    return min(r, g, b) >= 205 and max(r, g, b) - min(r, g, b) < 26


def _sync_white_fraction(image_source: bytes | Path) -> float:
    """Measure white image area independently of ColorThief's color palette."""
    from PIL import Image

    source = BytesIO(image_source) if isinstance(image_source, bytes) else image_source
    with Image.open(source) as image:
        image.thumbnail((128, 128))
        rgb = image.convert("RGB")
        pixels = list(rgb.getdata())
    return sum(_is_bright_neutral(pixel) for pixel in pixels) / len(pixels) if pixels else 0.0


async def extract_white_fraction(hass: HomeAssistant, image_source: bytes | Path) -> float:
    """Sample white coverage without blocking Home Assistant's event loop."""
    try:
        return await hass.async_add_executor_job(_sync_white_fraction, image_source)
    except Exception as err:
        _LOGGER.warning("Unable to measure image white coverage: %s", type(err).__name__)
        return 0.0


def normalize_palette_brightness(colors: list[RGBColor]) -> list[RGBColor]:
    """Make extracted colors bright and colorful without changing their hue.

    HSV value equalizes RGB output level; a saturation floor keeps muted image
    swatches colorful on LEDs. Nearly neutral or black colors have no reliable
    hue, so they become white rather than an arbitrary saturated color.
    """
    result: list[RGBColor] = []
    for color in colors:
        r, g, b = clamp_rgb_color(color)
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if v < 0.05 or s < 0.08:
            result.append((255, 255, 255))
            continue
        bright_r, bright_g, bright_b = colorsys.hsv_to_rgb(h, max(s, 0.65), 1.0)
        result.append((round(bright_r * 255), round(bright_g * 255), round(bright_b * 255)))
    return result


def _sync_extract_dominant_color(image_path: str, quality: int) -> RGBColor | None:
    """Synchronous color extraction - runs in executor."""
    from colorthief import ColorThief

    color_thief = ColorThief(image_path)
    return color_thief.get_color(quality=quality)


def _sync_extract_palette(image_path: str, color_count: int, quality: int) -> list[RGBColor]:
    """Synchronous palette extraction - runs in executor."""
    from colorthief import ColorThief

    color_thief = ColorThief(image_path)
    return color_thief.get_palette(color_count=color_count, quality=quality)


def _sync_extract_palette_bytes(image_bytes: bytes, color_count: int, quality: int) -> list[RGBColor]:
    """Extract a palette from in-memory image bytes."""
    from colorthief import ColorThief

    color_thief = ColorThief(BytesIO(image_bytes))
    return color_thief.get_palette(color_count=color_count, quality=quality)


async def extract_dominant_color(
    hass: HomeAssistant,
    image_path: Path,
    quality: int = DEFAULT_QUALITY,
) -> RGBColor | None:
    """
    Extract the single dominant color from an image.

    Args:
        hass: Home Assistant instance (needed for executor)
        image_path: Path to the image file
        quality: Color extraction quality (1=highest, 10=fastest)

    Returns:
        RGB tuple (r, g, b) or None if extraction fails
    """
    try:
        _LOGGER.debug("Extracting dominant color from %s", image_path)
        color = await hass.async_add_executor_job(
            _sync_extract_dominant_color,
            str(image_path),
            quality,
        )
        _LOGGER.debug("Extracted dominant color: %s", color)
        return _normalize_color(color)
    except Exception as e:
        _LOGGER.error("Failed to extract dominant color from %s: %s", image_path, e)
        return None


async def extract_color_palette(
    hass: HomeAssistant,
    image_path: Path,
    color_count: int = DEFAULT_COLOR_COUNT,
    quality: int = DEFAULT_QUALITY,
) -> list[RGBColor]:
    """
    Extract a palette of colors from an image.

    Args:
        hass: Home Assistant instance (needed for executor)
        image_path: Path to the image file
        color_count: Number of colors to extract
        quality: Color extraction quality (1=highest, 10=fastest)

    Returns:
        List of RGB tuples, or empty list if extraction fails
    """
    try:
        _LOGGER.debug("Extracting %d colors from %s", color_count, image_path)
        colors = await hass.async_add_executor_job(
            _sync_extract_palette,
            str(image_path),
            color_count,
            quality,
        )
        _LOGGER.debug("Extracted %d colors: %s", len(colors), colors)
        return _normalize_palette(colors)
    except Exception as e:
        _LOGGER.error("Failed to extract color palette from %s: %s", image_path, e)
        return []


async def extract_color_palette_bytes(
    hass: HomeAssistant,
    image_bytes: bytes,
    color_count: int = DEFAULT_COLOR_COUNT,
    quality: int = DEFAULT_QUALITY,
) -> list[RGBColor]:
    """Extract a palette from downloaded image bytes without persisting artwork."""
    try:
        colors = await hass.async_add_executor_job(
            _sync_extract_palette_bytes,
            image_bytes,
            color_count,
            quality,
        )
        _LOGGER.debug("Extracted %d colors from album artwork", len(colors))
        return _normalize_palette(colors)
    except Exception as e:
        _LOGGER.error("Failed to extract colors from album artwork: %s", e)
        return []


def generate_gradient_path(
    colors: list[RGBColor],
    steps_between: int = 10,
) -> list[RGBColor]:
    """
    Generate a smooth gradient path between a list of colors.

    This creates intermediate colors between each pair of colors in the palette,
    resulting in a smooth color progression suitable for animation.

    Args:
        colors: List of RGB colors from palette extraction
        steps_between: Number of intermediate steps between each color pair

    Returns:
        List of RGB tuples representing the full gradient path
    """
    if len(colors) < 2:
        return colors

    gradient: list[RGBColor] = []

    for i in range(len(colors)):
        current_color = colors[i]
        next_color = colors[(i + 1) % len(colors)]  # Loop back to first color

        # Add intermediate steps
        for step in range(steps_between):
            t = step / steps_between
            r = int(current_color[0] + (next_color[0] - current_color[0]) * t)
            g = int(current_color[1] + (next_color[1] - current_color[1]) * t)
            b = int(current_color[2] + (next_color[2] - current_color[2]) * t)
            gradient.append((r, g, b))

    return gradient


def rgb_to_hs(rgb: RGBColor) -> tuple[float, float]:
    """
    Convert RGB to Hue/Saturation for Home Assistant light services.

    Home Assistant uses hs_color as (hue, saturation) where:
    - hue: 0-360 degrees
    - saturation: 0-100 percent

    Args:
        rgb: RGB tuple (0-255 for each channel)

    Returns:
        Tuple of (hue, saturation)
    """
    r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    diff = max_c - min_c

    # Calculate hue
    if diff == 0:
        hue = 0
    elif max_c == r:
        hue = (60 * ((g - b) / diff) + 360) % 360
    elif max_c == g:
        hue = (60 * ((b - r) / diff) + 120) % 360
    else:
        hue = (60 * ((r - g) / diff) + 240) % 360

    # Calculate saturation
    if max_c == 0:
        saturation = 0
    else:
        saturation = (diff / max_c) * 100

    return (hue, saturation)
