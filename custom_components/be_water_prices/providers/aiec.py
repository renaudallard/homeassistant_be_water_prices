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

"""AIEC -- Association Intercommunale des Eaux du Condroz.

Tiny operator covering parts of the Condroz region. AIEC publishes its
card only as a picture: ``Prix.htm`` embeds a JPEG whose name carries
the day the card took effect (``Tarif-2026-04-1-<cms hash>.jpg``).

The picture is read by the project's card archiver, with Tesseract, and
the text it read is kept on the archive branch beside the row it made
(``scripts/archive_cards.py``). Nothing in Home Assistant decodes a
picture: an installation reads that text back and finds the tariff in it
itself, with :func:`._card_figures.read_figures`. That check takes the
one CVD, CVA and Fonds social whose ten derived figures the card prints
are all in the text, and refuses anything else, so a misread figure, a
row edited by hand or a text read off another picture is never served.

AIEC has drawn every edition differently since 2021, in other fonts, at
other scales and in another layout, which is why the check looks for
figures rather than labels or positions.

A card transcribed by hand in ``_TRANSCRIBED`` is served before any
reading, and when the archiver reads that card too, a reading that
disagrees with it is reported. A picture neither transcribed nor read
falls back to the Callmepower aggregator dated from the picture, which is
what every AIEC entry had until the reading existed: the aggregator sat on
2,460 for the five months after AIEC moved to 3,050, which under-billed
an 80 m3 household by 53.16 EUR a year with nothing anywhere to show it.

Sources: https://www.eauxducondroz.be/Prix.htm
         https://callmepower.be/fr/eau/distributeurs/aiec
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from datetime import date
from functools import partial
from urllib.parse import urljoin, urlparse

import aiohttp

from ..const import REGION_WALLONIA
from ._archive import archived_picture_text
from ._card_figures import CardFigures, read_figures
from ._html import fetch_html
from ._pdf import fetch_picture_text, note_unread_picture, reads_pictures
from ._walloon_simple import build_extractor, build_tariff, spge_components
from ._walloon_simple import parse_tariff as _parse_tariff
from .base import (
    ExtractorError,
    TransientFetchError,
    WaterExtractor,
    WaterTariff,
    belgian_today,
)

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "aiec"
LABEL = "AIEC"
SOURCE_URL = "https://callmepower.be/fr/eau/distributeurs/aiec"
_LABEL_PREFIX = "AIEC tarifs (via Callmepower)"


def parse_tariff(html: str, year: int | None = None) -> WaterTariff:
    return _parse_tariff(
        html,
        utility_id=UTILITY_ID,
        source_url=SOURCE_URL,
        label_prefix=_LABEL_PREFIX,
        year=year,
    )


# The operator's own page, and the card picture it embeds. The trailing
# hash is the CMS dedupe suffix, as on the Water-link and Aquaduin links.
# The gap before the extension is bounded: unbounded, every date with no
# picture after it scanned to the end of the page and back, which made a
# hostile page cost the square of its length.
OPERATOR_URL = "https://www.eauxducondroz.be/Prix.htm"
_CARD_IMAGE_RE = re.compile(
    r"Tarif-(20\d\d)-(\d{1,2})-(\d{1,2})[^\"'>\s]{0,200}\.jpe?g",
    re.IGNORECASE,
)
# How far back from a picture's name its path may start, and what ends it:
# the attribute's quote or ``=``, or a space.
_PATH_MAX = 200
_PATH_STOPS = "\"'= \t\r\n<>"
# AIEC's site, with or without its www.
_SITE = "eauxducondroz.be"
# The VAT every AIEC card has printed its TVAC column at.
_VAT = 0.06


@dataclasses.dataclass(frozen=True)
class _Card:
    """What a card picture prints, in EUR/m3 ex-VAT."""

    cvd: float
    cva: float
    fse: float
    vat: float


@dataclasses.dataclass(frozen=True)
class Picture:
    """The newest card picture on AIEC's page: the day it took effect and,
    when its path stays on AIEC's own site, where it is."""

    published: date
    url: str | None


