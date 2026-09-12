"""scripts/archive_cards.py: the daily writer behind the repository's card archive."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from custom_components.be_water_prices.providers import _pdf
from custom_components.be_water_prices.providers._pdf import fetch_pdf_text_layout, fetch_text
from custom_components.be_water_prices.providers.base import (
    CommuneOption,
    ExtractorError,
    TransientFetchError,
    WaterExtractor,
    WaterTariff,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# scripts/ is not a package, so it is added to sys.path above rather than
# imported by dotted path; mypy cannot follow that.
import archive_cards as ac  # type: ignore[import-not-found]

NOW = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
PAGE_URL = "https://acme.test/tarieven"
PDF_URL = "https://acme.test/tarieven.pdf"

Fetch = Callable[[Any], Awaitable[WaterTariff]]


class _Chunks:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        yield self._payload


class _Response:
    status = 200
    content_length = None
    charset = None
    history: tuple[object, ...] = ()

    def __init__(self, payload: bytes, content_type: str) -> None:
        self.content = _Chunks(payload)
        self.content_type = content_type

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Session:
    """Just enough of aiohttp for the readers: one canned body per URL."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.hits = 0

    def get(self, url: str, **_kw: Any) -> _Response:
        self.hits += 1
        payload = self.pages[url]
        kind = "application/pdf" if payload.startswith(b"%PDF") else "text/html"
        return _Response(payload, kind)


def _tariff(fee: float = 100.0, label: str = "Tarieven 2026") -> WaterTariff:
    return WaterTariff(
        utility="acme",
        region="flanders",
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 12, 31),
        publication_label=label,
        source_url=PAGE_URL,
        yearly_fixed_fee=fee,
        basis_eur_per_m3=2.0,
        comfort_eur_per_m3=4.0,
    )


def _extractor(
    fetch: Fetch,
    *,
    uid: str = "acme",
    communes: tuple[CommuneOption, ...] | None = None,
    fetch_for_commune: Callable[[Any, str], Awaitable[WaterTariff]] | None = None,
) -> WaterExtractor:
    async def list_communes(_session: Any) -> tuple[CommuneOption, ...]:
        assert communes is not None
        return communes

    return WaterExtractor(
        id=uid,
        label="Acme",
        region="flanders",
        fetch=fetch,
        fetch_for_commune=fetch_for_commune,
        list_communes=list_communes if communes is not None else None,
    )


def _page_fetch(session: _Session | None = None, parser: dict[str, str] | None = None) -> Fetch:
    """A fetch that reads its page through the memoised reader, as every
    real extractor does, and parses ``fee=<eur>`` out of it; a parser knob
    the test turns doubles the fee."""
    page_session = session or _Session({PAGE_URL: b"fee=100.0"})
    knobs = parser or {"version": "plain"}

    async def fetch(_session: Any) -> WaterTariff:
        text = await fetch_text(page_session, PAGE_URL)  # type: ignore[arg-type]
        fee = float(text.split("=", 1)[1]) * (2 if knobs["version"] == "doubling" else 1)
        return _tariff(fee=fee)

    return fetch


async def _no_sleep(_seconds: float) -> None:
    return None


def _row(out: Path, commune: str = "default", month: str = "2026-09") -> dict[str, Any]:
    return json.loads((out / "acme" / commune / f"{month}.json").read_text())


async def test_a_stored_text_keeps_its_line_endings(tmp_path: Path) -> None:
    """A page with carriage returns read back with newline translation
    would be shorter than what the parser saw, and every replay would
    rewrite the row for nothing."""
    text = "line one\r\nline two\rline three"
    rel = ac._write_text(tmp_path, text)
    assert ac.read_text(tmp_path / rel) == text
    assert rel == f"texts/{hashlib.sha256(text.encode()).hexdigest()}.txt"


async def test_a_card_is_filed_under_the_month_it_was_seen_in(tmp_path: Path) -> None:
    summary = await ac.archive(
        tmp_path, extractors=[_extractor(_page_fetch())], now=NOW, sleep=_no_sleep
    )
    assert (summary.stored, summary.unchanged, summary.failed) == (1, 0, [])
    card = _row(tmp_path)
    assert card["publication_label"] == "Tarieven 2026"
    assert card["valid_from"] == "2026-01-01"
    assert card["yearly_fixed_fee"] == 100.0
    assert card["_seen_on"] == "2026-09-12"
    assert "_commune" not in card
    [source] = card["_sources"]
    assert source == {"url": PAGE_URL, "variant": "text", "text": source["text"]}
    assert source["text"].startswith("texts/")
    assert (tmp_path / source["text"]).read_text() == "fee=100.0"
    assert (tmp_path / "README.md").exists()
    assert (tmp_path / "parser.txt").exists()


