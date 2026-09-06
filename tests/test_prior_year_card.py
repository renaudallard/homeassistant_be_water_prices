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

"""Last year's card stands in until 31 March, for every extractor alike.

Water-link, Aquaduin and VIVAQUA already extended the prior card's
validity when they fell back to it; Pidpa, AGSO, Farys and De Watergroep
served it with its own 31 December, so the stale Repair stood from the
first day of the year for however long the utility took to publish.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.be_water_prices.providers import (
    _html,
    agso_knokke,
    de_watergroep,
    farys,
    pidpa,
)
from custom_components.be_water_prices.providers.base import (
    ExtractorError,
    WaterTariff,
    carry_prior_year_card,
)
from tests import fixture_html


def _fake_today(module: object, monkeypatch: pytest.MonkeyPatch, today: date) -> None:
    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return today

    monkeypatch.setattr(module, "date", _FakeDate)


def _card(year: int) -> WaterTariff:
    return WaterTariff(
        utility="x",
        region="flanders",
        valid_from=date(year, 1, 1),
        valid_until=date(year, 12, 31),
        publication_label=str(year),
        source_url="https://example.invalid/",
        yearly_fixed_fee=100.0,
        basis_eur_per_m3=2.0,
    )


def test_a_prior_card_runs_to_the_end_of_march() -> None:
    assert carry_prior_year_card(_card(2026), 2027).valid_until == date(2027, 3, 31)


def test_the_current_card_is_left_alone() -> None:
    assert carry_prior_year_card(_card(2027), 2027).valid_until == date(2027, 12, 31)
    # A card dated ahead of the calendar is not shortened either.
    assert carry_prior_year_card(_card(2028), 2027).valid_until == date(2028, 12, 31)


def test_pidpa_commune_page_still_on_last_year(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_today(pidpa, monkeypatch, date(2027, 1, 5))
    t = pidpa.parse_commune_tariff(fixture_html("pidpa_geel_2026.html"), commune_slug="geel")
    assert t.valid_from == date(2026, 1, 1)
    assert t.valid_until == date(2027, 3, 31)


def test_agso_page_still_on_last_year(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_today(agso_knokke, monkeypatch, date(2027, 1, 5))
    t = agso_knokke.parse_tariff(fixture_html("agso_knokke_2026.html"))
    assert t.valid_from == date(2026, 1, 1)
    assert t.valid_until == date(2027, 3, 31)


def test_farys_page_still_on_last_year(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_today(farys, monkeypatch, date(2027, 1, 5))
    t = farys.parse_tariff(fixture_html("farys_gent_2026.json"))
    assert t.valid_from == date(2026, 1, 1)
    assert t.valid_until == date(2027, 3, 31)


async def test_de_watergroep_prior_article_runs_to_the_end_of_march(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_today(de_watergroep, monkeypatch, date(2027, 1, 5))
    with (
        patch.object(
            de_watergroep,
            "_fetch_commune_ajax",
            new=AsyncMock(side_effect=ExtractorError("empty body")),
        ),
        patch.object(
            _html,
            "fetch_html",
            new=AsyncMock(
                side_effect=[ExtractorError("HTTP 404"), fixture_html("dewatergroep_2026.html")]
            ),
        ),
    ):
        t = await de_watergroep.fetch(session=None)  # type: ignore[arg-type]
    assert t.valid_from == date(2026, 1, 1)
    assert t.valid_until == date(2027, 3, 31)
