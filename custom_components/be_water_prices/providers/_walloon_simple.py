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

"""Shared builder for the small Walloon intercommunales.

IEG, AIEM, AIEC, CIESAC and IDEN all follow the standard CWaPE
residential structure (same as SWDE / inBW / CILE / INASEP) but each
serves a tiny territory and publishes their CVD on a single web page
in plain prose like::

    CVD : Coût Vérité de Distribution : 2,3800€/m³
    Valeur actuelle du CVD : 2,87€ HTVA
    (CVD) : 2,460 €/m³

CVA and FSE are the SPGE flat-Wallonia constants and come from
:mod:`const`. The redevance is materialised at parse time from the
regulator-defined ``20·CVD + 30·CVA`` formula.

IEG and AIEM expose their CVD on the operator's own site; AIEC,
CIESAC and IDEN don't carry the number on their official pages so
they pull from Callmepower's public aggregator listing instead.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import aiohttp
from bs4 import BeautifulSoup

from ..const import (
    DEFAULT_VAT_RATE,
    REGION_WALLONIA,
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from ._html import fetch_html
from ._pdf import to_float
from .base import ExtractorError, WaterExtractor, WaterTariff

_LOGGER = logging.getLogger(__name__)


# Match "CVD" followed (within ~120 chars, possibly across labels and
# colons) by the next ``N,NNN €`` amount. Tolerant of the three
# distinct phrasings observed across operator sites and Callmepower.
_CVD_RE = re.compile(
    r"CVD[^\d€]{0,120}?(\d+,\d{1,5})\s*€",
    re.IGNORECASE | re.DOTALL,
)
# Strong anchor that wins over the generic regex when present: AIEM's
# page introduces example formulas like "0,5 x CVD (soit 1,435€)"
# before the real value, so a first-match-wins approach picks the
# example and under-reports. "Valeur actuelle du CVD : 2,87€" gives
# us the right one.
_ACTUAL_CVD_RE = re.compile(
    r"actuelle\s+du\s+CVD[^\d]{0,40}(\d+,\d{1,5})\s*€?",
    re.IGNORECASE,
)
# Plausibility window for residential Walloon CVDs (smallest distributor
# ~2.30 EUR/m³, largest ~3.60 EUR/m³ in 2026). Filters out example
# values like 0.5·CVD that show up in formula descriptions.
_MIN_PLAUSIBLE_CVD = 1.5
_MAX_PLAUSIBLE_CVD = 6.0


def warn_constant_drift(
    *,
    published: float | None,
    constant: float,
    label: str,
    logger: logging.Logger,
    threshold: float = 0.005,
) -> None:
    """Log a warning when a CVA / FSE value scraped from a utility's page
    diverges from the SPGE flat-Wallonia constant in :mod:`const`.

    ``label`` should identify both the utility and the component, e.g.
    ``"SWDE CVA"`` or ``"CILE FSE"``. No-op when ``published`` is ``None``
    (the row was not present on the page).
    """
    if published is None:
        return
    if abs(published - constant) > threshold:
        logger.warning(
            "%s published value %s differs from Wallonia constant %s",
            label,
            published,
            constant,
        )


def parse_cvd(html: str) -> float:
    """Return the CVD in EUR/m³ from a captured Walloon utility page.

    Tries in order:
      1. ``actuelle du CVD : N,NNN €`` (the AIEM "current value" phrasing).
      2. The largest CVD reference whose value falls inside the
         plausibility window. Picking the largest rather than the first
         protects against pages that quote a historic value before the
         current one (CVDs only index up).
      3. The first CVD reference, plausible or not.

    Raises :class:`ExtractorError` when no ``CVD … N,NNN €`` string is
    found inside the rendered text.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)

    explicit = _ACTUAL_CVD_RE.search(text)
    if explicit is not None:
        candidate = to_float(explicit.group(1))
        # Keep the explicit match only when it lands in the plausibility
        # window. If a page ever surfaces "actuelle du CVD" in an
        # example / historic context (0,5 €, etc.), the fallback
        # _CVD_RE branch has a better shot at finding the real value.
        if _MIN_PLAUSIBLE_CVD <= candidate <= _MAX_PLAUSIBLE_CVD:
            return candidate

    matches = [to_float(m) for m in _CVD_RE.findall(text)]
    if not matches:
        raise ExtractorError("could not find CVD on the published page")
    plausible = [v for v in matches if _MIN_PLAUSIBLE_CVD <= v <= _MAX_PLAUSIBLE_CVD]
    if plausible:
        return max(plausible)
    # Every match fell outside the plausibility window. Surface the
    # failure rather than silently emitting whichever value happened
    # to come first; a stale/cached/garbage page would otherwise let
    # an example-only figure ride into pricing and downstream sensors.
    raise ExtractorError(
        f"no plausible CVD on the page (matches outside [{_MIN_PLAUSIBLE_CVD}, "
        f"{_MAX_PLAUSIBLE_CVD}]: {matches!r})"
    )


def build_tariff(
    *,
    utility_id: str,
    cvd: float,
    source_url: str,
    publication_label: str,
    year: int,
) -> WaterTariff:
    """Build a Walloon :class:`WaterTariff` from the parsed CVD.

    Materialises the redevance as ``20·CVD + 30·CVA`` and pulls CVA /
    FSE from the SPGE flat-Wallonia constants.
    """
    cva = WALLONIA_CVA_EUR_PER_M3
    fse = WALLONIA_FSE_EUR_PER_M3
    redevance = 20.0 * cvd + 30.0 * cva
    return WaterTariff(
        utility=utility_id,
        region=REGION_WALLONIA,
        valid_from=date(year, 1, 1),
        valid_until=date(year, 12, 31),
        publication_label=publication_label,
        source_url=source_url,
        yearly_fixed_fee=redevance,
        cvd_eur_per_m3=cvd,
        cva_eur_per_m3=cva,
        fse_eur_per_m3=fse,
        vat_rate=DEFAULT_VAT_RATE,
    )


def parse_tariff(
    html: str,
    *,
    utility_id: str,
    source_url: str,
    label_prefix: str,
    year: int | None = None,
) -> WaterTariff:
    """One-call parser for any of the small Walloon intercommunales."""
    cvd = parse_cvd(html)
    target = year or date.today().year
    return build_tariff(
        utility_id=utility_id,
        cvd=cvd,
        source_url=source_url,
        publication_label=f"{label_prefix} {target}",
        year=target,
    )


async def fetch_tariff(
    session: aiohttp.ClientSession,
    *,
    utility_id: str,
    source_url: str,
    label_prefix: str,
) -> WaterTariff:
    """Async fetch + parse for a small-Walloon utility."""
    html = await fetch_html(session, source_url)
    return parse_tariff(
        html,
        utility_id=utility_id,
        source_url=source_url,
        label_prefix=label_prefix,
    )


def build_extractor(
    *,
    utility_id: str,
    label: str,
    source_url: str,
    publication_label_prefix: str,
) -> WaterExtractor:
    """Return a fully-wired :class:`WaterExtractor` for a small-Walloon utility."""

    async def _fetch(session: aiohttp.ClientSession) -> WaterTariff:
        return await fetch_tariff(
            session,
            utility_id=utility_id,
            source_url=source_url,
            label_prefix=publication_label_prefix,
        )

    return WaterExtractor(
        id=utility_id,
        label=label,
        region=REGION_WALLONIA,
        fetch=_fetch,
    )