async def test_a_shared_page_is_read_once_and_credited_to_every_row(tmp_path: Path) -> None:
    """The memo spans the run, so the second utility's parse is a memo hit;
    the hit still lands in that row's sources."""
    session = _Session({PAGE_URL: b"fee=100.0"})
    extractors = [
        _extractor(_page_fetch(session)),
        _extractor(_page_fetch(session), uid="beta"),
    ]
    summary = await ac.archive(tmp_path, extractors=extractors, now=NOW, sleep=_no_sleep)
    assert summary.stored == 2
    assert session.hits == 1
    texts = {
        json.loads((tmp_path / f"{u}/default/2026-09.json").read_text())["_sources"][0]["text"]
        for u in ("acme", "beta")
    }
    assert len(texts) == 1


async def test_a_repeat_run_keeps_the_first_capture(tmp_path: Path) -> None:
    """The same parse a day later changes nothing on disk, so a quiet day
    has nothing to commit; a changed parse is a new capture."""
    session = _Session({PAGE_URL: b"fee=100.0"})
    extractors = [_extractor(_page_fetch(session))]
    await ac.archive(tmp_path, extractors=extractors, now=NOW, sleep=_no_sleep)
    path = tmp_path / "acme/default/2026-09.json"
    first = path.read_text()
    later = NOW.replace(day=13)
    summary = await ac.archive(tmp_path, extractors=extractors, now=later, sleep=_no_sleep)
    assert (summary.stored, summary.unchanged) == (0, 1)
    assert path.read_text() == first
    session.pages[PAGE_URL] = b"fee=110.0"
    summary = await ac.archive(tmp_path, extractors=extractors, now=later, sleep=_no_sleep)
    assert (summary.stored, summary.unchanged) == (1, 0)
    card = _row(tmp_path)
    assert card["yearly_fixed_fee"] == 110.0
    assert card["_seen_on"] == "2026-09-13"


async def test_a_new_month_of_the_same_card_points_at_last_months_page(tmp_path: Path) -> None:
    """A water tariff is annual: the October row of an unchanged card names
    the text September stored rather than storing the page again, and a
    changed page in November gets its own."""
    session = _Session({PAGE_URL: b"fee=100.0"})
    extractors = [_extractor(_page_fetch(session))]
    await ac.archive(tmp_path, extractors=extractors, now=NOW, sleep=_no_sleep)
    summary = await ac.archive(
        tmp_path, extractors=extractors, now=datetime(2026, 10, 3, 6, tzinfo=UTC), sleep=_no_sleep
    )
    assert (summary.stored, summary.unchanged) == (1, 0)
    assert _row(tmp_path, month="2026-10")["_sources"] == _row(tmp_path)["_sources"]
    assert len(list((tmp_path / "texts").glob("*.txt"))) == 1
    session.pages[PAGE_URL] = b"fee=110.0"
    await ac.archive(
        tmp_path, extractors=extractors, now=datetime(2026, 11, 3, 6, tzinfo=UTC), sleep=_no_sleep
    )
    assert _row(tmp_path, month="2026-11")["_sources"] != _row(tmp_path)["_sources"]
    assert len(list((tmp_path / "texts").glob("*.txt"))) == 2


async def test_transient_failures_are_retried_and_permanent_ones_are_not(tmp_path: Path) -> None:
    good = _page_fetch()
    attempts = 0

    async def flaky(session: Any) -> WaterTariff:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TransientFetchError(f"network error fetching {PAGE_URL}: reset")
        return await good(session)

    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    summary = await ac.archive(tmp_path, extractors=[_extractor(flaky)], now=NOW, sleep=sleep)
    assert summary.stored == 1
    assert slept == [10, 30]

    async def gone(_session: Any) -> WaterTariff:
        raise ExtractorError(f"HTTP 404 fetching {PAGE_URL}")

    slept.clear()
    summary = await ac.archive(
        tmp_path, extractors=[_extractor(gone, uid="beta")], now=NOW, sleep=sleep
    )
    assert summary.stored == 0
    assert slept == []
    assert summary.failed == [f"beta/default: ExtractorError: HTTP 404 fetching {PAGE_URL}"]


