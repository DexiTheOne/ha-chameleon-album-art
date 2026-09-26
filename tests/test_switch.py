"""Tests for the Random assignment control switch."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.chameleon.const import CONF_INTERESTING_COLORS, CONF_RANDOMIZE_COLOR_ASSIGNMENT, DOMAIN
from custom_components.chameleon.switch import ChameleonInterestingColorsSwitch, ChameleonRandomizeColorAssignmentSwitch


@pytest.mark.asyncio
async def test_switch_preserves_enabled_option_and_updates_light():
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {}
    entry.options = {CONF_RANDOMIZE_COLOR_ASSIGNMENT: True, "normalize_brightness": True}
    light = MagicMock()
    hass = MagicMock()
    hass.data = {DOMAIN: {entry.entry_id: {"chameleon_light": light}}}
    with patch("custom_components.chameleon.switch.get_entity_base_name", return_value="one"):
        control = ChameleonRandomizeColorAssignmentSwitch(hass, entry, ["light.one"])

    assert control.is_on is True
    await control.async_turn_off()

    hass.config_entries.async_update_entry.assert_called_once_with(
        entry,
        options={CONF_RANDOMIZE_COLOR_ASSIGNMENT: False, "normalize_brightness": True},
    )
    light.set_randomize_color_assignment.assert_called_once_with(False)
    assert control.is_on is False


@pytest.mark.asyncio
async def test_switch_does_not_write_when_state_is_unchanged():
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {}
    entry.options = {}
    hass = MagicMock()
    hass.data = {}
    with patch("custom_components.chameleon.switch.get_entity_base_name", return_value="one"):
        control = ChameleonRandomizeColorAssignmentSwitch(hass, entry, ["light.one"])

    await control.async_turn_off()

    hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_switch_turn_on_persists_new_value():
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {}
    entry.options = {"normalize_brightness": True}
    hass = MagicMock()
    hass.data = {}
    with patch("custom_components.chameleon.switch.get_entity_base_name", return_value="one"):
        control = ChameleonRandomizeColorAssignmentSwitch(hass, entry, ["light.one"])

    await control.async_turn_on()

    assert control.is_on is True
    hass.config_entries.async_update_entry.assert_called_once_with(
        entry,
        options={"normalize_brightness": True, CONF_RANDOMIZE_COLOR_ASSIGNMENT: True},
    )


@pytest.mark.asyncio
async def test_interesting_colors_switch_updates_light_and_preserves_options():
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {}
    entry.options = {"normalize_brightness": True}
    light = MagicMock()
    hass = MagicMock()
    hass.data = {DOMAIN: {entry.entry_id: {"chameleon_light": light}}}
    with patch("custom_components.chameleon.switch.get_entity_base_name", return_value="one"):
        control = ChameleonInterestingColorsSwitch(hass, entry, ["light.one"])
    assert control.is_on is False
    await control.async_turn_on()
    hass.config_entries.async_update_entry.assert_called_once_with(
        entry, options={"normalize_brightness": True, CONF_INTERESTING_COLORS: True}
    )
    light.set_interesting_colors.assert_called_once_with(True)
