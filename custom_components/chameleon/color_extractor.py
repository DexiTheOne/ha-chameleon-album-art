"""Color extraction logic for Chameleon integration."""

from __future__ import annotations

import colorsys
import logging
import math
from contextlib import ExitStack
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


def _is_skin_tone(color: RGBColor) -> bool:
    """Rank common skin-like swatches conservatively; this is not face detection."""
    r, g, b = color
    hue, saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return r > g > b and hue <= 55 / 360 and 0.10 <= saturation <= 0.65 and value >= 0.20


def rank_palette_colors(
    colors: list[RGBColor], white_fraction: float = 0.0, coverage: list[float] | None = None, connected_coverage: list[float] | None = None,
) -> list[tuple[RGBColor, int]]:
    """Score candidates by usefulness, then measured image coverage within tiers."""
    ranked = []
    for index, color in enumerate(colors):
        color = clamp_rgb_color(color)
        _, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in color))
        if value == 0:
            continue
        if _is_bright_neutral(color):
            score = 100 if white_fraction >= 0.7 else 50
        elif saturation <= 0.03:
            score = 10
        elif _is_skin_tone(color):
            # Only a large, genuinely warm connected region may override the
            # normal skin penalty. Nearest-swatch area alone includes white.
            patch = connected_coverage[index] if connected_coverage is not None and index < len(connected_coverage) else 0.0
            area = coverage[index] if coverage is not None and index < len(coverage) else 0.0
            score = 80 if (0.10 <= saturation <= 0.55 and value >= 0.60
                           and math.isfinite(area) and area >= 0.60
                           and math.isfinite(patch) and 0.30 <= patch <= area <= 1.0) else 30
        elif saturation >= 0.35 and value >= 0.25:
            score = 90
        else:
            # No brightness floor: genuinely dark hues remain useful candidates.
            score = 60
        covered = coverage[index] if coverage is not None and index < len(coverage) else 0.0
        # Substantial visible hues can share the palette with saturated accents.
        # Skin-like swatches stay penalized except for the spatial exception above.
        if (score == 60 and not _is_skin_tone(color) and value >= 64 / 255
                and saturation >= 0.10 and math.isfinite(covered) and 0.05 <= covered <= 1.0):
            score = 75
        ranked.append((color, score, covered))
    return [(color, score) for color, score, _ in sorted(ranked, key=lambda item: (item[1], item[2]), reverse=True)]


def select_interesting_colors(
    colors: list[RGBColor], white_fraction: float = 0.0, coverage: list[float] | None = None, connected_coverage: list[float] | None = None,
) -> list[RGBColor]:
    """Use the strongest available tier, with low-ranked shades as fallbacks."""
    ranked = rank_palette_colors(colors, white_fraction, coverage, connected_coverage)
    if not ranked:
        return []
    cutoff = max(1, ranked[0][1] - 15)
    selected = [color for color, score in ranked if score >= cutoff]
    # Do not promote near-black shadows to bright accents when the same tier
    # already supplies visible chromatic colors. Dark-only artwork still keeps
    # its colors, and white-heavy artwork retains its explicit dark accents.
    visible_chromatic = any(
        max(color) >= 64 and (max(color) - min(color)) / max(color) >= 0.10
        and not _is_bright_neutral(color)
        for color in selected
    )
    if white_fraction < 0.7 and visible_chromatic:
        measured = {color: sum(
            coverage[index] for index, candidate in enumerate(colors)
            if candidate == color and index < len(coverage)
        ) for color in selected} if coverage is not None else {}
        selected = [color for color in selected if max(color) >= 64 or measured.get(color, 0.0) >= 0.15]
    if white_fraction >= 0.7:
        # Mostly white covers can still carry a genuine dark/color accent.
        # Skin/gray swatches only become accents when there is no better hue.
        accents = [(color, score) for color, score in ranked if not _is_bright_neutral(color) and score >= 30]
        if accents:
            accent_cutoff = accents[0][1] - 15
            selected.extend(color for color, score in accents if score >= accent_cutoff and color not in selected)
    return selected


