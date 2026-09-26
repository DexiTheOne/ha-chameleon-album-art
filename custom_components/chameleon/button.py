"""Button platform for choosing a random image scene."""

from homeassistant.components.button import ButtonEntity

from .const import CONF_LIGHT_ENTITIES, CONF_LIGHT_ENTITY, SCENE_ALBUM_ART, SCENE_RANDOM
from .scene_control import ChameleonSceneControl


async def async_setup_entry(hass, entry, async_add_entities):
    light_entities = entry.data.get(CONF_LIGHT_ENTITIES)
    if light_entities is None:
        light_entities = [entry.data[CONF_LIGHT_ENTITY]]
    async_add_entities([ChameleonRandomSceneButton(hass, entry, light_entities)])


class ChameleonRandomSceneButton(ChameleonSceneControl, ButtonEntity):
    """Turn on the controlled lights with a newly chosen image palette."""

    _attr_translation_key = "choose_random_scene"
    _attr_icon = "mdi:shuffle-variant"

    def __init__(self, hass, entry, light_entities):
        super().__init__(hass, entry, light_entities, "button", "choose_random_scene")

    @property
    def available(self):
        light = self.chameleon_light
        return super().available and any(
            scene not in (SCENE_RANDOM, SCENE_ALBUM_ART) for scene in light.effect_list
        )

    async def async_press(self):
        await self.async_apply_scene(SCENE_RANDOM)
