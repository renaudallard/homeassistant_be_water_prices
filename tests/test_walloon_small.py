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

"""Tests for the small Walloon intercommunales (IEG, AIEM, AIEC, CIESAC, IDEN)."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.be_water_prices.const import (
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers._walloon_simple import parse_cvd
from custom_components.be_water_prices.providers.aiec import parse_tariff as parse_aiec
from custom_components.be_water_prices.providers.aiem import parse_tariff as parse_aiem
from custom_components.be_water_prices.providers.ciesac import parse_tariff as parse_ciesac
from custom_components.be_water_prices.providers.iden import parse_tariff as parse_iden
from custom_components.be_water_prices.providers.ieg import parse_tariff as parse_ieg
from tests import fixture_html


def test_each_utility_parses_its_2026_cvd() -> None:
    cases = [
        (parse_ieg, "ieg_2026.html", 2.38),
        (parse_aiem, "aiem_2026.html", 2.87),
        (parse_aiec, "aiec_callmepower_2026.html", 2.46),
        (parse_ciesac, "ciesac_callmepower_2026.html", 2.9),
        (parse_iden, "iden_2026.html", 3.3552),
    ]
    for parse_fn, fixture, expected_cvd in cases:
        t = parse_fn(fixture_html(fixture), year=2026)
        assert t.cvd_eur_per_m3 == expected_cvd, fixture
        assert t.cva_eur_per_m3 == WALLONIA_CVA_EUR_PER_M3
        assert t.fse_eur_per_m3 == WALLONIA_FSE_EUR_PER_M3
        # Redevance materialised from 20·CVD + 30·CVA.
        assert round(t.yearly_fixed_fee, 2) == round(
            20 * expected_cvd + 30 * WALLONIA_CVA_EUR_PER_M3, 2
        )
        assert t.region == "wallonia"
        assert t.valid_from.year == 2026


def test_aiem_parser_skips_the_example_value_in_the_formula_text() -> None:
    # AIEM's page spells out "0,5 x CVD (soit 1,435€)" before listing the
    # actual current value. The parser anchors on "actuelle du CVD" so
    # the example value does not win.
    t = parse_aiem(fixture_html("aiem_2026.html"), year=2026)
    assert t.cvd_eur_per_m3 == 2.87
    assert t.cvd_eur_per_m3 != 1.435


def test_callmepower_parser_ignores_the_summary_card_cva_value() -> None:
    # Callmepower's 2026 redesign leads with a summary-card grid whose
    # cards render the value before the label, and the CVA card (2,748 €)
    # follows the "CVD (distribution)" label. A naive forward scan grabs
    # the CVA; for AIEC (CVD 2,46 < CVA 2,748) that inflated the rate. The
    # parser anchors on the prose "distribution (CVD) : N €" so the real
    # CVD wins on every Callmepower page, whichever side of the CVA it sits.
    aiec = parse_aiec(fixture_html("aiec_callmepower_2026.html"), year=2026)
    assert aiec.cvd_eur_per_m3 == 2.46
    assert aiec.cvd_eur_per_m3 != 2.748  # the CVA card value, not the CVD
    assert (
        parse_ciesac(fixture_html("ciesac_callmepower_2026.html"), year=2026).cvd_eur_per_m3 == 2.9
    )
    assert parse_iden(fixture_html("iden_2026.html"), year=2026).cvd_eur_per_m3 == 3.3552


def test_the_cva_decoy_cannot_win_the_generic_scan() -> None:
    """With the prose anchor gone, the fallback must still skip the CVA.

    The anchor is one phrasing change away from not matching, and the
    generic scan then picks the largest plausible number on the page.
    On a Callmepower page that is the CVA card, which is flat across
    Wallonia and larger than several distributors' own CVD.
    """
    page = fixture_html("aiec_callmepower_2026.html")
    without_anchor = page.replace(
        "<strong>Co\u00fbt v\u00e9rit\u00e9 distribution</strong> (CVD)",
        "<strong>Kostprijs distributie</strong> (CVD)",
    )
    assert without_anchor != page, "the prose anchor was not neutralised"
    assert parse_cvd(without_anchor) == 2.46
    assert parse_cvd(without_anchor) != WALLONIA_CVA_EUR_PER_M3


def test_the_actuelle_anchor_wins_over_a_larger_example() -> None:
    """The anchor has to be what picks the value, not max() by luck.

    On the real AIEM page the example is exactly half the CVD, so the
    max-of-plausible fallback lands on the right number whether the
    anchor matched or not -- which is why the AIEM assertion held with
    the anchor deleted. Put a larger figure in the example and only the
    anchor can still get it right.
    """
    page = (
        "<html><body><p>Exemple : 2 x CVD (soit 5,740€) pour deux m³. "
        "Valeur actuelle du CVD : 2,870€.</p></body></html>"
    )
    assert parse_cvd(page) == 2.87


def test_an_example_only_page_raises_rather_than_publishing_the_example() -> None:
    """AIEM prints "0,5 x CVD (soit 1,435 EUR)" before the real value.

    If a redesign ever drops the real value, the worked example is the
    only figure left on the page. Publishing it would halve the rate
    silently, so the floor of the plausibility window exists to refuse
    it -- which nothing tested.
    """
    page = "<html><body><p>Le CVA vaut 0,5 x CVD (soit 1,435€) par m³.</p></body></html>"
    with pytest.raises(ExtractorError, match="no plausible CVD"):
        parse_cvd(page)


def test_a_historic_value_does_not_beat_the_current_one() -> None:
    """Taking the first match would answer with last year's rate."""
    page = (
        "<html><body><p>En 2024 le CVD était de 2,300 €/m³. "
        "Aujourd'hui le CVD s'élève à 2,870 €/m³.</p></body></html>"
    )
    assert parse_cvd(page) == 2.87


