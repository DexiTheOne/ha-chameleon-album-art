"""Shared scene controls backed by the entry's registered light entity."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .helpers import get_chameleon_device_name, get_entity_base_name


class ChameleonSceneControl:
    """Follow the light without keeping a second copy of its scene state."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass, entry, light_entities, domain, suffix):
        self.hass = hass
        self._entry = entry
        self._light_entities = light_entities
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{suffix}"
        base_name = get_entity_base_name(hass, light_entities)
        self.entity_id = f"{domain}.chameleon_{base_name}_{suffix}"

    @property
    def chameleon_light(self):
        return self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {}).get("chameleon_light")

    @property
    def available(self):
        light = self.chameleon_light
        return light is not None and light.available

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": get_chameleon_device_name(self.hass, self._light_entities),
            "manufacturer": "Chameleon",
            "model": "Scene Selector",
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen("state_changed", self._handle_light_change))

    @callback
    def _handle_light_change(self, event):
        light = self.chameleon_light
        if light is not None and event.data.get("entity_id") == light.entity_id:
            self.async_write_ha_state()

    async def async_apply_scene(self, scene):
        light = self.chameleon_light
        if light is None or not light.available:
            raise HomeAssistantError("Chameleon light is unavailable")
        await light.async_turn_on(effect=scene)
        self.async_write_ha_state()
