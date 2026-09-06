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

"""INASEP extractor against the captured 2026 fixture."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.be_water_prices.const import (
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.inasep import parse_tariff
from tests import fixture_html


def test_parses_2026_cvd() -> None:
    t = parse_tariff(fixture_html("inasep_2026.html"), year=2026)
    assert t.cvd_eur_per_m3 == 3.6734
    assert t.cva_eur_per_m3 == WALLONIA_CVA_EUR_PER_M3
    assert t.fse_eur_per_m3 == WALLONIA_FSE_EUR_PER_M3
    assert round(t.yearly_fixed_fee, 2) == round(20 * 3.6734 + 30 * WALLONIA_CVA_EUR_PER_M3, 2)


def test_raises_when_cvd_missing() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>nothing here</body></html>")


@pytest.mark.timeout(5)
def test_the_cvd_regex_stays_linear_on_a_run_of_whitespace() -> None:
    """A page padded with spaces after the day must not hold the parser.

    The date tail once read `\\s*(?:er)?\\s+`, which backtracks over every
    way of splitting the run; 8 000 spaces cost seconds and 100 KB never
    returned. The same page has to parse in well under the timeout.
    """
    page = fixture_html("inasep_2026.html")
    assert page.count("depuis le 27 avril 2026") == 1
    padded = page.replace("depuis le 27 avril 2026", "depuis le 27" + " " * 50_000 + "X")
    t = parse_tariff(padded, year=2026)
    assert t.cvd_eur_per_m3 == 3.6734
    assert t.valid_from == date(2026, 1, 1)


def test_dates_the_cvd_from_the_day_the_page_says_it_applies() -> None:
    t = parse_tariff(fixture_html("inasep_2026.html"), year=2026)
    assert t.valid_from == date(2026, 4, 27)
    assert t.valid_until == date(2026, 12, 31)


def test_a_rate_dated_in_an_earlier_year_keeps_january() -> None:
    page = fixture_html("inasep_2026.html")
    html = page.replace("depuis le 27 avril 2026", "depuis le 27 avril 2025")
    assert html != page
    assert parse_tariff(html, year=2026).valid_from == date(2026, 1, 1)


def test_the_cva_date_does_not_stand_in_for_a_missing_cvd_date() -> None:
    page = fixture_html("inasep_2026.html")
    # The CVA is dated 1 January on the real page, which is also the
    # default, so move it to a day that would show up if it were read.
    cva_date = "depuis le 1<sup>er</sup> janvier 2026"
    assert page.count(cva_date) == 1
    html = page.replace("depuis le 27 avril 2026", "").replace(cva_date, "depuis le 15 mars 2026")
    t = parse_tariff(html, year=2026)
    assert t.cvd_eur_per_m3 == 3.6734
    assert t.valid_from == date(2026, 1, 1)


def test_a_page_still_on_last_years_card_is_dated_last_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.be_water_prices.providers import inasep

    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2027, 1, 5)

    monkeypatch.setattr(inasep, "date", _FakeDate)
    t = parse_tariff(fixture_html("inasep_2026.html"))
    assert t.valid_from == date(2026, 4, 27)
    assert t.valid_until == date(2026, 12, 31)


def test_a_moved_cva_or_fonds_social_fails_the_fetch() -> None:
    """The docstring promised these checks for a long time before they existed."""
    page = fixture_html("inasep_2026.html")
    assert page.count("2,748") == 1 and page.count("0,0339") == 1
    with pytest.raises(ExtractorError, match="CVA published value"):
        parse_tariff(page.replace("2,748", "2,900"), year=2026)
    with pytest.raises(ExtractorError, match="FSE published value"):
        parse_tariff(page.replace("0,0339", "0,0400"), year=2026)


def test_a_rate_rounded_to_two_decimals_is_still_a_rate() -> None:
    """The anchor makes the match unambiguous; the decimal count was doing no work."""
    page = fixture_html("inasep_2026.html")
    assert page.count("3,6734") == 1
    assert parse_tariff(page.replace("3,6734", "3,70"), year=2026).cvd_eur_per_m3 == 3.7
