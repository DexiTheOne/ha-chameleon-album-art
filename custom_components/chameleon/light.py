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
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
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
    extract_dominant_color,
    extract_white_fraction,
    generate_gradient_path,
    normalize_palette_brightness,
    select_interesting_colors,
)
from .const import (
    CONF_ANIMATION_ENABLED,
    CONF_LIGHT_ENTITIES,
    CONF_LIGHT_ENTITY,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_NORMALIZE_BRIGHTNESS,
    CONF_INTERESTING_COLORS,
    CONF_RANDOMIZE_COLOR_ASSIGNMENT,
    CONF_SEND_PALETTE_TO_WLED,
    CONF_TRANSITION,
    DEFAULT_ANIMATION_ENABLED,
    DEFAULT_BRIGHTNESS,
    DEFAULT_COLOR_COUNT,
    DEFAULT_NORMALIZE_BRIGHTNESS,
    DEFAULT_INTERESTING_COLORS,
    DEFAULT_RANDOMIZE_COLOR_ASSIGNMENT,
    DEFAULT_SEND_PALETTE_TO_WLED,
    DEFAULT_TRANSITION,
    DEFAULT_TRANSITION_STYLE,
    DOMAIN,
    IMAGE_DIRECTORY,
    MAX_ALBUM_ART_BYTES,
    SCENE_ALBUM_ART,
    SCENE_OFF,
    SCENE_RANDOM,
    SUPPORTED_EXTENSIONS,
)
from .helpers import get_chameleon_device_name, get_entity_base_name
from .light_controller import ApplyColorsResult, LightController, LightResult
from .wled_palette import send_wled_palette, three_palette_colors, wled_entry_id