async def test_every_commune_of_a_per_commune_utility_gets_its_own_row(tmp_path: Path) -> None:
    """The default fetch and each listed commune are rows of their own,
    named by the commune id and carrying its label; a listing that cannot
    be read costs the commune rows only."""
    session = _Session({PAGE_URL: b"fee=100.0"})
    asked: list[str] = []

    async def fetch_for_commune(_session: Any, commune: str) -> WaterTariff:
        asked.append(commune)
        await fetch_text(session, PAGE_URL)  # type: ignore[arg-type]
        return _tariff(fee=100.0 + float(commune), label=f"Tarieven 2026 ({commune})")

    communes = (
        CommuneOption(id="1", label="9000 - Gent"),
        CommuneOption(id="2", label="1930 - Zaventem"),
    )
    extractor = _extractor(
        _page_fetch(session), communes=communes, fetch_for_commune=fetch_for_commune
    )
    summary = await ac.archive(tmp_path, extractors=[extractor], now=NOW, sleep=_no_sleep)
    assert (summary.stored, summary.failed) == (3, [])
    assert asked == ["1", "2"]
    assert _row(tmp_path, "2") == {
        **_row(tmp_path, "2"),
        "_commune": "2",
        "_commune_label": "1930 - Zaventem",
        "yearly_fixed_fee": 102.0,
        "publication_label": "Tarieven 2026 (2)",
    }
    coverage = (tmp_path / "coverage.md").read_text()
    assert "| commune | label | 2026-09 |" in coverage
    assert "| default |  | page json |" in coverage
    assert "| 1 | 9000 - Gent | page json |" in coverage

    async def no_listing(_session: Any) -> tuple[CommuneOption, ...]:
        raise ExtractorError("the commune list has moved")

    broken = WaterExtractor(
        id="beta",
        label="Beta",
        region="flanders",
        fetch=_page_fetch(session),
        fetch_for_commune=fetch_for_commune,
        list_communes=no_listing,
    )
    summary = await ac.archive(tmp_path, extractors=[broken], now=NOW, sleep=_no_sleep)
    assert summary.stored == 1
    assert summary.failed == ["beta/communes: ExtractorError: the commune list has moved"]
    # Six days a week only the default rows are asked for: the communes
    # are neither listed nor fetched, and the rows they have stay.
    asked.clear()
    summary = await ac.archive(
        tmp_path, extractors=[extractor], defaults_only=True, now=NOW, sleep=_no_sleep
    )
    assert (summary.stored, summary.unchanged, asked) == (0, 1, [])
    assert (tmp_path / "acme/2/2026-09.json").exists()


async def test_a_commune_id_that_cannot_name_a_directory_is_refused(tmp_path: Path) -> None:
    async def fetch_for_commune(_session: Any, _commune: str) -> WaterTariff:
        return _tariff()

    extractor = _extractor(
        _page_fetch(),
        communes=(CommuneOption(id="a/b", label="x"), CommuneOption(id="..", label="y")),
        fetch_for_commune=fetch_for_commune,
    )
    summary = await ac.archive(tmp_path, extractors=[extractor], now=NOW, sleep=_no_sleep)
    assert summary.stored == 1
    assert [f.split(":")[0] for f in summary.failed] == ["acme/a/b", "acme/.."]


def _pdf_fetch(session: _Session) -> Fetch:
    """A fetch that reads its card through the PDF reader, the way the
    real extractors do."""

    async def fetch(_session: Any) -> WaterTariff:
        # The canned session while it holds the card; once the test empties
        # it, the session handed in, which in a replay is the kept copy.
        reader = session if PDF_URL in session.pages else _session
        text = await fetch_pdf_text_layout(reader, PDF_URL)  # type: ignore[arg-type]
        return _tariff(fee=100.0 if "v1" in text else 200.0)

    return fetch


