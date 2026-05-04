# Copyright (c) 2026, Renaud Allard <renaud@allard.it>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Config + options flow for be_water_prices.

The flow is three steps:

  1. ``user``     -- ask for a postcode. Brussels (1000-1299) → VIVAQUA,
                     Antwerp (2000-2999) → Pidpa, the rest of Flanders
                     → De Watergroep, Wallonia (4000-7999) → SWDE.
                     Anything else falls through to ``manual``.
  2. ``manual``   -- shown when the postcode does not resolve. User
                     picks a utility from the dropdown built from the
                     registry.
  3. ``options``  -- annual consumption (m³/yr) for everyone, plus
                     Flanders-only ``gedomicilieerd_persons`` (1-5)
                     and ``social_tariff`` (boolean) when the chosen
                     utility is Flemish. Block-tariff math relies on
                     persons; social tariff applies the VMM 80 %
                     reduction post-calc.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_COMMUNE,
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_PERSONS,
    CONF_POSTCODE,
    CONF_SOCIAL_TARIFF,
    CONF_UTILITY,
    CONF_WATER_METER_SENSOR,
    DEFAULT_CONSUMPTION_M3,
    DEFAULT_PERSONS,
    DOMAIN,
    MAX_PERSONS,
    MIN_PERSONS,
    REGION_FLANDERS,
)
from .providers import all_extractors, get
from .providers._postcodes import resolve as _resolve_postcode
from .providers.base import CommuneOption

_LOGGER = logging.getLogger(__name__)


def _utility_options() -> list[SelectOptionDict]:
    return [SelectOptionDict(value=e.id, label=e.label) for e in all_extractors()]


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


def _options_schema(
    current: dict[str, Any],
    *,
    flanders: bool,
    communes: tuple[CommuneOption, ...] = (),
) -> vol.Schema:
    fields: dict[Any, Any] = {
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
    if flanders:
        fields[
            vol.Required(
                CONF_PERSONS,
                default=current.get(CONF_PERSONS, DEFAULT_PERSONS),
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=MIN_PERSONS,
                max=MAX_PERSONS,
                step=1,
                mode=NumberSelectorMode.BOX,
            )
        )
        fields[
            vol.Required(
                CONF_SOCIAL_TARIFF,
                default=current.get(CONF_SOCIAL_TARIFF, False),
            )
        ] = BooleanSelector()

    # Per-commune utilities (De Watergroep, Farys, Water-link) get a
    # commune dropdown. The id is the utility's internal opaque
    # identifier (GUID for DWG, integer for Farys, name for Water-link).
    if communes:
        commune_default = current.get(CONF_COMMUNE)
        fields[
            vol.Optional(
                CONF_COMMUNE,
                description={"suggested_value": commune_default} if commune_default else None,
            )
        ] = SelectSelector(
            SelectSelectorConfig(
                options=[SelectOptionDict(value=c.id, label=c.label) for c in communes],
                mode=SelectSelectorMode.DROPDOWN,
            )
        )

    # Optional: a water-meter sensor (cumulative m³) that powers the
    # ``water_current_year_cost`` YTD sensor. Filtered to entities whose
    # device_class is ``water`` so the dropdown only surfaces sensors
    # the integration actually knows how to read.
    meter_default = current.get(CONF_WATER_METER_SENSOR)
    fields[
        vol.Optional(
            CONF_WATER_METER_SENSOR,
            description={"suggested_value": meter_default} if meter_default else None,
        )
    ] = EntitySelector(
        EntitySelectorConfig(
            domain="sensor",
            device_class=SensorDeviceClass.WATER,
        )
    )
    return vol.Schema(fields)


def _is_flanders(utility_id: str) -> bool:
    return get(utility_id).region == REGION_FLANDERS


async def _async_communes(hass: HomeAssistant, utility_id: str) -> tuple[CommuneOption, ...]:
    """Return the commune list for a per-commune utility, or () otherwise.

    Network failures are swallowed -- the OptionsFlow falls back to
    no commune selector rather than blocking the user.
    """
    extractor = get(utility_id)
    if not extractor.supports_communes:
        return ()
    assert extractor.list_communes is not None
    try:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(hass)
        return await extractor.list_communes(session)
    except Exception:  # network or parser failure: degrade gracefully
        _LOGGER.exception("could not fetch commune list for %s", utility_id)
        return ()


class BeWaterPricesConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg, unused-ignore]
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
                title=get(self._utility).label,
                data={CONF_UTILITY: self._utility},
                options=user_input,
            )
        communes = await _async_communes(self.hass, self._utility)
        return self.async_show_form(
            step_id="options",
            data_schema=_options_schema(
                {}, flanders=_is_flanders(self._utility), communes=communes
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BeWaterPricesOptionsFlow()


class BeWaterPricesOptionsFlow(OptionsFlow):
    # ``config_entry`` is a read-only property exposed by HA's OptionsFlow base
    # class; assigning to it from __init__ raises in modern HA. Inherit the
    # default no-arg constructor and use ``self.config_entry`` directly.

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        utility_id = self.config_entry.data[CONF_UTILITY]
        communes = await _async_communes(self.hass, utility_id)
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(
                dict(self.config_entry.options),
                flanders=_is_flanders(utility_id),
                communes=communes,
            ),
        )
