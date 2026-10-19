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

"""AIEC's card picture: the archive's reading of it, checked before it is
served, and the reading the archiver makes of it."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sys
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from custom_components.be_water_prices.providers import _archive, aiec
from custom_components.be_water_prices.providers._card_figures import read_figures
from custom_components.be_water_prices.providers._pdf import (
    collect_unread_pictures,
    read_pictures_through,
)
from custom_components.be_water_prices.providers.base import (
    TransientFetchError,
    WaterTariff,
)
from tests import fixture_html

FIXTURES = Path(__file__).parent / "fixtures"
APRIL = "https://www.eauxducondroz.be/WEFiles/Image/WEImage/Tarif-2026-04-1-WEaf268c1c2a.jpg"
JANUARY = APRIL.replace("2026-04-1", "2026-01-1")
JPEG = b"\xff\xd8\xff a picture"


def _ocr(edition: str) -> str:
    return (FIXTURES / f"aiec_ocr_{edition}.txt").read_text(encoding="utf-8")


def _page(picture: str = "Tarif-2026-04-1-") -> str:
    """AIEC's page, showing the picture named ``picture``."""
    return fixture_html("aiec_operator_2026.html").replace("Tarif-2026-04-1-", picture)


def _aggregator() -> WaterTariff:
    return aiec.parse_tariff(fixture_html("aiec_callmepower_2026.html"), year=2026)


class _Chunks:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        yield self._payload


class _Response:
    status = 200
    content_length = None
    content_type = "image/jpeg"
    history: tuple[object, ...] = ()

    def __init__(self, payload: bytes) -> None:
        self.content = _Chunks(payload)

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Session:
    """The picture's bytes, for whoever downloads it."""

    def __init__(self, payload: bytes = JPEG) -> None:
        self.payload = payload
        self.asked: list[str] = []

    def get(self, url: str, **_kw: Any) -> _Response:
        self.asked.append(url)
        return _Response(self.payload)


def _fetch(
    page: str,
    archived: AsyncMock,
    *,
    reader: Any = None,
    session: Any = None,
) -> tuple[WaterTariff, list[str]]:
    """aiec.fetch on ``page``, the archive answering ``archived``; the
    readings it refused come back beside the card."""
    unread: list[str] = []
    with (
        patch.object(aiec, "fetch_html", new=AsyncMock(return_value=page)),
        patch.object(aiec, "_fetch_aggregator", new=AsyncMock(return_value=_aggregator())),
        patch.object(aiec, "archived_picture_text", new=archived),
        collect_unread_pictures(unread),
    ):
        if reader is None:
            card = asyncio.run(aiec.fetch(session))
        else:
            with read_pictures_through(reader):
                card = asyncio.run(aiec.fetch(session))
    return card, unread


def test_an_installation_serves_the_archives_reading_once_it_checks_out() -> None:
    archived = AsyncMock(return_value=_ocr("2026-01-01"))
    card, unread = _fetch(_page("Tarif-2026-01-1-"), archived)
    assert (card.cvd_eur_per_m3, card.cva_eur_per_m3, card.fse_eur_per_m3) == (2.46, 2.748, 0.0339)
    assert card.valid_from == date(2026, 1, 1)
    assert card.vat_rate == 0.06
    assert card.publication_label == "AIEC tarifs au 01/01/2026 (lu sur l'image)"
    assert unread == []
    _session, utility, url, _today = archived.await_args.args
    assert (utility, url) == ("aiec", JANUARY)


def test_a_reading_that_does_not_check_out_is_not_served() -> None:
    """Tesseract lost the point of 1.2694 on the 2021 card."""
    card, unread = _fetch(_page("Tarif-2026-01-1-"), AsyncMock(return_value=_ocr("2021-06-01")))
    assert card.publication_label.startswith("AIEC tarifs (via Callmepower)")
    assert len(unread) == 1 and "misses first total TVAC" in unread[0]


def test_a_reading_of_another_card_is_not_served_for_this_one() -> None:
    """April's text under January's picture agrees with itself, so only the
    date the card states tells it apart."""
    card, unread = _fetch(_page("Tarif-2026-01-1-"), AsyncMock(return_value=_ocr("2026-04-01")))
    assert card.publication_label.startswith("AIEC tarifs (via Callmepower)")
    assert len(unread) == 1 and "does not state 01/01/2026" in unread[0]