@pytest.fixture
def renders(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """A PDF renderer that counts what it rendered instead of reading a
    real card."""
    rendered: list[bytes] = []

    def render(payload: bytes) -> str:
        rendered.append(payload)
        return f"card text for {payload.decode()}"

    monkeypatch.setattr(_pdf, "extract_pdf_text_layout", render)
    return rendered


async def test_an_unchanged_card_is_kept_once_and_never_rendered_again(
    tmp_path: Path, renders: list[bytes]
) -> None:
    """The bytes' digest decides: the first run renders the card and writes
    it under the PDF directory for upload; a later run with the same bytes
    serves the stored text and renders nothing. The PDF is offered again
    until the manifest says it was uploaded, and a changed card is a new
    digest, rendered and kept afresh."""
    out, pdfs = tmp_path / "out", tmp_path / "pdfs"
    session = _Session({PDF_URL: b"%PDF v1"})
    extractor = _extractor(_pdf_fetch(session))
    summary = await ac.archive(out, extractors=[extractor], pdf_dir=pdfs, now=NOW, sleep=_no_sleep)
    assert (summary.rendered, summary.unrendered, summary.pdfs_saved) == (1, 0, 1)
    digest = hashlib.sha256(b"%PDF v1").hexdigest()
    assert (pdfs / f"water-2026-09/{digest}.pdf").read_bytes() == b"%PDF v1"
    card = _row(out)
    [source] = card["_sources"]
    assert source["pdf"] == digest
    assert source["variant"] == "layout"
    assert card["yearly_fixed_fee"] == 100.0

    # Same bytes the next day: nothing rendered, the text came from the
    # branch, the PDF is written again because nothing says it was uploaded.
    shutil.rmtree(pdfs)
    summary = await ac.archive(
        out, extractors=[extractor], pdf_dir=pdfs, now=NOW.replace(day=13), sleep=_no_sleep
    )
    assert (summary.rendered, summary.unrendered, summary.pdfs_saved) == (0, 1, 1)
    assert (summary.stored, summary.unchanged) == (0, 1)
    assert renders == [b"%PDF v1"]

    # Once the manifest records the upload it is neither written nor rendered.
    (out / "pdfs.json").write_text(json.dumps({digest: f"water-2026-09/{digest}.pdf"}))
    shutil.rmtree(pdfs)
    summary = await ac.archive(
        out, extractors=[extractor], pdf_dir=pdfs, now=NOW.replace(day=14), sleep=_no_sleep
    )
    assert (summary.rendered, summary.unrendered, summary.pdfs_saved) == (0, 1, 0)
    assert not pdfs.exists()

    # A corrected card is new bytes: rendered, kept, and the row rewritten.
    session.pages[PDF_URL] = b"%PDF v2"
    summary = await ac.archive(
        out, extractors=[extractor], pdf_dir=pdfs, now=NOW.replace(day=15), sleep=_no_sleep
    )
    assert (summary.rendered, summary.pdfs_saved, summary.stored) == (1, 1, 1)
    digest2 = hashlib.sha256(b"%PDF v2").hexdigest()
    assert (pdfs / f"water-2026-09/{digest2}.pdf").exists()
    card = _row(out)
    assert card["_sources"][0]["pdf"] == digest2
    assert card["yearly_fixed_fee"] == 200.0


def _dated_fetch(session: _Session, parser: dict[str, str], seen: list[date]) -> Fetch:
    """The page fetch, noting today's date as an extractor keyed on the
    calendar would."""
    inner = _page_fetch(session, parser)

    async def fetch(_session: Any) -> WaterTariff:
        seen.append(date.today())
        return await inner(_session)

    return fetch


async def test_stored_rows_are_replayed_only_when_the_parser_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parser change replays every stored row from its stored texts, with
    the clock pinned to the day the row was captured and no utility asked;
    a run under the same parser replays nothing."""
    session = _Session({PAGE_URL: b"fee=100.0"})
    parser = {"version": "plain"}
    seen: list[date] = []
    extractor = _extractor(_dated_fetch(session, parser, seen))
    monkeypatch.setattr(ac, "_parser_digest", lambda: "digest-a")
    august = datetime(2026, 8, 5, 6, 0, tzinfo=UTC)
    await ac.archive(tmp_path, extractors=[extractor], now=august, sleep=_no_sleep)
    assert (tmp_path / "parser.txt").read_text().strip() == "digest-a"
    row = tmp_path / "acme/default/2026-08.json"
    assert json.loads(row.read_text())["yearly_fixed_fee"] == 100.0

    # September, same parser: the live walk stores this month's row on the
    # real clock, and nothing is replayed.
    seen.clear()
    later = NOW.replace(day=18)
    summary = await ac.archive(tmp_path, extractors=[extractor], now=later, sleep=_no_sleep)
    assert (summary.replayed, summary.reparsed) == (0, 0)
    assert seen == [date.today()]
    assert (tmp_path / "acme/default/2026-09.json").exists()

    # The parser changed: today's live walk rewrites September, then August
    # is replayed from its stored text under August's clock and rewritten,
    # and September is replayed and found already right.
    parser["version"] = "doubling"
    monkeypatch.setattr(ac, "_parser_digest", lambda: "digest-b")
    seen.clear()
    hits_before = session.hits
    summary = await ac.archive(tmp_path, extractors=[extractor], now=later, sleep=_no_sleep)
    assert (summary.replayed, summary.reparsed, summary.unreplayable) == (2, 1, [])
    assert session.hits == hits_before + 1  # the live walk only
    card = json.loads(row.read_text())
    assert card["yearly_fixed_fee"] == 200.0
    assert card["_seen_on"] == "2026-08-05"
    assert (tmp_path / "parser.txt").read_text().strip() == "digest-b"
    assert seen == [date.today(), date(2026, 8, 5), date(2026, 9, 18)]

    # And the forced flag replays even when the digest matches.
    summary = await ac.archive(
        tmp_path, extractors=[extractor], now=later, reparse=True, sleep=_no_sleep
    )
    assert (summary.replayed, summary.reparsed) == (2, 0)


async def test_a_row_that_cannot_be_reproduced_offline_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parser that now wants a page the row never read is refused, and a
    row whose text file is gone is skipped; both are reported and neither
    is rewritten."""
    session = _Session({PAGE_URL: b"fee=100.0"})
    extractor = _extractor(_page_fetch(session))
    monkeypatch.setattr(ac, "_parser_digest", lambda: "digest-a")
    august = datetime(2026, 8, 5, 6, 0, tzinfo=UTC)
    await ac.archive(tmp_path, extractors=[extractor], now=august, sleep=_no_sleep)
    row = tmp_path / "acme/default/2026-08.json"
    before = row.read_text()

    async def wants_more(_session: Any) -> WaterTariff:
        # The page the row read comes from the memo; the extra one goes to
        # the session handed in, which in a replay refuses it.
        await fetch_text(session, PAGE_URL)  # type: ignore[arg-type]
        await fetch_text(_session, "https://acme.test/extra")
        return _tariff()

    monkeypatch.setattr(ac, "_parser_digest", lambda: "digest-b")
    summary = await ac.archive(
        tmp_path, extractors=[_extractor(wants_more)], now=NOW, sleep=_no_sleep
    )
    # Today's live walk fails on the extra page too, so August is the
    # only row, and it is refused.
    assert summary.replayed == 0
    [reason] = summary.unreplayable
    assert reason.startswith("acme/default/2026-08: TransientFetchError: network error")
    assert row.read_text() == before

    # The text the row read is gone from the branch.
    card = json.loads(before)
    card["_sources"][0]["text"] = "texts/gone.txt"
    row.write_text(json.dumps(card))
    monkeypatch.setattr(ac, "_parser_digest", lambda: "digest-c")
    summary = await ac.archive(tmp_path, extractors=[extractor], now=NOW, sleep=_no_sleep)
    assert summary.replayed == 1
    assert summary.unreplayable == ["acme/default/2026-08: texts/gone.txt is missing"]
    assert json.loads(row.read_text()) == card


async def test_a_rerender_reads_every_card_back_from_the_kept_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, renders: list[bytes]
) -> None:
    """Under --rerender a stored month's PDF text is not seeded: the card is
    fetched back from the kept copy and rendered again, which is how a reader
    upgrade reaches the branch; an unchanged parse is not rewritten."""
    out, pdfs = tmp_path / "out", tmp_path / "pdfs"
    session = _Session({PDF_URL: b"%PDF v1"})
    monkeypatch.setattr(ac, "_parser_digest", lambda: "digest-a")
    await ac.archive(
        out,
        extractors=[_extractor(_pdf_fetch(session))],
        pdf_dir=pdfs,
        now=NOW.replace(day=5),
        sleep=_no_sleep,
    )
    assert renders == [b"%PDF v1"]
    row = out / "acme/default/2026-09.json"
    before = json.loads(row.read_text())
    session.pages.clear()  # the utility is gone; only the kept copy is left
    summary = await ac.archive(
        out,
        extractors=[_extractor(_pdf_fetch(session))],
        pdf_dir=pdfs,
        now=NOW.replace(day=6),
        rerender=True,
        sleep=_no_sleep,
    )
    assert (summary.replayed, summary.reparsed, summary.unreplayable) == (1, 0, [])
    assert renders == [b"%PDF v1", b"%PDF v1"]
    after = json.loads(row.read_text())
    assert after["yearly_fixed_fee"] == before["yearly_fixed_fee"]
    assert after["_seen_on"] == "2026-09-05"


