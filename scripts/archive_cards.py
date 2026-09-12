#!/usr/bin/env python3
"""Store today's tariff cards in the repository's card archive.

Walks every registered utility, fetches the tariff the integration would
price on right now, once for the utility's default and once for each
commune a per-commune utility lists, and writes what it parsed to
``<out>/<utility>/<commune>/<YYYY-MM>.json`` (``default`` for the
no-commune fetch). A water tariff is annual, so a card is filed under the
month it was seen in. Every text the parse read is kept under
``<out>/texts/<sha256>.txt``, stored once however many rows read it, and a
month whose card is the same as the previous month's points at the texts
that row already holds instead of storing the page again, so a year of an
unchanged tariff costs one copy of its page rather than twelve.

Run daily by .github/workflows/archive_cards.yml against the ``archive``
branch. A month already on disk is rewritten only when the parse changed,
so a quiet day leaves nothing to commit, and months older than
``--keep-months`` are removed on every run, with the texts nothing refers
to any more.

The cards themselves are kept too. With ``--pdfs DIR`` every PDF whose
bytes the branch has not recorded yet is written to
``DIR/water-<YYYY-MM>/<sha256>.pdf`` and the workflow uploads each
directory as the assets of the release of that name in the cards
repository shared with be_electricity_prices. Where each one landed is
recorded in ``<out>/pdfs.json``; a row's ``_sources`` entry names its PDF
by digest alone. The same digest keeps a daily run cheap: a card whose
bytes have not changed is served the text the branch already holds for it
instead of being rendered again.

A parser fix reaches the stored months on its own. Every row carries the
texts its parse read, so the run replays each row through the current
extractor with those texts served from the branch, the clock pinned to
the day the row was captured and no utility contacted, and rewrites the
row when the parse came out differently. That happens only when the
parser sources changed since the branch was last replayed (a digest of
them is stamped in ``parser.txt``); ``--reparse`` forces it. A parser that
now reads a PDF the row has no text for gets the kept PDF back from the
cards releases; ``--rerender`` asks for that on every card, which is the
way to pick up a PDF reader upgrade.

Exits 0 when at least one card was stored or confirmed unchanged and 1
when none was: that is a runner-wide problem rather than a utility's, so
the run goes red without filing anything. The live check already files
per-utility issues and this is not a second checker.

A water tariff is annual, and the commune rows are most of the walk: De
Watergroep alone lists about seven hundred communes. ``--defaults-only``
skips the commune listings and stores each utility's default row only,
which is what the daily run asks for on six days of the week; the
communes are walked on Sundays and on request.

Usage:
    python scripts/archive_cards.py --out tmp/archive --pdfs tmp/pdfs [--only pidpa ...]
        [--defaults-only] [--reparse] [--rerender]
        [--pdf-base-url https://github.com/<owner>/<cards repo>/releases/download]
        [--archive-base-url https://github.com/<owner>/<this repo>/blob/archive]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from freezegun import freeze_time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# scripts/ is not a package; the line above puts it on sys.path so the render
# cache is the one the live check reads too, and the runner skip list is
# the live check's own.
from card_texts import StoredTexts, digest_of, read_text  # noqa: E402
from live_check import CI_BLOCKED  # noqa: E402

from custom_components.be_water_prices.providers import all_extractors  # noqa: E402
from custom_components.be_water_prices.providers._pdf import (  # noqa: E402
    memoise_text_fetches,
    render_through,
)
from custom_components.be_water_prices.providers.base import (  # noqa: E402
    TransientFetchError,
    WaterExtractor,
    WaterTariff,
    tariff_to_dict,
)

_BRUSSELS = ZoneInfo("Europe/Brussels")
_ATTEMPTS = 3
_RETRY_BACKOFF_S = (10, 30)
# A utility whose pages fail on the network this many times in a row is
# not answering this runner today, and every further commune would cost
# the same three timeouts and two sleeps, a couple of minutes each: a
# hundred communes of it would run the job into its timeout with nothing
# committed. Skip the rest of the utility and say so; tomorrow is another
# run.
_GIVE_UP_AFTER = 3
# One card, fetch and parse together. The readers cap each download on
# their own; this bounds a parse that never returns so the rest of the
# registry is still archived and the summary still prints.
_CARD_TIMEOUT_S = 300
# The row of a utility's no-commune fetch.
_DEFAULT = "default"
# Keys the daily run rewrites; two files that differ only here hold the
# same card and the older one is kept.
_VOLATILE_KEYS = ("_seen_on",)
# The digest of the parser sources the branch was last replayed with.
_PARSER_STAMP = "parser.txt"
_MANIFEST = "pdfs.json"
# This integration's namespace in the cards repository, which it shares
# with be_electricity_prices: its releases are water-<YYYY-MM>, its
# listings live under water/ in that repository's tree.
_RELEASE_PREFIX = "water"
# One table per utility of which months the branch holds, for the reader
# who wants to know whether a given month of a given commune is covered
# without listing directories, each month linking to what it was parsed
# from (a release lists PDFs by digest only) and to the JSON it produced.
_COVERAGE = "coverage.md"
# What a parse depends on: the extractors, the shared readers beside them
# and the constants they key on.
_PARSER_SOURCES = ("providers/*.py", "const.py")

_README = """# Tariff card archive

