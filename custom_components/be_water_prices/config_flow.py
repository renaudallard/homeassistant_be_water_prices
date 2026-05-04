"""Config + options flow for be_water_prices.

v0.1 ships with VIVAQUA as the only registered extractor; the flow is
intentionally tiny:

  1. ``user``     -- ask for a postcode. If it resolves to a known
                     utility, persist that utility and continue to step
                     ``options``. Brussels postcodes (1000-1299) resolve
                     to VIVAQUA; everything else falls through.
  2. ``manual``   -- shown when the postcode does not resolve. User
                     picks a utility from the dropdown built from the
                     registry.
  3. ``options``  -- annual consumption (m³/yr). The Flanders-only
                     ``gedomicilieerd_persons`` and ``social_tariff``
                     fields land in v0.2.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_POSTCODE,
    CONF_UTILITY,
    DEFAULT_CONSUMPTION_M3,
    DOMAIN,
)
from .providers import all_extractors
from .providers._postcodes import resolve as _resolve_postcode


def _utility_options() -> list[dict[str, str]]:
    return [{"value": e.id, "label": e.label} for e in all_extractors()]


_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_POSTCODE): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
    }
)


def _manual_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_UTILITY): SelectSelector(
                SelectSelectorConfig(
                    options=_utility_options(),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_CONSUMPTION_M3_PER_YEAR,
                default=current.get(CONF_CONSUMPTION_M3_PER_YEAR, DEFAULT_CONSUMPTION_M3),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=2000,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


class BeWaterPricesConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    VERSION = 1

    def __init__(self) -> None:
        self._utility: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            postcode = user_input[CONF_POSTCODE].strip()
            resolved = _resolve_postcode(postcode)
            if resolved is not None:
                self._utility = resolved
                return await self.async_step_options()
            return await self.async_step_manual()
        return self.async_show_form(step_id="user", data_schema=_USER_SCHEMA)

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._utility = user_input[CONF_UTILITY]
            return await self.async_step_options()
        return self.async_show_form(step_id="manual", data_schema=_manual_schema())

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._utility is not None
        await self.async_set_unique_id(f"{DOMAIN}_{self._utility}")
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title=self._utility.upper(),
                data={CONF_UTILITY: self._utility},
                options=user_input,
            )
        return self.async_show_form(
            step_id="options",
            data_schema=_options_schema({}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BeWaterPricesOptionsFlow(config_entry)


class BeWaterPricesOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options)),
        )
