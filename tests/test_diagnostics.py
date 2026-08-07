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

"""Diagnostics redaction helpers."""

from __future__ import annotations

from custom_components.be_water_prices._redact import scrub_tokens, sensitive_tokens
from custom_components.be_water_prices.const import (
    CONF_COMMUNE,
    CONF_COMMUNE_LABEL,
    CONF_POSTCODE,
)


class _Entry:
    def __init__(self, data: dict[str, str], options: dict[str, str]) -> None:
        self.data = data
        self.options = options


def test_sensitive_tokens_are_commune_only_not_postcode() -> None:
    entry = _Entry(
        data={CONF_POSTCODE: "2030"},
        options={CONF_COMMUNE: "geel", CONF_COMMUNE_LABEL: "Geel"},
    )
    tokens = sensitive_tokens(entry)  # type: ignore[arg-type]
    # Commune id + label only. The postcode is excluded: as a bare 4-digit
    # token it would match years / dates in the snapshot and corrupt them.
    assert set(tokens) == {"geel", "Geel"}
    assert "2030" not in tokens
    # Longest first so a label containing the slug is replaced whole.
    assert tokens == sorted(tokens, key=len, reverse=True)


def test_postcode_like_year_is_not_scrubbed_from_snapshot() -> None:
    entry = _Entry(data={CONF_POSTCODE: "2030"}, options={})
    snapshot = {"tariff": {"valid_until": "2030-12-31"}}
    scrubbed = scrub_tokens(snapshot, sensitive_tokens(entry))  # type: ignore[arg-type]
    assert scrubbed["tariff"]["valid_until"] == "2030-12-31"


def test_scrub_tokens_removes_commune_from_snapshot() -> None:
    snapshot = {
        "tariff": {
            "source_url": "https://www.pidpa.be/ons-aanbod/je-gemeente/geel",
            "publication_label": "Pidpa per-commune tarieven 2026 (geel)",
            "basis_eur_per_m3": 2.1888,
        },
        "last_error": "could not locate huishoudelijk 2026 table for commune 'geel'",
    }
    scrubbed = scrub_tokens(snapshot, ["geel"])
    assert "geel" not in scrubbed["tariff"]["source_url"]
    assert "geel" not in scrubbed["tariff"]["publication_label"]
    assert "geel" not in scrubbed["last_error"]
    # Non-string fields are left untouched.
    assert scrubbed["tariff"]["basis_eur_per_m3"] == 2.1888


def test_scrub_tokens_passes_none_and_empty_through() -> None:
    assert scrub_tokens(None, ["geel"]) is None
    assert scrub_tokens("nothing sensitive", []) == "nothing sensitive"


async def test_dump_carries_no_postcode_commune_or_meter(hass) -> None:  # type: ignore[no-untyped-def]
    """The whole dump must be free of the identifying fields.

    The file is attached to GitHub issues, and the existing tests only
    exercised the private helpers, so nothing checked what the assembled
    dump actually contains or that the redaction key set stayed complete.
    """
    import json
    from datetime import date
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.be_water_prices.const import (
        CONF_COMMUNE,
        CONF_COMMUNE_LABEL,
        CONF_CONSUMPTION_M3_PER_YEAR,
        CONF_POSTCODE,
        CONF_UTILITY,
        CONF_WATER_METER_SENSOR,
        DOMAIN,
    )
    from custom_components.be_water_prices.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    from custom_components.be_water_prices.providers.base import WaterExtractor, WaterTariff

    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.my_house_water_meter", "100")

    async def _fetch(_session):  # type: ignore[no-untyped-def]
        return WaterTariff(
            utility="pidpa",
            region="flanders",
            valid_from=date(2026, 1, 1),
            valid_until=date(2026, 12, 31),
            publication_label="Pidpa tarieven 2026 (Geel)",
            source_url="https://www.pidpa.be/ons-aanbod/je-gemeente/geel",
            yearly_fixed_fee=50.0,
            basis_eur_per_m3=2.0,
            comfort_eur_per_m3=4.0,
        )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pidpa",
        data={CONF_UTILITY: "pidpa", CONF_POSTCODE: "2440", CONF_COMMUNE: "geel"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 80,
            CONF_COMMUNE_LABEL: "Geel",
            CONF_WATER_METER_SENSOR: "sensor.my_house_water_meter",
        },
        unique_id=f"{DOMAIN}_pidpa",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(id="pidpa", label="Pidpa", region="flanders", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        dump = await async_get_config_entry_diagnostics(hass, entry)

    blob = json.dumps(dump).lower()
    for secret in ("2440", "geel", "my_house_water_meter"):
        assert secret not in blob, f"{secret!r} leaked into the diagnostics dump"
    # The dump is still useful: the tariff block survived, dates included.
    assert dump["snapshot"]["tariff"]["valid_from"] == "2026-01-01"
