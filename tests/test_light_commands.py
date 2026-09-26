"""Exercise the group's actual command handlers and per-member routing."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.chameleon.light import ChameleonLight
from custom_components.chameleon.light_controller import LightResult
from custom_components.chameleon.const import DOMAIN


@pytest.fixture
def group():
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": {"transition": 0}}}
    hass.services.async_call = AsyncMock()
    entry = SimpleNamespace(entry_id="entry", options={"light_entity_controls": {
        "light.disabled": {"enabled": False}, "light.enabled": {"brightness": 50},
    }})
    with patch("custom_components.chameleon.light.get_entity_base_name", return_value="test"):
        light = ChameleonLight(hass, entry, ["light.disabled", "light.enabled"], 0)
    light._light_controller.apply_color_to_light = AsyncMock(
        return_value=LightResult("light.enabled", True, (255, 0, 0))
    )
    with patch("custom_components.chameleon.light.controlled_entities", side_effect=lambda hass, entities: entities), patch(
        "custom_components.chameleon.light.wled_entry_id", return_value=None
    ):
        yield light


async def test_manual_color_uses_multiplier_and_excludes_disabled_member(group):
    await group.async_turn_on(rgb_color=[255, 0, 0], brightness=204)
    group._light_controller.apply_color_to_light.assert_awaited_once_with(
        "light.enabled", (255, 0, 0), brightness=40, transition=0
    )
    assert group.is_on
    assert group.rgb_color == (255, 0, 0)
    assert group.effect is None
    assert group._last_error is None
    await group.async_turn_on(brightness=102)
    assert group._light_controller.apply_color_to_light.await_args.kwargs["brightness"] == 20


async def test_scene_palette_filters_disabled_member(group):
    result = await group._apply_palette_static([(255, 0, 0)], 80)
    assert result.all_succeeded
    group._light_controller.apply_color_to_light.assert_awaited_once_with(
        "light.enabled", (255, 0, 0), brightness=40, transition=0
    )


async def test_group_power_off_skips_disabled_member(group):
    await group.async_turn_off()
    group.hass.services.async_call.assert_awaited_once_with(
        "light", "turn_off", {"entity_id": "light.enabled"}, blocking=True
    )


async def test_all_excluded_can_save_manual_color_without_sending_commands(group):
    group._entry.options["light_entity_controls"]["light.enabled"]["enabled"] = False
    await group.async_turn_on(rgb_color=[255, 0, 0])
    group._light_controller.apply_color_to_light.assert_not_awaited()
    assert group.is_on
    assert group.rgb_color == (255, 0, 0)
    assert group._last_error is None


async def test_reapply_publishes_updated_group_state(group):
    group._is_on = True
    group._manual_color = (255, 0, 0)
    group.async_write_ha_state = MagicMock()
    await group.async_reapply_current_scene()
    group.async_write_ha_state.assert_called_once()
    assert list(group._applied_colors) == ["light.enabled"]
