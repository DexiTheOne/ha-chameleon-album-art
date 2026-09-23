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


def _normalize_palette(colors: list[RGBColor]) -> list[RGBColor]:
    """Normalize every color in a palette."""
    return [clamp_rgb_color(color) for color in colors]


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
