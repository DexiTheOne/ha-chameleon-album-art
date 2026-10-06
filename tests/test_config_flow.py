"""Tests for config_flow module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.chameleon.const import (
    CONF_LIGHT_ENTITIES,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_NORMALIZE_BRIGHTNESS,
    CONF_RANDOMIZE_COLOR_ASSIGNMENT,
    CONF_TRANSITION,
)

# Import mocked FlowResultType from conftest
from tests.conftest import FlowResultType

# Use mocked SOURCE_USER constant
SOURCE_USER = "user"


def _setup_config_flow(flow, hass):
    """Common setup for config flow tests."""
    flow.hass = hass
    flow.context = {"source": SOURCE_USER}
    # Make async methods return coroutines
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()


class TestChameleonConfigFlow:
    """Tests for ChameleonConfigFlow."""

    @pytest.mark.asyncio
    async def test_form_shows_on_init(self, hass: MagicMock):
        """Test that form is shown on initialization."""
        from custom_components.chameleon.config_flow import ChameleonConfigFlow

        flow = ChameleonConfigFlow()
        _setup_config_flow(flow, hass)

        # Patch async_show_form to capture what it's called with
        # since the actual voluptuous schema with selectors is HA-specific
        called_args = {}

        def mock_async_show_form(step_id, data_schema, errors=None):
            called_args["step_id"] = step_id
            called_args["errors"] = errors
            return {
                "type": FlowResultType.FORM,
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
            }

        flow.async_show_form = mock_async_show_form

        result = await flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {}
        fields = {key.schema for key in result["data_schema"].schema}
        assert CONF_RANDOMIZE_COLOR_ASSIGNMENT not in fields
        assert "interesting_colors" not in fields

    @pytest.mark.asyncio
    async def test_create_entry_single_light(self, hass: MagicMock):
        """Test creating entry with single light."""
        from custom_components.chameleon.config_flow import ChameleonConfigFlow

        flow = ChameleonConfigFlow()
        _setup_config_flow(flow, hass)

        # Mock the state for friendly name lookup
        mock_state = MagicMock()
        mock_state.attributes = {"friendly_name": "Bedroom Lamp"}
        hass.states.get.return_value = mock_state

        result = await flow.async_step_user(
            user_input={
                CONF_LIGHT_ENTITIES: ["light.bedroom_lamp"],
                CONF_TRANSITION: 5,
                CONF_MEDIA_PLAYER_ENTITY: "media_player.music",
            }
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"]
        assert result["data"][CONF_LIGHT_ENTITIES] == ["light.bedroom_lamp"]
        assert result["data"][CONF_TRANSITION] == 5
        assert result["data"][CONF_MEDIA_PLAYER_ENTITY] == "media_player.music"

    @pytest.mark.asyncio
    async def test_create_entry_multiple_lights(self, hass: MagicMock):
        """Test creating entry with multiple lights."""
        from custom_components.chameleon.config_flow import ChameleonConfigFlow

        flow = ChameleonConfigFlow()
        _setup_config_flow(flow, hass)

        # Mock friendly names
        def get_state(entity_id):
            names = {
                "light.one": "Light One",
                "light.two": "Light Two",
            }
            mock_state = MagicMock()
            mock_state.attributes = {"friendly_name": names.get(entity_id, entity_id)}
            return mock_state

        hass.states.get.side_effect = get_state

        result = await flow.async_step_user(
            user_input={
                CONF_LIGHT_ENTITIES: ["light.one", "light.two"],
                CONF_TRANSITION: 10,
            }
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_TRANSITION] == 10

    @pytest.mark.asyncio
    async def test_create_entry_many_lights(self, hass: MagicMock):
        """Test creating entry with many lights shows abbreviated title."""
        from custom_components.chameleon.config_flow import ChameleonConfigFlow

        flow = ChameleonConfigFlow()
        _setup_config_flow(flow, hass)

        # Mock friendly name for first light
        mock_state = MagicMock()
        mock_state.attributes = {"friendly_name": "First Light"}
        hass.states.get.return_value = mock_state

        result = await flow.async_step_user(
            user_input={
                CONF_LIGHT_ENTITIES: [
                    "light.one",
                    "light.two",
                    "light.three",
                    "light.four",
                ],
                CONF_TRANSITION: 5,
            }
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY


class TestChameleonOptionsFlow:
    """Tests for album-art source options."""

    @pytest.mark.asyncio
    async def test_save_media_player(self):
        """The selected media player is persisted in entry options."""
        from custom_components.chameleon.config_flow import ChameleonOptionsFlow

        flow = ChameleonOptionsFlow()
        flow.config_entry = MagicMock(options={CONF_RANDOMIZE_COLOR_ASSIGNMENT: True}, data={})
        result = await flow.async_step_init({CONF_MEDIA_PLAYER_ENTITY: "media_player.eversolo_dmp_a6"})

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"] == {
            CONF_MEDIA_PLAYER_ENTITY: "media_player.eversolo_dmp_a6",
            CONF_RANDOMIZE_COLOR_ASSIGNMENT: True,
            "interesting_colors": False,
            "send_palette_to_wled": False,
            "coverage_based_assignment": False,
            "use_averaged_color": False,
        }

    @pytest.mark.asyncio
    async def test_save_normalize_brightness(self):
        """The palette toggle is stored in the integration options."""
        from custom_components.chameleon.config_flow import ChameleonOptionsFlow

        flow = ChameleonOptionsFlow()
        flow.config_entry = MagicMock(options={CONF_RANDOMIZE_COLOR_ASSIGNMENT: True}, data={})
        result = await flow.async_step_init({CONF_NORMALIZE_BRIGHTNESS: True})

        assert result["data"] == {CONF_NORMALIZE_BRIGHTNESS: True, CONF_RANDOMIZE_COLOR_ASSIGNMENT: True, "interesting_colors": False, "send_palette_to_wled": False, "coverage_based_assignment": False, "use_averaged_color": False}

    @pytest.mark.asyncio
    async def test_interesting_colors_option_survives_configure(self):
        """The device switch setting survives an unrelated Configure change."""
        from custom_components.chameleon.config_flow import ChameleonOptionsFlow

        flow = ChameleonOptionsFlow()
        flow.config_entry = MagicMock(
            options={CONF_RANDOMIZE_COLOR_ASSIGNMENT: True, "interesting_colors": True}, data={}
        )
        result = await flow.async_step_init({CONF_NORMALIZE_BRIGHTNESS: True})
        assert result["data"]["interesting_colors"] is True
        assert "animation_enabled" not in result["data"]
        assert result["data"]["send_palette_to_wled"] is False

    @pytest.mark.asyncio
    async def test_randomize_color_assignment_is_not_in_options_form(self):
        """The switch is the only user-facing control for this setting."""
        from custom_components.chameleon.config_flow import ChameleonOptionsFlow

        flow = ChameleonOptionsFlow()
        flow.config_entry = MagicMock(options={CONF_RANDOMIZE_COLOR_ASSIGNMENT: True}, data={})
        result = await flow.async_step_init()

        fields = {key.schema for key in result["data_schema"].schema}
        assert CONF_RANDOMIZE_COLOR_ASSIGNMENT not in fields
        assert "interesting_colors" not in fields


@pytest.mark.asyncio
async def test_configure_preserves_independent_coverage_assignment():
    from custom_components.chameleon.config_flow import ChameleonOptionsFlow
    from custom_components.chameleon.const import CONF_COVERAGE_BASED_ASSIGNMENT
    flow = ChameleonOptionsFlow()
    flow.config_entry = MagicMock(options={CONF_COVERAGE_BASED_ASSIGNMENT: True, CONF_RANDOMIZE_COLOR_ASSIGNMENT: False}, data={})
    result = await flow.async_step_init({CONF_MEDIA_PLAYER_ENTITY: "media_player.test"})
    assert result["data"][CONF_COVERAGE_BASED_ASSIGNMENT] is True
    assert result["data"][CONF_RANDOMIZE_COLOR_ASSIGNMENT] is False



@pytest.mark.asyncio
async def test_configure_preserves_average_switch():
    from custom_components.chameleon.config_flow import ChameleonOptionsFlow
    flow = ChameleonOptionsFlow()
    flow.config_entry = MagicMock(options={"use_averaged_color": True, "interesting_colors": True}, data={})
    result = await flow.async_step_init({CONF_NORMALIZE_BRIGHTNESS: True})
    assert result["data"]["use_averaged_color"] is True
    assert result["data"]["interesting_colors"] is True


@pytest.mark.asyncio
async def test_membership_retains_order_identity_and_unrelated_options():
    from custom_components.chameleon.config_flow import ChameleonOptionsFlow
    from custom_components.chameleon.helpers import get_configured_lights

    flow = ChameleonOptionsFlow()
    flow.hass = MagicMock()
    entry = MagicMock(
        entry_id="existing-entry", unique_id="original-identity", title="Existing group",
        data={CONF_LIGHT_ENTITIES: ["light.a", "light.b"], "transition": 2.5},
        options={"light_entity_controls": {"light.a": {"brightness": 60}}, "custom_option": "kept"},
    )
    flow.config_entry = entry
    original_data = dict(entry.data)
    result = await flow.async_step_init({CONF_LIGHT_ENTITIES: ["light.b", "light.new", "light.a", "light.new"]})
    assert result["data"][CONF_LIGHT_ENTITIES] == ["light.a", "light.b", "light.new"]
    assert result["data"]["light_entity_controls"] == entry.options["light_entity_controls"]
    assert result["data"]["custom_option"] == "kept"
    assert entry.data == original_data
    assert entry.entry_id == "existing-entry"
    assert entry.unique_id == "original-identity"
    assert entry.title == "Existing group"
    entry.options = result["data"]
    assert get_configured_lights(entry) == ["light.a", "light.b", "light.new"]
    flow.hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_membership_removal_and_unrelated_save():
    from custom_components.chameleon.config_flow import ChameleonOptionsFlow
    flow = ChameleonOptionsFlow()
    flow.hass = MagicMock()
    flow.config_entry = MagicMock(data={CONF_LIGHT_ENTITIES: ["light.original"]}, options={CONF_LIGHT_ENTITIES: ["light.a", "light.b"]})
    result = await flow.async_step_init({CONF_LIGHT_ENTITIES: ["light.b"]})
    assert result["data"][CONF_LIGHT_ENTITIES] == ["light.b"]
    flow.config_entry.options = result["data"]
    result = await flow.async_step_init({CONF_NORMALIZE_BRIGHTNESS: True})
    assert result["data"][CONF_LIGHT_ENTITIES] == ["light.b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("selected,error", [([], "no_lights"), (["switch.a"], "invalid_light"), (None, "no_lights")])
async def test_invalid_membership_does_not_save(selected, error):
    from custom_components.chameleon.config_flow import ChameleonOptionsFlow
    flow = ChameleonOptionsFlow()
    flow.hass = MagicMock()
    flow.config_entry = MagicMock(data={CONF_LIGHT_ENTITIES: ["light.a"]}, options={})
    result = await flow.async_step_init({CONF_LIGHT_ENTITIES: selected})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_LIGHT_ENTITIES: error}
    assert flow.config_entry.options == {}


@pytest.mark.asyncio
async def test_chameleon_group_cannot_be_a_member():
    from unittest.mock import patch
    from custom_components.chameleon.config_flow import ChameleonOptionsFlow
    flow = ChameleonOptionsFlow()
    flow.hass = MagicMock()
    flow.config_entry = MagicMock(data={CONF_LIGHT_ENTITIES: ["light.a"]}, options={})
    with patch("custom_components.chameleon.config_flow.er.async_get") as registry:
        registry.return_value.async_get.return_value = MagicMock(platform="chameleon")
        result = await flow.async_step_init({CONF_LIGHT_ENTITIES: ["light.chameleon_other"]})
    assert result["errors"] == {CONF_LIGHT_ENTITIES: "invalid_light"}


def test_membership_falls_back_to_setup_and_legacy_data():
    from custom_components.chameleon.helpers import get_configured_lights
    assert get_configured_lights(MagicMock(options={}, data={CONF_LIGHT_ENTITIES: ["light.b", "light.a"]})) == ["light.b", "light.a"]
    assert get_configured_lights(MagicMock(options={}, data={"light_entity": "light.legacy"})) == ["light.legacy"]


@pytest.mark.asyncio
async def test_membership_form_uses_current_options():
    from custom_components.chameleon.config_flow import ChameleonOptionsFlow
    flow = ChameleonOptionsFlow()
    flow.config_entry = MagicMock(data={CONF_LIGHT_ENTITIES: ["light.old"]}, options={CONF_LIGHT_ENTITIES: ["light.new"]})
    result = await flow.async_step_init()
    membership = next(key for key in result["data_schema"].schema if key.schema == CONF_LIGHT_ENTITIES)
    assert membership.default() == ["light.new"]


@pytest.mark.asyncio
@pytest.mark.parametrize("platform,constructor", [
    ("light", "ChameleonLight"),
    ("number", "ChameleonTransitionNumber"),
    ("select", "ChameleonSceneSelect"),
    ("switch", "ChameleonRandomizeColorAssignmentSwitch"),
    ("button", "ChameleonRandomSceneButton"),
])
async def test_all_platforms_use_edited_membership(platform, constructor):
    import importlib
    from unittest.mock import patch
    module = importlib.import_module(f"custom_components.chameleon.{platform}")
    entry = MagicMock(data={CONF_LIGHT_ENTITIES: ["light.old"]}, options={CONF_LIGHT_ENTITIES: ["light.new"]})
    with patch.object(module, constructor) as entity_class, patch.object(module, "get_entity_base_name", return_value="test", create=True):
        await module.async_setup_entry(MagicMock(), entry, MagicMock())
    assert entity_class.call_args.args[2] == ["light.new"]