Written daily by `.github/workflows/archive_cards.yml` running
`scripts/archive_cards.py` from the main branch. Not edited by hand.

- `<utility>/<commune>/<YYYY-MM>.json`: the tariff as the integration
  parsed it in that month, for the utility's no-commune fetch (`default`)
  or for one commune of a per-commune utility, named by the commune id the
  integration uses (`_commune_label` in the file says which commune that
  is).
- `texts/<sha256>.txt`: every page and rendered PDF a parse read, stored
  once and shared between the rows that read it. Each row lists its own
  under `_sources`, and names a PDF it read by SHA-256.
- `pdfs.json`: where each PDF is kept, as `<release tag>/<sha256>.pdf` in
  the releases of the cards repository (`be_price_cards`, shared with
  be_electricity_prices; this integration's releases are `water-<YYYY-MM>`).
- `coverage.md`: which months the branch holds for each utility and
  commune, each linking to the PDF or the page it was parsed from and to
  the JSON above.

To get the original card of a utility, commune and month: open
`coverage.md`, find the row, click `pdf` or `page`; `json` is what the
integration parsed out of it. Months older than three years are removed.
"""


class _RecordingMemo(dict[str, str]):
    """The text memo the readers consult, noting what one fetch touched.

    One memo spans the whole run so a page two rows share is downloaded
    and parsed once. Attribution is still per row, and a shared dict
    cannot say which entries a given parse read, so every read and write
    lands in ``touched`` and the caller clears it before each fetch. The
    readers test membership and then index, so a hit is a read here and a
    miss is a write.
    """

    def __init__(self) -> None:
        super().__init__()
        self.touched: set[str] = set()

    def __getitem__(self, key: str) -> str:
        self.touched.add(key)
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: str) -> None:
        self.touched.add(key)
        super().__setitem__(key, value)


@dataclass
class _Summary:
    stored: int = 0
    unchanged: int = 0
    rendered: int = 0
    unrendered: int = 0
    pdfs_saved: int = 0
    replayed: int = 0
    reparsed: int = 0
    unreplayable: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    given_up: list[str] = field(default_factory=list)


def _transient(err: BaseException) -> bool:
    return isinstance(err, TransientFetchError | TimeoutError)


class _Patience:
    """Per-utility count of network failures in a row, and the verdict."""

    def __init__(self) -> None:
        self.failures: dict[str, int] = {}
        self.given_up: set[str] = set()

    def note(self, utility: str, err: BaseException) -> bool:
        """Record one failed card; True when the utility is now given up on."""
        if not _transient(err):
            self.failures[utility] = 0
            return False
        self.failures[utility] = self.failures.get(utility, 0) + 1
        if self.failures[utility] >= _GIVE_UP_AFTER:
            self.given_up.add(utility)
            return True
        return False

    def ok(self, utility: str) -> None:
        self.failures[utility] = 0


class _Cards(StoredTexts):
    """What the run knows about card bytes, plus where they are kept.

    The render cache is the shared one (``card_texts.StoredTexts``): a
    downloaded card whose bytes the branch has already seen is served the
    stored text instead of being rendered again. On top of it, ``pdfs.json``
    says which digests are already uploaded, and bytes the branch has not
    recorded yet are held until a row names them and then written under
    ``pdf_dir`` in the directory of the month, for the workflow to upload
    to the release of that month.
    """

    def __init__(
        self,
        out: Path,
        pdf_dir: Path | None,
        seen_month: str,
        *,
        serve_texts: bool = True,
    ) -> None:
        super().__init__(out, serve=serve_texts)
        self.out = out
        self.pdf_dir = pdf_dir
        self.seen_month = seen_month
        self.kept: dict[str, str] = {}
        manifest = out / _MANIFEST
        if manifest.exists():
            self.kept = json.loads(manifest.read_text(encoding="utf-8"))
        self.saved: dict[str, str] = {}
        # Downloaded, not recorded anywhere yet, waiting for a row to name it.
        self.pending: dict[str, bytes] = {}

    def keep(self, digest: str, payload: bytes) -> None:
        if self.pdf_dir is None or digest in self.kept or digest in self.saved:
            return
        self.pending[digest] = payload

    def file(self, month_id: str, digests: Iterable[str]) -> None:
        """Write the pending bytes a row read under that row's month."""
        if self.pdf_dir is None:
            return
        for digest in digests:
            payload = self.pending.pop(digest, None)
            if payload is None:
                continue
            rel = f"{_RELEASE_PREFIX}-{month_id}/{digest}.pdf"
            path = self.pdf_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            self.saved[digest] = rel

    def file_the_rest(self) -> None:
        """Bytes no row ever named, a card whose parse failed, go under the
        month they were captured in: kept, findable by digest, just without
        a row to say what they are."""
        self.file(self.seen_month, list(self.pending))


class _Chunks:
    """A body the readers stream in chunks, over bytes already in hand."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        yield self._payload


class _KeptResponse:
    """The response shape the readers use, over a kept card."""

    status = 200
    content_length = None
    content_type = "application/pdf"
    charset = None
    history: tuple[object, ...] = ()

    def __init__(self, payload: bytes) -> None:
        self.content = _Chunks(payload)

    async def __aenter__(self) -> _KeptResponse:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _NoJar:
    """De Watergroep clears its commune cookie after every request."""

    def clear(self, _predicate: Any = None) -> None:
        return None


class _ReplaySession:
    """What a replayed parse may fetch: nothing from a utility.

    Every text the row read is seeded into the memo before the parse runs,
    so the readers never reach this session for those. The one request
    that can still arrive is a PDF the row has no text for (every PDF
    under ``--rerender``), and that is served from the kept copy: the
    local one first, the cards releases otherwise, with a download kept on
    disk for the sibling rows that read the same card. Anything else is
    refused as a network error, which the readers wrap the way they wrap
    a real one, and the row is left as it was and reported.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        pdf_dir: Path | None,
        pdf_base_url: str | None,
        kept: dict[str, str] | None = None,
    ) -> None:
        self._session = session
        self._pdf_dir = pdf_dir
        self._pdf_base_url = pdf_base_url
        # digest -> release path, the manifest; where a kept card is served from.
        self._kept = kept or {}
        # url -> digest for the row being replayed.
        self.pdfs: dict[str, str] = {}
        self._cache = Path(tempfile.mkdtemp(prefix="cards-replay-"))
        self.cookie_jar = _NoJar()

    def get(self, url: str, **_kw: Any) -> _Pending:
        return _Pending(self._fetch(url))

    def post(self, url: str, **_kw: Any) -> _Pending:
        return _Pending(self._fetch(url))

    async def _fetch(self, url: str) -> bytes:
        digest = self.pdfs.get(url)
        if digest is None:
            raise aiohttp.ClientConnectionError(f"offline replay has nothing for {url}")
        if self._pdf_dir is not None:
            local = next(self._pdf_dir.glob(f"*/{digest}.pdf"), None)
            if local is not None:
                return local.read_bytes()
        cached = self._cache / f"{digest}.pdf"
        if cached.exists():
            return cached.read_bytes()
        path = self._kept.get(digest)
        if self._pdf_base_url is None or path is None:
            raise aiohttp.ClientConnectionError(f"no kept copy of {url} to replay from")
        async with self._session.get(
            f"{self._pdf_base_url}/{path}", timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            if resp.status >= 400:
                raise aiohttp.ClientConnectionError(
                    f"HTTP {resp.status} fetching the kept copy of {url}"
                )
            payload = await resp.read()
        cached.write_bytes(payload)
        return payload


class _Pending:
    """An enterable stand-in for aiohttp's request context."""

    def __init__(self, fetch: Awaitable[bytes]) -> None:
        self._fetch = fetch

    async def __aenter__(self) -> _KeptResponse:
        return _KeptResponse(await self._fetch)

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _parser_digest() -> str:
    """One digest over every source a parse depends on."""
    root = ROOT / "custom_components" / "be_water_prices"
    digest = hashlib.sha256()
    for pattern in _PARSER_SOURCES:
        for path in sorted(root.glob(pattern)):
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _month_id(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _release_month(path: str) -> str:
    """The month a release path was captured in, from its tag."""
    found = re.search(r"(\d{4}-\d{2})", path.split("/")[0])
    return found.group(1) if found else ""


def _months_before(today: date, months: int) -> str:
    """The month id ``months`` before ``today``'s month."""
    index = today.year * 12 + today.month - 1 - months
    return _month_id(index // 12, index % 12 + 1)


@dataclass(frozen=True)
class _Source:
    """One thing a parse read: a page or a rendered PDF, its text, and the
    PDF's digest when there was one."""

    url: str
    variant: str
    text: str
    pdf: str | None = None

    def entry(self, path: str) -> dict[str, str]:
        """The row's record of it, with the text stored at ``path``."""
        out = {"url": self.url, "variant": self.variant, "text": path}
        if self.pdf is not None:
            out["pdf"] = self.pdf
        return out


def _source_of(key: str, text: str, cards: _Cards) -> _Source:
    """Describe one memo entry: the readers key a rendered PDF as
    ``<variant>\\0<url>`` and a page by its URL alone (with what was
    asked of the endpoint after a ``#`` when the URL does not say). A
    rendered PDF also names its bytes by digest when they were seen;
    ``pdfs.json`` says where that digest lives."""
    variant, sep, url = key.partition("\0")
    if not sep:
        return _Source(key, "text", text)
    return _Source(url, variant, text, cards.digest_for(url))


def _sources_of(memo: _RecordingMemo, cards: _Cards) -> list[_Source]:
    """Everything one parse read: the memo entries it touched, plus any card
    the render hook saw that never passed through the memo, in a stable
    order."""
    sources = [_source_of(key, memo[key], cards) for key in sorted(memo.touched)]
    named = {(s.variant, s.url) for s in sources}
    for variant, url, digest, text in cards.calls:
        if (variant, url) in named:
            continue
        named.add((variant, url))
        sources.append(_Source(url, variant, text, digest))
    return sorted(sources, key=lambda s: (s.variant != "text", s.variant, s.url))


def _write_text(out: Path, text: str) -> str:
    """Store ``text`` once, content-addressed, and return its path in ``out``."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    rel = f"texts/{digest}.txt"
    path = out / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    return rel


def _read_row(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def _previous_row(out: Path, utility: str, commune: str, month_id: str) -> dict[str, Any] | None:
    """The row of the latest month before ``month_id``, if any."""
    earlier = sorted(p for p in (out / utility / commune).glob("????-??.json") if p.stem < month_id)
    return _read_row(earlier[-1]) if earlier else None


def _same_card(existing: dict[str, Any] | None, fresh: dict[str, Any]) -> bool:
    """Whether two rows hold the same card.

    The capture day is not the card, and neither is the path of a text a
    source was read from: a page that carries a nonce gives a new text
    every day while the parse, the URL, the reader variant and the PDF are
    all the same. What a source was and what it parsed to is what counts.
    """
    if existing is None:
        return False

    def settled(card: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in card.items() if k not in _VOLATILE_KEYS}
        out["_sources"] = [
            {k: v for k, v in source.items() if k != "text"} for source in card.get("_sources", [])
        ]
        return out

    return settled(existing) == settled(fresh)


def _write_card(
    out: Path,
    utility: str,
    commune: str,
    month_id: str,
    tariff: WaterTariff,
    sources: list[_Source],
    meta: dict[str, str],
    seen_on: date,
) -> bool:
    """Write the row's month file; True when the file changed.

    The tariff is laid out one key per line so a day's diff on the branch
    is readable, with the row's own keys beside it: the commune it was
    fetched for, the day it was seen and what it read. The texts are stored
    only when the row is: a month whose card is the same as the previous
    month's points at the texts that row holds, so an unchanged page is
    kept once rather than once a month.
    """
    card: dict[str, Any] = tariff_to_dict(tariff)
    card.update(meta)
    card["_seen_on"] = seen_on.isoformat()
    card["_sources"] = [s.entry("") for s in sources]
    path = out / utility / commune / f"{month_id}.json"
    if _same_card(_read_row(path), card):
        return False
    reuse: dict[tuple[str, str], str] = {}
    previous = _previous_row(out, utility, commune, month_id)
    if previous is not None and _same_card(previous, card):
        reuse = {
            (s["variant"], s["url"]): s["text"]
            for s in previous["_sources"]
            if (out / s["text"]).exists()
        }
    card["_sources"] = [
        s.entry(reuse.get((s.variant, s.url)) or _write_text(out, s.text)) for s in sources
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(card, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def _rows(out: Path) -> list[Path]:
    return sorted(out.glob("*/*/????-??.json"))


def _prune(out: Path, keep_months: int, today: date) -> int:
    """Remove months more than ``keep_months`` before today's, then the
    texts no remaining row refers to; count them."""
    cutoff = _months_before(today, keep_months)
    removed = 0
    for path in _rows(out):
        if path.stem < cutoff:
            path.unlink()
            removed += 1
    referenced = {
        source["text"]
        for path in _rows(out)
        for source in (_read_row(path) or {}).get("_sources", [])
    }
    for path in out.glob("texts/*.txt"):
        if path.relative_to(out).as_posix() not in referenced:
            path.unlink()
            removed += 1
    # Deepest first, so a commune directory emptied above goes too.
    for folder in sorted((p for p in out.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(folder.iterdir()):
            folder.rmdir()
    manifest = out / _MANIFEST
    if manifest.exists():
        kept = json.loads(manifest.read_text(encoding="utf-8"))
        # A release path is <prefix>-<YYYY-MM>[-n]/<digest>.pdf; the workflow
        # deletes the release itself on the same cutoff.
        current = {d: p for d, p in kept.items() if _release_month(p) >= cutoff}
        if len(current) != len(kept):
            removed += len(kept) - len(current)
            manifest.write_text(
                json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )
    return removed


@dataclass
class _Held:
    """One stored month, as the coverage table needs it."""

    label: str
    digests: list[str]
    page: str | None
    path: str


def _kept_rows(out: Path) -> tuple[dict[str, dict[str, dict[str, _Held]]], dict[str, str]]:
    """Every row on disk, by utility and commune, then month: the commune's
    label, the digests of the PDFs it read, the page it read and its own
    path; and the manifest."""
    held: dict[str, dict[str, dict[str, _Held]]] = {}
    for path in _rows(out):
        utility, commune = path.parts[-3], path.parts[-2]
        row = _read_row(path)
        if row is None:
            continue
        sources = row.get("_sources", [])
        pages = [s["text"] for s in sources if s.get("variant") == "text"]
        held.setdefault(utility, {}).setdefault(commune, {})[path.stem] = _Held(
            row.get("_commune_label", ""),
            [digest_of(s["pdf"]) for s in sources if "pdf" in s],
            pages[0] if pages else None,
            path.relative_to(out).as_posix(),
        )
    manifest = out / _MANIFEST
    kept: dict[str, str] = (
        json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    )
    return held, kept


def _pdf_link(digest: str, kept: dict[str, str], pdf_base_url: str | None) -> str | None:
    """Where a person can download this PDF, once it has been uploaded."""
    path = kept.get(digest)
    if path is None or pdf_base_url is None:
        return None
    return f"{pdf_base_url}/{path}"


def _link(label: str, base: str | None, rel: str) -> str:
    """A link into the branch, or the bare label with nowhere to link to."""
    return f"[{label}]({base}/{rel})" if base else label


def _cell(
    held: _Held | None,
    kept: dict[str, str],
    pdf_base_url: str | None,
    archive_base_url: str | None,
) -> str:
    """What a month links to: the card when one was read (once it is
    uploaded), the page it was parsed from otherwise, and the JSON the
    parse produced."""
    if held is None:
        return ""
    links = [link for link in (_pdf_link(d, kept, pdf_base_url) for d in held.digests) if link]
    if links:
        parts = [f"[pdf]({links[0]})"]
    elif held.digests:
        parts = ["pdf"]
    elif held.page is not None:
        parts = [_link("page", archive_base_url, held.page)]
    else:
        parts = []
    parts.append(_link("json", archive_base_url, held.path))
    return " ".join(parts)


def _write_coverage(
    out: Path, pdf_base_url: str | None = None, archive_base_url: str | None = None
) -> None:
    """Rewrite the coverage table from the rows on disk.

    Deterministic in its order, so a day that changed nothing rewrites the
    file to the same bytes and the branch gets no commit for it.
    """
    held, kept = _kept_rows(out)
    months = sorted({m for rows in held.values() for have in rows.values() for m in have})
    lines = [
        "# Coverage",
        "",
        "One row per utility and commune (`default` is the no-commune fetch), one column",
        "per month the branch holds. Each month links to what its tariff was parsed from",
        "and to what came out of it: `pdf` is the card itself, in the cards repository's",
        "releases, `page` the text of the page as it was read, and `json` the tariff as",
        "the integration parsed it, both on this branch. A blank cell is a month the",
        "branch does not hold.",
        "",
    ]
    for utility in sorted(held):
        lines += [
            f"## {utility}",
            "",
            "| commune | label | " + " | ".join(months) + " |",
            "| --- | --- | " + " | ".join("---" for _ in months) + " |",
        ]
        by_commune = sorted(held[utility].items(), key=lambda item: (item[0] != _DEFAULT, item[0]))
        for commune, have in by_commune:
            label = next((h.label for _m, h in sorted(have.items(), reverse=True) if h.label), "")
            cells = [_cell(have.get(m), kept, pdf_base_url, archive_base_url) for m in months]
            lines.append(f"| {commune} | {label} | " + " | ".join(cells) + " |")
        lines.append("")
    (out / _COVERAGE).write_text("\n".join(lines), encoding="utf-8")


def _write_listings(
    out: Path, pdf_base_url: str | None = None, archive_base_url: str | None = None
) -> None:
    """The coverage table and the branch README, rewritten when out of date.
    The index of PDFs by release that earlier versions wrote is removed, the
    coverage table having taken it over."""
    _write_coverage(out, pdf_base_url, archive_base_url)
    readme = out / "README.md"
    if not readme.exists() or readme.read_text(encoding="utf-8") != _README:
        readme.write_text(_README, encoding="utf-8")
    (out / "pdfs.md").unlink(missing_ok=True)


async def _fetch_card[T](
    fetch: Callable[[], Awaitable[T]],
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> T:
    """One card, retrying the transient failures the live check retries.

    ``fetch`` builds a fresh awaitable per attempt, since one can only be
    awaited once.
    """
    for attempt in range(_ATTEMPTS):
        try:
            return await asyncio.wait_for(fetch(), _CARD_TIMEOUT_S)
        except Exception as err:
            if attempt == _ATTEMPTS - 1 or not _transient(err):
                raise
            await sleep(_RETRY_BACKOFF_S[min(attempt, len(_RETRY_BACKOFF_S) - 1)])
    raise AssertionError("unreachable")


def _targets(
    extractors: Iterable[WaterExtractor], only: set[str], on_ci: bool
) -> list[WaterExtractor]:
    """Every utility with a card to fetch today. A utility whose publication
    refuses runner addresses is skipped on a runner, as the live check
    skips it; a local run archives it."""
    return [
        ex
        for ex in extractors
        if (not only or ex.id in only) and not (on_ci and ex.id in CI_BLOCKED)
    ]


@dataclass
class _Run:
    """What one walk shares between its rows."""

    out: Path
    seen_month: str
    today: date
    memo: _RecordingMemo
    cards: _Cards
    patience: _Patience
    summary: _Summary
    sleep: Callable[[float], Any]


async def _capture(
    run: _Run,
    ex: WaterExtractor,
    commune: str,
    label: str | None,
    fetch: Callable[[], Awaitable[WaterTariff]],
) -> None:
    """One row: fetch, attribute what was read, write when it changed."""
    name = f"{ex.id}/{commune}"
    if "/" in commune or commune in ("", ".", ".."):
        run.summary.failed.append(f"{name}: a commune id that cannot name a directory")
        return
    run.memo.touched.clear()
    run.cards.calls.clear()
    try:
        tariff = await _fetch_card(fetch, run.sleep)
    except Exception as err:  # one card must not stop the walk
        run.summary.failed.append(f"{name}: {type(err).__name__}: {err}")
        if run.patience.note(ex.id, err):
            run.summary.given_up.append(ex.id)
        return
    run.patience.ok(ex.id)
    sources = _sources_of(run.memo, run.cards)
    meta = {} if commune == _DEFAULT else {"_commune": commune, "_commune_label": label or ""}
    if _write_card(run.out, ex.id, commune, run.seen_month, tariff, sources, meta, run.today):
        run.summary.stored += 1
    else:
        run.summary.unchanged += 1
    run.cards.file(run.seen_month, (s.pdf for s in sources if s.pdf is not None))


async def _walk(
    run: _Run,
    session: aiohttp.ClientSession,
    targets: list[WaterExtractor],
    *,
    defaults_only: bool = False,
) -> None:
    """Every utility's default fetch, then every commune it lists unless
    the communes are not asked for today."""
    for ex in targets:
        await _capture(run, ex, _DEFAULT, None, partial(ex.fetch, session))
        list_communes, fetch_for_commune = ex.list_communes, ex.fetch_for_commune
        if list_communes is None or fetch_for_commune is None or ex.id in run.patience.given_up:
            continue
        if defaults_only:
            continue
        run.memo.touched.clear()
        try:
            communes = await _fetch_card(partial(list_communes, session), run.sleep)
        except Exception as err:  # the default row is stored either way
            run.summary.failed.append(f"{ex.id}/communes: {type(err).__name__}: {err}")
            if run.patience.note(ex.id, err):
                run.summary.given_up.append(ex.id)
            continue
        for option in communes:
            if ex.id in run.patience.given_up:
                break
            await _capture(
                run, ex, option.id, option.label, partial(fetch_for_commune, session, option.id)
            )


async def _replay_row(
    path: Path,
    extractors: dict[str, WaterExtractor],
    cards: _Cards,
    replay: _ReplaySession,
    summary: _Summary,
    *,
    rerender: bool = False,
) -> None:
    """Re-run one stored row through the current parser, offline.

    Under ``rerender`` the PDF texts are not seeded, so every card is
    fetched back from the kept copy and rendered afresh; the pages still
    come from the branch, since there is nothing to re-render there.
    """
    out = cards.out
    utility, commune = path.parts[-3], path.parts[-2]
    label = f"{utility}/{commune}/{path.stem}"
    row = _read_row(path)
    if row is None:
        summary.unreplayable.append(f"{label}: not a JSON row")
        return
    extractor = extractors.get(utility)
    if extractor is None:
        summary.unreplayable.append(f"{label}: no extractor registered")
        return
    memo = _RecordingMemo()
    for source in row.get("_sources", []):
        text_path = out / source["text"]
        if not text_path.exists():
            summary.unreplayable.append(f"{label}: {source['text']} is missing")
            return
        if rerender and source["variant"] != "text":
            continue
        key = (
            source["url"]
            if source["variant"] == "text"
            else f"{source['variant']}\0{source['url']}"
        )
        # Seeded, not touched: only what the parse actually reads counts.
        dict.__setitem__(memo, key, read_text(text_path))
    replay.pdfs = {s["url"]: digest_of(s["pdf"]) for s in row.get("_sources", []) if "pdf" in s}
    cards.digests.update(replay.pdfs)
    cards.calls.clear()
    try:
        seen_on = date.fromisoformat(row["_seen_on"])
    except (KeyError, ValueError):
        summary.unreplayable.append(f"{label}: no capture date")
        return
    session: Any = replay
    with memoise_text_fetches(memo), render_through(cards.render):
        try:
            if commune == _DEFAULT:
                tariff = await extractor.fetch(session)
            else:
                fetch_for_commune = extractor.fetch_for_commune
                if fetch_for_commune is None:
                    summary.unreplayable.append(f"{label}: the utility has no communes now")
                    return
                tariff = await fetch_for_commune(session, row.get("_commune", commune))
        except Exception as err:  # a row that will not replay is reported, not fatal
            summary.unreplayable.append(f"{label}: {type(err).__name__}: {err}")
            return
    sources = _sources_of(memo, cards)
    # A card the row had never named is kept now, under this row's month.
    cards.file(path.stem, (s.pdf for s in sources if s.pdf is not None))
    summary.replayed += 1
    meta: dict[str, str] = {k: row[k] for k in ("_commune", "_commune_label") if k in row}
    if _write_card(out, utility, commune, path.stem, tariff, sources, meta, seen_on):
        summary.reparsed += 1


async def _replay_all(
    out: Path,
    extractors: dict[str, WaterExtractor],
    cards: _Cards,
    replay: _ReplaySession,
    summary: _Summary,
    *,
    rerender: bool = False,
) -> None:
    """Every stored row, grouped by capture day so the clock is pinned
    once per day rather than once per row."""
    by_day: dict[str, list[Path]] = {}
    for path in _rows(out):
        by_day.setdefault((_read_row(path) or {}).get("_seen_on", ""), []).append(path)
    for day, paths in sorted(by_day.items()):
        if not day:
            for path in paths:
                await _replay_row(path, extractors, cards, replay, summary, rerender=rerender)
            continue
        # Ticking, so the loop's own timers and the render threads keep
        # working; the date stays the capture day for the seconds this takes.
        with freeze_time(f"{day}T12:00:00+02:00", tick=True):
            for path in paths:
                await _replay_row(path, extractors, cards, replay, summary, rerender=rerender)


async def archive(
    out: Path,
    *,
    only: set[str] | None = None,
    keep_months: int = 36,
    pdf_dir: Path | None = None,
    pdf_base_url: str | None = None,
    archive_base_url: str | None = None,
    defaults_only: bool = False,
    reparse: bool = False,
    rerender: bool = False,
    extractors: Iterable[WaterExtractor] | None = None,
    now: datetime | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
    on_ci: bool | None = None,
) -> _Summary:
    """Fetch every card, store what changed, replay what the parser
    changed for, prune the old, report."""
    now = now or datetime.now(UTC)
    today = now.astimezone(_BRUSSELS).date()
    seen_month = _month_id(today.year, today.month)
    out.mkdir(parents=True, exist_ok=True)
    if on_ci is None:
        on_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    cards = _Cards(out, pdf_dir, seen_month, serve_texts=not rerender)
    run = _Run(out, seen_month, today, _RecordingMemo(), cards, _Patience(), _Summary(), sleep)
    registry = tuple(all_extractors() if extractors is None else extractors)
    targets = _targets(registry, only or set(), on_ci)
    async with aiohttp.ClientSession() as session:
        with memoise_text_fetches(run.memo), render_through(cards.render):
            await _walk(run, session, targets, defaults_only=defaults_only)
        # A fresh archive holds nothing older than this parser, so the first
        # run only stamps it; from then on a changed digest replays the rows.
        stamp = out / _PARSER_STAMP
        parser = _parser_digest()
        stamped = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else parser
        if reparse or rerender or stamped != parser:
            replay = _ReplaySession(session, pdf_dir, pdf_base_url, cards.kept)
            await _replay_all(
                out,
                {ex.id: ex for ex in registry},
                cards,
                replay,
                run.summary,
                rerender=rerender,
            )
        stamp.write_text(parser + "\n", encoding="utf-8")
    cards.file_the_rest()
    summary = run.summary
    summary.rendered = cards.rendered
    summary.unrendered = cards.unrendered
    summary.pdfs_saved = len(cards.saved)
    removed = _prune(out, keep_months, today)
    _write_listings(out, pdf_base_url, archive_base_url)
    print(
        f"{summary.stored} stored, {summary.unchanged} unchanged, "
        f"{len(summary.failed)} failed, {removed} pruned, "
        f"{len(targets)} utilities asked; {summary.rendered} rendered, "
        f"{summary.unrendered} served from stored text, "
        f"{summary.pdfs_saved} new PDFs kept; {summary.replayed} replayed, "
        f"{summary.reparsed} reparsed, {len(summary.unreplayable)} not replayable"
    )
    for line in summary.failed:
        print(f"  failed {line[:300]}")
    for utility in summary.given_up:
        print(f"  gave up on {utility} after {_GIVE_UP_AFTER} network failures in a row")
    for line in summary.unreplayable:
        print(f"  not replayable {line[:300]}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="archive checkout")
    parser.add_argument("--only", action="append", default=[], help="restrict to a utility id")
    parser.add_argument("--keep-months", type=int, default=36)
    parser.add_argument(
        "--pdfs",
        type=Path,
        default=None,
        metavar="DIR",
        help="write every PDF the branch has not recorded yet under DIR",
    )
    parser.add_argument(
        "--pdf-base-url",
        default=None,
        metavar="URL",
        help="where the kept PDFs are served from, for the listings and a replay that needs one",
    )
    parser.add_argument(
        "--archive-base-url",
        default=None,
        metavar="URL",
        help="where the branch is browsed, for the listing's links to rows and pages",
    )
    parser.add_argument(
        "--defaults-only",
        action="store_true",
        help="store each utility's default row only; do not list or walk the communes",
    )
    parser.add_argument(
        "--reparse",
        action="store_true",
        help="replay every stored row through the parser even if it did not change",
    )
    parser.add_argument(
        "--rerender",
        action="store_true",
        help="replay every row with its PDFs rendered afresh, for a reader upgrade",
    )
    parser.add_argument(
        "--index-only",
        action="store_true",
        help="only rewrite coverage.md from what is on disk; no fetch",
    )
    args = parser.parse_args()
    if args.index_only:
        # After the workflow's upload step has extended the manifest, so the
        # links written by the walk before it point at files that now exist.
        _write_listings(args.out, args.pdf_base_url, args.archive_base_url)
        return 0
    summary = asyncio.run(
        archive(
            args.out,
            only=set(args.only),
            keep_months=args.keep_months,
            pdf_dir=args.pdfs,
            pdf_base_url=args.pdf_base_url,
            archive_base_url=args.archive_base_url,
            defaults_only=args.defaults_only,
            reparse=args.reparse,
            rerender=args.rerender,
        )
    )
    return 0 if summary.stored or summary.unchanged else 1


if __name__ == "__main__":
    sys.exit(main())
