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
        }

    @pytest.mark.asyncio
    async def test_save_normalize_brightness(self):
        """The palette toggle is stored in the integration options."""
        from custom_components.chameleon.config_flow import ChameleonOptionsFlow

        flow = ChameleonOptionsFlow()
        flow.config_entry = MagicMock(options={CONF_RANDOMIZE_COLOR_ASSIGNMENT: True}, data={})
        result = await flow.async_step_init({CONF_NORMALIZE_BRIGHTNESS: True})

        assert result["data"] == {CONF_NORMALIZE_BRIGHTNESS: True, CONF_RANDOMIZE_COLOR_ASSIGNMENT: True, "interesting_colors": False, "send_palette_to_wled": False}

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