def test_an_anchor_on_a_placeholder_falls_through_to_the_real_value() -> None:
    """Both anchors are gated on the window for the same reason.

    An anchor that lands on an example or a placeholder would otherwise
    ride straight out, and it wins over the generic scan.
    """
    actual = (
        "<html><body><p>Valeur actuelle du CVD : 0,500€ (exemple). "
        "Le CVD réel est de 2,870 €.</p></body></html>"
    )
    assert parse_cvd(actual) == 2.87
    labeled = (
        "<html><body><p>Coût vérité distribution (CVD) : 0,500 €. "
        "Le CVD facturé est de 2,870 €.</p></body></html>"
    )
    assert parse_cvd(labeled) == 2.87


def test_an_absurd_figure_raises_rather_than_being_published() -> None:
    """The ceiling catches a cached or garbled page."""
    with pytest.raises(ExtractorError, match="no plausible CVD"):
        parse_cvd("<html><body><p>CVD 87,000 €</p></body></html>")


def test_parse_cvd_raises_on_garbage() -> None:
    with pytest.raises(ExtractorError):
        parse_cvd("<html><body>nothing about water here</body></html>")


def test_the_page_dates_the_tariff_when_it_says_so() -> None:
    """A page still on last year has to be dated last year.

    Stamping the clock's year made a stale page look current, so the
    snapshot_stale check -- which compares that stamp against today --
    could never fire on any of these nine utilities.
    """
    from datetime import date

    from custom_components.be_water_prices.providers._walloon_simple import (
        detect_published_year,
    )

    today = date(2026, 6, 1)
    assert detect_published_year("Tarifs 2026 en vigueur", today=today) == 2026
    assert detect_published_year("Prix au 1er janvier 2025", today=today) == 2025
    # A page whose heading names the new card while the SPGE lines it
    # keeps below still carry last year's date.
    assert detect_published_year("Tarifs 2027. CVA fixée au 1er janvier 2026.", today=today) == 2027
    # Archive references far from now are not the tariff in force.
    assert detect_published_year("Comparez avec les tarifs 2019", today=today) is None
    assert detect_published_year("nothing dated here", today=today) is None


