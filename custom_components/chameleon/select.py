"""Select platform for Chameleon — transition style picker.

The scene picker lives on the ``light`` platform (``effect``/``effect_list``);
this file just hosts the synchronized-vs-staggered transition style select.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_LIGHT_ENTITIES,
    CONF_LIGHT_ENTITY,
    CONF_TRANSITION_STYLE,
    CONF_WLED_BLEND_STYLE,
    DEFAULT_TRANSITION_STYLE,
    DEFAULT_WLED_BLEND_STYLE,
    DOMAIN,
    TRANSITION_STYLES,
    WLED_BLEND_STYLES,
)
from .helpers import get_chameleon_device_name, get_entity_base_name

if TYPE_CHECKING:
    from .animations import AnimationManager

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Chameleon transition style select from a config entry."""
    if CONF_LIGHT_ENTITIES in entry.data:
        light_entities = entry.data[CONF_LIGHT_ENTITIES]
    else:
        light_entities = [entry.data[CONF_LIGHT_ENTITY]]

    async_add_entities(
        [ChameleonTransitionStyleSelect(hass, entry, light_entities), ChameleonWledBlendSelect(hass, entry, light_entities)],
        True,
    )


def _entry_data(hass: HomeAssistant, entry_id: str) -> dict:
    """Return (creating if needed) the per-entry runtime dict in hass.data."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(entry_id, {})


class ChameleonTransitionStyleSelect(SelectEntity):
    """Transition style picker — synchronized vs staggered."""

    _attr_has_entity_name = True
    _attr_translation_key = "transition_style"
    _attr_icon = "mdi:animation"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        light_entities: list[str],
    ) -> None:
        """Initialize the transition style select."""
        self.hass = hass
        self._entry = entry
        self._light_entities = light_entities
        self._attr_options = list(TRANSITION_STYLES)
        saved = entry.options.get(CONF_TRANSITION_STYLE, DEFAULT_TRANSITION_STYLE)
        self._current_option: str = saved if saved in TRANSITION_STYLES else DEFAULT_TRANSITION_STYLE

        # Seed runtime data so the light entity sees the right style immediately.
        _entry_data(hass, entry.entry_id)["transition_style"] = self._current_option

        base_name = get_entity_base_name(hass, light_entities)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_transition_style"
        self.entity_id = f"select.chameleon_{base_name}_transition_style"

    def _get_animation_manager(self) -> AnimationManager | None:
        """Get the AnimationManager from hass.data."""
        return self.hass.data.get(DOMAIN, {}).get("animation_manager")

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
    def current_option(self) -> str:
        """Return the currently selected style."""
        return self._current_option

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        return {"light_entities": self._light_entities}

    async def async_select_option(self, option: str) -> None:
        """Handle a style change.

        If an animation is running, push the new style in live. Otherwise the
        new style just takes effect on the next scene change.
        """
        if option not in TRANSITION_STYLES:
            _LOGGER.warning("Unknown transition style: %s", option)
            return

        previous = self._current_option
        self._current_option = option
        _entry_data(self.hass, self._entry.entry_id)["transition_style"] = option
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_TRANSITION_STYLE: option}
        )
        _LOGGER.info("Transition style set to '%s' for %s", option, self._light_entities)

        manager = self._get_animation_manager()
        if previous != option and "wled" in (previous, option):
            light = _entry_data(self.hass, self._entry.entry_id).get("chameleon_light")
            if light is not None:
                await light.async_reapply_current_scene()
        elif manager and manager.is_running(self._entry.entry_id):
            manager.update_style(self._entry.entry_id, option)

        self.async_write_ha_state()


class ChameleonWledBlendSelect(SelectEntity):
    """Select the native WLED blend used for scene changes."""

    _attr_has_entity_name = True
    _attr_translation_key = "wled_blend_style"
    _attr_icon = "mdi:gradient-horizontal"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, light_entities: list[str]) -> None:
        self.hass = hass
        self._entry = entry
        self._light_entities = light_entities
        self._attr_options = list(WLED_BLEND_STYLES)
        saved = entry.options.get(CONF_WLED_BLEND_STYLE, DEFAULT_WLED_BLEND_STYLE)
        self._current_option = saved if saved in WLED_BLEND_STYLES else DEFAULT_WLED_BLEND_STYLE
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
        if option not in WLED_BLEND_STYLES:
            return
        self._current_option = option
        _entry_data(self.hass, self._entry.entry_id)["wled_blend_style"] = option
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_WLED_BLEND_STYLE: option}
        )
        self.async_write_ha_state()