if TYPE_CHECKING:
    from .animations import AnimationManager

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

    initial_transition = entry.data.get(CONF_TRANSITION, DEFAULT_TRANSITION)
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
        self._animation_enabled = entry.options.get(
            CONF_ANIMATION_ENABLED, entry.data.get(CONF_ANIMATION_ENABLED, DEFAULT_ANIMATION_ENABLED)
        )
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
        # over the animation manager state.
        self._lock = asyncio.Lock()

        self._light_controller = LightController(hass)

        base_name = get_entity_base_name(hass, light_entities)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_light"
        self.entity_id = f"light.chameleon_{base_name}"

    # ── Helpers ──────────────────────────────────────────────────────────

    def _get_animation_manager(self) -> AnimationManager | None:
        return self.hass.data.get(DOMAIN, {}).get("animation_manager")

    def _get_runtime_transition(self) -> float:
        return _entry_data(self.hass, self._entry.entry_id).get("transition", self._initial_transition)

    def _get_runtime_transition_style(self) -> str:
        return _entry_data(self.hass, self._entry.entry_id).get("transition_style", DEFAULT_TRANSITION_STYLE)

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

    async def async_set_animation_enabled(self, enabled: bool) -> None:
        """Change continuous animation without changing the fade duration."""
        self._animation_enabled = enabled
        await self.async_reapply_current_scene()
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
        """Stop any animation and clear our registration."""
        await super().async_will_remove_from_hass()

        manager = self._get_animation_manager()
        if manager:
            await manager.stop(self._entry.entry_id)

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
        manager = self._get_animation_manager()
        attrs: dict[str, Any] = {
            "light_entities": self._light_entities,
            "light_count": len(self._light_entities),
            "applied_colors": self._applied_colors,
            "is_animating": manager.is_running(self._entry.entry_id) if manager else False,
            "animation_enabled": self._animation_enabled,
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
        async with self._lock:
            await self._do_turn_on(**kwargs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop animation and turn off all underlying lights."""
        async with self._lock:
            await self._do_turn_off()

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
            # Already on; brightness-only update. Push live to running animation
            # if any, otherwise just bump brightness on the lights without
            # re-extracting the palette.
            await self._update_brightness_only()
        # else: bare turn_on while already on with no change → no-op

        self._is_on = True
        self.async_write_ha_state()

    async def _do_turn_off(self) -> None:
        """Inner turn-off, runs under ``self._lock``."""
        manager = self._get_animation_manager()
        if manager:
            await manager.stop(self._entry.entry_id)

        # transition=0 → instant off, ignoring any trailing fade duration
        # the lights might inherit from the just-cancelled animation's last
        # turn_on with transition=speed.
        for light_entity in self._light_entities:
            try:
                await self.hass.services.async_call(
                    "light",
                    "turn_off",
                    {"entity_id": light_entity, "transition": 0},
                    blocking=True,
                )
            except Exception as e:
                _LOGGER.error("Failed to turn off %s: %s", light_entity, e)

        self._is_on = False
        self._effect = None
        self._manual_color = None
        self._applied_colors = {}
        # _last_effect is preserved so a bare turn_on can restore it.
        self.async_write_ha_state()

    async def _update_brightness_only(self) -> None:
        """Apply a brightness change without disturbing color/scene state.

        - Running animation: push live to the controller.
        - Manual color: re-apply at the new brightness.
        - Static scene: just bump brightness on the underlying lights; they
          remember their color from the last static apply.
        """
        manager = self._get_animation_manager()
        if manager and manager.is_running(self._entry.entry_id):
            manager.update_brightness(self._entry.entry_id, self._brightness_pct)
            return

        if self._manual_color is not None:
            await self._apply_manual_color(self._manual_color)
            return

        ha_brightness = int(self._brightness_pct * 255 / 100)
        for light_entity in self._light_entities:
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
        the mode select when its value changes mid-animation. Held under the
        same lock as turn_on/turn_off so re-applies serialize with user
        actions.
        """
        async with self._lock:
            if not self._is_on:
                return
            if self._manual_color is not None:
                await self._apply_manual_color(self._manual_color)
            elif self._effect is not None:
                await self._apply_effect(self._effect, reuse_random_assignment=True)

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
            manager = self._get_animation_manager()
            if manager:
                await manager.stop(self._entry.entry_id)
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
            effect = random.choice(random_options)
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

        # Stop any running animation before re-applying. The animated path will
        # start a new one; the static path stays stopped.
        manager = self._get_animation_manager()
        if manager:
            await manager.stop(self._entry.entry_id)

        transition = self._get_runtime_transition()
        brightness = self._brightness_pct

        if self._animation_enabled and transition > 0:
            result = await self._apply_colors_animated(image_path, brightness)
        else:
            result = await self._apply_colors_static(image_path, brightness)

        if not result.results:
            return

        if result.all_succeeded:
            self._effect = effect
            self._last_effect = effect
            self._applied_colors = result.applied_colors
            self._last_scene_change = datetime.now()
            verb = "animation started" if self._animation_enabled and transition > 0 else "applied"
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

        self.hass.async_create_task(self.async_apply_album_art_update())

    async def async_apply_album_art_update(self) -> None:
        """Serialize a track-driven artwork refresh with user light actions."""
        async with self._lock:
            if self._is_on and self._effect == SCENE_ALBUM_ART:
                await self._apply_album_art()
                self.async_write_ha_state()

    async def _apply_album_art(self, *, force: bool = False, reuse_random_assignment: bool = False) -> None:
        """Download, extract, and apply the configured media player's artwork."""
        if not self._media_player_entity:
            self._last_error = "No album-art media player configured"
            return

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

        colors = await extract_color_palette_bytes(
            self.hass,
            image_bytes,
            color_count=max(len(self._light_entities), DEFAULT_COLOR_COUNT),
        )
        white_fraction = await extract_white_fraction(self.hass, image_bytes) if self._interesting_colors else 0.0
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

        manager = self._get_animation_manager()
        if manager:
            await manager.stop(self._entry.entry_id)

        transition = self._get_runtime_transition()
        # Apply the new cover's colors now. The animated controller can wait
        # for a staggered phase offset and otherwise starts with a long fade.
        result = await self._apply_palette_static(
            colors, self._brightness_pct,
            transition=transition,
        )

        if result.all_failed:
            self._random_assignment_order = previous_order
            self._last_error = "Failed to apply album-art colors to any lights"
            self._failed_lights = result.failed_lights
            return
        if self._animation_enabled and transition > 0:
            await self._apply_palette_animated(colors, self._brightness_pct, send_palette=False)

        self._effect = SCENE_ALBUM_ART
        self._last_effect = SCENE_ALBUM_ART
        self._manual_color = None
        self._extracted_palette = colors
        self._applied_colors = result.applied_colors
        self._failed_lights = result.failed_lights
        self._last_artwork_key = artwork_key
        self._last_scene_change = datetime.now()
        self._album_art_updated_at = self._last_scene_change
        self._last_error = None if result.all_succeeded else f"Partial failure: {result.failed_count}/{len(result.results)} lights failed"

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
        """Apply a single RGB color directly to all underlying lights."""
        rgb_color = clamp_rgb_color(rgb_color)
        self._random_assignment_order = None
        manager = self._get_animation_manager()
        if manager:
            await manager.stop(self._entry.entry_id)

        result = await self._light_controller.apply_colors_to_lights(
            dict.fromkeys(self._light_entities, rgb_color),
            brightness=self._brightness_pct,
        )

        if result.all_succeeded:
            self._manual_color = rgb_color
            self._effect = None
            self._applied_colors = result.applied_colors
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

        if num_lights == 1 and not self._interesting_colors:
            color = await extract_dominant_color(self.hass, image_path)
            if color:
                color = self._prepare_palette([color])[0]
                self._extracted_palette = [color]
                result = await self._light_controller.apply_colors_to_lights(
                    {self._light_entities[0]: color},
                    brightness=brightness,
                    transition=self._get_runtime_transition() if not self._animation_enabled else None,
                )
                return result
            _LOGGER.error("Failed to extract dominant color from %s", image_path)
            return ApplyColorsResult()

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
        eligible_entries: dict[str, tuple[str, int]] = {}
        if self._send_palette_to_wled and three_palette_colors(colors):
            for index, entity in enumerate(light_order):
                entry_id = wled_entry_id(self.hass, entity)
                if entry_id and entry_id not in eligible_entries and await send_wled_palette(
                    self.hass, entity, colors, index, brightness=brightness, check_only=True,
                ):
                    eligible_entries[entry_id] = (entity, index)
        transition_time = transition
        if transition_time is None and not self._animation_enabled:
            transition_time = self._get_runtime_transition()
        ordinary_colors = {
            entity: color for entity, color in light_colors.items()
            if wled_entry_id(self.hass, entity) not in eligible_entries
        }
        result = await self._light_controller.apply_colors_to_lights(
            ordinary_colors, brightness=brightness, transition=transition_time,
        ) if ordinary_colors else ApplyColorsResult()
        if eligible_entries:
            fade_time = transition_time if transition_time is not None else self._light_controller.transition_time
            if fade_time > 0:
                await asyncio.sleep(fade_time)
            for entry_id, (entity, index) in eligible_entries.items():
                members = {light: color for light, color in light_colors.items() if wled_entry_id(self.hass, light) == entry_id}
                if await send_wled_palette(self.hass, entity, colors, index, brightness=brightness):
                    result.results.extend(LightResult(entity_id=light, success=True, color=color) for light, color in members.items())
                else:
                    fallback = await self._light_controller.apply_colors_to_lights(members, brightness=brightness, transition=0)
                    result.results.extend(fallback.results)
        return result

    async def _apply_colors_animated(self, image_path: Path, brightness: int) -> ApplyColorsResult:
        """Extract colors and start an animation across the configured lights."""
        manager = self._get_animation_manager()
        if not manager:
            _LOGGER.error("AnimationManager not available")
            return ApplyColorsResult()

        colors = await extract_color_palette(
            self.hass,
            image_path,
            color_count=DEFAULT_COLOR_COUNT,
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
        return await self._apply_palette_animated(colors, brightness)

    async def _apply_palette_animated(self, colors: list[RGBColor], brightness: int, *, send_palette: bool = True) -> ApplyColorsResult:
        """Animate an already-extracted palette across configured lights."""
        manager = self._get_animation_manager()
        if not manager:
            _LOGGER.error("AnimationManager not available")
            return ApplyColorsResult()

        eligible_entries: dict[str, tuple[str, int]] = {}
        if self._send_palette_to_wled and three_palette_colors(colors):
            for index, entity in enumerate(self._random_assignment_order or self._light_entities):
                entry_id = wled_entry_id(self.hass, entity)
                if entry_id and entry_id not in eligible_entries and await send_wled_palette(
                    self.hass, entity, colors, index, brightness=brightness, check_only=True,
                ):
                    eligible_entries[entry_id] = (entity, index)

        transition = self._get_runtime_transition()
        style = self._get_runtime_transition_style()
        gradient = generate_gradient_path(colors, steps_between=10)
        if self._normalize_brightness:
            gradient = normalize_palette_brightness(gradient)

        # Pre-flight availability so we don't animate dead lights.
        results: list[LightResult] = []
        available_lights: list[str] = []
        for light_entity in self._random_assignment_order or self._light_entities:
            if wled_entry_id(self.hass, light_entity) in eligible_entries:
                results.append(LightResult(entity_id=light_entity, success=True, color=colors[0]))
                continue
            is_available, error, error_msg = self._light_controller.check_light_availability(light_entity)
            if is_available:
                available_lights.append(light_entity)
                results.append(
                    LightResult(
                        entity_id=light_entity,
                        success=True,
                        color=gradient[0] if gradient else None,
                    )
                )
            else:
                results.append(
                    LightResult(
                        entity_id=light_entity,
                        success=False,
                        error=error,
                        error_message=error_msg,
                    )
                )

        if available_lights:
            await manager.start(
                self._entry.entry_id,
                available_lights,
                gradient,
                transition=transition,
                style=style,
                brightness=brightness,
            )
            _LOGGER.info(
                "Started %s animation for %d lights (transition=%.1fs)",
                style,
                len(available_lights),
                transition,
            )

        if send_palette and eligible_entries:
            # Staggered loops may wait one phase before beginning their first fade.
            await asyncio.sleep(transition * (2 if style != "synchronized" else 1))
            for entry_id, (entity, index) in eligible_entries.items():
                members = [light for light in self._random_assignment_order or self._light_entities if wled_entry_id(self.hass, light) == entry_id]
                if await send_wled_palette(self.hass, entity, colors, index, brightness=brightness):
                    results.extend(LightResult(entity_id=light, success=True, color=colors[0]) for light in members)
                else:
                    fallback = await self._light_controller.apply_colors_to_lights(
                        dict.fromkeys(members, colors[0]), brightness=brightness, transition=0,
                    )
                    results.extend(fallback.results)
        return ApplyColorsResult(results=results)

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
