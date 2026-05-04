"""VIVAQUA -- monopoly water utility for the Brussels-Capital Region.

Linear tariff since 2022 (no consumption blocks). Brugel approves the
multi-year period; the current card runs through 2026, with a new one
expected for 2027.

Source: https://www.vivaqua.be/en/the-domestic-linear-rate/
The page exposes one ``<table>`` per year; the parser picks the table
whose header includes the current year (matching ``"<year>"`` plus the
``"VAT"`` keyword to avoid the side "rates 2025" comparison block).

The table uses the layout::

    Price from January 1st <YYYY> (VAT included 6 %)
    Fixed charge (per year)            € 40,23
      supply                            € 19,69
      sanitation                        € 20,54
    Variable charge (per m³)            € 5,35
      supply                            € 2,62
      sanitation                        € 2,73

VIVAQUA publishes VAT-incl numbers; this parser divides by ``1 + VAT``
so :class:`WaterTariff` keeps its ex-VAT convention. The two breakdown
rows ("supply" / "sanitation") are surfaced as ``linear_eur_per_m3``
(supply) and ``sanering_gemeentelijk_eur_per_m3`` (sanitation, billed
on behalf of Hydria). Their sum equals the variable-charge total.
"""

from __future__ import annotations

import logging
from datetime import date

import aiohttp
from bs4 import BeautifulSoup

from ..const import DEFAULT_VAT_RATE, REGION_BRUSSELS
from ._html import extract_amounts, fetch_html, find_table
from .base import ExtractorError, WaterExtractor, WaterTariff

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "vivaqua"
LABEL = "VIVAQUA"
SOURCE_URL = "https://www.vivaqua.be/en/the-domestic-linear-rate/"


def _label_for_year(year: int) -> str:
    return f"Price from January 1st {year} (VAT included 6 %)"


def _parse_year_table(soup: BeautifulSoup, year: int) -> WaterTariff | None:
    """Try to parse the ``<table>`` for ``year``; return None if absent."""
    table = find_table(soup, must_contain=(str(year), "vat"))
    if table is None:
        return None

    # Walk rows to anchor each amount to its label rather than relying on
    # positional order; the page has occasionally swapped supply/sanitation
    # rows on prior re-renders.
    fixed_total = fixed_supply = fixed_sanitation = None
    var_total = var_supply = var_sanitation = None

    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True).lower() for td in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        label, value = cells[0], cells[-1]
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
        return None
    assert fixed_total is not None
    assert var_total is not None
    assert var_supply is not None
    assert var_sanitation is not None

    # Cross-check: supply + sanitation must reconstruct the totals to the
    # cent. Catches a published page where one of the rows was edited but
    # the headline value was not.
    if abs(var_supply + var_sanitation - var_total) > 0.005:
        raise ExtractorError(
            f"VIVAQUA variable supply ({var_supply}) + sanitation ({var_sanitation})"
            f" != total ({var_total})"
        )
    if (
        fixed_supply is not None
        and fixed_sanitation is not None
        and abs(fixed_supply + fixed_sanitation - fixed_total) > 0.01
    ):
        raise ExtractorError(
            f"VIVAQUA fixed supply ({fixed_supply}) + sanitation"
            f" ({fixed_sanitation}) != total ({fixed_total})"
        )

    vat = 1.0 + DEFAULT_VAT_RATE
    return WaterTariff(
        utility=UTILITY_ID,
        region=REGION_BRUSSELS,
        valid_from=date(year, 1, 1),
        valid_until=date(year, 12, 31),
        publication_label=_label_for_year(year),
        source_url=SOURCE_URL,
        yearly_fixed_fee=fixed_total / vat,
        linear_eur_per_m3=var_supply / vat,
        sanering_gemeentelijk_eur_per_m3=var_sanitation / vat,
        vat_rate=DEFAULT_VAT_RATE,
    )


def parse_tariff(html: str, year: int | None = None) -> WaterTariff:
    """Parse a captured ``vivaqua/the-domestic-linear-rate`` page.

    ``year`` defaults to the current calendar year and falls back to
    the previous year if the page has not yet been refreshed (Brugel
    typically publishes the new card in late December).
    """
    soup = BeautifulSoup(html, "html.parser")
    target = year or date.today().year
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
        return fallback
    raise ExtractorError(f"could not locate a VIVAQUA tariff table for {target} or {target - 1}")


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    html = await fetch_html(session, SOURCE_URL)
    return parse_tariff(html)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_BRUSSELS,
    fetch=fetch,
)
