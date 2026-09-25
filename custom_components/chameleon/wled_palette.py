"""Send three distinct scene colors to a WLED device's JSON API."""

from __future__ import annotations

import colorsys
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er

from .color_extractor import RGBColor, clamp_rgb_color

_LOGGER = logging.getLogger(__name__)


def three_palette_colors(colors: list[RGBColor], offset: int = 0) -> list[RGBColor]:
    """Rotate source swatches and fill missing slots with distinct hues."""
    unique = list(dict.fromkeys(clamp_rgb_color(color) for color in colors))
    if not unique:
        return []
    chosen = [unique[(offset + index) % len(unique)] for index in range(min(3, len(unique)))]
    hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in chosen[0]))
    for step in range(1, 7):
        if len(chosen) == 3:
            break
        rgb = colorsys.hsv_to_rgb((hue + step / 3) % 1, max(saturation, 0.7), max(value, 0.7))
        candidate = tuple(round(channel * 255) for channel in rgb)
        if candidate not in chosen:
            chosen.append(candidate)
    return chosen


async def send_wled_palette(
    hass: HomeAssistant,
    entity_id: str,
    colors: list[RGBColor],
    offset: int,
    sent_entries: set[str] | None = None,
) -> None:
    """Update every active segment of a configured WLED device."""
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None or not entity.config_entry_id:
        return
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    if entry is None or entry.domain != "wled":
        return
    if sent_entries is not None and entry.entry_id in sent_entries:
        return
    host = entry.data.get("host")
    if not isinstance(host, str) or not host or any(char in host for char in "/@?#"):
        return
    palette = three_palette_colors(colors, offset)
    if not palette:
        return
    try:
        session = async_get_clientsession(hass)
        async with session.get(f"http://{host}/json/state", timeout=5) as response:
            response.raise_for_status()
            state = await response.json()
        segments = state.get("seg", [])
        segment_ids = [segment["id"] for segment in segments if isinstance(segment, dict) and isinstance(segment.get("id"), int)]
        if not segment_ids:
            _LOGGER.warning("WLED light %s reported no segments", entity_id)
            return
        async with session.get(f"http://{host}/json/eff", timeout=5) as response:
            response.raise_for_status()
            effects = await response.json()
        try:
            palette_effect = effects.index("Palette")
        except (AttributeError, ValueError):
            _LOGGER.warning("WLED light %s does not provide the Palette effect", entity_id)
            return
        async with session.post(
            f"http://{host}/json/state",
            json={"seg": [
                {"id": segment_id, "col": [list(color) for color in palette], "pal": 5, "fx": palette_effect}
                for segment_id in segment_ids
            ]},
            timeout=5,
        ) as response:
            response.raise_for_status()
        if sent_entries is not None:
            sent_entries.add(entry.entry_id)
    except Exception as err:
        _LOGGER.warning("Could not send palette to WLED light %s: %s", entity_id, type(err).__name__)
