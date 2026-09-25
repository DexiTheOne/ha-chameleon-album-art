"""Send three distinct scene colors to a WLED device's JSON API."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er

from .color_extractor import RGBColor, clamp_rgb_color

_LOGGER = logging.getLogger(__name__)


def three_palette_colors(colors: list[RGBColor], offset: int = 0) -> list[RGBColor]:
    """Use three distinct source swatches; never invent colors."""
    unique = list(dict.fromkeys(clamp_rgb_color(color) for color in colors))
    if len(unique) < 3:
        return []
    return [unique[(offset + index) % len(unique)] for index in range(3)]


def wled_entry_id(hass: HomeAssistant, entity_id: str) -> str | None:
    """Find the WLED config entry owning a light entity."""
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None or not entity.config_entry_id:
        return None
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    return entry.entry_id if entry is not None and entry.domain == "wled" else None


async def send_wled_palette(
    hass: HomeAssistant,
    entity_id: str,
    colors: list[RGBColor],
    offset: int,
    sent_entries: set[str] | None = None,
    brightness: int | None = None,
) -> str | None:
    """Update source colors only when every segment uses a configured palette."""
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None or not entity.config_entry_id:
        return None
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    if entry is None or entry.domain != "wled":
        return None
    if sent_entries is not None and entry.entry_id in sent_entries:
        return None
    host = entry.data.get("host")
    if not isinstance(host, str) or not host or any(char in host for char in "/@?#"):
        return None
    palette = three_palette_colors(colors, offset)
    if not palette:
        return None
    try:
        session = async_get_clientsession(hass)
        async with session.get(f"http://{host}/json/state", timeout=5) as response:
            response.raise_for_status()
            state = await response.json()
        segments = state.get("seg", [])
        if not isinstance(segments, list) or not segments:
            return None
        async with session.get(f"http://{host}/json/fxdata", timeout=5) as response:
            response.raise_for_status()
            effect_metadata = await response.json()
        if not isinstance(effect_metadata, list):
            return None
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get("id"), int):
                return None
            effect_id = segment.get("fx")
            metadata = effect_metadata[effect_id] if isinstance(effect_id, int) and 0 <= effect_id < len(effect_metadata) else ""
            sections = metadata.split(";") if isinstance(metadata, str) else []
            if segment.get("pal") not in (4, 5) or len(sections) < 3 or sections[2] != "!":
                return None
        payload = {"seg": [
            {"id": segment["id"], "col": [list(color) for color in palette]}
            for segment in segments
        ]}
        if brightness is not None:
            payload["bri"] = round(max(0, min(100, brightness)) * 255 / 100)
        async with session.post(
            f"http://{host}/json/state",
            json=payload,
            timeout=5,
        ) as response:
            response.raise_for_status()
        if sent_entries is not None:
            sent_entries.add(entry.entry_id)
        return entry.entry_id
    except Exception as err:
        _LOGGER.warning("Could not send palette to WLED light %s: %s", entity_id, type(err).__name__)
        return None
