"""Exercise the group's actual command handlers and per-member routing."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.chameleon.const import DOMAIN
from custom_components.chameleon.light import ChameleonLight
from custom_components.chameleon.light_controller import LightResult


@pytest.fixture
async def group():
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": {"transition": 0}}}
    hass.services.async_call = AsyncMock()
    hass.async_create_background_task = lambda coroutine, name: asyncio.create_task(coroutine, name=name)
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
        try:
            yield light
        finally:
            await light.async_will_remove_from_hass()


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
        "light", "turn_off", {"entity_id": "light.enabled", "transition": 0}, blocking=True
    )


async def test_non_wled_commands_keep_configured_transition_duration(group):
    group.hass.data[DOMAIN]["entry"]["transition"] = 2.5
    await group.async_turn_on(rgb_color=[255, 0, 0])
    assert group._light_controller.apply_color_to_light.await_args.kwargs["transition"] == 2.5
    await group.async_turn_off()
    assert group.hass.services.async_call.await_args.args[2]["transition"] == 2.5


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


async def test_ten_moves_include_active_transition_and_discard_eleventh(group):
    group.hass.data[DOMAIN]["entry"]["transition"] = 2.5
    started = []
    durations = []
    gates = asyncio.Queue()
    sleep_started = asyncio.Queue()

    async def wait_transition(duration):
        durations.append(duration)
        sleep_started.put_nowait(True)
        await gates.get()

    async def apply(**kwargs):
        started.append(kwargs["effect"])

    group._do_turn_on = apply
    with patch("custom_components.chameleon.light.asyncio.sleep", side_effect=wait_transition):
        first = asyncio.create_task(group.async_turn_on(effect="Random"))
        await first
        await sleep_started.get()
        pending = [asyncio.create_task(group.async_turn_on(effect=f"Scene {i}")) for i in range(9)]
        # An event-loop barrier admits all pending service calls without timing assumptions.
        admitted = asyncio.Event()
        asyncio.get_running_loop().call_soon(admitted.set)
        await admitted.wait()
        await group.async_turn_on(effect="Discarded")
        assert len(group._transition_queue) == 10
        assert started == ["Random"]
        for i in range(9):
            gates.put_nowait(True)
            await sleep_started.get()
            assert started[-1] == f"Scene {i}"
        gates.put_nowait(True)
        await group._transition_worker
        await asyncio.gather(*pending)
    assert started == ["Random", *(f"Scene {i}" for i in range(9))]
    assert durations == [2.5] * 10
    assert not group._transition_queue
    group.hass.data[DOMAIN]["entry"]["transition"] = 0
    await group.async_turn_on(effect="Next")
    await group._transition_worker
    assert started[-1] == "Next"


async def test_scenes_keep_order_and_waiting_artwork_is_replaced(group):
    from custom_components.chameleon.const import SCENE_ALBUM_ART
    group._is_on = True
    group._effect = SCENE_ALBUM_ART
    order = []
    group._media_player_entity = "media_player.test"

    async def scene(**kwargs):
        order.append(kwargs["effect"])

    async def artwork(**kwargs):
        order.append(kwargs["media_state"].attributes["entity_picture"])

    group._do_turn_on = scene
    group._apply_album_art = artwork
    cover_a = SimpleNamespace(attributes={"entity_picture": "/a.jpg"})
    cover_b = SimpleNamespace(attributes={"entity_picture": "/b.jpg"})
    group.hass.states.get.return_value = cover_b
    await asyncio.gather(
        group.async_turn_on(effect="Random"),
        group.async_apply_album_art_update(cover_a),
        group.async_turn_on(effect="Selected scene"),
        group.async_apply_album_art_update(cover_b),
    )
    await group._transition_worker
    assert order == ["Random", "Selected scene", "/b.jpg"]


async def test_queue_continues_after_failed_move_and_cleans_up_on_unload(group):
    async def fail():
        raise ValueError("test failure")
    with pytest.raises(ValueError):
        await group._queue_transition(fail)
    await group.async_turn_on(rgb_color=[255, 0, 0])
    await group._transition_worker
    assert group.is_on
    group.hass.data[DOMAIN]["entry"]["transition"] = 10
    await group.async_turn_off()
    queued = asyncio.create_task(group.async_turn_on(rgb_color=[0, 255, 0]))
    barrier = asyncio.Event()
    asyncio.get_running_loop().call_soon(barrier.set)
    await barrier.wait()
    await group.async_will_remove_from_hass()
    assert queued.cancelled() or (await asyncio.gather(queued, return_exceptions=True))[0].__class__ is asyncio.CancelledError
    assert not group._transition_queue
    assert group._transition_worker.done()


@pytest.mark.parametrize("scene_count", [0, 8])
async def test_artwork_burst_finishes_current_then_uses_latest_even_when_full(group, scene_count):
    from custom_components.chameleon.const import SCENE_ALBUM_ART
    group._is_on = True
    group._effect = SCENE_ALBUM_ART
    group._media_player_entity = "media_player.test"
    group.hass.data[DOMAIN]["entry"]["transition"] = 2.5
    order = []
    gates = asyncio.Queue()
    sleeps = asyncio.Queue()

    async def wait_transition(duration):
        assert duration == 2.5
        sleeps.put_nowait(True)
        await gates.get()

    async def artwork(**kwargs):
        order.append(kwargs["media_state"].attributes["entity_picture"])

    async def scene(**kwargs):
        order.append(kwargs["effect"])

    group._apply_album_art = artwork
    group._do_turn_on = scene
    def cover(i):
        return SimpleNamespace(attributes={"entity_picture": f"/{i}.jpg"})
    with patch("custom_components.chameleon.light.asyncio.sleep", side_effect=wait_transition):
        await group.async_apply_album_art_update(cover(0))
        await sleeps.get()
        pending = [asyncio.create_task(group.async_turn_on(effect=f"Scene {i}")) for i in range(scene_count)]
        for i in range(1, 11):
            pending.append(asyncio.create_task(group.async_apply_album_art_update(cover(i))))
            barrier = asyncio.Event()
            asyncio.get_running_loop().call_soon(barrier.set)
            await barrier.wait()
            assert len(group._transition_queue) == scene_count + 2
            assert order == ["/0.jpg"]
        for _ in range(scene_count + 1):
            gates.put_nowait(True)
            await sleeps.get()
        assert order == ["/0.jpg", *(f"Scene {i}" for i in range(scene_count)), "/10.jpg"]
        gates.put_nowait(True)
        await group._transition_worker
        await asyncio.gather(*pending)
    assert not group._transition_queue


async def test_random_style_changes_per_palette_excludes_fade_and_reuses_for_off(group):
    group.hass.data[DOMAIN]["entry"]["wled_blend_style"] = "random"
    with patch("custom_components.chameleon.light.random.choice", side_effect=[16, 5]) as choose, patch(
        "custom_components.chameleon.light.wled_entry_id", return_value="wled-entry"
    ), patch("custom_components.chameleon.light.send_wled_transition", new_callable=AsyncMock, return_value=True) as send, patch(
        "custom_components.chameleon.light.send_wled_power_off", new_callable=AsyncMock, return_value=True
    ) as off:
        await group._apply_palette_static([(255, 0, 0)], 100)
        assert send.await_args.args[6] == 16
        await group._apply_palette_static([(0, 255, 0)], 100)
        assert send.await_args.args[6] == 5
        await group._do_turn_off()
        assert off.await_args.args[3] == 5
        assert choose.call_count == 2
        assert all(0 not in call.args[0] for call in choose.call_args_list)
        assert set(choose.call_args.args[0]) == {1, 2, 3, 4, 5, 16, 17}


async def test_discarded_artwork_is_never_downloaded_or_extracted(group):
    from custom_components.chameleon.const import SCENE_ALBUM_ART
    from custom_components.chameleon.light_controller import ApplyColorsResult
    group._is_on = True
    group._effect = SCENE_ALBUM_ART
    group._media_player_entity = "media_player.test"
    group._interesting_colors = False
    group._normalize_brightness = False
    group._async_download_artwork = AsyncMock(return_value=b"used artwork only")
    group._apply_palette_static = AsyncMock(return_value=ApplyColorsResult(results=[
        LightResult("light.enabled", True, (255, 0, 0))
    ]))
    # Admission of the burst precedes the worker: only the latest move survives.
    with patch("custom_components.chameleon.light.extract_color_palette_bytes", new_callable=AsyncMock,
               return_value=[(255, 0, 0)]) as extract:
        await asyncio.gather(*(group.async_apply_album_art_update(SimpleNamespace(
            state="playing", attributes={"entity_picture": f"/cover-{i}.jpg"}
        )) for i in range(10)))
        await group._transition_worker
    group._async_download_artwork.assert_awaited_once_with("/cover-9.jpg")
    extract.assert_awaited_once()
    assert group._last_artwork_key == "/cover-9.jpg"


async def test_artwork_bytes_released_before_light_updates_palette_retained(group):
    import inspect

    from custom_components.chameleon.light_controller import ApplyColorsResult
    group._media_player_entity = "media_player.test"
    group._interesting_colors = True
    group._normalize_brightness = False
    group._prepare_palette = lambda colors, white_fraction: colors
    group._async_download_artwork = AsyncMock(return_value=b"artwork")

    async def apply(colors, brightness):
        frame = inspect.currentframe().f_back
        try:
            assert frame.f_code.co_name == "_apply_album_art"
            assert "image_bytes" not in frame.f_locals
        finally:
            del frame
        return ApplyColorsResult(results=[LightResult("light.enabled", True, colors[0])])

    group._apply_palette_static = apply
    with patch("custom_components.chameleon.light.extract_color_palette_bytes", new_callable=AsyncMock,
               return_value=[(255, 0, 0)]), patch(
        "custom_components.chameleon.light.extract_white_fraction", new_callable=AsyncMock, return_value=0.2
    ) as white:
        await group._apply_album_art(media_state=SimpleNamespace(
            state="playing", attributes={"entity_picture": "/cover.jpg"}
        ))
    white.assert_awaited_once_with(group.hass, b"artwork")
    assert group._extracted_palette == [(255, 0, 0)]


@pytest.mark.parametrize("scene_name", ["Aquatic", "Beach Sunset", "Album Art"])
async def test_scene_select_keeps_scene_during_off_and_restores_on(group, scene_name):
    from custom_components.chameleon.select import ChameleonSceneSelect

    group.hass.data[DOMAIN]["entry"]["chameleon_light"] = group
    with patch("custom_components.chameleon.scene_control.get_entity_base_name", return_value="test"):
        select = ChameleonSceneSelect(group.hass, group._entry, group._light_entities)
    group._effect = scene_name
    group._last_effect = scene_name
    group._is_on = True
    assert select.current_option == scene_name

    await group.async_turn_off()
    assert not group.is_on
    assert group.effect is None
    assert select.current_option == scene_name

    async def apply_scene(effect):
        group._effect = effect
        group._last_effect = effect

    with patch.object(group, "_apply_effect", new=AsyncMock(side_effect=apply_scene)) as apply:
        await group.async_turn_on()
    apply.assert_awaited_once_with(scene_name)
    assert group.is_on
    assert select.current_option == scene_name


async def test_scene_select_has_no_selection_before_first_scene(group):
    assert group.selected_scene is None
