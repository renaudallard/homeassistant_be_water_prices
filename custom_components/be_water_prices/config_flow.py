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

Initial setup walks three steps:

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

A ``reconfigure`` flow (Settings → Devices & services → entry → ⋯ →
Reconfigure) re-prompts only the postcode and rewrites the entry's
utility in place; commune-tied options are dropped on a utility change
because operator A's commune IDs mean nothing to operator B. Annual
consumption / persons / social tariff / meter carry over.
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
    CONF_COMMUNE_LABEL,
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

        communes = await _async_communes(self.hass, self._utility)
        if user_input is not None:
            final = dict(user_input)
            chosen = final.get(CONF_COMMUNE)
            if chosen:
                # Resolve the opaque commune id to its display label so the
                # very first publication_label / diagnostics dump shows
                # "Gent" rather than the bare GUID; the OptionsFlow does
                # the same on later edits.
                for option in communes:
                    if option.id == chosen:
                        final[CONF_COMMUNE_LABEL] = option.label
                        break
            return self.async_create_entry(
                title=get(self._utility).label,
                data={CONF_UTILITY: self._utility},
                options=final,
            )
        return self.async_show_form(
            step_id="options",
            data_schema=_options_schema(
                {}, flanders=_is_flanders(self._utility), communes=communes
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-prompt for the postcode on an existing entry.

        Surfaced as Settings → Devices & services → entry → ⋯ →
        Reconfigure. Useful when the user moved house or the original
        postcode picked the wrong default. Falls through to a manual
        utility picker if the new postcode does not resolve. Annual
        consumption / persons / meter remain in the entry's options
        and are unchanged; only the underlying utility (and the
        commune-tied options that depend on it) get rewritten.
        """
        if user_input is not None:
            postcode = user_input[CONF_POSTCODE].strip()
            resolved = _resolve_postcode(postcode)
            if resolved is not None:
                self._utility = resolved
                return await self._async_finish_reconfigure()
            return await self.async_step_reconfigure_manual()
        return self.async_show_form(step_id="reconfigure", data_schema=_USER_SCHEMA)

    async def async_step_reconfigure_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual utility picker for the reconfigure flow."""
        if user_input is not None:
            self._utility = user_input[CONF_UTILITY]
            return await self._async_finish_reconfigure()
        return self.async_show_form(step_id="reconfigure_manual", data_schema=_manual_schema())

    async def _async_finish_reconfigure(self) -> ConfigFlowResult:
        """Apply the reconfigure to the existing entry and reload it.

        When the resolved utility differs from the saved one, drop the
        commune-tied options: ``CONF_COMMUNE`` and ``CONF_COMMUNE_LABEL``
        carry an opaque, operator-specific identifier (DWG GUID, Farys
        numeric, Pidpa slug, Water-link name) that means nothing to a
        different operator. The other options (consumption, persons,
        social tariff, meter) carry over. The user can re-pick a
        commune from the new operator's dropdown via OptionsFlow.
        """
        assert self._utility is not None
        entry = self._get_reconfigure_entry()
        new_utility = self._utility
        old_utility = entry.data[CONF_UTILITY]

        new_unique_id = f"{DOMAIN}_{new_utility}"
        existing = await self.async_set_unique_id(new_unique_id)
        if existing is not None and existing.entry_id != entry.entry_id:
            return self.async_abort(reason="already_configured")

        if new_utility == old_utility:
            # No-op rewrite: postcode resolved to the same utility we
            # already have. Reload the entry anyway so the user sees a
            # fresh fetch on the next sensor tick rather than the stale
            # cached snapshot.
            return self.async_update_reload_and_abort(entry)

        new_options = {
            k: v for k, v in entry.options.items() if k not in (CONF_COMMUNE, CONF_COMMUNE_LABEL)
        }
        return self.async_update_reload_and_abort(
            entry,
            unique_id=new_unique_id,
            title=get(new_utility).label,
            data_updates={CONF_UTILITY: new_utility},
            options=new_options,
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
        utility_id = self.config_entry.data[CONF_UTILITY]
        communes = await _async_communes(self.hass, utility_id)
        if user_input is not None:
            final = dict(user_input)
            chosen = final.get(CONF_COMMUNE)
            if chosen:
                # Resolve the opaque commune id back to its display label so
                # downstream surfaces (publication_label, diagnostics) show
                # "Gent" rather than the bare "25071" or GUID.
                for option in communes:
                    if option.id == chosen:
                        final[CONF_COMMUNE_LABEL] = option.label
                        break
            elif not communes:
                # Could not fetch the commune list (transient network /
                # parser failure). The form did not render the commune
                # field, so user_input cannot carry it. Preserve the
                # previously-saved commune so a transient outage does
                # not silently wipe the user's selection back to the
                # operator-wide default.
                existing = self.config_entry.options
                if existing.get(CONF_COMMUNE):
                    final[CONF_COMMUNE] = existing[CONF_COMMUNE]
                if existing.get(CONF_COMMUNE_LABEL):
                    final[CONF_COMMUNE_LABEL] = existing[CONF_COMMUNE_LABEL]
            return self.async_create_entry(title="", data=final)
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(
                dict(self.config_entry.options),
                flanders=_is_flanders(utility_id),
                communes=communes,
            ),
        )
