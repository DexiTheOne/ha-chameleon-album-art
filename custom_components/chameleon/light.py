"""Light platform for Chameleon — primary entity exposing scenes as effects.

The Chameleon light is a facade over the user's configured RGB lights. It owns
the on/off state, brightness, and the currently-displayed color (either the
dominant of an active scene or a user-supplied manual override). Scenes are
exposed via HA's standard ``LightEntityFeature.EFFECT`` mechanism so stock
light cards (Tile, Mushroom, Bubble) render the scene picker natively.

Setters:

- ``turn_on(effect=X)`` → apply the named scene; clear any manual override.
- ``turn_on(rgb_color=X)`` → bypass scenes; apply X directly to all
  underlying lights and remember it as a manual override until the next
  scene/turn_off.
- ``turn_on(brightness=N)`` → update brightness and re-apply current state.
  ``N == 0`` is treated as ``turn_off`` (HA cards convert slider-to-zero into
  a turn_off, but we handle the explicit case defensively too).
- ``turn_on()`` (bare) → restore the last applied scene; if none, apply
  ``Random``.
- ``turn_off()`` → stop any animation; turn off all underlying lights.
"""

from __future__ import annotations

import asyncio
import colorsys
import logging
import random
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.network import get_url

from .assignments import randomized_light_order
from .color_extractor import (
    RGBColor,
    balance_mostly_white_palette,
    clamp_rgb_color,
    extract_color_palette,
    extract_color_palette_bytes,
    extract_white_fraction,
    normalize_palette_brightness,
    select_interesting_colors,
)
from .const import (
    CONF_INTERESTING_COLORS,
    CONF_LIGHT_ENTITIES,
    CONF_LIGHT_ENTITY,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_NORMALIZE_BRIGHTNESS,
    CONF_RANDOMIZE_COLOR_ASSIGNMENT,
    CONF_SEND_PALETTE_TO_WLED,
    CONF_TRANSITION,
    DEFAULT_BRIGHTNESS,
    DEFAULT_COLOR_COUNT,
    DEFAULT_INTERESTING_COLORS,
    DEFAULT_NORMALIZE_BRIGHTNESS,
    DEFAULT_RANDOMIZE_COLOR_ASSIGNMENT,
    DEFAULT_SEND_PALETTE_TO_WLED,
    DEFAULT_TRANSITION,
    DEFAULT_WLED_BLEND_STYLE,
    DOMAIN,
    IMAGE_DIRECTORY,
    MAX_ALBUM_ART_BYTES,
    SCENE_ALBUM_ART,
    SCENE_OFF,
    SCENE_RANDOM,
    SUPPORTED_EXTENSIONS,
    WLED_BLEND_STYLES,
)
from .entity_controls import controlled_entities, settings
from .helpers import get_chameleon_device_name, get_entity_base_name
from .light_controller import ApplyColorsResult, LightController, LightResult
from .wled_palette import send_wled_power_off, send_wled_transition, wled_entry_id

_LOGGER = logging.getLogger(__name__)

# Default RGB shown when no scene has been applied yet (white).
_DEFAULT_RGB: RGBColor = (255, 255, 255)
_PLACEHOLDER_ART_NAMES = {
    "default", "default.jpg", "default.png", "no-art", "no-art.jpg",
    "no-cover.jpg", "no-cover.png", "placeholder.jpg", "placeholder.png",
    "unknown-album.jpg", "unknown-album.png",
}


