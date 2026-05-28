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
Reconfigure) opens a menu offering two paths: re-prompt the postcode
(``reconfigure_postcode``) or pick the operator directly from the
dropdown (``reconfigure_manual``). The manual path is the escape hatch
for users whose postcode resolves to the wrong operator -- e.g. a
Pidpa ring commune that defaults to Pidpa but is actually served by
Water-link. If either path resolves to a per-commune operator (DWG /
Farys / Pidpa / Water-link), a third step (``reconfigure_commune``)
shows the commune dropdown so the user can pick their commune in the
same flow rather than needing a follow-up OptionsFlow. The dropdown
pre-fills with the saved commune when the resolved utility matches
the entry's current utility. Every path runs through
``_async_finish_reconfigure``, which rewrites the entry in place;
commune-tied options are dropped on a utility change because operator
A's commune IDs mean nothing to operator B. Annual consumption /
persons / social tariff / meter carry over.
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
from .providers._postcodes import resolve_candidates as _resolve_candidates
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


def _choose_schema(candidates: tuple[str, ...]) -> vol.Schema:
    """Operator picker limited to a curated candidate list.

    Used when ``resolve_candidates`` returns more than one operator
    because the postcode is genuinely split at street level.
    """
    return vol.Schema(
        {
            vol.Required(CONF_UTILITY): SelectSelector(
                SelectSelectorConfig(
                    options=[SelectOptionDict(value=c, label=get(c).label) for c in candidates],
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
    VERSION = 2

    def __init__(self) -> None:
        self._utility: str | None = None
        # The postcode the user entered (when they came through the
        # postcode path). Carried into the entry's options so future
        # migrations can re-route on resolver changes.
        self._postcode: str | None = None
        # Candidate operators for a postcode that resolves to multiple
        # utilities (street-level splits). Populated in
        # ``async_step_user`` / ``async_step_reconfigure_postcode`` when
        # the resolver returns >1 candidate; consumed by
        # ``async_step_choose`` / ``async_step_reconfigure_choose``.
        self._candidates: tuple[str, ...] = ()
        # Carry a manual-picker reconfigure commune choice across steps so
        # ``_async_finish_reconfigure`` can layer it on top of the trimmed
        # options when the user picked a per-commune operator.
        self._reconfigure_commune: str | None = None
        self._reconfigure_commune_label: str | None = None
        # Cache the live commune list across form-render / form-submit
        # within one flow instance. Without this each step makes two
        # HTTP calls to the operator's dropdown page, and a transient
        # second-call failure silently drops the user's commune pick.
        self._communes_cache: dict[str, tuple[CommuneOption, ...]] = {}

    async def _async_communes_cached(self, utility_id: str) -> tuple[CommuneOption, ...]:
        """``_async_communes`` memoised per flow instance + utility."""
        if utility_id not in self._communes_cache:
            self._communes_cache[utility_id] = await _async_communes(self.hass, utility_id)
        return self._communes_cache[utility_id]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            postcode = user_input[CONF_POSTCODE].strip()
            self._postcode = postcode
            candidates = _resolve_candidates(postcode)
            if len(candidates) == 1:
                self._utility = candidates[0]
                return await self.async_step_options()
            if len(candidates) > 1:
                self._candidates = candidates
                return await self.async_step_choose()
            return await self.async_step_manual()
        return self.async_show_form(step_id="user", data_schema=_USER_SCHEMA)

    async def async_step_choose(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Operator chooser shown when the postcode is split.

        The user picks one of two or three operators that genuinely
        share the postcode at street level (e.g. 8400 Oostende, where
        DWG serves Stene and Farys serves Mariakerke).
        """
        assert self._candidates
        if user_input is not None:
            self._utility = user_input[CONF_UTILITY]
            return await self.async_step_options()
        return self.async_show_form(step_id="choose", data_schema=_choose_schema(self._candidates))

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

        communes = await self._async_communes_cached(self._utility)
        if user_input is not None:
            final = dict(user_input)
            if self._postcode is not None:
                final[CONF_POSTCODE] = self._postcode
            chosen = final.get(CONF_COMMUNE)
            if chosen is not None:
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
        """Reconfigure entry point: offer postcode re-resolve or manual pick.

        Surfaced as Settings → Devices & services → entry → ⋯ →
        Reconfigure. Two paths:

        * ``reconfigure_postcode`` re-prompts the postcode and re-runs
          the resolver (useful after a move).
        * ``reconfigure_manual`` jumps straight to the utility dropdown
          (useful when the postcode resolves to the wrong operator,
          e.g. a Pidpa ring commune actually served by Water-link).
        """
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["reconfigure_postcode", "reconfigure_manual"],
        )

    async def async_step_reconfigure_postcode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-prompt for the postcode and re-run the utility resolver.

        Per-commune resolutions chain into ``reconfigure_commune`` so
        the user can confirm or re-pick the commune in the same flow
        (the commune dropdown is pre-filled with the current commune
        when the resolved utility matches the saved one). Split
        postcodes chain into ``reconfigure_choose`` instead.
        """
        if user_input is not None:
            postcode = user_input[CONF_POSTCODE].strip()
            self._postcode = postcode
            candidates = _resolve_candidates(postcode)
            if len(candidates) == 1:
                self._utility = candidates[0]
                if get(self._utility).supports_communes:
                    return await self.async_step_reconfigure_commune()
                return await self._async_finish_reconfigure()
            if len(candidates) > 1:
                self._candidates = candidates
                return await self.async_step_reconfigure_choose()
            return await self.async_step_reconfigure_manual()
        return self.async_show_form(step_id="reconfigure_postcode", data_schema=_USER_SCHEMA)

    async def async_step_reconfigure_choose(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Operator chooser for the reconfigure flow's postcode path."""
        assert self._candidates
        if user_input is not None:
            self._utility = user_input[CONF_UTILITY]
            if get(self._utility).supports_communes:
                return await self.async_step_reconfigure_commune()
            return await self._async_finish_reconfigure()
        return self.async_show_form(
            step_id="reconfigure_choose", data_schema=_choose_schema(self._candidates)
        )

    async def async_step_reconfigure_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual utility picker for the reconfigure flow.

        Per-commune operators (DWG / Farys / Pidpa / Water-link) chain
        into ``reconfigure_commune`` so the user can pick their commune
        in the same flow instead of needing a follow-up OptionsFlow.
        """
        if user_input is not None:
            self._utility = user_input[CONF_UTILITY]
            if get(self._utility).supports_communes:
                return await self.async_step_reconfigure_commune()
            return await self._async_finish_reconfigure()
        return self.async_show_form(step_id="reconfigure_manual", data_schema=_manual_schema())

    async def async_step_reconfigure_commune(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Commune picker shown after the manual or postcode pick.

        Pre-fills the dropdown with the saved commune when the resolved
        utility matches the entry's current utility, so the user can
        submit as-is to keep their commune (typical for a postcode
        refresh after a move within the same operator's territory).

        Skips itself when the commune list cannot be fetched (transient
        network / parser failure); the entry then loads on the
        operator-wide default and the user can pick a commune later via
        OptionsFlow.
        """
        assert self._utility is not None
        communes = await self._async_communes_cached(self._utility)
        if not communes:
            return await self._async_finish_reconfigure()
        if user_input is not None:
            chosen = user_input.get(CONF_COMMUNE)
            if chosen is not None:
                self._reconfigure_commune = chosen
                for option in communes:
                    if option.id == chosen:
                        self._reconfigure_commune_label = option.label
                        break
            return await self._async_finish_reconfigure()
        entry = self._get_reconfigure_entry()
        suggested = (
            entry.options.get(CONF_COMMUNE)
            if entry.data.get(CONF_UTILITY) == self._utility
            else None
        )
        commune_field = (
            vol.Optional(CONF_COMMUNE, description={"suggested_value": suggested})
            if suggested
            else vol.Optional(CONF_COMMUNE)
        )
        schema = vol.Schema(
            {
                commune_field: SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=c.id, label=c.label) for c in communes],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="reconfigure_commune", data_schema=schema)

    async def _async_finish_reconfigure(self) -> ConfigFlowResult:
        """Apply the reconfigure to the existing entry and reload it.

        When the resolved utility differs from the saved one, drop the
        commune-tied options: ``CONF_COMMUNE`` and ``CONF_COMMUNE_LABEL``
        carry an opaque, operator-specific identifier (DWG GUID, Farys
        numeric, Pidpa slug, Water-link name) that means nothing to a
        different operator. The other options (consumption, persons,
        social tariff, meter) carry over. If the user picked a commune
        in the manual flow (``async_step_reconfigure_commune``), layer
        it on top.
        """
        assert self._utility is not None
        entry = self._get_reconfigure_entry()
        new_utility = self._utility
        old_utility = entry.data[CONF_UTILITY]

        new_unique_id = f"{DOMAIN}_{new_utility}"
        existing = await self.async_set_unique_id(new_unique_id)
        if existing is not None and existing.entry_id != entry.entry_id:
            return self.async_abort(reason="already_configured")

        new_options = dict(entry.options)
        if new_utility != old_utility:
            new_options.pop(CONF_COMMUNE, None)
            new_options.pop(CONF_COMMUNE_LABEL, None)
        if self._reconfigure_commune is not None:
            new_options[CONF_COMMUNE] = self._reconfigure_commune
            if self._reconfigure_commune_label is not None:
                new_options[CONF_COMMUNE_LABEL] = self._reconfigure_commune_label
        if self._postcode is not None:
            new_options[CONF_POSTCODE] = self._postcode

        if new_utility == old_utility and new_options == dict(entry.options):
            # No-op rewrite (postcode resolved to the same utility we
            # already have, no commune change). Reload the entry anyway
            # so the user sees a fresh fetch on the next sensor tick.
            return self.async_update_reload_and_abort(entry)

        if new_utility == old_utility:
            # Same utility but commune changed via the manual flow.
            return self.async_update_reload_and_abort(entry, options=new_options)

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

    def __init__(self) -> None:
        # Cache the live commune list across form-render / form-submit
        # within one options flow. Same rationale as the ConfigFlow's
        # cache: HA calls async_step_init twice per user click-through
        # and a transient second-call failure silently drops the user's
        # selection.
        self._communes_cache: dict[str, tuple[CommuneOption, ...]] = {}

    async def _async_communes_cached(self, utility_id: str) -> tuple[CommuneOption, ...]:
        if utility_id not in self._communes_cache:
            self._communes_cache[utility_id] = await _async_communes(self.hass, utility_id)
        return self._communes_cache[utility_id]

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        utility_id = self.config_entry.data[CONF_UTILITY]
        communes = await self._async_communes_cached(utility_id)
        if user_input is not None:
            final = dict(user_input)
            chosen = final.get(CONF_COMMUNE)
            if chosen is not None:
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