async def test_a_commune_row_replays_through_the_commune_it_was_captured_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _Session({PAGE_URL: b"fee=100.0"})
    calls: list[str | None] = []

    async def fetch(_session: Any) -> WaterTariff:
        calls.append(None)
        await fetch_text(session, PAGE_URL)  # type: ignore[arg-type]
        return _tariff()

    async def fetch_for_commune(_session: Any, commune: str) -> WaterTariff:
        calls.append(commune)
        await fetch_text(session, PAGE_URL)  # type: ignore[arg-type]
        return _tariff(label=f"Tarieven 2026 ({commune})")

    extractor = _extractor(
        fetch,
        communes=(CommuneOption(id="{GUID-1}", label="1500 - Halle"),),
        fetch_for_commune=fetch_for_commune,
    )
    monkeypatch.setattr(ac, "_parser_digest", lambda: "digest-a")
    await ac.archive(tmp_path, extractors=[extractor], now=NOW, sleep=_no_sleep)
    calls.clear()
    monkeypatch.setattr(ac, "_parser_digest", lambda: "digest-b")
    summary = await ac.archive(tmp_path, extractors=[extractor], now=NOW, sleep=_no_sleep)
    assert summary.replayed == 2
    # The walk, then the replay in row order: default sorts first.
    assert calls == [None, "{GUID-1}", None, "{GUID-1}"]
    assert _row(tmp_path, "{GUID-1}")["_commune_label"] == "1500 - Halle"


