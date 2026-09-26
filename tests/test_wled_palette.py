"""WLED palette requests must use image colors and preserve device effects."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.chameleon.wled_palette import (
    send_wled_palette,
    send_wled_power_off,
    send_wled_transition,
    three_palette_colors,
    wled_main_lights,
)


def test_wled_main_lights_resolves_renamed_parent_once():
    hass = MagicMock()
    entries = {
        "light.segment_1": SimpleNamespace(platform="wled", config_entry_id="entry", device_id="device", unique_id="mac_1"),
        "light.segment_2": SimpleNamespace(platform="wled", config_entry_id="entry", device_id="device", unique_id="mac_2"),
        "light.renamed_parent": SimpleNamespace(platform="wled", config_entry_id="entry", device_id="device", unique_id="mac"),
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
    def __init__(self, palette_id=5, segment_lengths=None):
        self.palette_id = palette_id
        self.segment_lengths = segment_lengths
        self.posts = []

    def get(self, url, **_kwargs):
        if url.endswith("/state"):
            return _Response({"seg": [
                {"id": index, "fx": 65, "pal": self.palette_id, "bm": 0,
                 **({"len": length} if self.segment_lengths is not None else {})}
                for index, length in enumerate(self.segment_lengths or [84, 84])
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
@pytest.mark.parametrize("duration, expected_tt", [(0, 0), (1.5, 15)])
async def test_native_transition_updates_every_segment_without_changing_effect(duration, expected_tt):
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session(palette_id=0)
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ):
        registry.return_value.async_get.return_value = SimpleNamespace(config_entry_id="wled-1")
        assert await send_wled_transition(hass, "light.one", [(255, 120, 30)], 0, 80, duration, 4)
    payload = session.posts[0]
    assert payload["tt"] == expected_tt
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


@pytest.mark.asyncio
@pytest.mark.parametrize("style", [0, 1, 2, 3, 4, 5, 16, 17])
@pytest.mark.parametrize("duration", [0, 2.5])
async def test_native_power_off_uses_selected_style_and_duration_for_all_segments(style, duration):
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session()
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ):
        registry.return_value.async_get.return_value = SimpleNamespace(config_entry_id="wled-1")
        with patch("custom_components.chameleon.wled_palette.asyncio.sleep", new_callable=AsyncMock) as sleep:
            assert await send_wled_power_off(hass, "light.one", duration, style)
            if duration:
                sleep.assert_awaited_once_with(duration)
            else:
                sleep.assert_not_awaited()
    final = {"on": False, "tt": 0, "bs": style,
             "seg": [{"id": i, "fx": 65, "pal": 5, "on": False} for i in [0, 1]]}
    if duration:
        assert session.posts == [{
            "seg": [{"id": i, "col": [[0, 0, 0, 0]] * 3, "fx": 0, "pal": 0} for i in [0, 1]],
            "tt": round(duration * 10), "bs": style,
        }, final]
    else:
        assert session.posts == [final]


@pytest.mark.asyncio
@pytest.mark.parametrize("style", [1, 2, 3, 4, 5, 16, 17])
async def test_single_led_segment_fades_on_and_off_with_selected_duration(style):
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session(segment_lengths=[84, 1])
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ):
        registry.return_value.async_get.return_value = SimpleNamespace(config_entry_id="wled-1")
        assert await send_wled_transition(hass, "light.one", [(255, 120, 30)], 0, 80, 1.5, style)
        with patch("custom_components.chameleon.wled_palette.asyncio.sleep", new_callable=AsyncMock):
            assert await send_wled_power_off(hass, "light.one", 1.5, style)
    assert [(payload["bs"], payload["tt"]) for payload in session.posts[:2]] == [(0, 15), (0, 15)]
    assert session.posts[2]["on"] is False and session.posts[2]["tt"] == 0
    assert all(segment["fx"] == 65 and segment["pal"] == 5 for segment in session.posts[2]["seg"])


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_shutdown_keeps_master_power_until_segment_animation_finishes(partial):
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session()
    members = ["light.segment"] if partial else None

    async def during_animation(duration):
        assert duration == 2.5
        assert len(session.posts) == 1
        assert "on" not in session.posts[0]
        expected_ids = [0] if partial else [0, 1]
        assert session.posts[0]["seg"] == [
            {"id": i, "col": [[0, 0, 0, 0]] * 3, "fx": 0, "pal": 0} for i in expected_ids
        ]

    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ), patch("custom_components.chameleon.wled_palette.asyncio.sleep", new_callable=AsyncMock,
             side_effect=during_animation) as sleep:
        registry.return_value.async_get.return_value = SimpleNamespace(config_entry_id="wled-1", unique_id="device_0")
        assert await send_wled_power_off(hass, "light.segment", 2.5, 4, members)
    sleep.assert_awaited_once_with(2.5)
    assert len(session.posts) == 2
    assert session.posts[1]["tt"] == 0
    assert session.posts[1]["seg"] == [
        {"id": i, "fx": 65, "pal": 5, "on": False} for i in ([0] if partial else [0, 1])
    ]
    if partial:
        assert "on" not in session.posts[1]
    else:
        assert session.posts[1]["on"] is False



@pytest.mark.asyncio
async def test_shutdown_reports_failed_master_finalization_for_service_fallback():
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session()
    original_post = session.post

    def post(url, **kwargs):
        if kwargs["json"].get("on") is False:
            raise OSError("Master unavailable")
        return original_post(url, **kwargs)

    session.post = post
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ), patch("custom_components.chameleon.wled_palette.asyncio.sleep", new_callable=AsyncMock):
        registry.return_value.async_get.return_value = SimpleNamespace(config_entry_id="wled-1")
        assert not await send_wled_power_off(hass, "light.one", 2.5, 4)
    assert "on" not in session.posts[0]


@pytest.mark.asyncio
async def test_shutdown_restores_effect_settings_even_when_cancelled():
    import asyncio

    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        entry_id="wled-1", domain="wled", data={"host": "10.0.0.5"}
    )
    session = _Session()
    source = {"id": 0, "on": True, "fx": 161, "pal": 4, "col": [[1, 2, 3, 4]] * 3,
              "bri": 123, "sx": 12, "ix": 45, "c1": 80, "c2": 90, "c3": 10,
              "o1": True, "o2": False, "o3": True, "si": 2, "m12": 0,
              "rev": True, "mi": True, "rY": True, "mY": True, "tp": True}
    session.get = lambda *_args, **_kwargs: _Response({"seg": [source]})
    with patch("custom_components.chameleon.wled_palette.er.async_get") as registry, patch(
        "custom_components.chameleon.wled_palette.async_get_clientsession", return_value=session
    ), patch("custom_components.chameleon.wled_palette.asyncio.sleep", new_callable=AsyncMock,
             side_effect=asyncio.CancelledError):
        registry.return_value.async_get.return_value = SimpleNamespace(config_entry_id="wled-1")
        with pytest.raises(asyncio.CancelledError):
            await send_wled_power_off(hass, "light.one", 2.5, 5)
    assert session.posts[0]["seg"][0]["fx"] == 0
    final = session.posts[1]["seg"][0]
    assert final == {key: value for key, value in {**source, "on": False}.items() if key != "bri"}
    assert session.posts[1]["on"] is False
