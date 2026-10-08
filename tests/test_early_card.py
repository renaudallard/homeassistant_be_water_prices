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
"""Next year's card put up in December waits for 1 January.

Farys applies its page's active tab as published and the Walloon simple
parsers read a page that names only next year as next year's card, so a
December fetch can hand over a card that is not in force yet. The rate
sensors show it, but the closing year's running bill is not billed on it:
priced on that card the whole year was re-billed at next year's rates, and
each flip of the page between the two cards dropped the cost floor.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.be_water_prices.const import (
    CONF_COMMUNE,
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_UTILITY,
    CONF_WATER_METER_SENSOR,
    DOMAIN,
)
from custom_components.be_water_prices.coordinator import (
    WaterCoordinator,
    _card_from_record,
    _cost_basis,
)
from custom_components.be_water_prices.providers.base import (
    WaterExtractor,
    WaterTariff,
    tariff_to_dict,
)
from tests.test_ha_coordinator import _fresh_tariff

_CARD_2026 = _fresh_tariff()


def _next_years_card(factor: float) -> WaterTariff:
    return replace(
        _CARD_2026,
        valid_from=date(2027, 1, 1),
        valid_until=date(2027, 12, 31),
        publication_label="VIVAQUA test 2027",
        yearly_fixed_fee=_CARD_2026.yearly_fixed_fee * factor,
        linear_eur_per_m3=(_CARD_2026.linear_eur_per_m3 or 0.0) * factor,
        sanering_gemeentelijk_eur_per_m3=_CARD_2026.sanering_gemeentelijk_eur_per_m3 * factor,
    )


def _basis(card_year: int) -> str:
    return _cost_basis(
        utility="vivaqua", commune=None, persons=1, social=False, card_year=card_year
    )


async def _entry(hass: HomeAssistant, hass_storage: dict[str, Any]) -> MockConfigEntry:
    """A VIVAQUA entry whose meter has drawn 75 m3 of 2026."""
    hass.states.async_set("sensor.water_meter", "4075")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 80,
            CONF_WATER_METER_SENSOR: "sensor.water_meter",
        },
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}.ytd"] = {
        "version": 1,
        "minor_version": 2,
        "data": {
            "meter": "sensor.water_meter",
            "year": 2026,
            "m3": 75.0,
            "cost": None,
            "offset_m3": 4000.0,
            "basis": _basis(2026),
        },
    }
    return entry


@pytest.mark.asyncio
async def test_next_years_card_in_december_leaves_the_closing_year_alone(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer: Any
) -> None:
    freezer.move_to("2026-12-15 11:00:00+00:00")
    entry = await _entry(hass, hass_storage)
    served = {"card": _CARD_2026}

    async def _fetch(_session: Any) -> WaterTariff:
        return served["card"]

    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=(0.0, 0.0)),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        bill = coordinator.data.current_year_cost_eur
        assert coordinator.data.ytd_consumption_m3 == 75.0
        assert bill == coordinator._ytd_cost_from_m3(_CARD_2026, 75.0, 2026)

        # The page moves to its 2027 tab, dearer and then cheaper: the rate
        # sensors follow it, the closing year's bill and floor do not.
        for factor in (1.05, 0.95):
            served["card"] = _next_years_card(factor)
            await coordinator.async_refresh()
            await hass.async_block_till_done()
            assert coordinator.data.tariff.valid_from == date(2027, 1, 1)
            assert coordinator.data.current_year_cost_eur == bill
            assert coordinator._ytd.basis == _basis(2026)
            assert coordinator.data.ytd_started_at is None

        # A restart with the page still on 2027 remembers the card in force.
        served["card"] = _next_years_card(1.05)
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        stored = hass_storage[f"{DOMAIN}.{entry.entry_id}.ytd"]["data"]["card"]
        assert stored["valid_from"] == "2026-01-01"
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        assert coordinator.data.current_year_cost_eur == bill
        assert coordinator._ytd.basis == _basis(2026)

        # And the page flipping back to its 2026 tab moves nothing either.
        served["card"] = _CARD_2026
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.current_year_cost_eur == bill
        assert coordinator.data.ytd_started_at is None

        # On 1 January the 2027 card is in force and prices the new year.
        card = _next_years_card(1.05)
        served["card"] = card
        freezer.move_to("2027-01-01 11:00:00+00:00")
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator._ytd.year == 2027
        assert coordinator.data.ytd_consumption_m3 == 0.0
        assert coordinator._ytd.basis == _basis(2027)
        assert coordinator.data.current_year_cost_eur == coordinator._ytd_cost_from_m3(
            card, 0.0, 2027
        )
        assert coordinator.data.current_year_cost_eur != coordinator._ytd_cost_from_m3(
            _CARD_2026, 0.0, 2027
        )
        assert coordinator._ytd_card == (card, None)


@pytest.mark.asyncio
async def test_a_fresh_install_in_december_prices_on_the_card_it_has(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer: Any
) -> None:
    """Nothing is held, so the early card is all there is; it is not kept."""
    freezer.move_to("2026-12-15 11:00:00+00:00")
    entry = await _entry(hass, hass_storage)
    card = _next_years_card(1.05)

    async def _fetch(_session: Any) -> WaterTariff:
        return card

    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=(0.0, 0.0)),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator.data.current_year_cost_eur == coordinator._ytd_cost_from_m3(card, 75.0, 2026)
    assert coordinator._ytd_card is None


def _held(card: WaterTariff, commune: str | None, options: dict[str, Any]) -> Any:
    return SimpleNamespace(_ytd_card=(card, commune), entry=SimpleNamespace(options=options))


@pytest.mark.parametrize(
    ("held", "commune", "options", "in_force"),
    [
        # This household's card from this year stands in for the early one.
        (_CARD_2026, "gent", {CONF_COMMUNE: "gent"}, True),
        # One fetched for another operator or commune does not: the entry
        # was reconfigured, and that card was never this household's.
        (replace(_CARD_2026, utility="farys"), None, {}, False),
        (_CARD_2026, "gent", {CONF_COMMUNE: "aalst"}, False),
        # Nor does one dated after the year being priced.
        (replace(_CARD_2026, valid_from=date(2028, 1, 1)), None, {}, False),
    ],
)
def test_only_this_households_card_stands_in(
    freezer: Any, held: WaterTariff, commune: str | None, options: dict[str, Any], in_force: bool
) -> None:
    freezer.move_to("2026-12-15 11:00:00+00:00")
    early = _next_years_card(1.05)
    got = WaterCoordinator._card_in_force(_held(held, commune, options), early, 2026)  # type: ignore[arg-type]
    assert got is (held if in_force else early)


def test_the_card_in_force_round_trips_through_the_record() -> None:
    assert _card_from_record({**tariff_to_dict(_CARD_2026), "commune": "gent"}) == (
        _CARD_2026,
        "gent",
    )
    assert _card_from_record({**tariff_to_dict(_CARD_2026), "commune": None}) == (
        _CARD_2026,
        None,
    )
    assert _card_from_record(None) is None
    assert _card_from_record({"commune": None}) is None
    assert _card_from_record({**tariff_to_dict(_CARD_2026), "commune": 7}) is None
