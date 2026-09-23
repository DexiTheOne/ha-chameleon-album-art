"""Switch control for Random-scene color assignment."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_LIGHT_ENTITIES,
    CONF_LIGHT_ENTITY,
    CONF_RANDOMIZE_COLOR_ASSIGNMENT,
    DEFAULT_RANDOMIZE_COLOR_ASSIGNMENT,
    DOMAIN,
)
from .helpers import get_chameleon_device_name, get_entity_base_name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the assignment switch to the existing Chameleon device."""
    light_entities = entry.data.get(CONF_LIGHT_ENTITIES) or [entry.data[CONF_LIGHT_ENTITY]]
    async_add_entities([ChameleonRandomizeColorAssignmentSwitch(hass, entry, light_entities)], True)


class ChameleonRandomizeColorAssignmentSwitch(SwitchEntity):
    """Choose whether Random shuffles palette colors across lights."""

    _attr_has_entity_name = True
    _attr_translation_key = "randomize_color_assignment"
    _attr_icon = "mdi:shuffle-variant"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, light_entities: list[str]) -> None:
        self.hass = hass
        self._entry = entry
        self._light_entities = light_entities
        self._enabled = entry.options.get(
            CONF_RANDOMIZE_COLOR_ASSIGNMENT,
            entry.data.get(CONF_RANDOMIZE_COLOR_ASSIGNMENT, DEFAULT_RANDOMIZE_COLOR_ASSIGNMENT),
        )
        base_name = get_entity_base_name(hass, light_entities)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_randomize_color_assignment"
        self.entity_id = f"switch.chameleon_{base_name}_randomize_color_assignment"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": get_chameleon_device_name(self.hass, self._light_entities),
            "manufacturer": "Chameleon",
            "model": "Scene Selector",
        }

    @property
    def is_on(self) -> bool:
        return self._enabled

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_enabled(False)

    async def _set_enabled(self, enabled: bool) -> None:
        if self._enabled == enabled:
            return
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_RANDOMIZE_COLOR_ASSIGNMENT: enabled},
        )
        self._enabled = enabled
        light = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {}).get("chameleon_light")
        if light is not None:
            light.set_randomize_color_assignment(enabled)
        self.async_write_ha_state()