def test_a_text_that_changed_bytes_but_not_its_parse_is_not_a_new_card() -> None:
    source = {"url": "u", "variant": "text", "text": "texts/a.txt"}
    card: dict[str, Any] = {
        "_seen_on": "2026-09-12",
        "yearly_fixed_fee": 100.0,
        "_sources": [source],
    }
    same_parse = {**card, "_sources": [{**source, "text": "texts/b.txt"}]}
    assert ac._same_card(card, same_parse)
    other_variant = {**card, "_sources": [{**source, "variant": "layout"}]}
    assert not ac._same_card(card, other_variant)
    other_parse = {**card, "yearly_fixed_fee": 110.0}
    assert not ac._same_card(card, other_parse)
    assert not ac._same_card(None, card)


def test_replay_session_refuses_what_it_does_not_hold(tmp_path: Path) -> None:
    replay = ac._ReplaySession(None, tmp_path, None)  # type: ignore[arg-type]

    async def run(method: str = "get") -> bytes:
        async with getattr(replay, method)("https://acme.test/card.pdf") as resp:
            chunks = [chunk async for chunk in resp.content.iter_chunked(1)]
            return b"".join(chunks)

    with pytest.raises(aiohttp.ClientConnectionError):
        asyncio.run(run())
    with pytest.raises(aiohttp.ClientConnectionError):
        asyncio.run(run("post"))
    # A kept copy is found by digest under any release directory.
    (tmp_path / "water-2026-09").mkdir()
    (tmp_path / "water-2026-09/abc.pdf").write_bytes(b"%PDF kept")
    replay.pdfs = {"https://acme.test/card.pdf": "abc"}
    assert asyncio.run(run()) == b"%PDF kept"
    # Without a local copy and without a manifest entry there is nowhere to go.
    replay = ac._ReplaySession(None, None, "https://cards.test/download", {})  # type: ignore[arg-type]
    replay.pdfs = {"https://acme.test/card.pdf": "abc"}
    with pytest.raises(aiohttp.ClientConnectionError):
        asyncio.run(run())
    # De Watergroep clears its cookie after every request; there is none.
    replay.cookie_jar.clear(lambda _c: True)


def test_prune_removes_old_rows_and_the_texts_nothing_refers_to(tmp_path: Path) -> None:
    def row(commune: str, month: str, text: str) -> None:
        path = tmp_path / "acme" / commune / f"{month}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"_sources": [{"text": f"texts/{text}.txt"}]}))

    row("default", "2023-08", "old")
    row("default", "2023-09", "kept")
    row("default", "2026-09", "kept")
    row("gone", "2023-01", "old")
    (tmp_path / "texts").mkdir()
    for name in ("old", "kept", "orphan"):
        (tmp_path / "texts" / f"{name}.txt").write_text("x")
    (tmp_path / "pdfs.json").write_text(
        json.dumps({"old": "water-2023-08/old.pdf", "kept": "water-2023-09/kept.pdf"})
    )
    assert ac._prune(tmp_path, 36, date(2026, 9, 12)) == 5
    assert not (tmp_path / "acme/gone").exists()
    assert not (tmp_path / "acme/default/2023-08.json").exists()
    assert (tmp_path / "acme/default/2023-09.json").exists()
    assert sorted(p.name for p in (tmp_path / "texts").iterdir()) == ["kept.txt"]
    assert json.loads((tmp_path / "pdfs.json").read_text()) == {"kept": "water-2023-09/kept.pdf"}


