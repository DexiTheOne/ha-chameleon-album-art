"""Move controls onto the retained device without changing their identities."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.chameleon import async_migrate_entry
from custom_components.chameleon.const import DOMAIN


@pytest.mark.parametrize("unrelated_child_entity", [False, True])
async def test_v6_moves_controls_and_retires_only_palette_switch(unrelated_child_entity):
    entry = SimpleNamespace(entry_id="entry", version=5, data={}, options={"send_palette_to_wled": True})
    hass = MagicMock()
    entries = [
        SimpleNamespace(entity_id="number.renamed", device_id="child", config_entry_id="entry", unique_id="individual-number"),
        SimpleNamespace(entity_id="switch.renamed", device_id="child", config_entry_id="entry", unique_id="individual-switch"),
        SimpleNamespace(entity_id="light.chameleon", device_id="parent", config_entry_id="entry", unique_id="existing-light"),
        SimpleNamespace(entity_id="switch.palette", device_id="parent", config_entry_id="entry", unique_id=f"{DOMAIN}_entry_send_palette_to_wled"),
    ]
    if unrelated_child_entity:
        entries.append(SimpleNamespace(entity_id="sensor.other", device_id="child", config_entry_id="other", unique_id="other"))
    registry = MagicMock()
    registry.async_remove.side_effect = lambda entity_id: entries.remove(next(e for e in entries if e.entity_id == entity_id))
    def move(entity_id, device_id):
        next(e for e in entries if e.entity_id == entity_id).device_id = device_id
    registry.async_update_entity.side_effect = move
    with patch("custom_components.chameleon.er.async_get", return_value=registry), patch(
        "custom_components.chameleon.er.async_entries_for_config_entry", side_effect=lambda registry, entry_id: [e for e in entries if e.config_entry_id == entry_id]
    ), patch("custom_components.chameleon.er.async_entries_for_device", side_effect=lambda registry, device_id: [e for e in entries if e.device_id == device_id]), patch(
        "custom_components.chameleon.dr.async_get"
    ) as devices:
        devices.return_value.async_get_device.side_effect = lambda identifiers: SimpleNamespace(
            id="parent" if (DOMAIN, "entry") in identifiers else "child"
        )
        assert await async_migrate_entry(hass, entry)
        registry.async_remove.assert_called_once_with("switch.palette")
        assert [e.entity_id for e in entries[:3]] == ["number.renamed", "switch.renamed", "light.chameleon"]
        assert all(e.device_id == "parent" for e in entries[:3])
        if unrelated_child_entity:
            devices.return_value.async_remove_device.assert_not_called()
        else:
            devices.return_value.async_remove_device.assert_called_once_with("child")
        hass.config_entries.async_update_entry.assert_called_once_with(entry, version=6)
        assert entry.options["send_palette_to_wled"] is True
