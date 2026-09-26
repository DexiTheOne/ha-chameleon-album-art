"""Transition duration sent to WLED for each scene or color change."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_LIGHT_ENTITIES,
    CONF_LIGHT_ENTITY,
    CONF_TRANSITION,
    DEFAULT_TRANSITION,
    DOMAIN,
    MAX_TRANSITION,
    MIN_TRANSITION,
)
from .entity_controls import LightEntityBrightness, controlled_entities
from .helpers import get_chameleon_device_name, get_entity_base_name

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Chameleon transition number entity from a config entry."""
    if CONF_LIGHT_ENTITIES in entry.data:
        light_entities = entry.data[CONF_LIGHT_ENTITIES]
    else:
        light_entities = [entry.data[CONF_LIGHT_ENTITY]]

    initial_transition = entry.options.get(CONF_TRANSITION, entry.data.get(CONF_TRANSITION, DEFAULT_TRANSITION))

    async_add_entities(
        [ChameleonTransitionNumber(hass, entry, light_entities, initial_transition),
         *[LightEntityBrightness(hass, entry, entity, "brightness") for entity in controlled_entities(hass, light_entities)]],
        True,
    )


def _entry_data(hass: HomeAssistant, entry_id: str) -> dict:
    """Return (creating if needed) the per-entry runtime dict in hass.data."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(entry_id, {})


class ChameleonTransitionNumber(NumberEntity):
    """Transition slider. Value of 0 applies colors without a fade."""

    _attr_has_entity_name = True
    _attr_translation_key = "transition"
    _attr_native_min_value = MIN_TRANSITION
    _attr_native_max_value = MAX_TRANSITION
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "s"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:transition"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        light_entities: list[str],
        initial_transition: float,
    ) -> None:
        """Initialize the transition number entity."""
        self.hass = hass
        self._entry = entry
        self._light_entities = light_entities
        # Clamp to the current allowed range — older config entries may have stored
        # values from a wider range (the slider used to go up to 60s).
        self._transition = max(MIN_TRANSITION, min(MAX_TRANSITION, float(initial_transition)))

        # Seed runtime data so the light's initial read sees a valid transition.
        _entry_data(hass, entry.entry_id)["transition"] = self._transition

        base_name = get_entity_base_name(hass, light_entities)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_transition"
        self.entity_id = f"number.chameleon_{base_name}_transition"

    @property
    def device_info(self):
        """Return device info for this entity."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": get_chameleon_device_name(self.hass, self._light_entities),
            "manufacturer": "Chameleon",
            "model": "Scene Selector",
        }

    @property
    def native_value(self) -> float:
        """Return the current transition (seconds per fade)."""
        return self._transition

    async def async_set_native_value(self, value: float) -> None:
        """Save the duration for subsequent WLED requests."""
        new_value = max(MIN_TRANSITION, min(MAX_TRANSITION, round(float(value), 1)))
        previous = self._transition
        self._transition = new_value

        _entry_data(self.hass, self._entry.entry_id)["transition"] = new_value
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_TRANSITION: new_value}
        )

        _LOGGER.info(
            "Transition %.1fs → %.1fs for %s",
            previous,
            new_value,
            self._light_entities,
        )

        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        return {
            "light_entities": self._light_entities,
        }