def _is_placeholder_art(entity_picture: str) -> bool:
    """Recognize explicit placeholder paths without inspecting token-bearing queries."""
    path = urlsplit(entity_picture).path.lower().rstrip("/")
    return path.startswith("/static/icons/") or path.rsplit("/", 1)[-1] in _PLACEHOLDER_ART_NAMES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Chameleon light entity from a config entry."""
    if CONF_LIGHT_ENTITIES in entry.data:
        light_entities = entry.data[CONF_LIGHT_ENTITIES]
    else:
        light_entities = [entry.data[CONF_LIGHT_ENTITY]]

    initial_transition = entry.options.get(CONF_TRANSITION, entry.data.get(CONF_TRANSITION, DEFAULT_TRANSITION))
    media_player_entity = entry.options.get(
        CONF_MEDIA_PLAYER_ENTITY,
        entry.data.get(CONF_MEDIA_PLAYER_ENTITY),
    )
    normalize_brightness = entry.options.get(
        CONF_NORMALIZE_BRIGHTNESS,
        entry.data.get(CONF_NORMALIZE_BRIGHTNESS, DEFAULT_NORMALIZE_BRIGHTNESS),
    )
    interesting_colors = entry.options.get(
        CONF_INTERESTING_COLORS, entry.data.get(CONF_INTERESTING_COLORS, DEFAULT_INTERESTING_COLORS)
    )
    randomize_color_assignment = entry.options.get(
        CONF_RANDOMIZE_COLOR_ASSIGNMENT,
        entry.data.get(CONF_RANDOMIZE_COLOR_ASSIGNMENT, DEFAULT_RANDOMIZE_COLOR_ASSIGNMENT),
    )
    send_palette_to_wled = entry.options.get(
        CONF_SEND_PALETTE_TO_WLED,
        entry.data.get(CONF_SEND_PALETTE_TO_WLED, DEFAULT_SEND_PALETTE_TO_WLED),
    )

    async_add_entities(
        [ChameleonLight(hass, entry, light_entities, initial_transition, media_player_entity, normalize_brightness, randomize_color_assignment, interesting_colors, send_palette_to_wled)],
        True,
    )


def _scene_name_from_filename(filename: str) -> str:
    """Convert an image filename stem to a human-readable scene name."""
    return filename.replace("_", " ").replace("-", " ").title()


def _entry_data(hass: HomeAssistant, entry_id: str) -> dict:
    """Return (creating if needed) the per-entry runtime dict in hass.data."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(entry_id, {})


