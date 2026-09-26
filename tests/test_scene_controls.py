"""Verify scene controls share the light state and use its turn-on path."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The suite uses small HA stubs rather than a running HA installation.
from homeassistant.components.select import SelectEntity

button_module = MagicMock()
button_module.ButtonEntity = SelectEntity
sys.modules.setdefault("homeassistant.components.button", button_module)
exceptions_module = MagicMock()
exceptions_module.HomeAssistantError = RuntimeError
sys.modules.setdefault("homeassistant.exceptions", exceptions_module)

from custom_components.chameleon.button import ChameleonRandomSceneButton  # noqa: E402
from custom_components.chameleon.const import DOMAIN  # noqa: E402
from custom_components.chameleon.select import ChameleonSceneSelect  # noqa: E402


@pytest.fixture
def controls():
    entry = SimpleNamespace(entry_id="entry-1")
    light = SimpleNamespace(
        entity_id="light.renamed_chameleon", available=True,
        effect="Aquatic", effect_list=["Random", "Album Art", "Aquatic", "Sunset"],
        async_turn_on=AsyncMock(),
    )
    hass = MagicMock()
    hass.data = {DOMAIN: {entry.entry_id: {"chameleon_light": light}}}
    with patch("custom_components.chameleon.scene_control.get_entity_base_name", return_value="common_area"):
        scene = ChameleonSceneSelect(hass, entry, ["light.one"])
        button = ChameleonRandomSceneButton(hass, entry, ["light.one"])
    return hass, light, scene, button


async def test_button_uses_random_turn_on(controls):
    _, light, _, button = controls
    await button.async_press()
    light.async_turn_on.assert_awaited_once_with(effect="Random")
    assert button.entity_id == "button.chameleon_common_area_choose_random_scene"


async def test_select_applies_scene_and_tracks_resolved_random(controls):
    _, light, scene, _ = controls
    await scene.async_select_option("Random")
    light.async_turn_on.assert_awaited_once_with(effect="Random")
    light.effect = "Sunset"
    assert scene.current_option == "Sunset"
    light.effect = None
    assert scene.current_option is None
    light.effect_list.append("New Image")
    assert scene.options[-1] == "New Image"


async def test_invalid_scene_never_turns_on_light(controls):
    _, light, scene, _ = controls
    with pytest.raises(ValueError):
        await scene.async_select_option("Missing")
    light.async_turn_on.assert_not_awaited()


async def test_listener_tracks_actual_light_id_and_is_removed(controls):
    hass, light, scene, _ = controls
    scene.async_on_remove = MagicMock()
    scene.async_write_ha_state = MagicMock()
    await scene.async_added_to_hass()
    scene.async_on_remove.assert_called_once_with(hass.bus.async_listen.return_value)
    listener = hass.bus.async_listen.call_args.args[1]
    listener(SimpleNamespace(data={"entity_id": "light.other"}))
    scene.async_write_ha_state.assert_not_called()
    listener(SimpleNamespace(data={"entity_id": light.entity_id}))
    scene.async_write_ha_state.assert_called_once()


async def test_unavailable_light_and_no_random_images(controls):
    hass, light, scene, button = controls
    light.effect_list = ["Random", "Album Art"]
    assert not button.available
    light.available = False
    assert not scene.available
    with pytest.raises(RuntimeError):
        await button.async_press()
    hass.data[DOMAIN]["entry-1"].pop("chameleon_light")
    assert not button.available
    assert scene.options == []


async def test_random_wled_style_is_selectable_saved_and_restored():
    from custom_components.chameleon.select import ChameleonWledBlendSelect
    entry = SimpleNamespace(entry_id="entry", options={})
    hass = MagicMock()
    hass.data = {}
    with patch("custom_components.chameleon.select.get_entity_base_name", return_value="common_area"):
        control = ChameleonWledBlendSelect(hass, entry, [])
        assert "random" in control._attr_options
        await control.async_select_option("random")
        assert control.current_option == "random"
        assert hass.data[DOMAIN]["entry"]["wled_blend_style"] == "random"
        saved = hass.config_entries.async_update_entry.call_args.kwargs["options"]
        restored = ChameleonWledBlendSelect(hass, SimpleNamespace(entry_id="entry", options=saved), [])
        assert restored.current_option == "random"
