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

CVA and FSE are the SPGE flat-Wallonia figures, which every page prints
beside its CVD and which are read off it (see :func:`spge_components`).
The redevance is materialised at parse time from the regulator-defined
``20·CVD + 30·CVA`` formula.

IEG, AIEM and IDEN expose their CVD on the operator's own site. AIEC
publishes its card as a picture, so :mod:`aiec` reads the picture's date
and serves the rate transcribed against it. CIESAC's site has answered
ERR_UPDATING_SERVER for months, which leaves Callmepower's public
aggregator listing as the only source for it, and an unverified one: two
of the three cards that aggregator used to supply here were found wrong
on 2026-09-08.
"""

from __future__ import annotations

import re
from datetime import date

import aiohttp
from bs4 import BeautifulSoup

from ..const import DEFAULT_VAT_RATE, REGION_WALLONIA
from ._html import fetch_and_parse
from ._pdf import to_float
from .base import (
    ExtractorError,
    WaterExtractor,
    WaterTariff,
    belgian_today,
    carry_prior_year_card,
    vat_from_percent,
)

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
# Callmepower's mid-2026 redesign leads with a summary-card grid that
# renders each value BEFORE its label ("2,460 €/m³" then "CVD
# (distribution)"), with the CVA card immediately after. The generic
# forward-looking _CVD_RE reads the label "CVD (distribution)" and grabs
# the next card's number -- the CVA -- so for AIEC (CVD 2,46 < CVA 2,748)
# it silently returned the CVA. Anchor instead on the authoritative prose
# "Coût vérité distribution (CVD) : N €", where the value FOLLOWS the
# "distribution (CVD)" label; the reversed card text cannot match it.
# IEG and AIEM sit on operator sites without this phrasing, so they fall
# through to the anchors below unchanged. The whitespace after the
# optional colon belongs to the colon, for the reason inasep._CVD_RE
# gives: two free runs around it make a failed match quadratic.
_LABELED_DIST_CVD_RE = re.compile(
    r"distribution\s*\(\s*CVD\s*\)\s*(?::\s*)?(\d+,\d{1,5})\s*€",
    re.IGNORECASE,
)
# Plausibility window for residential Walloon CVDs. As of 2026 the
# smallest distributor publishes ~2.30 EUR/m³ and the largest ~3.60
# EUR/m³. The lower bound MUST exclude AIEM's documented example
# value "0,5 x CVD (soit 1,435€)" -- the figure shown in that formula
# (1.435) is exactly the trap the example-context filter has to
# catch. 1.5 is the tightest floor that achieves that.
#
# Trade-off: a future regulatory cut to ~1.4 (CWaPE-mandated, a
# subsidised sub-region) would flip a correctly-published value into
# UpdateFailed until this bound is widened. That visible failure is
# preferable to silently emitting the example figure -- the Repairs
# UI surfaces it and a maintainer can adjust the bound for the
# affected utility once the new minimum is known.
_MIN_PLAUSIBLE_CVD = 1.5
_MAX_PLAUSIBLE_CVD = 6.0


# Where the prose pages print the two SPGE components. Each pattern binds
# the amount to its own label, so Callmepower's summary cards, which put
# the value before the label and the CVD card right next to the CVA one,
# cannot answer for each other. Every gap is bounded, and the whitespace
# after an optional separator belongs to the separator, as in
# _LABELED_DIST_CVD_RE.
_CVA_RES = (
    # AIEM: "Valeur actuelle du CVA : 2,748€"
    re.compile(r"actuelle\s+du\s+CVA[^\d€]{0,40}(\d+,\d{1,5})\s*€", re.IGNORECASE),
    # Callmepower prose and INASEP: "assainissement (CVA) : 2,748 €", "(CVA) = 2,748 €"
    re.compile(r"assainissement\s*\(\s*CVA\s*\)\s*(?:[:=]\s*)?(\d+,\d{1,5})\s*€", re.IGNORECASE),
    # IEG: "CVA : Coût Vérité d'Assainissement : 2,7480€"
    re.compile(
        r"CVA\s*:\s*Co[ûu]t\s+V[ée]rit[ée]\s+d.Assainissement\s*:\s*(\d+,\d{1,5})\s*€",
        re.IGNORECASE,
    ),
)
_FSE_RES = (
    # Callmepower and INASEP: "Fonds social de l'eau : 0,0339 €", "= 0,0339 €"
    re.compile(r"fonds\s+social\s+de\s+l.eau\s*[:=]\s*(\d+,\d{1,5})\s*€", re.IGNORECASE),
    # IEG: "fonds social de l'eau de 0,0339€"
    re.compile(r"fonds\s+social\s+de\s+l.eau\s+de\s+(\d+,\d{1,5})\s*€", re.IGNORECASE),
    # AIEM: "Fonds social de l'eau ... Nombre de m³ x 0,0339€"
    re.compile(r"fonds\s+social\s+de\s+l.eau[^€]{0,80}?x\s*(\d+,\d{1,5})\s*€", re.IGNORECASE),
)


def _first_amount(text: str, patterns: tuple[re.Pattern[str], ...]) -> float | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match is not None:
            return to_float(match.group(1))
    return None


def parse_cva(text: str) -> float | None:
    """The CVA a prose page prints, or ``None`` when it prints none."""
    return _first_amount(text, _CVA_RES)


def parse_fse(text: str) -> float | None:
    """The Fonds social contribution a prose page prints, or ``None``."""
    return _first_amount(text, _FSE_RES)


# What the two SPGE figures can plausibly be, in EUR/m3 ex-VAT: 2,748 and
# 0,0339 in 2026. Wide enough for years of indexation, narrow enough that
# a figure a decimal place out, or one label's number read for the other,
# is refused rather than billed.
_SPGE_WINDOWS = {"CVA": (1.0, 10.0), "FSE": (0.01, 0.2)}


def spge_components(*, cva: float | None, fse: float | None, label: str) -> tuple[float, float]:
    """The CVA and FSE a card is priced on: the ones its page printed.

    CWaPE moves both on 1 January and every Walloon page picks the new
    figures up at once, so the page is the source. A page that prints
    neither, or one outside its window, fails the fetch: the entry keeps
    its last good card behind the stale-snapshot Repair, and the daily
    live check, which also holds the pages of one year to each other on
    both figures, opens an issue.
    """
    if cva is None or fse is None:
        raise ExtractorError(
            f"{label} {'CVA' if cva is None else 'FSE'} is not printed on its card"
        )
    for name, value in (("CVA", cva), ("FSE", fse)):
        low, high = _SPGE_WINDOWS[name]
        if not low <= value <= high:
            raise ExtractorError(f"{label} {name} {value} is outside [{low}, {high}] EUR/m3")
    return cva, fse


# Where the Walloon pages state their VAT rate: "TVA : 6 %", "TVA de 6%",
# "TVA (6 %", "TVA 6%" in CILE's table, AIEM's "HTVA 6%" after each figure,
# Callmepower's "6 % TVA" and SWDE's English "VAT For water supply, it
# amounts to 6 %", the first number after the word. The gaps are bounded,
# as everywhere here.
_VAT_RES = (
    re.compile(
        r"\bH?TVA\s{0,3}(?:de|:|\()?\s{0,3}(\d{1,2}(?:[.,]\d{1,2})?)\s{0,3}%", re.IGNORECASE
    ),
    re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s{0,3}%\s{0,3}TVA\b", re.IGNORECASE),
    re.compile(r"\bVAT\b[^%\d]{0,40}(\d{1,2}(?:[.,]\d{1,2})?)\s{0,3}%"),
)


def stated_vat_rate(text: str, *, label: str) -> float:
    """The VAT rate a Walloon page states, or ``DEFAULT_VAT_RATE`` when it
    states none (IDEN's page does not). A page stating two
    rates cannot say which its own figures carry, so it fails the fetch."""
    found = {
        vat_from_percent(label, to_float(match.group(1)))
        for pattern in _VAT_RES
        for match in pattern.finditer(text)
    }
    if not found:
        return DEFAULT_VAT_RATE
    if len(found) > 1:
        raise ExtractorError(f"{label} states more than one VAT rate: {sorted(found)}")
    return found.pop()


def read_spge(text: str, *, utility_id: str) -> tuple[float, float]:
    """The CVA and FSE a prose page prints, read with :func:`parse_cva` and
    :func:`parse_fse` and checked by :func:`spge_components`."""
    return spge_components(cva=parse_cva(text), fse=parse_fse(text), label=utility_id)


def parse_cvd(html: str) -> float:
    """Return the CVD in EUR/m³ from a captured Walloon utility page.

    Tries in order:
      1. ``actuelle du CVD : N,NNN €`` (the AIEM "current value" phrasing).
      2. ``distribution (CVD) : N,NNN €`` -- the Callmepower prose label,
         where the value FOLLOWS the "distribution (CVD)" text. This wins
         on Callmepower's redesigned summary-card pages, whose cards place
         "CVD (distribution)" just before the CVA card's number and would
         otherwise mislead the forward scan below.
      3. The largest CVD reference whose value falls inside the
         plausibility window. Picking the largest rather than the first
         protects against pages that quote a historic value before the
         current one (CVDs only index up).

    Raises :class:`ExtractorError` when no ``CVD … N,NNN €`` string is
    found inside the rendered text.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)

    for pattern in (_ACTUAL_CVD_RE, _LABELED_DIST_CVD_RE):
        # Every plausible match the anchor finds, not the first. Taking
        # the first meant a page that prints a historic value under the
        # same label before the current one, or a neighbouring
        # distributor's, priced the household on whichever came earlier in
        # the markup: AIEC's 2,46 against 2,90 is 40 EUR a year. The
        # generic scan below already reasons this way, and for the same
        # reason: a CVD only indexes up, so the largest of the values a
        # page presents under a current-value label is the current one.
        #
        # The window gate stays: a labelled but out-of-window value falls
        # through to that scan rather than riding straight out.
        candidates = [
            value
            for value in (to_float(m) for m in pattern.findall(text))
            if _MIN_PLAUSIBLE_CVD <= value <= _MAX_PLAUSIBLE_CVD
        ]
        if candidates:
            return max(candidates)

    matches = [to_float(m) for m in _CVD_RE.findall(text)]
    if not matches:
        raise ExtractorError("could not find CVD on the published page")
    # Callmepower renders the CVA card right next to the CVD one, and the
    # CVA is flat across Wallonia and larger than several distributors'
    # CVD, so it sits in the window and wins a max() every time. The page
    # prints it under its own label too, so drop that figure rather than
    # let it stand in for a rate it is not. A distributor whose real CVD
    # lands on the same figure loses the fallback and raises, which is the
    # visible failure.
    cva = parse_cva(text)
    plausible = [
        v
        for v in matches
        if _MIN_PLAUSIBLE_CVD <= v <= _MAX_PLAUSIBLE_CVD and (cva is None or abs(v - cva) > 1e-9)
    ]
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