# The figures each card prints, keyed by the day it took effect, which is
# the day its picture's file name carries. Read off the picture by hand.
# A date that is not here is served from the archive's reading of the
# picture once it checks out, and from the aggregator until then.
_TRANSCRIBED: dict[date, _Card] = {
    # "STRUCTURE TARIFAIRE DE L'EAU AU 01/04/2026": CVD 3,050 EUR, CVA
    # 2,748 EUR and the "Contribution au Fonds social" 0,0339 EUR HTVA,
    # their TVAC column headed "TVAC 6%".
    # The same card's own per-inhabitant table (233 / 449 / 665 / 882 /
    # 1.098 / 1.314 / 1.531 EUR) is reproduced to within rounding by this
    # CVD and by no other.
    date(2026, 4, 1): _Card(cvd=3.050, cva=2.748, fse=0.0339, vat=_VAT),
}


def _tariff(card: _Card, published: date, label: str) -> WaterTariff:
    cva, fse = spge_components(cva=card.cva, fse=card.fse, label=LABEL)
    return build_tariff(
        utility_id=UTILITY_ID,
        cvd=card.cvd,
        source_url=OPERATOR_URL,
        publication_label=label,
        year=published.year,
        cva=cva,
        fse=fse,
        vat_rate=card.vat,
        valid_from=published,
    )


def card_from_operator_page(published: date | None) -> WaterTariff | None:
    """The card AIEC's page is showing, when its rate has been transcribed.

    ``published`` is what :func:`published_card_date` read off the page.
    ``None`` when the page shows a card that is not in the table above.
    """
    if published is None:
        return None
    card = _TRANSCRIBED.get(published)
    if card is None:
        return None
    return _tariff(card, published, f"AIEC tarifs au {published.strftime('%d/%m/%Y')}")


def card_from_figures(figures: CardFigures, published: date) -> WaterTariff:
    """The card a reading of the picture found, dated from the picture."""
    card = _Card(cvd=float(figures.cvd), cva=float(figures.cva), fse=float(figures.fse), vat=_VAT)
    label = f"AIEC tarifs au {published.strftime('%d/%m/%Y')} (lu sur l'image)"
    return _tariff(card, published, label)


def _picture_url(html: str, start: int) -> str | None:
    """The absolute URL of the attribute value a picture's name sits in,
    when it stays on AIEC's site over https."""
    low = max(0, start - _PATH_MAX)
    begin = max(html.rfind(stop, low, start) for stop in _PATH_STOPS) + 1
    if begin == 0 and low > 0:
        # No stop within reach: a path longer than any real one.
        return None
    end = start
    while end < len(html) and end - start <= _PATH_MAX and html[end] not in _PATH_STOPS:
        end += 1
    url = urljoin(OPERATOR_URL, html[max(begin, low) : end])
    host = urlparse(url).hostname or ""
    if not url.startswith("https://") or host.removeprefix("www.") != _SITE:
        return None
    return url


def published_card(html: str) -> Picture | None:
    """The newest card picture on AIEC's page.

    ``None`` when the page carries no card whose name states a date, which
    is not a reason to refuse the aggregator: it is the state the page was
    in before AIEC started dating its pictures.
    """
    newest: Picture | None = None
    for match in _CARD_IMAGE_RE.finditer(html):
        try:
            published = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        if newest is None or published > newest.published:
            newest = Picture(published, _picture_url(html, match.start()))
    return newest


def published_card_date(html: str) -> date | None:
    """The day the newest card picture on AIEC's page took effect."""
    picture = published_card(html)
    return picture.published if picture is not None else None


def date_against_operator(tariff: WaterTariff, published: date | None) -> WaterTariff:
    """Date the aggregator card from the operator's own card picture.

    The aggregator prints no effective date, so :func:`build_tariff` stamps
    every card 1 January. The operator's picture is dated, and when it is
    dated later in the same year that is the day the rate it carries took
    effect, so the card is re-dated to it.

    That is all this can do. Comparing the two dates and refusing the
    mismatch, which is what this did first, cannot work: the aggregator
    card is 1 January by construction, so the refusal could never be
    cleared by the aggregator catching up, only by the next 1 January.
    Every AIEC entry went dark, and a fresh one could not finish setup.

    Re-dating instead leaves the household with a working entry whose
    ``valid_from`` says which card it is on, and hands the staleness
    machinery something real to work with.
    """
    if published is None or published <= tariff.valid_from:
        return tariff
    if published.year != tariff.valid_from.year:
        # A picture from another year says nothing about this card.
        return tariff
    _LOGGER.info(
        "AIEC published a card effective %s; dating the %s card to it until the "
        "archive's reading of the picture on %s checks out",
        published.isoformat(),
        SOURCE_URL,
        OPERATOR_URL,
    )
    return dataclasses.replace(tariff, valid_from=published)


