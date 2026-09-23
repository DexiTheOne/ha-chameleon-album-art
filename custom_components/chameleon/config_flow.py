"""Config flow for Chameleon integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_LIGHT_ENTITIES,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_NORMALIZE_BRIGHTNESS,
    CONF_RANDOMIZE_COLOR_ASSIGNMENT,
    CONF_TRANSITION,
    DEFAULT_NORMALIZE_BRIGHTNESS,
    DEFAULT_RANDOMIZE_COLOR_ASSIGNMENT,
    DEFAULT_TRANSITION,
    DOMAIN,
    MAX_TRANSITION,
    MIN_TRANSITION,
)
from .helpers import get_entry_title


class ChameleonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Chameleon."""

    VERSION = 4  # v4 collapses scene select + brightness number into a single light entity with EFFECT support

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ChameleonOptionsFlow:
        """Return the options flow used to select an album-art source."""
        return ChameleonOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            light_entities: list[str] = user_input[CONF_LIGHT_ENTITIES]

            unique_id = "_".join(sorted(light_entities))
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            title = get_entry_title(self.hass, light_entities)
            return self.async_create_entry(
                title=title,
                data=user_input,
            )

        # Transition default; runtime control is via number.chameleon_{light}_transition
        # (set to 0 to disable animation entirely).
        data_schema = vol.Schema(
            {
                vol.Required(CONF_LIGHT_ENTITIES): EntitySelector(
                    EntitySelectorConfig(
                        domain="light",
                        multiple=True,
                    )
                ),
                vol.Required(CONF_TRANSITION, default=DEFAULT_TRANSITION): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_TRANSITION,
                        max=MAX_TRANSITION,
                        step=0.1,
                        unit_of_measurement="seconds",
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Optional(CONF_MEDIA_PLAYER_ENTITY): EntitySelector(EntitySelectorConfig(domain="media_player")),
                vol.Required(CONF_NORMALIZE_BRIGHTNESS, default=DEFAULT_NORMALIZE_BRIGHTNESS): BooleanSelector(),
                vol.Required(CONF_RANDOMIZE_COLOR_ASSIGNMENT, default=DEFAULT_RANDOMIZE_COLOR_ASSIGNMENT): BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )


class ChameleonOptionsFlow(OptionsFlowWithReload):
    """Configure album art and LED palette enhancement."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage Chameleon options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_media_player = self.config_entry.options.get(
            CONF_MEDIA_PLAYER_ENTITY,
            self.config_entry.data.get(CONF_MEDIA_PLAYER_ENTITY),
        )
        schema_key = vol.Optional(CONF_MEDIA_PLAYER_ENTITY)
        if current_media_player:
            schema_key = vol.Optional(CONF_MEDIA_PLAYER_ENTITY, default=current_media_player)

        normalize_brightness = self.config_entry.options.get(
            CONF_NORMALIZE_BRIGHTNESS,
            self.config_entry.data.get(CONF_NORMALIZE_BRIGHTNESS, DEFAULT_NORMALIZE_BRIGHTNESS),
        )
        randomize_color_assignment = self.config_entry.options.get(
            CONF_RANDOMIZE_COLOR_ASSIGNMENT,
            self.config_entry.data.get(CONF_RANDOMIZE_COLOR_ASSIGNMENT, DEFAULT_RANDOMIZE_COLOR_ASSIGNMENT),
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                schema_key: EntitySelector(EntitySelectorConfig(domain="media_player")),
                vol.Required(CONF_NORMALIZE_BRIGHTNESS, default=normalize_brightness): BooleanSelector(),
                vol.Required(CONF_RANDOMIZE_COLOR_ASSIGNMENT, default=randomize_color_assignment): BooleanSelector(),
            }),
        )