# The phrasings these pages date themselves with. CILE heads its table
# "au 1er janvier YYYY", INASEP and the Callmepower pages use "Tarifs
# YYYY" / "en YYYY", and AIEM dates its rates "à partir du 01/01/YYYY" or
# "depuis le 01 janvier YYYY". Only the 1st of January dates a tariff year:
# AIEM's CVD line reads "à partir du 01/02/2025", the day it last moved.
_PUBLISHED_YEAR_RES = (
    re.compile(r"tarifs?\s+(20\d\d)", re.IGNORECASE),
    re.compile(r"(?:\b1\s*er\s+janvier\s+|\b0?1\s+janvier\s+|\b0?1/0?1/)(20\d\d)", re.IGNORECASE),
    re.compile(r"\ben\s+(20\d\d)", re.IGNORECASE),
)


def detect_published_year(text: str, *, today: date | None = None) -> int | None:
    """The tariff year the page states, or ``None`` if it states none.

    Stamping the clock's year instead meant a page still publishing last
    year's rate was dated as current, so the snapshot never looked stale
    and nothing downstream could tell. Years far from today are ignored:
    these pages carry historic references and archive links, and only a
    year adjacent to now can be the one in force.
    """
    now = (today or belgian_today()).year
    # The patterns are tried in order of how much they say. "Tarifs YYYY"
    # names the card; "1er janvier YYYY" dates a rate on it, and the SPGE
    # lines an INASEP page keeps under a new "Tarifs" heading still carry
    # last year's date, so it counts only when no heading names the card.
    # A bare "en YYYY" is prose and counts only when nothing better is on
    # the page, otherwise a forward-looking sentence ("prochaine
    # indexation en 2027") would date the card a year ahead of the rate
    # it carries.
    for pattern in _PUBLISHED_YEAR_RES:
        found = {int(match.group(1)) for match in pattern.finditer(text)}
        plausible = [year for year in found if now - 1 <= year <= now + 1]
        if not plausible:
            continue
        # The year in force wins over one merely announced. Taking the
        # largest meant a page that names next year's card alongside the
        # one it is actually billing dated the rate a year ahead, so a
        # card that had gone stale never looked it. The current year is
        # the answer whenever the page names it; failing that the largest,
        # which is how a December page naming only next year is read.
        return now if now in plausible else max(plausible)
    return None