async def _read_picture(
    session: aiohttp.ClientSession, published: date, url: str
) -> WaterTariff | None:
    """The card the picture at ``url`` prints, when its reading checks out.

    None when nobody has read the picture yet, or the reading is refused;
    a refusal is logged and reported to whoever collects them. A network
    failure, on the picture or on the archive, is raised.
    """
    archived = partial(archived_picture_text, session, UTILITY_ID, url, belgian_today())
    try:
        text = await fetch_picture_text(session, url, archived)
        if text is None:
            return None
        figures = await asyncio.to_thread(read_figures, text, label=f"AIEC picture {url}")
        # Every edition heads its card "au <dd/mm/yyyy>"; a text that does
        # not state the picture's own date is another card's, however well
        # its figures agree.
        stated = published.strftime("%d/%m/%Y")
        if stated not in text:
            raise ExtractorError(f"AIEC picture {url}: the reading does not state {stated}")
        return card_from_figures(figures, published)
    except TransientFetchError:
        raise
    except ExtractorError as err:
        _LOGGER.warning("AIEC's picture %s was not read: %s", url, err)
        note_unread_picture(str(err))
        return None


def _same_rates(a: WaterTariff, b: WaterTariff) -> bool:
    pairs = (
        (a.cvd_eur_per_m3, b.cvd_eur_per_m3),
        (a.cva_eur_per_m3, b.cva_eur_per_m3),
        (a.fse_eur_per_m3, b.fse_eur_per_m3),
    )
    return all(x is not None and y is not None and abs(x - y) < 1e-9 for x, y in pairs)


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    try:
        html = await fetch_html(session, OPERATOR_URL)
    except TransientFetchError:
        # The rate lives on this page now, so a DNS blip or a 5xx must not
        # quietly swap it for the aggregator's, which was 53 EUR a year
        # short the last time the two disagreed. Let it through: an entry
        # that already has a card keeps serving it behind the ordinary
        # stale-snapshot machinery, and a fresh one retries.
        raise
    except ExtractorError as err:
        # Not a blip: the page has moved or stopped being a page. There is
        # then no way to tell which card is on screen, and the aggregator
        # standing in undated is better than nothing at all.
        _LOGGER.warning("could not read %s: %s; serving %s", OPERATOR_URL, err, SOURCE_URL)
        return await _fetch_aggregator(session)
    # The page is parsed off the event loop, as every other extractor's is.
    picture = await asyncio.to_thread(published_card, html)
    published = picture.published if picture is not None else None
    transcribed = card_from_operator_page(published)
    if transcribed is not None:
        if picture is not None and picture.url is not None and reads_pictures():
            # The archiver reads the transcribed card too, so a reading
            # that would have disagreed with a person is known about. Only
            # a comparison: a picture that cannot be fetched today must not
            # cost the transcribed card its row.
            try:
                read = await _read_picture(session, picture.published, picture.url)
            except TransientFetchError as err:
                _LOGGER.debug("AIEC's picture %s not compared today: %s", picture.url, err)
                read = None
            if read is not None and not _same_rates(read, transcribed):
                note_unread_picture(
                    f"AIEC picture {picture.url} reads CVD {read.cvd_eur_per_m3}, CVA "
                    f"{read.cva_eur_per_m3}, FSE {read.fse_eur_per_m3}, against the transcribed "
                    f"{transcribed.cvd_eur_per_m3}, {transcribed.cva_eur_per_m3}, "
                    f"{transcribed.fse_eur_per_m3}"
                )
        return transcribed
    if picture is not None and picture.url is not None:
        read = await _read_picture(session, picture.published, picture.url)
        if read is not None:
            return read
    if published is not None:
        _LOGGER.warning(
            "AIEC published a card effective %s that neither this release nor the "
            "archive has read yet; serving %s until one does, which was 53.16 EUR a "
            "year short the last time the two disagreed",
            published.isoformat(),
            SOURCE_URL,
        )
    tariff = await _fetch_aggregator(session)
    return date_against_operator(tariff, published)


_fetch_aggregator = build_extractor(
    utility_id=UTILITY_ID,
    label=LABEL,
    source_url=SOURCE_URL,
    publication_label_prefix=_LABEL_PREFIX,
).fetch

EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_WALLONIA,
    fetch=fetch,
)