async def test_a_utility_not_answering_is_given_up_on_for_the_day(tmp_path: Path) -> None:
    """Three network failures in a row and the rest of that utility's
    communes are skipped; a parse failure does not count, and another
    utility is unaffected."""
    asked: list[str] = []

    async def blocked(_session: Any) -> WaterTariff:
        asked.append("default")
        raise TransientFetchError("network error fetching x: timeout")

    async def blocked_commune(_session: Any, commune: str) -> WaterTariff:
        asked.append(commune)
        raise TransientFetchError("network error fetching x: timeout")

    communes = tuple(CommuneOption(id=c, label=c) for c in ("a", "b", "c", "d"))
    dwg = _extractor(blocked, uid="dwg", communes=communes, fetch_for_commune=blocked_commune)
    fine = _extractor(_page_fetch())
    summary = await ac.archive(tmp_path, extractors=[dwg, fine], now=NOW, sleep=_no_sleep)
    # Each card is retried, so count the cards asked, not the attempts.
    assert list(dict.fromkeys(asked)) == ["default", "a", "b"]
    assert summary.given_up == ["dwg"]
    assert len(summary.failed) == 3
    assert summary.stored == 1

    # A parse failure is not the network: it resets the count.
    asked.clear()
    calls = 0

    async def flaky(_session: Any, commune: str) -> WaterTariff:
        nonlocal calls
        calls += 1
        asked.append(commune)
        if calls % 3 == 0:
            raise ExtractorError("could not parse the card")
        raise TransientFetchError("network error fetching x: timeout")

    mixed = _extractor(_page_fetch(), uid="mixed", communes=communes, fetch_for_commune=flaky)
    summary = await ac.archive(tmp_path, extractors=[mixed], now=NOW, sleep=_no_sleep)
    assert list(dict.fromkeys(asked)) == ["a", "b", "c", "d"]
    assert summary.given_up == []


async def test_the_coverage_table_links_a_month_to_what_it_was_parsed_from(
    tmp_path: Path, renders: list[bytes]
) -> None:
    """A page row links to its stored text on the branch, a PDF row to the
    kept card once the manifest says where it is; without the base URLs
    the cells still say which it was. Rewritten to the same bytes when
    nothing changed."""
    out, pdfs = tmp_path / "out", tmp_path / "pdfs"
    session = _Session({PAGE_URL: b"fee=100.0", PDF_URL: b"%PDF v1"})
    extractors = [_extractor(_page_fetch(session)), _extractor(_pdf_fetch(session), uid="beta")]
    cards = "https://cards.test/releases/download"
    branch = "https://github.test/repo/blob/archive"
    await ac.archive(
        out,
        extractors=extractors,
        pdf_dir=pdfs,
        pdf_base_url=cards,
        archive_base_url=branch,
        now=NOW,
        sleep=_no_sleep,
    )
    coverage = (out / "coverage.md").read_text()
    text = _row(out)["_sources"][0]["text"]
    assert "## acme" in coverage
    acme_json = f"[json]({branch}/acme/default/2026-09.json)"
    beta_json = f"[json]({branch}/beta/default/2026-09.json)"
    assert f"| default |  | [page]({branch}/{text}) {acme_json} |" in coverage
    # The PDF is not uploaded yet: the cell says so without a link.
    assert f"| default |  | pdf {beta_json} |" in coverage
    digest = hashlib.sha256(b"%PDF v1").hexdigest()
    (out / "pdfs.json").write_text(json.dumps({digest: f"water-2026-09/{digest}.pdf"}))
    ac._write_coverage(out, cards, branch)
    assert (
        f"| default |  | [pdf]({cards}/water-2026-09/{digest}.pdf) {beta_json} |"
        in (out / "coverage.md").read_text()
    )
    # Nothing to link to without the base URLs.
    ac._write_coverage(out)
    coverage = (out / "coverage.md").read_text()
    assert "| default |  | page json |" in coverage
    assert "| default |  | pdf json |" in coverage
    again = await ac.archive(
        out,
        extractors=extractors,
        pdf_dir=pdfs,
        pdf_base_url=cards,
        archive_base_url=branch,
        now=NOW,
        sleep=_no_sleep,
    )
    assert again.unchanged == 2
    first = (out / "coverage.md").read_text()
    await ac.archive(
        out,
        extractors=extractors,
        pdf_dir=pdfs,
        pdf_base_url=cards,
        archive_base_url=branch,
        now=NOW,
        sleep=_no_sleep,
    )
    assert (out / "coverage.md").read_text() == first


