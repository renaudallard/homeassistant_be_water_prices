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

"""VIVAQUA -- monopoly water utility for the Brussels-Capital Region.

Linear tariff since 2022 (no consumption blocks). Brugel approves the
multi-year period; the current card runs through 2026, with a new one
expected for 2027.

Source: https://www.vivaqua.be/en/the-domestic-linear-rate/
The page exposes one ``<table>`` per year; the parser picks the table
whose header includes the current year and states a VAT rate (which
keeps out the side "rates 2025" comparison block), the lowest rate among
them if more than one does, and reads that rate off the header.

The table uses the layout::

    Price from January 1st <YYYY> (VAT included 6 %)
    Fixed charge (per year)            € 40,23
      supply                            € 19,69
      sanitation                        € 20,54
    Variable charge (per m³)            € 5,35
      supply                            € 2,62
      sanitation                        € 2,73

VIVAQUA publishes VAT-incl numbers; this parser divides by ``1 + VAT``,
the rate the header states, so :class:`WaterTariff` keeps its ex-VAT
convention. The two breakdown
rows ("supply" / "sanitation") are surfaced as ``linear_eur_per_m3``
(supply) and ``sanering_gemeentelijk_eur_per_m3`` (sanitation, billed
on behalf of Hydria). Their sum equals the variable-charge total.

The Region's intervention sociale for households with BIM status is on
a second page, https://www.vivaqua.be/fr/intervention-sociale/, which
states the year and two amounts::

    En 2026, l'intervention sociale s'élève à
      58,70 € pour un ménage d'1 personne
      53,36 € par personne supplémentaire composant le ménage

Both are read onto the card. The French page is read because the
English one has lagged behind it. A page that cannot be read leaves them
off rather than failing the card: only a household with BIM status is
priced on them, and pricing leaves its cost unknown without them.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from datetime import date

import aiohttp
from bs4 import BeautifulSoup, Tag

from ..const import REGION_BRUSSELS
from ._html import extract_amounts, fetch_and_parse
from ._pdf import fold_accents, to_float
from .base import (
    ExtractorError,
    TransientFetchError,
    WaterExtractor,
    WaterTariff,
    belgian_today,
    carry_prior_year_card,
    vat_from_percent,
)

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "vivaqua"
LABEL = "VIVAQUA"
SOURCE_URL = "https://www.vivaqua.be/en/the-domestic-linear-rate/"
INTERVENTION_URL = "https://www.vivaqua.be/fr/intervention-sociale/"


def _label_for_year(year: int, vat: float) -> str:
    return f"Price from January 1st {year} (VAT included {vat * 100:g} %)"


# The VAT rate a card's header states, wherever it puts it. Matching the
# literal "VAT included 6" meant any rewording of the header ("6 % VAT
# included", "incl. 6% VAT") read as "card not published yet", and the
# parser then served last year's rates until 31 March with only a
# warning, which is exactly the outcome the fallback exists to avoid on a
# card that IS there.
_VAT_PERCENT_RE = re.compile(r"\b(\d{1,2}(?:[.,]\d{1,2})?)\s{0,3}%")


def _residential_table(soup: BeautifulSoup, year: int) -> tuple[Tag, float] | None:
    """The ``<table>`` for ``year`` and the VAT rate its header states.

    Water for a household carries the reduced rate, so where more than one
    card of the year states a rate, the one with the lowest is the
    household's: a non-residential 21 % card beside it also names the
    year and VAT.
    """
    cards: list[tuple[float, Tag]] = []
    for table in soup.find_all("table"):
        text = fold_accents(table.get_text(" ", strip=True))
        stated = _VAT_PERCENT_RE.search(text)
        if str(year) in text and "vat" in text and stated is not None:
            cards.append((vat_from_percent(LABEL, to_float(stated.group(1))), table))
    if not cards:
        return None
    vat, table = min(cards, key=lambda card: card[0])
    return table, vat


def _parse_year_table(soup: BeautifulSoup, year: int) -> WaterTariff | None:
    """Try to parse the ``<table>`` for ``year``; return None if absent.

    Absent means the year's card is not on the page yet, which the caller
    answers by falling back to the previous year. A card that *is* present
    but whose rows no longer parse raises instead: that is a broken parser,
    and silently serving last year's rates for it would hide the breakage.
    """
    found = _residential_table(soup, year)
    if found is None:
        return None
    table, vat_rate = found

    # Walk rows to anchor each amount to its label rather than relying on
    # positional order; the page has occasionally swapped supply/sanitation
    # rows on prior re-renders.
    fixed_total = fixed_supply = fixed_sanitation = None
    var_total = var_supply = var_sanitation = None

    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True).lower() for td in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        label_only = cells[0]
        reads_this_row = (
            "fixed charge" in label_only
            or "variable charge" in label_only
            or (label_only in ("supply", "sanitation"))
        )
        if len(cells) > 2 and reads_this_row:
            # One label, one value: that is the shape this card has always
            # had. Reading cells[-1] out of a wider row would silently take
            # whichever column happens to sit last, and VIVAQUA putting the
            # current year first in a merged multi-year table would price
            # the previous one -- 416.54 EUR a year against 468.23 on the
            # 2025/2026 pair, with the supply-plus-sanitation cross-check
            # passing, because all three figures would come from the same
            # wrong column.
            raise ExtractorError(
                f"VIVAQUA {year} row {cells[0]!r} has {len(cells)} cells; this card is "
                "read as one label and one value, so a wider row needs the column "
                "picking rather than guessing"
            )
        if len(cells) > 2:
            # A wide row the parser does not read is a footnote or a note
            # appended to the same table. It used to be skipped, and
            # refusing the whole card for it killed VIVAQUA on a purely
            # cosmetic edit.
            continue
        label, value = cells[0], cells[1]
        amounts = extract_amounts(value)
        if not amounts:
            continue
        amt = amounts[0]
        if "fixed charge" in label:
            fixed_total = amt
        elif "variable charge" in label:
            var_total = amt
        elif label == "supply":
            if fixed_supply is None:
                fixed_supply = amt
            else:
                var_supply = amt
        elif label == "sanitation":
            if fixed_sanitation is None:
                fixed_sanitation = amt
            else:
                var_sanitation = amt

    if None in (fixed_total, var_total, var_supply, var_sanitation):
        raise ExtractorError(f"VIVAQUA {year} table found but its rows could not be parsed")
    assert fixed_total is not None
    assert var_total is not None
    assert var_supply is not None
    assert var_sanitation is not None

    # Cross-check: supply + sanitation must reconstruct the totals to the
    # cent, one cent of rounding allowed since the rows are rounded on
    # their own. Catches a published page where one of the rows was
    # edited but the headline value was not. Compared in whole cents: on
    # binary floats the real 2025 card's one-cent gap came out as
    # 0.00999, and the same gap with other cent values came out over.
    if _cents_apart(var_supply, var_sanitation, var_total) > 1:
        raise ExtractorError(
            f"VIVAQUA variable supply ({var_supply}) + sanitation ({var_sanitation})"
            f" != total ({var_total})"
        )
    if (
        fixed_supply is not None
        and fixed_sanitation is not None
        and _cents_apart(fixed_supply, fixed_sanitation, fixed_total) > 1
    ):
        raise ExtractorError(
            f"VIVAQUA fixed supply ({fixed_supply}) + sanitation"
            f" ({fixed_sanitation}) != total ({fixed_total})"
        )

    vat = 1.0 + vat_rate
    return WaterTariff(
        utility=UTILITY_ID,
        region=REGION_BRUSSELS,
        valid_from=date(year, 1, 1),
        valid_until=date(year, 12, 31),
        publication_label=_label_for_year(year, vat_rate),
        source_url=SOURCE_URL,
        yearly_fixed_fee=fixed_total / vat,
        linear_eur_per_m3=var_supply / vat,
        sanering_gemeentelijk_eur_per_m3=var_sanitation / vat,
        vat_rate=vat_rate,
    )


def _cents_apart(part: float, other: float, total: float) -> int:
    """How many cents the two parts miss the total by."""
    return abs(round(part * 100) + round(other * 100) - round(total * 100))


def parse_tariff(html: str, year: int | None = None) -> WaterTariff:
    """Parse a captured ``vivaqua/the-domestic-linear-rate`` page.

    ``year`` defaults to the current calendar year and falls back to
    the previous year if the page has not yet been refreshed (Brugel
    typically publishes the new card in late December).
    """
    soup = BeautifulSoup(html, "html.parser")
    target = year or belgian_today().year
    snapshot = _parse_year_table(soup, target)
    if snapshot is not None:
        return snapshot
    fallback = _parse_year_table(soup, target - 1)
    if fallback is not None:
        _LOGGER.warning(
            "VIVAQUA page does not yet show %d rates, falling back to %d",
            target,
            target - 1,
        )
        return carry_prior_year_card(fallback, target)
    raise ExtractorError(f"could not locate a VIVAQUA tariff table for {target} or {target - 1}")


# Read off accent-folded text, so "s'élève à" is "s'eleve a" and either
# apostrophe the page uses matches the dot.
_INTERVENTION_YEAR_RE = re.compile(r"\ben (20\d\d), l.intervention sociale s.eleve a\b")
_FIRST_PERSON_RE = re.compile(r"(\d{1,3},\d{2})\s{0,3}€\s{0,3}pour un menage d.1 personne\b")
_EXTRA_PERSON_RE = re.compile(r"(\d{1,3},\d{2})\s{0,3}€\s{0,3}par personne supplementaire\b")
# The two amounts follow the year's sentence directly. Reading further
# would take a figure out of the example or the FAQ below it.
_INTERVENTION_SPAN = 200


def parse_intervention(html: str, year: int | None = None) -> tuple[float, float]:
    """The intervention sociale for the first person and for each further one.

    ``year`` defaults to the Belgian clock's. The page names the year its
    amounts are for; one a year either side is taken with a warning, as
    the page turns over some days after the tariff card does, and the
    amounts are indexed by a few percent a year. Anything else raises.
    """
    text = fold_accents(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    target = year or belgian_today().year
    anchor = _INTERVENTION_YEAR_RE.search(text)
    if anchor is None:
        raise ExtractorError("VIVAQUA intervention sociale page states no year's amounts")
    stated = int(anchor.group(1))
    if abs(stated - target) > 1:
        raise ExtractorError(f"VIVAQUA intervention sociale page states {stated}, not {target}")
    span = text[anchor.end() : anchor.end() + _INTERVENTION_SPAN]
    first = _FIRST_PERSON_RE.search(span)
    extra = _EXTRA_PERSON_RE.search(span)
    if first is None or extra is None:
        raise ExtractorError(f"VIVAQUA intervention sociale {stated} amounts could not be parsed")
    if stated != target:
        _LOGGER.warning(
            "VIVAQUA intervention sociale page states %d amounts, using them for %d",
            stated,
            target,
        )
    return to_float(first.group(1)), to_float(extra.group(1))


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    tariff = await fetch_and_parse(session, SOURCE_URL, parse_tariff)
    try:
        first, extra = await fetch_and_parse(session, INTERVENTION_URL, parse_intervention)
    except TransientFetchError:
        # A blip, as on the tariff page: the refresh keeps the card it
        # holds, intervention included, and asks again on the next one.
        raise
    except ExtractorError as err:
        # Only a household with BIM status is priced on these. Failing the
        # card over them kept every Brussels entry on its last one, so a
        # reworded sentence on this page held a new year's rates back from
        # all of them. Without the figures pricing leaves that household's
        # cost unknown, and the daily live check fails on the card.
        _LOGGER.warning(
            "VIVAQUA's intervention sociale could not be read (%s); serving the tariff "
            "without it, so a household with BIM status shows no cost until it can",
            err,
        )
        return tariff
    return dataclasses.replace(
        tariff,
        social_intervention_first_person=first,
        social_intervention_per_extra_person=extra,
    )


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_BRUSSELS,
    fetch=fetch,
)