def build_tariff(
    *,
    utility_id: str,
    cvd: float,
    source_url: str,
    publication_label: str,
    year: int,
    cva: float,
    fse: float,
    vat_rate: float,
    valid_from: date | None = None,
) -> WaterTariff:
    """Build a Walloon :class:`WaterTariff` from the parsed CVD.

    Materialises the redevance as ``20·CVD + 30·CVA``, the CVA and FSE
    being what :func:`spge_components` read off the card and ``vat_rate``
    what :func:`stated_vat_rate` did. ``valid_from`` dates a
    rate that took effect inside ``year``; it defaults to 1 January,
    which is when the CWaPE cards normally turn over. A card dated last
    year, a page not yet updated in January, stands until 31 March like
    every other utility's, rather than counting as stale from the first
    day of the year.
    """
    if not _MIN_PLAUSIBLE_CVD <= cvd <= _MAX_PLAUSIBLE_CVD:
        # The shared page scan applies this window; the four extractors
        # that read the CVD off their own table did not, and a zero cell
        # went out as a card whose cost sensors then showed nothing.
        raise ExtractorError(
            f"{utility_id} CVD {cvd} is outside [{_MIN_PLAUSIBLE_CVD}, {_MAX_PLAUSIBLE_CVD}]"
        )
    redevance = 20.0 * cvd + 30.0 * cva
    tariff = WaterTariff(
        utility=utility_id,
        region=REGION_WALLONIA,
        valid_from=valid_from or date(year, 1, 1),
        valid_until=date(year, 12, 31),
        publication_label=publication_label,
        source_url=source_url,
        yearly_fixed_fee=redevance,
        cvd_eur_per_m3=cvd,
        cva_eur_per_m3=cva,
        fse_eur_per_m3=fse,
        vat_rate=vat_rate,
    )
    return carry_prior_year_card(tariff, belgian_today().year)


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
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    stated = year or detect_published_year(text)
    # Dated by the clock only as a last resort, and said so, as SWDE's is.
    target = stated or belgian_today().year
    cva, fse = read_spge(text, utility_id=utility_id)
    return build_tariff(
        utility_id=utility_id,
        cvd=cvd,
        source_url=source_url,
        publication_label=(
            f"{label_prefix} {target}"
            if stated
            else f"{label_prefix} {target} (page states no year)"
        ),
        year=target,
        cva=cva,
        fse=fse,
        vat_rate=stated_vat_rate(text, label=utility_id),
        valid_from=effective_date(text, target),
    )


# AIEM prints the day its CVD took effect next to the value:
# "Valeur actuelle du CVD : 2,87€ HTVA 6% (à partir du 01/02/2025)".
_EFFECTIVE_FROM_RE = re.compile(
    r"actuelle\s+du\s+CVD[^()]{0,60}\(\s*à\s+partir\s+du\s+(\d{1,2})/(\d{1,2})/(20\d\d)\s*\)",
    re.IGNORECASE,
)


def effective_date(text: str, year: int) -> date | None:
    """The day the page says its CVD took effect, when that day is in ``year``.

    A date in an earlier year is the previous change and says nothing
    about this card; a date this year moves valid_from off 1 January,
    the way the INASEP parser already dates a mid-year revision.
    """
    match = _EFFECTIVE_FROM_RE.search(text)
    if match is None or int(match.group(3)) != year:
        return None
    try:
        return date(year, int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


async def fetch_tariff(
    session: aiohttp.ClientSession,
    *,
    utility_id: str,
    source_url: str,
    label_prefix: str,
) -> WaterTariff:
    """Async fetch + parse for a small-Walloon utility."""
    return await fetch_and_parse(
        session,
        source_url,
        parse_tariff,
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