def test_a_picture_nobody_has_read_falls_back_to_the_aggregator() -> None:
    card, unread = _fetch(_page("Tarif-2026-01-1-"), AsyncMock(return_value=None))
    assert card.publication_label.startswith("AIEC tarifs (via Callmepower)")
    assert unread == []


def test_an_archive_blip_is_raised_rather_than_swapped_for_the_aggregator() -> None:
    archived = AsyncMock(side_effect=TransientFetchError("HTTP 503 from the archive"))
    with pytest.raises(TransientFetchError):
        _fetch(_page("Tarif-2026-01-1-"), archived)


def test_a_transcribed_card_does_not_ask_the_archive() -> None:
    archived = AsyncMock(side_effect=AssertionError("asked the archive"))
    card, unread = _fetch(_page(), archived)
    assert card.cvd_eur_per_m3 == 3.05
    assert card.publication_label == "AIEC tarifs au 01/04/2026"
    assert unread == []
    archived.assert_not_awaited()


def test_a_picture_off_aiecs_site_is_never_read() -> None:
    page = '<img src="https://evil.test/Tarif-2027-01-1-x.jpg">'
    archived = AsyncMock(side_effect=AssertionError("asked the archive"))
    card, _unread = _fetch(page, archived)
    assert card.publication_label.startswith("AIEC tarifs (via Callmepower)")
    archived.assert_not_awaited()


def test_the_archiver_reads_the_picture_itself() -> None:
    """With a reader installed the picture is downloaded and handed to it,
    and the archive is not asked."""
    handed: list[tuple[str, bytes]] = []

    async def reader(url: str, payload: bytes) -> str:
        handed.append((url, payload))
        return _ocr("2026-01-01")

    session = _Session()
    archived = AsyncMock(side_effect=AssertionError("asked the archive"))
    card, _unread = _fetch(_page("Tarif-2026-01-1-"), archived, reader=reader, session=session)
    assert handed == [(JANUARY, JPEG)]
    assert card.cvd_eur_per_m3 == 2.46


def test_the_archiver_reports_a_reading_that_disagrees_with_a_transcription() -> None:
    async def january(_url: str, _payload: bytes) -> str:
        # January's figures, under April's date: a reading that would have
        # served the wrong figures for this card.
        return _ocr("2026-01-01").replace("01/01/2026", "01/04/2026")

    async def april(_url: str, _payload: bytes) -> str:
        return _ocr("2026-04-01")

    archived = AsyncMock(side_effect=AssertionError("asked the archive"))
    card, unread = _fetch(_page(), archived, reader=january, session=_Session())
    assert card.cvd_eur_per_m3 == 3.05
    assert len(unread) == 1 and "against the transcribed" in unread[0]
    _card, unread = _fetch(_page(), archived, reader=april, session=_Session())
    assert unread == []


def test_a_picture_blip_does_not_cost_a_transcribed_card_its_row() -> None:
    """In the archiver the transcribed card is read too, only to compare; a
    picture that cannot be fetched today leaves the transcription served."""

    async def reader(_url: str, _payload: bytes) -> str:
        raise AssertionError("handed a picture that never arrived")

    class _Down:
        def get(self, url: str, **_kw: Any) -> Any:
            raise aiohttp.ClientConnectionError(f"cannot reach {url}")

    archived = AsyncMock(side_effect=AssertionError("asked the archive"))
    card, unread = _fetch(_page(), archived, reader=reader, session=_Down())
    assert card.publication_label == "AIEC tarifs au 01/04/2026"
    assert unread == []


def test_an_answer_that_is_not_a_picture_is_not_read() -> None:
    async def reader(_url: str, _payload: bytes) -> str:
        raise AssertionError("handed a page")

    session = _Session(b"<html>moved</html>")
    archived = AsyncMock(side_effect=AssertionError("asked the archive"))
    card, unread = _fetch(_page("Tarif-2026-01-1-"), archived, reader=reader, session=session)
    assert card.publication_label.startswith("AIEC tarifs (via Callmepower)")
    assert len(unread) == 1 and "not a JPEG or a PNG" in unread[0]


def test_an_ocr_engine_that_fails_is_a_refusal_not_a_crash() -> None:
    async def reader(_url: str, _payload: bytes) -> str:
        raise OSError("tesseract: no such file")

    archived = AsyncMock(side_effect=AssertionError("asked the archive"))
    card, unread = _fetch(_page("Tarif-2026-01-1-"), archived, reader=reader, session=_Session())
    assert card.publication_label.startswith("AIEC tarifs (via Callmepower)")
    assert len(unread) == 1 and "could not read the picture" in unread[0]


