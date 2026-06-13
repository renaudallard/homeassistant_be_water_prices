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

"""Diagnostics dump for be_water_prices."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_COMMUNE,
    CONF_COMMUNE_LABEL,
    CONF_POSTCODE,
    CONF_WATER_METER_SENSOR,
    DOMAIN,
)
from .coordinator import WaterCoordinator

# Fields that uniquely or near-uniquely identify the household; the
# diagnostics file ends up attached to GitHub issues so anything that
# could narrow down to a specific address has to be redacted.
_REDACT_KEYS = {CONF_POSTCODE, CONF_COMMUNE, CONF_COMMUNE_LABEL, CONF_WATER_METER_SENSOR}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: WaterCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data
    snapshot = (
        {
            "tariff": _serialise(asdict(data.tariff)),
            "fetched_at": data.fetched_at.isoformat(),
            "snapshot_age_hours": data.snapshot_age_hours,
            "snapshot_stale": data.snapshot_stale,
            "projected_annual_cost_eur": data.projected_annual_cost_eur,
            "current_year_cost_eur": data.current_year_cost_eur,
            "ytd_consumption_m3": data.ytd_consumption_m3,
            "last_error": data.last_error,
        }
        if data is not None
        else None
    )
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), _REDACT_KEYS),
            "options": async_redact_data(dict(entry.options), _REDACT_KEYS),
        },
        # The tariff's source_url / publication_label and any last_error can
        # embed the configured commune slug or label verbatim, bypassing the
        # key-based redaction above; scrub those values out of the snapshot.
        "snapshot": _scrub_tokens(snapshot, _sensitive_tokens(entry)),
    }


def _sensitive_tokens(entry: ConfigEntry) -> list[str]:
    """Commune / postcode strings that must not survive into the snapshot."""
    tokens: set[str] = set()
    for src in (entry.data, entry.options):
        for key in (CONF_COMMUNE, CONF_COMMUNE_LABEL, CONF_POSTCODE):
            value = src.get(key)
            if isinstance(value, str) and value:
                tokens.add(value)
    # Longest first so a label that contains the slug is replaced whole.
    return sorted(tokens, key=len, reverse=True)


def _scrub_tokens(value: Any, tokens: list[str]) -> Any:
    if isinstance(value, str):
        for token in tokens:
            value = value.replace(token, "**REDACTED**")
        return value
    if isinstance(value, dict):
        return {k: _scrub_tokens(v, tokens) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_tokens(v, tokens) for v in value]
    return value


def _serialise(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _serialise(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_serialise(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
