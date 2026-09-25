"""WLED palette requests must use image colors and preserve device effects."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.chameleon.wled_palette import send_wled_palette, send_wled_transition, three_palette_colors, wled_main_lights


def test_wled_main_lights_resolves_renamed_parent_once():
    hass = MagicMock()
    entries = {
        "light.segment_1": SimpleNamespace(platform="wled", config_entry_id="entry", device_id="device", unique_id="mac_1"),
        "light.segment_2": SimpleNamespace(platform="wled", config_entry_id="entry", device_id="device", unique_id="mac_2"),
        "light.renamed_parent": SimpleNamespace(platform="wled", config_entry_id="entry", device_id="device", unique_id="mac_0"),
    }
    with patch("custom_components.chameleon.wled_palette.er.async_get") as get_registry:
        registry = get_registry.return_value
        registry.async_get.side_effect = entries.get
        registry.async_get_entity_id.return_value = "light.renamed_parent"
        assert wled_main_lights(hass, ["light.segment_1", "light.segment_2"]) == ["light.renamed_parent"]
        assert wled_main_lights(hass, ["light.segment_1", "light.renamed_parent"]) == []


def test_three_palette_colors_repeats_source_swatches():
    assert three_palette_colors([]) == []
    assert three_palette_colors([(255, 120, 30)]) == [(255, 120, 30)] * 3
    assert three_palette_colors([(255, 120, 30), (255, 120, 30), (255, 100, 20)]) == [
        (255, 120, 30), (255, 100, 20), (255, 120, 30)
    ]
    source = [(255, 120, 30), (255, 100, 20), (255, 80, 10)]
    assert three_palette_colors(source, 1) == [source[1], source[2], source[0]]


class _Response:
    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        return self.data

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self, palette_id=5):
        self.palette_id = palette_id
        self.posts = []

    def get(self, url, **_kwargs):
        if url.endswith("/state"):
            return _Response({"seg": [
                {"id": 0, "fx": 65, "pal": self.palette_id, "bm": 0},
                {"id": 1, "fx": 65, "pal": self.palette_id, "bm": 0},
            ]})
        return _Response([""] * 65 + ["Shift,Size,Rotation;;!;12"])

    def post(self, _url, **kwargs):
        self.posts.append(kwargs["json"])
        return _Response({"success": True})


@pytest.mark.asyncio
async def test_send_palette_preserves_effect_and_updates_all_segments():
    hass = MagicMock()
    entry = SimpleNamespace(entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"})
    hass.config_entries.async_get_entry.return_value = entry
    entity = SimpleNamespace(config_entry_id="wled-1")
    session = _Session()
    source = [(255, 120, 30), (255, 100, 20), (255, 80, 10)]
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ):
        registry.return_value.async_get.return_value = entity
        assert await send_wled_palette(hass, "light.one", source, 0) == "wled-1"
    assert [segment["id"] for segment in session.posts[0]["seg"]] == [0, 1]
    assert all(segment["col"] == [list(color) for color in source] for segment in session.posts[0]["seg"])
    assert all("fx" not in segment and "pal" not in segment for segment in session.posts[0]["seg"])


@pytest.mark.asyncio
async def test_send_palette_skips_unconfigured_device():
    hass = MagicMock()
    entry = SimpleNamespace(entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"})
    hass.config_entries.async_get_entry.return_value = entry
    session = _Session(palette_id=0)
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ):
        registry.return_value.async_get.return_value = SimpleNamespace(config_entry_id="wled-1")
        assert await send_wled_palette(
            hass, "light.one", [(255, 120, 30), (255, 100, 20), (255, 80, 10)], 0
        ) is None
    assert session.posts == []


@pytest.mark.asyncio
async def test_palette_preflight_does_not_write_colors():
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session()
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ):
        registry.return_value.async_get.return_value = SimpleNamespace(config_entry_id="wled-1")
        assert await send_wled_palette(
            hass, "light.one", [(255, 120, 30)], 0, check_only=True
        ) == "wled-1"
    assert session.posts == []


@pytest.mark.asyncio
async def test_palette_update_uses_one_request_transition():
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session()
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ):
        registry.return_value.async_get.return_value = SimpleNamespace(config_entry_id="wled-1")
        assert await send_wled_palette(
            hass, "light.one", [(255, 120, 30)], 0, transition=1.5
        ) == "wled-1"
    assert session.posts[0]["tt"] == 15
    assert all(segment["bm"] == 4 for segment in session.posts[0]["seg"])
    assert all("fx" not in segment and "pal" not in segment for segment in session.posts[0]["seg"])


@pytest.mark.asyncio
async def test_single_source_color_replaces_all_three_slots():
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session()
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ):
        registry.return_value.async_get.return_value = SimpleNamespace(config_entry_id="wled-1")
        assert await send_wled_palette(hass, "light.one", [(255, 120, 30)], 0) == "wled-1"
    assert all(segment["col"] == [[255, 120, 30]] * 3 for segment in session.posts[0]["seg"])
    assert all("fx" not in segment and "pal" not in segment for segment in session.posts[0]["seg"])


@pytest.mark.asyncio
async def test_native_transition_updates_every_segment_without_changing_effect():
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session(palette_id=0)
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ):
        registry.return_value.async_get.return_value = SimpleNamespace(config_entry_id="wled-1")
        assert await send_wled_transition(hass, "light.one", [(255, 120, 30)], 0, 80, 1.5, 4)
    payload = session.posts[0]
    assert payload["tt"] == 15
    assert payload["bs"] == 4
    assert payload["bri"] == 204
    assert payload["on"] is True
    assert [segment["id"] for segment in payload["seg"]] == [0, 1]
    assert all(segment["on"] is True and "bm" not in segment for segment in payload["seg"])
    assert all(segment["col"] == [[255, 120, 30]] * 3 for segment in payload["seg"])
    assert all("fx" not in segment and "pal" not in segment for segment in payload["seg"])


@pytest.mark.asyncio
async def test_native_transition_keeps_each_configured_segment_color():
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session(palette_id=0)
    entities = {
        "light.main": SimpleNamespace(config_entry_id="wled-1", unique_id="device_0"),
        "light.segment_1": SimpleNamespace(config_entry_id="wled-1", unique_id="device_1"),
    }
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ):
        registry.return_value.async_get.side_effect = entities.get
        assert await send_wled_transition(
            hass, "light.main", [(255, 0, 0), (0, 0, 255)], 0, 100, 2, 0,
            {"light.main": (255, 0, 0), "light.segment_1": (0, 0, 255)},
        )
    segments = session.posts[0]["seg"]
    assert segments[0]["col"][0] == [255, 0, 0]
    assert segments[1]["col"][0] == [0, 0, 255]
    assert session.posts[0]["bs"] == 0
    assert all("bm" not in segment for segment in segments)