def _text_path(text: str) -> str:
    return f"texts/{hashlib.sha256(text.encode('utf-8')).hexdigest()}.txt"


def _archive_holding(
    monkeypatch: pytest.MonkeyPatch, rows: dict[str, Any], texts: dict[str, str]
) -> list[str]:
    """An archive holding ``rows`` by month (YYYY-MM) and ``texts`` by path;
    returns what was asked of it."""
    asked: list[str] = []

    async def row(_session: Any, utility: str, commune: str, month: date) -> Any:
        asked.append(f"{utility}/{commune}/{month:%Y-%m}")
        return rows.get(f"{month:%Y-%m}")

    async def text(_session: Any, url: str) -> str:
        asked.append(url)
        return texts[url.rsplit("/", 2)[-2] + "/" + url.rsplit("/", 1)[-1]]

    monkeypatch.setattr(_archive, "archived_row", row)
    monkeypatch.setattr(_archive, "fetch_text", text)
    return asked


def _row(url: str, text_path: str) -> dict[str, Any]:
    return {
        "_sources": [
            {"url": aiec.OPERATOR_URL, "variant": "text", "text": "texts/page.txt"},
            {"url": url, "variant": "ocr", "picture": "d" * 64, "text": text_path},
        ]
    }


def _ask(url: str = APRIL) -> str | None:
    session: Any = None
    return asyncio.run(_archive.archived_picture_text(session, "aiec", url, date(2026, 10, 3)))


def test_the_archive_gives_the_newest_rows_reading_of_that_picture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _ocr("2026-04-01")
    path = _text_path(text)
    asked = _archive_holding(monkeypatch, {"2026-09": _row(APRIL, path)}, {path: text})
    assert _ask() == text
    assert asked[:2] == ["aiec/default/2026-10", "aiec/default/2026-09"]


def test_a_row_that_read_another_picture_gives_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The newest row read April's picture; the page now shows another. An
    older row cannot hold the newer one, so the walk stops there."""
    text = _ocr("2026-04-01")
    path = _text_path(text)
    asked = _archive_holding(
        monkeypatch, {"2026-10": _row(APRIL, path), "2026-09": _row(JANUARY, path)}, {path: text}
    )
    assert _ask(JANUARY) is None
    assert asked == ["aiec/default/2026-10"]


def test_a_text_that_is_not_what_its_name_says_gives_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _ocr("2026-04-01")
    path = _text_path(text)
    _archive_holding(monkeypatch, {"2026-10": _row(APRIL, path)}, {path: text + " edited"})
    assert _ask() is None


def test_a_text_path_outside_the_texts_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _archive_holding(
        monkeypatch, {"2026-10": _row(APRIL, "https://elsewhere.test/a.txt")}, {}
    )
    assert _ask() is None
    assert asked == ["aiec/default/2026-10"]


def test_the_archive_is_not_asked_when_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _archive_holding(monkeypatch, {}, {})
    with _archive.archive_reads(False):
        assert _ask() is None
    assert asked == []


def test_an_archive_that_does_not_answer_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    async def row(*_args: Any) -> Any:
        raise TransientFetchError("HTTP 429 fetching the archive")

    monkeypatch.setattr(_archive, "archived_row", row)
    with pytest.raises(TransientFetchError):
        _ask()


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
@pytest.mark.parametrize(
    ("picture", "cvd", "cva", "fse"),
    [
        ("aiec_tarif_2026-04-01.jpg", "3.05", "2.748", "0.0339"),
        ("aiec_tarif_2026-01-01.jpg", "2.46", "2.748", "0.0339"),
    ],
)
def test_the_installed_tesseract_still_reads_aiecs_pictures(
    picture: str, cvd: str, cva: str, fse: str
) -> None:
    """The real engine, as the archive workflow installs it: an upgrade that
    reads worse shows here before a walk relies on it."""
    from decimal import Decimal

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import card_texts  # type: ignore[import-not-found]

    text = card_texts.ocr_picture((FIXTURES / picture).read_bytes())
    got = read_figures(text, label="AIEC")
    assert (got.cvd, got.cva, got.fse) == (Decimal(cvd), Decimal(cva), Decimal(fse))