def distribute_palette_by_coverage(
    selected: list[RGBColor], source: list[RGBColor], coverage: list[float] | None,
    light_count: int,
) -> list[RGBColor]:
    """Allocate whole light slots proportionally using largest remainders.

    Do not guarantee a slot to every small detail. Missing or invalid coverage
    retains the existing palette instead of inventing area measurements.
    """
    if not selected or light_count < 1 or coverage is None or len(coverage) != len(source):
        return selected
    if any(not math.isfinite(value) or value < 0 for value in coverage):
        return selected
    unique = list(dict.fromkeys(selected))
    weights = [sum(area for color, area in zip(source, coverage, strict=True) if color == candidate)
               for candidate in unique]
    total = sum(weights)
    if total <= 0:
        return selected
    quotas = [light_count * weight / total for weight in weights]
    counts = [int(quota) for quota in quotas]
    remainder_order = sorted(range(len(unique)), key=lambda index: quotas[index] - counts[index], reverse=True)
    for index in remainder_order[:light_count - sum(counts)]:
        counts[index] += 1
    return [color for color, count in zip(unique, counts, strict=True) for _ in range(count)]


def balance_mostly_white_palette(colors: list[RGBColor], white_fraction: float, light_count: int) -> list[RGBColor]:
    """Represent high white coverage while assigning the best accents sparingly."""
    if white_fraction < 0.7 or light_count < 1:
        return colors
    accents = [color for color in colors if not _is_bright_neutral(color)]
    if not accents or light_count == 1:
        return [(255, 255, 255)] * light_count
    accent_count = min(2, light_count - 1, max(1, round((1 - white_fraction) * light_count)))
    return [(255, 255, 255)] * (light_count - accent_count) + [accents[index % len(accents)] for index in range(accent_count)]


def _is_bright_neutral(color: RGBColor) -> bool:
    r, g, b = clamp_rgb_color(color)
    return min(r, g, b) >= 205 and max(r, g, b) - min(r, g, b) < 26


def _sync_white_fraction(image_source: bytes | Path) -> float:
    """Measure white image area independently of ColorThief's color palette."""
    from PIL import Image

    with ExitStack() as resources:
        source = resources.enter_context(BytesIO(image_source)) if isinstance(image_source, bytes) else image_source
        image = resources.enter_context(Image.open(source))
        image.thumbnail((128, 128))
        rgb = resources.enter_context(image.convert("RGB"))
        pixels = list(rgb.getdata())
    return sum(_is_bright_neutral(pixel) for pixel in pixels) / len(pixels) if pixels else 0.0


async def extract_white_fraction(hass: HomeAssistant, image_source: bytes | Path) -> float:
    """Sample white coverage without blocking Home Assistant's event loop."""
    try:
        return await hass.async_add_executor_job(_sync_white_fraction, image_source)
    except Exception as err:
        _LOGGER.warning("Unable to measure image white coverage: %s", type(err).__name__)
        return 0.0



def _sync_palette_coverage(image_source: bytes | Path, colors: list[RGBColor]) -> list[float]:
    """Estimate swatch coverage by assigning sampled pixels to their nearest RGB."""
    from PIL import Image

    if not colors:
        return []
    colors = [clamp_rgb_color(color) for color in colors]
    counts = [0] * len(colors)
    with ExitStack() as resources:
        source = resources.enter_context(BytesIO(image_source)) if isinstance(image_source, bytes) else image_source
        image = resources.enter_context(Image.open(source))
        image.thumbnail((128, 128))
        rgb = resources.enter_context(image.convert("RGB"))
        pixels = list(rgb.getdata())
    for r, g, b in pixels:
        nearest = min(range(len(colors)), key=lambda index: (
            (r - colors[index][0]) ** 2 + (g - colors[index][1]) ** 2 + (b - colors[index][2]) ** 2
        ))
        counts[nearest] += 1
    return [count / len(pixels) for count in counts] if pixels else [0.0] * len(colors)


async def extract_palette_coverage(hass: HomeAssistant, image_source: bytes | Path, colors: list[RGBColor]) -> list[float]:
    """Measure swatch coverage in an executor; keep source order if unavailable."""
    try:
        return await hass.async_add_executor_job(_sync_palette_coverage, image_source, colors)
    except Exception as err:
        _LOGGER.warning("Unable to measure palette coverage: %s", type(err).__name__)
        return [0.0] * len(colors)


