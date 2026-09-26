"""Scene and WLED transition style controls for Chameleon."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_LIGHT_ENTITIES,
    CONF_LIGHT_ENTITY,
    CONF_WLED_BLEND_STYLE,
    DEFAULT_WLED_BLEND_STYLE,
    DOMAIN,
    WLED_BLEND_STYLE_OPTIONS,
)
from .helpers import get_chameleon_device_name, get_entity_base_name
from .scene_control import ChameleonSceneControl

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Chameleon scene and transition style selects."""
    if CONF_LIGHT_ENTITIES in entry.data:
        light_entities = entry.data[CONF_LIGHT_ENTITIES]
    else:
        light_entities = [entry.data[CONF_LIGHT_ENTITY]]

    async_add_entities(
        [ChameleonSceneSelect(hass, entry, light_entities), ChameleonWledBlendSelect(hass, entry, light_entities)],
        True,
    )


def _entry_data(hass: HomeAssistant, entry_id: str) -> dict:
    """Return (creating if needed) the per-entry runtime dict in hass.data."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(entry_id, {})


class ChameleonWledBlendSelect(SelectEntity):
    """Select the native WLED blend used for scene changes."""

    _attr_has_entity_name = True
    _attr_translation_key = "wled_blend_style"
    _attr_icon = "mdi:gradient-horizontal"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, light_entities: list[str]) -> None:
        self.hass = hass
        self._entry = entry
        self._light_entities = light_entities
        self._attr_options = list(WLED_BLEND_STYLE_OPTIONS)
        saved = entry.options.get(CONF_WLED_BLEND_STYLE, DEFAULT_WLED_BLEND_STYLE)
        self._current_option = saved if saved in WLED_BLEND_STYLE_OPTIONS else DEFAULT_WLED_BLEND_STYLE
        _entry_data(hass, entry.entry_id)["wled_blend_style"] = self._current_option
        base_name = get_entity_base_name(hass, light_entities)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_wled_blend_style"
        self.entity_id = f"select.chameleon_{base_name}_wled_blend_style"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": get_chameleon_device_name(self.hass, self._light_entities),
            "manufacturer": "Chameleon",
            "model": "Scene Selector",
        }

    @property
    def current_option(self) -> str:
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        if option not in WLED_BLEND_STYLE_OPTIONS:
            return
        self._current_option = option
        _entry_data(self.hass, self._entry.entry_id)["wled_blend_style"] = option
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_WLED_BLEND_STYLE: option}
        )
        self.async_write_ha_state()


class ChameleonSceneSelect(ChameleonSceneControl, SelectEntity):
    """Mirror the light's effects for use in dashboard select cards."""

    _attr_translation_key = "scene"
    _attr_icon = "mdi:palette"

    def __init__(self, hass, entry, light_entities):
        super().__init__(hass, entry, light_entities, "select", "scene")

    @property
    def options(self) -> list[str]:
        light = self.chameleon_light
        return list(light.effect_list) if light is not None else []

    @property
    def current_option(self) -> str | None:
        light = self.chameleon_light
        return light.selected_scene if light is not None else None

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unknown Chameleon scene: {option}")
        await self.async_apply_scene(option)
