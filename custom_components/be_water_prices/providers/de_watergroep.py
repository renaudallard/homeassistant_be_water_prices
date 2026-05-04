"""De Watergroep -- 167 communes / ~3.3 M customers (~49.5 % of Flanders).

The operator publishes its annual basistarief in a news-style article
on its own site (one URL per year):

    https://www.dewatergroep.be/nl-be/over-de-watergroep/nieuws/tarieven-<year>

That page carries the per-m³ basistarief in plain prose, e.g.::

    : 2,9521 euro voor 1.000 liter of 0,0029 euro (dat is 0,29 cent) per liter.

The full per-commune integrale waterprijs (drinkwater + bovengemeentelijk
+ gemeentelijk sanering) sits behind a JS-rendered commune picker on
``/nl-be/drinkwater/tarieven`` and is not in the static HTML. For v0.2
we publish only the **drinkwater leg** of the bill: drinkwater
basistarief / comforttarief and the drinkwater portion of vastrecht
(``FLANDERS_VASTRECHT_DRINKWATER`` = 50 EUR with 10 EUR/persoon
korting). Saneringsbijdragen stay at 0 -- the projected-cost sensor
will under-state the real bill by the per-commune sanering total
(typically 2-4 EUR/m³). v0.4 will add per-commune sanering once we
have a stable map.

Comforttarief is exactly ``2 ×`` the basistarief by VMM mandate; we
materialise it.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import aiohttp
from bs4 import BeautifulSoup

from ..const import (
    DEFAULT_VAT_RATE,
    FLANDERS_KORTING_DRINKWATER_PER_PERSON,
    FLANDERS_VASTRECHT_DRINKWATER,
    REGION_FLANDERS,
)
from ._html import fetch_html
from ._pdf import to_float
from .base import ExtractorError, WaterExtractor, WaterTariff

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "de_watergroep"
LABEL = "De Watergroep"
SOURCE_URL_FMT = "https://www.dewatergroep.be/nl-be/over-de-watergroep/nieuws/tarieven-{year}"

# The article phrases the basistarief as e.g. "2,9521 euro voor 1.000 liter".
# Anchor on the "voor 1.000 liter" suffix so an unrelated "X,YYYY euro"
# elsewhere in the article body cannot win.
_BASIS_RE = re.compile(
    r"([\d]+,\s*\d{3,5})\s*euro\s+voor\s+1[.,]?000\s+liter",
    re.IGNORECASE,
)


def parse_tariff(html: str, year: int) -> WaterTariff:
    """Parse a captured ``tarieven-<year>`` article."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    match = _BASIS_RE.search(text)
    if match is None:
        raise ExtractorError(
            f"could not locate De Watergroep basistarief for {year} on the news article"
        )
    basis = to_float(match.group(1))

    return WaterTariff(
        utility=UTILITY_ID,
        region=REGION_FLANDERS,
        valid_from=date(year, 1, 1),
        valid_until=date(year, 12, 31),
        publication_label=f"De Watergroep tarieven {year}",
        source_url=SOURCE_URL_FMT.format(year=year),
        yearly_fixed_fee=FLANDERS_VASTRECHT_DRINKWATER,
        yearly_fixed_fee_per_resident_discount=FLANDERS_KORTING_DRINKWATER_PER_PERSON,
        basis_eur_per_m3=basis,
        comfort_eur_per_m3=2.0 * basis,  # VMM-mandated 2× rule.
        # Sanering is per-commune; left at 0 for v0.2 (see module docstring).
        vat_rate=DEFAULT_VAT_RATE,
    )


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    """Try the current calendar year's article, fall back to last year's."""
    target = date.today().year
    try:
        html = await fetch_html(session, SOURCE_URL_FMT.format(year=target))
        return parse_tariff(html, year=target)
    except ExtractorError as err:
        _LOGGER.info(
            "De Watergroep %d article unavailable (%s); trying %d", target, err, target - 1
        )
        html = await fetch_html(session, SOURCE_URL_FMT.format(year=target - 1))
        return parse_tariff(html, year=target - 1)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_FLANDERS,
    fetch=fetch,
)
