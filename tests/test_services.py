"""The scene service must accept and route its advertised light target."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.chameleon import SERVICE_APPLY_SCENE_SCHEMA, _async_register_services


async def test_apply_scene_accepts_target_and_routes_duration_and_brightness():
    hass = MagicMock()
    hass.services.async_call = AsyncMock()
    await _async_register_services(hass)
    registration = next(call for call in hass.services.async_register.call_args_list if call.args[1] == "apply_scene")
    data = SERVICE_APPLY_SCENE_SCHEMA({
        "entity_id": "light.chameleon_common_area", "scene_name": "Aquatic",
        "brightness": 80, "transition": 1.5,
    })
    assert data["entity_id"] == ["light.chameleon_common_area"]
    await registration.args[2](SimpleNamespace(data=data))
    assert hass.services.async_call.await_args_list[0].args == (
        "number", "set_value", {"entity_id": "number.chameleon_common_area_transition", "value": 1.5},
    )
    assert hass.services.async_call.await_args_list[1].args == (
        "light", "turn_on", {"entity_id": "light.chameleon_common_area", "effect": "Aquatic", "brightness": 204},
    )
