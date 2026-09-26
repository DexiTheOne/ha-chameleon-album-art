"""Persistent controls for individual configured lights."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr

from .const import CONF_LIGHT_ENTITIES, CONF_LIGHT_ENTITY, DOMAIN
from .helpers import get_chameleon_device_name

OPTIONS_KEY = "light_entity_controls"


def wled_segment_id(entity):
    """WLED masters use the MAC alone; numbered suffixes identify segments."""
    if entity is None:
        return None
    _, separator, suffix = entity.unique_id.rpartition("_")
    return int(suffix) if separator and suffix.isdigit() else None


def controlled_entities(hass, entities):
    """Exclude WLED master entities, preserving configured segment order."""
    registry = er.async_get(hass)
    return [entity_id for entity_id in entities if not (
        (entity := registry.async_get(entity_id)) is not None
        and entity.platform == "wled" and wled_segment_id(entity) is None
    )]


def settings(entry, entity_id):
    return entry.options.get(OPTIONS_KEY, {}).get(entity_id, {})


class LightEntityControl:
    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass, entry, entity_id, suffix):
        self.hass = hass
        self._entry = entry
        self._target = entity_id
        state = hass.states.get(entity_id)
        name = state.attributes.get("friendly_name") if state else None
        if not name:
            target = er.async_get(hass).async_get(entity_id)
            device = dr.async_get(hass).async_get(target.device_id) if target and target.device_id else None
            device_name = (device.name_by_user or device.name) if device else None
            if target and target.name:
                name = target.name
            elif target and target.original_name:
                name = " ".join(part for part in (device_name, target.original_name) if part)
            else:
                name = device_name
        name = name or entity_id.split(".", 1)[-1].replace("_", " ").title()
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{entity_id}_{suffix}"

    @property
    def device_info(self):
        entities = self._entry.data.get(CONF_LIGHT_ENTITIES) or [
            self._entry.data.get(CONF_LIGHT_ENTITY, self._target)
        ]
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": get_chameleon_device_name(self.hass, entities),
            "manufacturer": "Chameleon",
            "model": "Scene Selector",
        }

    async def _save(self, key, value):
        light = self.hass.data[DOMAIN][self._entry.entry_id]["chameleon_light"]
        async with light._lock:
            controls = dict(self._entry.options.get(OPTIONS_KEY, {}))
            controls[self._target] = {**controls.get(self._target, {}), key: value}
            self.hass.config_entries.async_update_entry(
                self._entry, options={**self._entry.options, OPTIONS_KEY: controls}
            )
            if key == "enabled" and not value:
                await self.hass.services.async_call(
                    "light", "turn_off", {"entity_id": self._target}, blocking=True
                )
        if key != "enabled" or value:
            await light.async_reapply_current_scene()
        self.async_write_ha_state()


class LightEntitySwitch(LightEntityControl, SwitchEntity):
    _attr_icon = "mdi:lightbulb"

    @property
    def is_on(self):
        return settings(self._entry, self._target).get("enabled", True)

    async def async_turn_on(self, **kwargs):
        await self._save("enabled", True)

    async def async_turn_off(self, **kwargs):
        await self._save("enabled", False)


class LightEntityBrightness(LightEntityControl, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:brightness-percent"

    @property
    def native_value(self):
        return settings(self._entry, self._target).get("brightness", 100)

    async def async_set_native_value(self, value):
        await self._save("brightness", max(0, min(100, round(value))))
