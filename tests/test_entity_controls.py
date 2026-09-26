"""Verify persistent exclusion and individual brightness controls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.chameleon.const import DOMAIN
from custom_components.chameleon.entity_controls import (
    LightEntityBrightness, LightEntitySwitch, controlled_entities, settings,
)
from custom_components.chameleon.wled_palette import send_wled_transition, send_wled_power_off
from tests.test_wled_palette import _Session


def test_discovery_excludes_renamed_wled_master():
    entities = {
        "light.master": SimpleNamespace(platform="wled", unique_id="mac"),
        "light.segment": SimpleNamespace(platform="wled", unique_id="mac_1"),
        "light.segment_zero": SimpleNamespace(platform="wled", unique_id="mac_0"),
        "light.other": SimpleNamespace(platform="hue", unique_id="hue_0"),
    }
    with patch("custom_components.chameleon.entity_controls.er.async_get") as registry:
        registry.return_value.async_get.side_effect = entities.get
        assert controlled_entities(MagicMock(), list(entities)) == ["light.segment", "light.segment_zero", "light.other"]


async def test_toggle_turns_off_immediately_and_persists_exclusion():
    entry = SimpleNamespace(entry_id="entry", options={"existing": 42})
    light = SimpleNamespace(_lock=asyncio.Lock(), async_reapply_current_scene=AsyncMock())
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": {"chameleon_light": light}}}
    hass.services.async_call = AsyncMock()
    hass.config_entries.async_update_entry.side_effect = lambda entry, options: setattr(entry, "options", options)
    switch = LightEntitySwitch(hass, entry, "light.one", "enabled")
    assert switch.is_on
    await switch.async_turn_off()
    hass.services.async_call.assert_awaited_once_with("light", "turn_off", {"entity_id": "light.one"}, blocking=True)
    assert not switch.is_on
    assert entry.options["existing"] == 42
    light.async_reapply_current_scene.assert_not_awaited()
    assert not LightEntitySwitch(hass, entry, "light.one", "enabled").is_on
    slider = LightEntityBrightness(hass, entry, "light.one", "brightness")
    await slider.async_set_native_value(50)
    assert slider.native_value == 50
    assert not settings(entry, "light.one")["enabled"]
    await switch.async_turn_on()
    assert switch.is_on
    assert settings(entry, "light.one")["brightness"] == 50
    assert light.async_reapply_current_scene.await_count == 2


async def test_wled_excluded_segments_receive_no_palette_or_power_commands():
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session()
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ):
        registry.return_value.async_get.return_value = SimpleNamespace(config_entry_id="wled-1", unique_id="mac_1")
        assert await send_wled_transition(
            hass, "light.segment", [(255, 0, 0)], 0, 80, 1, 4,
            {"light.segment": (255, 0, 0)}, {"light.segment": 40},
        )
        assert session.posts[0]["bri"] == 255
        assert [segment["id"] for segment in session.posts[0]["seg"]] == [1]
        assert session.posts[0]["seg"][0]["bri"] == 102
        assert await send_wled_power_off(hass, "light.segment", 1, 4, ["light.segment"])
        assert session.posts[1]["seg"] == [{"id": 1, "on": False}]
        assert "on" not in session.posts[1]


async def test_configure_preserves_individual_overrides():
    from custom_components.chameleon.config_flow import ChameleonOptionsFlow
    from custom_components.chameleon.entity_controls import OPTIONS_KEY
    flow = ChameleonOptionsFlow()
    overrides = {"light.one": {"enabled": False, "brightness": 50}}
    flow.config_entry = SimpleNamespace(options={OPTIONS_KEY: overrides}, data={})
    result = await flow.async_step_init({"normalize_brightness": True})
    assert result["data"][OPTIONS_KEY] == overrides


async def test_wled_segment_zero_is_controlled_and_all_managed_segments_power_off_master():
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session()
    entities = {
        "light.zero": SimpleNamespace(config_entry_id="wled-1", unique_id="mac_0"),
        "light.one": SimpleNamespace(config_entry_id="wled-1", unique_id="mac_1"),
    }
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ):
        registry.return_value.async_get.side_effect = entities.get
        assert await send_wled_transition(
            hass, "light.zero", [(255, 0, 0)], 0, 80, 1, 4,
            {"light.zero": (255, 0, 0)}, {"light.zero": 0}, send_palette=False,
        )
        assert session.posts[0]["seg"] == [{"id": 0, "on": False, "bri": 0, "col": [[255, 0, 0]]}]
        assert await send_wled_power_off(hass, "light.zero", 1, 4, list(entities))
        assert session.posts[1]["on"] is False
        assert session.posts[1]["seg"] == [{"id": 0, "on": False}, {"id": 1, "on": False}]


def test_control_names_use_registry_when_target_state_not_loaded():
    hass = MagicMock()
    hass.states.get.return_value = None
    entry = SimpleNamespace(entry_id="entry", options={}, data={})
    with patch("custom_components.chameleon.entity_controls.er.async_get") as registry, patch(
        "custom_components.chameleon.entity_controls.dr.async_get"
    ) as devices:
        registry.return_value.async_get.return_value = SimpleNamespace(
            device_id="device", name=None, original_name="Segment 1"
        )
        devices.return_value.async_get.return_value = SimpleNamespace(name_by_user=None, name="Media Console LED Strip")
        control = LightEntitySwitch(hass, entry, "light.one", "enabled")
        assert control._attr_name == "Media Console LED Strip Segment 1"
        registry.return_value.async_get.return_value.original_name = None
        control = LightEntityBrightness(hass, entry, "light.zero", "brightness")
        assert control._attr_name == "Media Console LED Strip"


async def test_palette_setting_in_configure_can_be_changed():
    from custom_components.chameleon.config_flow import ChameleonOptionsFlow
    flow = ChameleonOptionsFlow()
    flow.config_entry = SimpleNamespace(options={"send_palette_to_wled": True}, data={})
    result = await flow.async_step_init({"normalize_brightness": True, "send_palette_to_wled": False})
    assert result["data"]["send_palette_to_wled"] is False
    form = await flow.async_step_init()
    assert any(str(key) == "send_palette_to_wled" for key in form["data_schema"].schema)



def test_individual_controls_use_existing_chameleon_device():
    hass = MagicMock()
    entry = SimpleNamespace(entry_id="entry", options={}, data={"light_entities": ["light.one"]})
    with patch("custom_components.chameleon.entity_controls.get_chameleon_device_name", return_value="Common Area"):
        control = LightEntitySwitch(hass, entry, "light.one", "enabled")
        assert control.device_info["identifiers"] == {(DOMAIN, "entry")}
        assert "via_device" not in control.device_info