def test_a_page_stuck_on_last_year_is_dated_last_year(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: the parsed tariff carries the page's year, not today's.

    The clock is pinned: left to the real one, the assertion held whether
    or not the page was read, and turned red on its own on 1 January 2027.
    """
    from datetime import date

    from custom_components.be_water_prices.providers import _walloon_simple
    from custom_components.be_water_prices.providers._walloon_simple import parse_tariff

    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 9, 6)

    monkeypatch.setattr(_walloon_simple, "date", _FakeDate)
    page = (
        "<html><body><p>Tarifs 2025. Coût vérité distribution (CVD) : 2,870 €/m³.</p></body></html>"
    )
    tariff = parse_tariff(
        page, utility_id="ieg", source_url="https://example.invalid/", label_prefix="IEG"
    )
    assert tariff.valid_from.year == 2025


@pytest.mark.parametrize("cvd", [0.0, 0.5, 12.0])
def test_the_builder_refuses_a_cvd_outside_the_plausible_window(cvd: float) -> None:
    """SWDE, CILE, inBW and INASEP hand it their own CVD, unchecked until here."""
    from custom_components.be_water_prices.providers._walloon_simple import build_tariff

    with pytest.raises(ExtractorError, match="outside"):
        build_tariff(
            utility_id="swde",
            cvd=cvd,
            source_url="https://example.invalid/",
            publication_label="test",
            year=2026,
        )


@pytest.mark.parametrize(
    ("today", "valid_until"),
    [(date(2027, 1, 15), date(2027, 3, 31)), (date(2026, 9, 6), date(2026, 12, 31))],
)
def test_a_page_still_on_last_years_card_stands_until_31_march(
    monkeypatch: pytest.MonkeyPatch, today: date, valid_until: date
) -> None:
    """The seven page-dated Walloon extractors were stale from 1 January."""
    from custom_components.be_water_prices.providers import _walloon_simple
    from custom_components.be_water_prices.providers._walloon_simple import parse_tariff

    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return today

    monkeypatch.setattr(_walloon_simple, "date", _FakeDate)
    page = (
        "<html><body><p>Tarifs 2026. Coût vérité distribution (CVD) : 2,870 €/m³.</p></body></html>"
    )
    tariff = parse_tariff(
        page, utility_id="ieg", source_url="https://example.invalid/", label_prefix="IEG"
    )
    assert tariff.valid_from == date(2026, 1, 1)
    assert tariff.valid_until == valid_until


@pytest.mark.parametrize(
    ("fixture", "utility_id"),
    [
        ("ieg_2026.html", "ieg"),
        ("aiem_2026.html", "aiem"),
        ("aiec_callmepower_2026.html", "aiec"),
        ("ciesac_callmepower_2026.html", "ciesac"),
    ],
)
def test_a_moved_cva_on_a_prose_page_fails_the_fetch(fixture: str, utility_id: str) -> None:
    """Most Walloon pages print the CVA and none of them checked it.

    IDEN is not here: it reads its own card through its own parser, which
    runs the same check (see test_iden_holds_its_page_to_the_spge_constants).
    """
    from custom_components.be_water_prices.providers._walloon_simple import parse_tariff

    page = fixture_html(fixture)
    assert "2,748" in page
    moved = page.replace("2,748", "2,900").replace("2,7480", "2,9000")
    with pytest.raises(ExtractorError, match="CVA published value"):
        parse_tariff(
            moved, utility_id=utility_id, source_url="https://example.invalid/", label_prefix="X"
        )


@pytest.mark.parametrize(
    "fixture",
    ["ieg_2026.html", "aiem_2026.html", "aiec_callmepower_2026.html"],
)
def test_a_moved_fonds_social_on_a_prose_page_fails_the_fetch(fixture: str) -> None:
    from custom_components.be_water_prices.providers._walloon_simple import parse_tariff

    page = fixture_html(fixture)
    assert "0,0339" in page
    with pytest.raises(ExtractorError, match="FSE published value"):
        parse_tariff(
            page.replace("0,0339", "0,0400"),
            utility_id="x",
            source_url="https://example.invalid/",
            label_prefix="X",
        )


def test_the_prose_pages_print_the_constants_the_engine_uses() -> None:
    """The regexes bind to every real page, so the checks are not vacuous."""
    from bs4 import BeautifulSoup

    from custom_components.be_water_prices.const import (
        WALLONIA_CVA_EUR_PER_M3,
        WALLONIA_FSE_EUR_PER_M3,
    )
    from custom_components.be_water_prices.providers._walloon_simple import parse_cva, parse_fse

    for name in (
        "ieg_2026.html",
        "aiem_2026.html",
        "aiec_callmepower_2026.html",
        "ciesac_callmepower_2026.html",
        "inasep_2026.html",
    ):
        soup = BeautifulSoup(fixture_html(name), "html.parser")
        text = soup.get_text(" ", strip=True)
        assert parse_cva(text) == WALLONIA_CVA_EUR_PER_M3, name
        if name != "ciesac_callmepower_2026.html":
            assert parse_fse(text) == WALLONIA_FSE_EUR_PER_M3, name


def test_a_forward_looking_sentence_does_not_date_the_card_ahead() -> None:
    """ "Tarifs 2026" dates the card; "en 2027" is prose and must not outrank it."""
    from datetime import date

    from custom_components.be_water_prices.providers._walloon_simple import (
        detect_published_year,
    )

    today = date(2026, 9, 6)
    text = "Tarifs 2026 en vigueur. Prochaine indexation en 2027."
    assert detect_published_year(text, today=today) == 2026
    # With nothing stronger on the page, the prose year still counts.
    assert detect_published_year("Le prix en 2026 est inchangé.", today=today) == 2026


def test_a_cvd_change_dated_this_year_moves_valid_from() -> None:
    """AIEM prints the day its CVD took effect; a day in an earlier year is the previous change."""
    page = fixture_html("aiem_2026.html")
    assert page.count("partir du 01/02/2025") == 1
    assert parse_aiem(page, year=2026).valid_from == date(2026, 1, 1)
    moved = parse_aiem(page.replace("partir du 01/02/2025", "partir du 01/02/2026"), year=2026)
    assert moved.valid_from == date(2026, 2, 1)


def test_iden_reads_the_operator_s_own_card_not_an_aggregator() -> None:
    """Callmepower carried 3,555 where IDEN's own page says 3,3552."""
    t = parse_iden(fixture_html("iden_2026.html"), year=2026)
    assert t.cvd_eur_per_m3 == 3.3552
    assert "callmepower" not in t.source_url.lower()
    assert t.source_url.startswith("https://www.iden-eau.be/")


def test_iden_holds_its_page_to_the_spge_constants() -> None:
    """The two flat-Wallonia rows sit beside the CVD, so they are checked."""
    page = fixture_html("iden_2026.html")
    assert "2,7480" in page and "0,0339" in page
    with pytest.raises(ExtractorError, match="CVA published value"):
        parse_iden(page.replace("2,7480", "2,8500"), year=2026)
    with pytest.raises(ExtractorError, match="FSE published value"):
        parse_iden(page.replace("0,0339", "0,0400"), year=2026)


def test_iden_does_not_read_the_rate_out_of_the_explanatory_section() -> None:
    """The FAQ below repeats every label with no value; only the card counts."""
    page = fixture_html("iden_2026.html")
    assert page.count("3,3552") == 1
    with pytest.raises(ExtractorError, match="could not locate IDEN's CVD"):
        parse_iden(page.replace('VALUE="3,3552', 'VALUE="x'), year=2026)
