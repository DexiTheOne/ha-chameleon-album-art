"""Fill WLED color slots with colors from the current image."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er

from .color_extractor import RGBColor, clamp_rgb_color

_LOGGER = logging.getLogger(__name__)


async def _read_json(session, url: str):
    """Retry a brief empty WLED response during simultaneous light updates."""
    for attempt in range(3):
        try:
            async with session.get(url, timeout=5) as response:
                response.raise_for_status()
                return await response.json()
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(0.1)


def three_palette_colors(colors: list[RGBColor], offset: int = 0) -> list[RGBColor]:
    """Fill three slots by repeating source swatches when necessary."""
    unique = list(dict.fromkeys(clamp_rgb_color(color) for color in colors))
    if not unique:
        return []
    return [unique[(offset + index) % len(unique)] for index in range(3)]


def wled_entry_id(hass: HomeAssistant, entity_id: str) -> str | None:
    """Find the WLED config entry owning a light entity."""
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None or not entity.config_entry_id:
        return None
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    return entry.entry_id if entry is not None and entry.domain == "wled" else None


def wled_main_lights(hass: HomeAssistant, light_entities: list[str]) -> list[str]:
    """Find WLED main lights for configured segment lights, once per device.

    WLED uses the same unique-ID stem for its main light (suffix _0) and
    segment lights (suffix _1, _2, ...). Resolve through the registry so
    user-renamed entity IDs continue to work.
    """
    registry = er.async_get(hass)
    mains: list[str] = []
    for entity_id in light_entities:
        entity = registry.async_get(entity_id)
        if entity is None or entity.platform != "wled" or not entity.config_entry_id:
            continue
        stem, separator, suffix = entity.unique_id.rpartition("_")
        if not separator or not suffix.isdigit() or suffix == "0":
            continue
        main_id = registry.async_get_entity_id("light", "wled", f"{stem}_0")
        main = registry.async_get(main_id) if main_id else None
        if (
            main is not None
            and main.config_entry_id == entity.config_entry_id
            and main.device_id == entity.device_id
            and main_id not in light_entities
            and main_id not in mains
        ):
            mains.append(main_id)
    return mains


async def send_wled_palette(
    hass: HomeAssistant,
    entity_id: str,
    colors: list[RGBColor],
    offset: int,
    sent_entries: set[str] | None = None,
    brightness: int | None = None,
    check_only: bool = False,
    transition: float | None = None,
) -> str | None:
    """Check or update a configured palette without changing its effect."""
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
        state = await _read_json(session, f"http://{host}/json/state")
        segments = state.get("seg", [])
        if not isinstance(segments, list) or not segments:
            return None
        effect_metadata = await _read_json(session, f"http://{host}/json/fxdata")
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
        if check_only:
            return entry.entry_id
        payload = {"seg": [
            {
                "id": segment["id"],
                "col": [list(color) for color in palette],
                **({"bm": 4} if isinstance(segment.get("bm"), int) else {}),
            }
            for segment in segments
        ]}
        if brightness is not None:
            payload["bri"] = round(max(0, min(100, brightness)) * 255 / 100)
        if transition is not None:
            payload["tt"] = round(max(0, min(65, transition)) * 10)
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


async def send_wled_transition(
    hass: HomeAssistant,
    entity_id: str,
    colors: list[RGBColor],
    offset: int,
    brightness: int,
    transition: float,
    blend_mode: int,
    member_colors: dict[str, RGBColor] | None = None,
) -> bool:
    """Apply one native transition to every segment of a WLED device."""
    entity = er.async_get(hass).async_get(entity_id)
    entry = hass.config_entries.async_get_entry(entity.config_entry_id) if entity and entity.config_entry_id else None
    if entry is None or entry.domain != "wled":
        return False
    host = entry.data.get("host")
    if not isinstance(host, str) or not host or any(char in host for char in "/@?#"):
        return False
    palette = three_palette_colors(colors, offset)
    if not palette:
        return False
    segment_colors: dict[int, RGBColor] = {}
    for member_id, color in (member_colors or {}).items():
        member = er.async_get(hass).async_get(member_id)
        if member is None or member.config_entry_id != entry.entry_id:
            continue
        _, separator, suffix = member.unique_id.rpartition("_")
        if separator and suffix.isdigit():
            segment_colors[int(suffix)] = color
    try:
        session = async_get_clientsession(hass)
        state = await _read_json(session, f"http://{host}/json/state")
        segments = state.get("seg", [])
        if not isinstance(segments, list) or not segments:
            return False
        segment_payload = []
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get("id"), int):
                return False
            segment_payload.append({
                "id": segment["id"],
                "on": True,
                "col": [list(color) for color in three_palette_colors(
                    [segment_colors[segment["id"]], *palette]
                    if segment["id"] in segment_colors else palette
                )],
            })
        payload = {
            "on": True,
            "bri": round(max(0, min(100, brightness)) * 255 / 100),
            "tt": round(max(0, min(65, transition)) * 10),
            "bs": blend_mode,
            "seg": segment_payload,
        }
        async with session.post(f"http://{host}/json/state", json=payload, timeout=5) as response:
            response.raise_for_status()
        return True
    except Exception as err:
        _LOGGER.warning("Could not transition WLED light %s: %s", entity_id, type(err).__name__)
        return False


async def send_wled_power_off(
    hass: HomeAssistant, entity_id: str, transition: float, blend_mode: int,
) -> bool:
    """Fade a WLED device and all its segments off using its native style."""
    entity = er.async_get(hass).async_get(entity_id)
    entry = hass.config_entries.async_get_entry(entity.config_entry_id) if entity and entity.config_entry_id else None
    if entry is None or entry.domain != "wled":
        return False
    host = entry.data.get("host")
    if not isinstance(host, str) or not host or any(char in host for char in "/@?#"):
        return False
    try:
        session = async_get_clientsession(hass)
        state = await _read_json(session, f"http://{host}/json/state")
        segments = state.get("seg", [])
        if not isinstance(segments, list) or not segments:
            return False
        ids = [segment.get("id") for segment in segments]
        if not all(isinstance(segment_id, int) for segment_id in ids):
            return False
        payload = {
            "on": False,
            "tt": round(max(0, min(65, transition)) * 10),
            "bs": blend_mode,
            "seg": [{"id": segment_id, "on": False} for segment_id in ids],
        }
        async with session.post(f"http://{host}/json/state", json=payload, timeout=5) as response:
            response.raise_for_status()
        return True
    except Exception as err:
        _LOGGER.warning("Could not fade WLED light %s off: %s", entity_id, type(err).__name__)
        return False