def _sync_connected_warm_coverage(image_source: bytes | Path, colors: list[RGBColor]) -> list[float]:
    """Measure largest four-connected warm patch; exclude whites and shadows.

    Each pixel must itself resemble the candidate hue and saturation, rather
    than merely having that candidate as its nearest available palette color.
    This is background evidence, not semantic face detection.
    """
    from PIL import Image

    with ExitStack() as resources:
        source = resources.enter_context(BytesIO(image_source)) if isinstance(image_source, bytes) else image_source
        image = resources.enter_context(Image.open(source))
        image.thumbnail((128, 128))
        rgb = resources.enter_context(image.convert("RGB"))
        width, height = rgb.size
        pixels = [colorsys.rgb_to_hsv(*(v / 255 for v in pixel)) for pixel in rgb.getdata()]
    result = []
    for color in colors:
        hue, saturation, value = colorsys.rgb_to_hsv(*(v / 255 for v in color))
        if not _is_skin_tone(color) or not (0.10 <= saturation <= 0.55 and value >= 0.60):
            result.append(0.0)
            continue
        mask = {i for i, (h, s, v) in enumerate(pixels)
                if min(abs(h - hue), 1 - abs(h - hue)) <= 15 / 360
                and 0.10 <= s <= 0.60 and abs(s - saturation) <= 0.15 and v >= 0.60}
        largest = 0
        while mask:
            start = mask.pop()
            stack = [start]
            count = 0
            while stack:
                current = stack.pop()
                count += 1
                x, y = current % width, current // width
                neighbors = []
                if x:
                    neighbors.append(current - 1)
                if x + 1 < width:
                    neighbors.append(current + 1)
                if y:
                    neighbors.append(current - width)
                if y + 1 < height:
                    neighbors.append(current + width)
                for neighbor in neighbors:
                    if neighbor in mask:
                        mask.remove(neighbor)
                        stack.append(neighbor)
            largest = max(largest, count)
        result.append(largest / len(pixels) if pixels else 0.0)
    return result


async def extract_connected_warm_coverage(hass: HomeAssistant, image_source: bytes | Path, colors: list[RGBColor]) -> list[float]:
    """Fail closed: unavailable spatial evidence never relaxes skin ranking."""
    try:
        return await hass.async_add_executor_job(_sync_connected_warm_coverage, image_source, colors)
    except Exception as err:
        _LOGGER.warning("Unable to measure warm connected coverage: %s", type(err).__name__)
        return [0.0] * len(colors)


def _sync_average_color(image_source: bytes | Path) -> RGBColor:
    """Average every RGB pixel; do not amplify saturation or neutral tint noise."""
    from PIL import Image, ImageStat

    with ExitStack() as resources:
        source = resources.enter_context(BytesIO(image_source)) if isinstance(image_source, bytes) else image_source
        image = resources.enter_context(Image.open(source))
        rgb = resources.enter_context(image.convert("RGB"))
        return tuple(round(value) for value in ImageStat.Stat(rgb).mean)


async def extract_average_color(hass: HomeAssistant, image_source: bytes | Path) -> RGBColor | None:
    """Unavailable average retains the regular palette."""
    try:
        return await hass.async_add_executor_job(_sync_average_color, image_source)
    except Exception as err:
        _LOGGER.warning("Unable to average image color: %s", type(err).__name__)
        return None


def normalize_palette_brightness(colors: list[RGBColor]) -> list[RGBColor]:
    """Brighten artwork colors without increasing saturation.

    Brighter swatches retain their original RGB levels. An 80% HSV value floor
    makes dark artwork visible while retaining brighter highlights.
    Small channel differences in muted grays are quantization/tint noise, not
    reliable hues; suppress those without bleaching truly chromatic dark colors.
    """
    result: list[RGBColor] = []
    for color in colors:
        r, g, b = clamp_rgb_color(color)
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        target_value = max(v, 0.80)
        if s <= 0.03 or (max(r, g, b) - min(r, g, b) <= 8 and s <= 0.25):
            level = round(target_value * 255)
            result.append((level, level, level))
            continue
        bright_r, bright_g, bright_b = colorsys.hsv_to_rgb(h, s, target_value)
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
    try:
        return _extract_nonblack_palette(color_thief, color_count, quality)
    finally:
        color_thief.image.close()


def _extract_nonblack_palette(color_thief, color_count: int, quality: int) -> list[RGBColor]:
    """Detect truly black images before ColorThief rounds black to (4, 4, 4)."""
    with color_thief.image.convert("RGB") as image:
        if image.getextrema() == ((0, 0), (0, 0), (0, 0)):
            return []
    return color_thief.get_palette(color_count=color_count, quality=quality)


def _sync_extract_palette_bytes(image_bytes: bytes, color_count: int, quality: int) -> list[RGBColor]:
    """Extract a palette from in-memory image bytes."""
    from colorthief import ColorThief

    with BytesIO(image_bytes) as source:
        color_thief = ColorThief(source)
        try:
            return _extract_nonblack_palette(color_thief, color_count, quality)
        finally:
            color_thief.image.close()


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