async def test_the_listing_is_refreshed_after_the_upload_and_the_old_index_dropped(
    tmp_path: Path, renders: list[bytes]
) -> None:
    """--index-only rewrites the coverage table without fetching anything,
    so a card uploaded after the walk gets its link, refreshes a stale
    branch README and drops the PDF index earlier versions wrote."""
    out, pdfs = tmp_path / "out", tmp_path / "pdfs"
    session = _Session({PDF_URL: b"%PDF v1"})

    async def fetch_for_commune(_session: Any, commune: str) -> WaterTariff:
        text = await fetch_pdf_text_layout(session, PDF_URL)  # type: ignore[arg-type]
        return _tariff(label=f"{text} ({commune})")

    extractor = _extractor(
        _pdf_fetch(session),
        communes=(CommuneOption(id="1", label="Gent"),),
        fetch_for_commune=fetch_for_commune,
    )
    base = "https://cards.test/releases/download"
    branch = "https://github.test/repo/blob/archive"
    await ac.archive(
        out, extractors=[extractor], pdf_dir=pdfs, pdf_base_url=base, now=NOW, sleep=_no_sleep
    )
    digest = hashlib.sha256(b"%PDF v1").hexdigest()
    (out / "pdfs.json").write_text(json.dumps({digest: f"water-2026-09/{digest}.pdf"}))
    (out / "pdfs.md").write_text("stale index")
    (out / "README.md").write_text("stale readme")
    ac._write_listings(out, base, branch)
    url = f"{base}/water-2026-09/{digest}.pdf"
    coverage = (out / "coverage.md").read_text()
    assert f"| default |  | [pdf]({url}) [json]({branch}/acme/default/2026-09.json) |" in coverage
    assert f"| 1 | Gent | [pdf]({url}) [json]({branch}/acme/1/2026-09.json) |" in coverage
    assert not (out / "pdfs.md").exists()
    assert (out / "README.md").read_text() == ac._README


def test_index_only_touches_nothing_but_the_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["archive_cards.py", "--out", str(tmp_path), "--index-only"])
    monkeypatch.setattr(
        ac, "all_extractors", lambda: (_ for _ in ()).throw(AssertionError("fetched"))
    )
    (tmp_path / "pdfs.md").write_text("stale index")
    assert ac.main() == 0
    assert (tmp_path / "coverage.md").exists()
    assert (tmp_path / "README.md").exists()
    assert not (tmp_path / "pdfs.md").exists()


def test_targets_skip_a_runner_blocked_utility_on_a_runner_only() -> None:
    acme = _extractor(_page_fetch())
    blocked = _extractor(_page_fetch(), uid="water_link")
    assert [e.id for e in ac._targets([acme, blocked], set(), on_ci=True)] == ["acme"]
    assert [e.id for e in ac._targets([acme, blocked], set(), on_ci=False)] == [
        "acme",
        "water_link",
    ]
    assert ac._targets([acme, blocked], {"beta"}, on_ci=False) == []
    assert [e.id for e in ac._targets([acme, blocked], {"water_link"}, on_ci=False)] == [
        "water_link"
    ]


def test_months_before() -> None:
    assert ac._months_before(date(2026, 9, 12), 36) == "2023-09"
    assert ac._months_before(date(2026, 1, 1), 1) == "2025-12"
    assert ac._months_before(date(2026, 1, 1), 0) == "2026-01"


def test_main_fails_the_run_only_when_nothing_was_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def gone(_session: Any) -> WaterTariff:
        raise ExtractorError("HTTP 404 fetching x")

    monkeypatch.setattr(sys, "argv", ["archive_cards.py", "--out", str(tmp_path)])
    monkeypatch.setattr(ac, "all_extractors", lambda: (_extractor(gone),))
    assert ac.main() == 1
    monkeypatch.setattr(ac, "all_extractors", lambda: (_extractor(_page_fetch()),))
    assert ac.main() == 0