class ChameleonLight(LightEntity):
    """Primary Chameleon entity — wraps the configured RGB lights as one facade.

    Effects are scene names (image-based palettes); brightness is owned here;
    rgb_color reflects either the active scene's dominant color or a manual
    color override.
    """

    _attr_has_entity_name = True
    _attr_name = None  # entity inherits the device name
    _attr_icon = "mdi:palette-outline"
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        light_entities: list[str],
        initial_transition: float,
        media_player_entity: str | None = None,
        normalize_brightness: bool = DEFAULT_NORMALIZE_BRIGHTNESS,
        randomize_color_assignment: bool = DEFAULT_RANDOMIZE_COLOR_ASSIGNMENT,
        interesting_colors: bool = DEFAULT_INTERESTING_COLORS,
        send_palette_to_wled: bool = DEFAULT_SEND_PALETTE_TO_WLED,
    ) -> None:
        """Initialize the Chameleon light entity."""
        self.hass = hass
        self._entry = entry
        self._light_entities = light_entities
        self._initial_transition = initial_transition
        self._media_player_entity = media_player_entity
        self._normalize_brightness = normalize_brightness
        self._interesting_colors = interesting_colors
        self._send_palette_to_wled = send_palette_to_wled
        self._randomize_color_assignment = randomize_color_assignment
        self._random_assignment_order: list[str] | None = None
        self._remove_media_listener: Callable[[], None] | None = None
        self._last_artwork_key: str | None = None
        self._album_art_updated_at: datetime | None = None

        # Visible state
        self._is_on = False
        self._brightness_pct = DEFAULT_BRIGHTNESS  # internal 0-100 scale
        self._last_nonzero_brightness = DEFAULT_BRIGHTNESS
        self._effect: str | None = None
        self._last_effect: str | None = None  # restored on bare turn_on
        self._manual_color: RGBColor | None = None  # set when user picks raw color

        # Diagnostics / palette
        self._extracted_palette: list[RGBColor] = []
        self._applied_colors: dict[str, RGBColor] = {}
        self._last_scene_change: datetime | None = None
        self._last_error: str | None = None
        self._failed_lights: dict[str, str] = {}

        # Scene cache (effect_list source)
        self._cached_options: list[str] = []
        self._scene_to_path: dict[str, Path] = {}

        # Serializes concurrent state-changing calls. HA can dispatch multiple
        # service calls (turn_on, turn_off, sibling re-apply) onto this entity
        # simultaneously; without the lock those interleave and race each other
        # over the current scene state.
        self._lock = asyncio.Lock()
        # Includes the move currently running or waiting for WLED to finish.
        self._transition_queue = deque()
        self._active_transition = None
        self._transition_worker: asyncio.Task | None = None
        self._queue_closed = False
        self._last_wled_blend_mode = WLED_BLEND_STYLES[DEFAULT_WLED_BLEND_STYLE]

        self._light_controller = LightController(hass)

        base_name = get_entity_base_name(hass, light_entities)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_light"
        self.entity_id = f"light.chameleon_{base_name}"

    # ── Helpers ──────────────────────────────────────────────────────────

    def _get_runtime_transition(self) -> float:
        return _entry_data(self.hass, self._entry.entry_id).get("transition", self._initial_transition)

    def _resolve_wled_blend_mode(self, *, palette_update: bool) -> int:
        """Choose once per palette; reuse that choice for subsequent power-off."""
        style = _entry_data(self.hass, self._entry.entry_id).get("wled_blend_style", DEFAULT_WLED_BLEND_STYLE)
        if style == "random":
            if palette_update:
                self._last_wled_blend_mode = random.choice([
                    mode for mode in WLED_BLEND_STYLES.values() if mode != 0
                ])
            return self._last_wled_blend_mode
        self._last_wled_blend_mode = WLED_BLEND_STYLES.get(style, WLED_BLEND_STYLES[DEFAULT_WLED_BLEND_STYLE])
        return self._last_wled_blend_mode

    def _prepare_palette(self, colors: list[RGBColor], white_fraction: float = 0.0) -> list[RGBColor]:
        """Adjust source-image RGB values before static or animated output."""
        if self._interesting_colors:
            colors = select_interesting_colors(colors)
            colors = balance_mostly_white_palette(colors, white_fraction, len(self._light_entities))
        if self._normalize_brightness:
            return normalize_palette_brightness(colors)
        return colors

    def set_interesting_colors(self, enabled: bool) -> None:
        """Use the selected filter for the next image or artwork palette."""
        self._interesting_colors = enabled
        self.async_write_ha_state()

    def set_send_palette_to_wled(self, enabled: bool) -> None:
        """Apply the JSON palette setting to subsequent scenes."""
        self._send_palette_to_wled = enabled
        self.async_write_ha_state()

    def set_randomize_color_assignment(self, enabled: bool) -> None:
        """Apply a control-switch change to the next Random selection."""
        self._randomize_color_assignment = enabled
        if not enabled:
            self._random_assignment_order = None
        self.async_write_ha_state()

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Register self for service handler discovery and run initial scan."""
        await super().async_added_to_hass()
        _entry_data(self.hass, self._entry.entry_id)["chameleon_light"] = self
        await self.async_refresh_options()
        if self._media_player_entity:
            self._remove_media_listener = async_track_state_change_event(
                self.hass,
                [self._media_player_entity],
                self._handle_media_player_change,
            )

    async def async_will_remove_from_hass(self) -> None:
        """Clear our registration."""
        await super().async_will_remove_from_hass()
        self._queue_closed = True
        if self._transition_worker is not None:
            self._transition_worker.cancel()
            await asyncio.gather(self._transition_worker, return_exceptions=True)
        for _, completion, _ in self._transition_queue:
            completion.cancel()
        self._transition_queue.clear()

        entry_data = _entry_data(self.hass, self._entry.entry_id)
        if entry_data.get("chameleon_light") is self:
            entry_data.pop("chameleon_light", None)
        if self._remove_media_listener:
            self._remove_media_listener()
            self._remove_media_listener = None

    # ── HA-facing properties ─────────────────────────────────────────────

    @property
    def device_info(self):
        """Return device info for this entity."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": get_chameleon_device_name(self.hass, self._light_entities),
            "manufacturer": "Chameleon",
            "model": "Scene Selector",
        }

    @property
    def is_on(self) -> bool:
        """Return whether the light is currently on."""
        return self._is_on

    @property
    def brightness(self) -> int:
        """Return brightness on HA's 0-255 scale."""
        return int(self._brightness_pct * 255 / 100)

    @property
    def rgb_color(self) -> RGBColor:
        """Return the displayed color: manual override if set, else palette dominant."""
        if self._manual_color is not None:
            return self._manual_color
        if self._extracted_palette:
            return self._extracted_palette[0]
        return _DEFAULT_RGB

    @property
    def effect(self) -> str | None:
        """Return the currently active effect, or None when in manual-color mode."""
        return self._effect

    @property
    def selected_scene(self) -> str | None:
        """Return the selected scene, retaining it while the light is off."""
        return self._effect or self._last_effect

    @property
    def effect_list(self) -> list[str]:
        """Return scene names available as effects.

        ``Off`` is intentionally excluded — that's expressed via ``turn_off()``.
        ``Random`` stays at the front so the alphabetically-sorted scenes that
        follow keep their own ordering.
        """
        special_effects = [SCENE_RANDOM]
        if self._media_player_entity:
            special_effects.append(SCENE_ALBUM_ART)
        return [*special_effects, *self._cached_options]

    @property
    def extra_state_attributes(self):
        """Return extra state attributes for templates and dashboards."""
        attrs: dict[str, Any] = {
            "light_entities": self._light_entities,
            "light_count": len(self._light_entities),
            "applied_colors": self._applied_colors,
            "normalize_brightness": self._normalize_brightness,
            "randomize_color_assignment": self._randomize_color_assignment,
        }

        if self._media_player_entity:
            attrs["album_art_media_player"] = self._media_player_entity
        if self._album_art_updated_at:
            attrs["album_art_updated_at"] = self._album_art_updated_at.isoformat()

        if self._extracted_palette:
            attrs["extracted_palette"] = [list(c) for c in self._extracted_palette]
            r, g, b = self._extracted_palette[0]
            attrs["dominant_color"] = [r, g, b]
            attrs["dominant_color_hex"] = f"#{r:02x}{g:02x}{b:02x}"
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            attrs["dominant_hue"] = round(h * 360, 1)
            attrs["dominant_saturation"] = round(s, 2)
            attrs["dominant_value"] = round(v, 2)

        if self._manual_color is not None:
            attrs["manual_color"] = list(self._manual_color)

        if self._last_scene_change:
            attrs["last_scene_change"] = self._last_scene_change.isoformat()
        if self._last_error:
            attrs["last_error"] = self._last_error
        if self._failed_lights:
            attrs["failed_lights"] = self._failed_lights

        return attrs

    # ── HA-facing setters ────────────────────────────────────────────────

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on with optional effect, rgb_color, and/or brightness."""
        await self._queue_transition(lambda: self._do_turn_on(**kwargs))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off all underlying lights."""
        await self._queue_transition(self._do_turn_off)

    async def _queue_transition(self, action, *, latest_artwork: bool = False) -> None:
        """Admit up to ten moves, keeping random selection deferred until execution."""
        if self._queue_closed:
            return
        if latest_artwork:
            # Remove only waiting covers. The running move (including its
            # cooldown) must finish, and explicit scene requests keep order.
            for move in list(self._transition_queue):
                _, completion, is_artwork = move
                if is_artwork and completion is not self._active_transition:
                    self._transition_queue.remove(move)
                    if not completion.done():
                        completion.set_result(None)
        if len(self._transition_queue) >= 10:
            return
        completion = asyncio.get_running_loop().create_future()
        self._transition_queue.append((action, completion, latest_artwork))
        if self._transition_worker is None or self._transition_worker.done():
            # Background task: HA must not wait for the whole queue on shutdown.
            self._transition_worker = self.hass.async_create_background_task(
                self._run_transition_queue(), "Chameleon transition queue"
            )
        await asyncio.shield(completion)

    async def _run_transition_queue(self) -> None:
        """Apply in arrival order and reserve the active slot through its duration."""
        while self._transition_queue:
            action, completion, _ = self._transition_queue[0]
            self._active_transition = completion
            try:
                async with self._lock:
                    duration = max(0, self._get_runtime_transition())
                    await action()
                if not completion.done():
                    completion.set_result(None)
                # Start the clock after all device requests have completed.
                await asyncio.sleep(duration)
            except asyncio.CancelledError:
                if not completion.done():
                    completion.cancel()
                raise
            except Exception as err:
                if not completion.done():
                    completion.set_exception(err)
                _LOGGER.error("Queued Chameleon action failed: %s", type(err).__name__)
            self._transition_queue.popleft()
            self._active_transition = None

    async def _do_turn_on(self, **kwargs: Any) -> None:
        """Inner turn-on, runs under ``self._lock``."""
        self._last_error = None
        self._failed_lights = {}

        brightness = kwargs.get(ATTR_BRIGHTNESS)
        rgb_color = kwargs.get(ATTR_RGB_COLOR)
        effect = kwargs.get(ATTR_EFFECT)

        # brightness=0 from a card almost never happens (cards convert to
        # turn_off) but handle defensively without re-acquiring the lock.
        if brightness is not None and brightness == 0:
            await self._do_turn_off()
            return

        # Update brightness if provided.
        brightness_changed = False
        if brightness is not None:
            new_pct = max(1, round(brightness * 100 / 255))
            if new_pct != self._brightness_pct:
                brightness_changed = True
                self._brightness_pct = new_pct
                self._last_nonzero_brightness = new_pct
                _entry_data(self.hass, self._entry.entry_id)["brightness"] = new_pct

        if effect is not None:
            # Scene change — full re-apply.
            if effect == SCENE_OFF:
                await self._do_turn_off()
                return
            if self._effect == SCENE_ALBUM_ART and effect != SCENE_ALBUM_ART:
                self._effect = None
            self._manual_color = None
            await self._apply_effect(effect)
        elif rgb_color is not None:
            # Manual color override — full re-apply.
            if self._effect == SCENE_ALBUM_ART:
                self._effect = None
            await self._apply_manual_color(tuple(rgb_color))
        elif not self._is_on:
            # Bare turn_on from off → restore last effect (Random if never set).
            target = self._last_effect or SCENE_RANDOM
            self._manual_color = None
            await self._apply_effect(target)
        elif brightness_changed:
            # Update brightness without re-extracting the palette.
            await self._update_brightness_only()
        # else: bare turn_on while already on with no change → no-op

        # Reveal WLED devices only after their configured segments have received
        # the new color. Turning the parent on first can flash an old scene.
        self._is_on = True
        self.async_write_ha_state()

    async def _do_turn_off(self) -> None:
        """Inner turn-off, runs under ``self._lock``."""

        transition = self._get_runtime_transition()

        async def turn_off_entity(entity_id: str) -> None:
            try:
                await self.hass.services.async_call(
                    "light", "turn_off", {"entity_id": entity_id, "transition": transition}, blocking=True,
                )
            except Exception as err:
                _LOGGER.error("Failed to turn off %s: %s", entity_id, err)

        blend_mode = self._resolve_wled_blend_mode(palette_update=False)
        enabled = self._enabled_entities()
        devices: dict[str, str] = {}
        for entity_id in enabled:
            entry_id = wled_entry_id(self.hass, entity_id)
            if entry_id and entry_id not in devices:
                devices[entry_id] = entity_id
        outcomes = await asyncio.gather(*(
            send_wled_power_off(
                self.hass, entity_id, transition, blend_mode,
                [member for member in enabled if wled_entry_id(self.hass, member) == entry_id],
            ) for entry_id, entity_id in devices.items()
        ))
        ordinary = [entity_id for entity_id in enabled if not wled_entry_id(self.hass, entity_id)]
        failed = {entry_id for entry_id, succeeded in zip(devices, outcomes, strict=True) if not succeeded}
        fallback = [entity_id for entity_id in enabled if wled_entry_id(self.hass, entity_id) in failed]
        await asyncio.gather(*(turn_off_entity(entity_id) for entity_id in [*ordinary, *fallback]))
        self._is_on = False
        self._effect = None
        self._manual_color = None
        self._applied_colors = {}
        # _last_effect is preserved so a bare turn_on can restore it.
        self.async_write_ha_state()

    def _enabled_entities(self):
        return [entity for entity in controlled_entities(self.hass, self._light_entities)
                if settings(self._entry, entity).get("enabled", True)]

    def _entity_brightness(self, entity, brightness):
        return brightness * settings(self._entry, entity).get("brightness", 100) / 100

    async def _update_brightness_only(self) -> None:
        """Apply a brightness change without disturbing color/scene state.

        - Manual color: re-apply at the new brightness.
        - Static scene: just bump brightness on the underlying lights; they
          remember their color from the last static apply.
        """
        if self._manual_color is not None:
            await self._apply_manual_color(self._manual_color)
            return

        for light_entity in self._enabled_entities():
            ha_brightness = round(self._entity_brightness(light_entity, self._brightness_pct) * 255 / 100)
            try:
                await self.hass.services.async_call(
                    "light",
                    "turn_on",
                    {"entity_id": light_entity, "brightness": ha_brightness},
                    blocking=True,
                )
            except Exception as e:
                _LOGGER.error("Failed to update brightness on %s: %s", light_entity, e)

    # ── Public API for sibling entities ──────────────────────────────────

    async def async_reapply_current_scene(self) -> None:
        """Re-run the current scene/color with current runtime state.

        Called by the speed slider when it crosses the zero boundary, or by
        the transition control. Held under the
        same lock as turn_on/turn_off so re-applies serialize with user
        actions.
        """
        async def reapply():
            if not self._is_on:
                return
            if self._manual_color is not None:
                await self._apply_manual_color(self._manual_color)
            elif self._effect is not None:
                await self._apply_effect(self._effect, reuse_random_assignment=True)
            self.async_write_ha_state()
        await self._queue_transition(reapply)

    async def async_refresh_options(self) -> None:
        """Refresh the scene cache (effect_list source).

        Public API: called by the chameleon.refresh_scenes service handler
        and on entity add. Also called internally on cache miss.
        """
        new_options, new_scene_to_path = await self.hass.async_add_executor_job(self._scan_image_directory)
        if new_options != self._cached_options or new_scene_to_path != self._scene_to_path:
            self._cached_options = new_options
            self._scene_to_path = new_scene_to_path
            self.async_write_ha_state()

    # ── Internals ────────────────────────────────────────────────────────

    async def _apply_effect(self, effect: str, *, reuse_random_assignment: bool = False) -> None:
        """Apply a scene effect by name (handles Random and Off specially)."""
        # "Off" via the service path is equivalent to turn_off.
        if effect == SCENE_OFF:
            await self._do_turn_off()
            return

        if effect == SCENE_ALBUM_ART:
            # Selecting Album Art is a state change even if artwork is temporarily
            # unavailable. Stop the old scene and keep listening for new artwork.
            self._effect = SCENE_ALBUM_ART
            self._last_effect = SCENE_ALBUM_ART
            self._manual_color = None
            if not self._randomize_color_assignment:
                self._random_assignment_order = None
            await self._apply_album_art(force=True, reuse_random_assignment=reuse_random_assignment)
            return

        is_random_request = effect == SCENE_RANDOM
        if is_random_request:
            random_options = [name for name in self._cached_options if name not in (SCENE_ALBUM_ART, SCENE_RANDOM, SCENE_OFF)]
            if not random_options:
                self._last_error = "No scenes available for random selection"
                _LOGGER.warning(self._last_error)
                return
            # A repeated choice has identical target colors, so no transition
            # can be seen. Prefer a different scene when one is available.
            choices = [name for name in random_options if name != self._effect] or random_options
            effect = random.choice(choices)
            _LOGGER.info("Random scene selected: '%s'", effect)

        image_path = await self._find_image_for_scene(effect)
        if image_path is None:
            self._last_error = f"Image not found for scene: {effect}"
            _LOGGER.error(self._last_error)
            return

        if is_random_request and self._randomize_color_assignment:
            self._random_assignment_order = randomized_light_order(self._light_entities, self._random_assignment_order)
        elif not reuse_random_assignment:
            self._random_assignment_order = None

        # Apply the scene through WLED native transitions.

        brightness = self._brightness_pct

        result = await self._apply_colors_static(image_path, brightness)

        if not result.results and self._enabled_entities():
            return

        if result.all_succeeded or not result.results:
            self._effect = effect
            self._last_effect = effect
            self._applied_colors = result.applied_colors
            self._failed_lights = {}
            self._last_error = None
            self._last_scene_change = datetime.now()
            verb = "applied"
            _LOGGER.info("Scene '%s' %s successfully", effect, verb)
        elif result.all_failed:
            self._last_error = "Failed to apply colors to any lights"
            self._failed_lights = result.failed_lights
            _LOGGER.error("Scene '%s' failed for all %d lights", effect, result.failed_count)
        else:
            self._effect = effect
            self._last_effect = effect
            self._applied_colors = result.applied_colors
            self._failed_lights = result.failed_lights
            self._last_scene_change = datetime.now()
            self._last_error = f"Partial failure: {result.failed_count}/{len(result.results)} lights failed"
            _LOGGER.warning(
                "Scene '%s' partially applied: %d/%d lights succeeded",
                effect,
                result.succeeded_count,
                len(result.results),
            )

    @callback
    def _handle_media_player_change(self, event: Event) -> None:
        """Refresh an active Album Art effect only when the artwork changes."""
        if not self._is_on or self._effect != SCENE_ALBUM_ART:
            return

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        old_picture = old_state.attributes.get("entity_picture") if old_state else None
        new_picture = new_state.attributes.get("entity_picture")
        if not new_picture or new_picture == old_picture:
            return

        self.hass.async_create_task(self.async_apply_album_art_update(new_state))

    async def async_apply_album_art_update(self, media_state=None) -> None:
        """Serialize a track-driven artwork refresh with user light actions."""
        # Preserve the latest waiting cover; intermediate covers are replaced
        # without interrupting the currently running transition.
        if media_state is None and self._media_player_entity:
            media_state = self.hass.states.get(self._media_player_entity)
        async def update_artwork():
            if self._is_on and self._effect == SCENE_ALBUM_ART:
                await self._apply_album_art(media_state=media_state)
                self.async_write_ha_state()
        await self._queue_transition(update_artwork, latest_artwork=True)

    async def _apply_album_art(self, *, force: bool = False, reuse_random_assignment: bool = False, media_state=None) -> None:
        """Download, extract, and apply the configured media player's artwork."""
        if not self._media_player_entity:
            self._last_error = "No album-art media player configured"
            return

        if media_state is None:
            media_state = self.hass.states.get(self._media_player_entity)
        if media_state is None:
            self._last_error = "Configured album-art media player was not found"
            return

        entity_picture = media_state.attributes.get("entity_picture")
        if not isinstance(entity_picture, str) or not entity_picture:
            self._last_error = "Configured media player has no album artwork"
            return
        if media_state.state in ("off", "idle", "unavailable", "unknown") or _is_placeholder_art(entity_picture):
            self._last_error = "Configured media player has no current album artwork"
            return
        artwork_key = entity_picture
        if not force and artwork_key == self._last_artwork_key:
            return

        try:
            image_bytes = await self._async_download_artwork(entity_picture)
        except Exception as err:
            # Never include the artwork URL because media proxy URLs can carry
            # authentication tokens.
            self._last_error = f"Unable to download album artwork: {type(err).__name__}"
            _LOGGER.warning("Unable to download album artwork for %s: %s", self._media_player_entity, type(err).__name__)
            return

        try:
            colors = await extract_color_palette_bytes(
                self.hass,
                image_bytes,
                color_count=max(len(self._light_entities), DEFAULT_COLOR_COUNT),
            )
            white_fraction = await extract_white_fraction(self.hass, image_bytes) if self._interesting_colors else 0.0
        finally:
            # Only palette/white-coverage data is needed for device updates.
            del image_bytes
        if not colors and white_fraction < 0.7:
            self._last_error = "Unable to extract colors from album artwork"
            return
        colors = self._prepare_palette(colors, white_fraction)
        if not colors:
            self._last_error = "No vivid colors found in album artwork"
            return

        previous_order = self._random_assignment_order
        if self._randomize_color_assignment and not reuse_random_assignment:
            self._random_assignment_order = randomized_light_order(self._light_entities, previous_order)
        elif not self._randomize_color_assignment:
            self._random_assignment_order = None

        # Apply the new cover colors through WLED.
        result = await self._apply_palette_static(
            colors, self._brightness_pct,
        )

        if result.all_failed:
            self._random_assignment_order = previous_order
            self._last_error = "Failed to apply album-art colors to any lights"
            self._failed_lights = result.failed_lights
            return
        self._effect = SCENE_ALBUM_ART
        self._last_effect = SCENE_ALBUM_ART
        self._manual_color = None
        self._extracted_palette = colors
        self._applied_colors = result.applied_colors
        self._failed_lights = result.failed_lights
        self._last_artwork_key = artwork_key
        self._last_scene_change = datetime.now()
        self._album_art_updated_at = self._last_scene_change
        self._last_error = None if result.all_succeeded or not result.results else f"Partial failure: {result.failed_count}/{len(result.results)} lights failed"

    async def _async_download_artwork(self, entity_picture: str) -> bytes:
        """Download artwork into bounded memory without persisting its token or bytes."""
        if entity_picture.startswith(("http://", "https://")):
            artwork_url = entity_picture
        elif entity_picture.startswith("/"):
            artwork_url = f"{get_url(self.hass, prefer_external=False).rstrip('/')}{entity_picture}"
        else:
            raise ValueError("Unsupported artwork URL")

        session = async_get_clientsession(self.hass)
        async with session.get(artwork_url, timeout=15) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
            if content_type and not content_type.startswith("image/"):
                raise ValueError("Artwork response is not an image")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_ALBUM_ART_BYTES:
                raise ValueError("Artwork exceeds size limit")
            image_data = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                image_data.extend(chunk)
                if len(image_data) > MAX_ALBUM_ART_BYTES:
                    raise ValueError("Artwork exceeds size limit")
            image_bytes = bytes(image_data)
            if not image_bytes:
                raise ValueError("Artwork response is empty")
            return image_bytes

    async def _apply_manual_color(self, rgb_color: RGBColor) -> None:
        """Apply a manual color using the same exclusion and brightness rules."""
        rgb_color = clamp_rgb_color(rgb_color)
        self._random_assignment_order = None
        result = await self._apply_palette_wled(
            [rgb_color], dict.fromkeys(self._light_entities, rgb_color), self._brightness_pct,
        )

        if result.all_succeeded or not result.results:
            self._manual_color = rgb_color
            self._effect = None
            self._applied_colors = result.applied_colors
            self._failed_lights = {}
            self._last_error = None
            self._last_scene_change = datetime.now()
            _LOGGER.info("Manual color RGB%s applied successfully", rgb_color)
        elif result.all_failed:
            self._last_error = "Failed to apply color to any lights"
            self._failed_lights = result.failed_lights
            _LOGGER.error("Manual color failed for all %d lights", result.failed_count)
        else:
            self._manual_color = rgb_color
            self._effect = None
            self._applied_colors = result.applied_colors
            self._failed_lights = result.failed_lights
            self._last_scene_change = datetime.now()
            self._last_error = f"Partial failure: {result.failed_count}/{len(result.results)} lights failed"

    async def _apply_colors_static(self, image_path: Path, brightness: int) -> ApplyColorsResult:
        """Extract and apply colors statically (no animation loop)."""
        num_lights = len(self._light_entities)

        colors = await extract_color_palette(
            self.hass,
            image_path,
            color_count=max(num_lights, DEFAULT_COLOR_COUNT),
        )
        white_fraction = await extract_white_fraction(self.hass, image_path) if self._interesting_colors else 0.0
        if not colors and white_fraction < 0.7:
            _LOGGER.error("Failed to extract color palette from %s", image_path)
            return ApplyColorsResult()

        colors = self._prepare_palette(colors, white_fraction)

        if not colors:
            self._last_error = "No vivid colors found in image scene"
            return ApplyColorsResult()
        self._extracted_palette = colors
        return await self._apply_palette_static(colors, brightness)

    async def _apply_palette_static(self, colors: list[RGBColor], brightness: int, *, transition: float | None = None) -> ApplyColorsResult:
        """Distribute an already-extracted palette across configured lights."""
        light_order = self._random_assignment_order or self._light_entities
        light_colors = {entity: colors[i % len(colors)] for i, entity in enumerate(light_order)}
        return await self._apply_palette_wled(colors, light_colors, brightness)

    async def _apply_palette_wled(
        self, colors: list[RGBColor], light_colors: dict[str, RGBColor], brightness: int,
    ) -> ApplyColorsResult:
        """Start native transitions concurrently and set other lights immediately."""
        light_colors = {entity: color for entity, color in light_colors.items() if entity in self._enabled_entities()}
        devices: dict[str, tuple[str, int]] = {}
        for index, entity in enumerate(light_colors):
            entry_id = wled_entry_id(self.hass, entity)
            if entry_id and entry_id not in devices:
                devices[entry_id] = (entity, index)
        ordinary = {entity: color for entity, color in light_colors.items() if not wled_entry_id(self.hass, entity)}
        blend_mode = self._resolve_wled_blend_mode(palette_update=True)
        tasks = [
            asyncio.create_task(send_wled_transition(
                self.hass, entity, colors, index, brightness,
                self._get_runtime_transition(), blend_mode,
                {member: color for member, color in light_colors.items() if wled_entry_id(self.hass, member) == wled_entry_id(self.hass, entity)},
                {member: self._entity_brightness(member, brightness) for member in light_colors},
                send_palette=self._send_palette_to_wled,
            ))
            for entity, index in devices.values()
        ]
        async def apply_members(members):
            results = await asyncio.gather(*(
                self._light_controller.apply_color_to_light(
                    entity, color, brightness=self._entity_brightness(entity, brightness), transition=self._get_runtime_transition()
                ) for entity, color in members.items()
            ))
            return ApplyColorsResult(results=list(results))

        ordinary_task = asyncio.create_task(apply_members(ordinary)) if ordinary else None
        outcomes = await asyncio.gather(*tasks)
        result = ApplyColorsResult()
        for entry_id, succeeded in zip(devices, outcomes, strict=True):
            members = {entity: color for entity, color in light_colors.items() if wled_entry_id(self.hass, entity) == entry_id}
            if succeeded:
                result.results.extend(LightResult(entity_id=entity, success=True, color=color) for entity, color in members.items())
            else:
                fallback = await apply_members(members)
                result.results.extend(fallback.results)
        if ordinary_task:
            result.results.extend((await ordinary_task).results)
        return result

    async def _find_image_for_scene(self, scene_name: str) -> Path | None:
        """Find the image file path for a given scene name."""
        if scene_name in self._scene_to_path:
            image_path = self._scene_to_path[scene_name]
            if image_path.exists():
                return image_path

        # Cache miss or stale — refresh and try again.
        await self.async_refresh_options()

        if scene_name in self._scene_to_path:
            image_path = self._scene_to_path[scene_name]
            if image_path.exists():
                return image_path

        _LOGGER.warning("No image found for scene '%s' in %s", scene_name, IMAGE_DIRECTORY)
        return None

    def _scan_image_directory(self) -> tuple[list[str], dict[str, Path]]:
        """Scan image directory for available scenes (runs in executor)."""
        image_dir = Path(IMAGE_DIRECTORY)

        if not image_dir.exists():
            _LOGGER.warning("Image directory does not exist: %s", IMAGE_DIRECTORY)
            return [], {}

        scene_to_path: dict[str, Path] = {}
        for ext in SUPPORTED_EXTENSIONS:
            for image_path in image_dir.glob(f"*{ext}"):
                scene_name = _scene_name_from_filename(image_path.stem)
                if scene_name in (SCENE_ALBUM_ART, SCENE_RANDOM, SCENE_OFF):
                    continue
                if scene_name not in scene_to_path:
                    scene_to_path[scene_name] = image_path

        return sorted(scene_to_path.keys()), scene_to_path
