#!/usr/bin/env python3
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

"""Live end-to-end check of every water-utility extractor.

Walks every registered :class:`WaterExtractor`, hits the utility's real
publication, parses the result, and verifies the snapshot is structurally
sane (region matches, fee in plausible range, at least one volumetric
component populated). An extractor that lists communes has its list
fetched too, and checked against a floor on its length, since the
config flow quietly drops the commune selector when that list fails.
Checks every extractor rather than stopping at the first failure,
prints a markdown report to stdout and folds the outcomes into the exit
code below.

Run by ``.github/workflows/live_check.yml`` daily; on persistent failure
the workflow opens or updates a GitHub issue with this report attached.

Exit code semantics (a bitmask so the workflow can retry on any failure
but only open an issue for a real one):

    0 = all reachable extractors green
    1 = at least one extractor really failed (parse error, sanity check
        missed, HTTP 4xx, or a fetch past the integration's own time
        budget); worth retrying and opening an issue
    2 = at least one extractor hit a transient infrastructure failure
        (timeout, connection reset, HTTP 5xx / 429); worth retrying but
        not worth an issue. A brief upstream hiccup is not a regression

The two bits combine (3 = both). The workflow retries while either bit
is set and opens the broken-extractor issue only when bit 1 is set.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import traceback
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Imports happen after sys.path mutation; the package's __init__ is lazy so
# this works without homeassistant being installed. The render cache is
# the card archiver's own, so a card the archive already holds is not
# rendered again here.
from card_texts import StoredTexts  # noqa: E402

from custom_components.be_water_prices.const import (  # noqa: E402
    FETCH_BUDGET_S,
    REGION_WALLONIA,
    WALLONIA_SPGE_YEAR,
)
from custom_components.be_water_prices.providers import (  # noqa: E402
    WaterExtractor,
    WaterTariff,
    all_extractors,
)
from custom_components.be_water_prices.providers._pdf import render_through  # noqa: E402
from custom_components.be_water_prices.providers.base import (  # noqa: E402
    CommuneOption,
    ExtractorError,
    TransientFetchError,
)

# Exit-code bits (see module docstring).
EXIT_REAL_FAIL = 1
EXIT_TRANSIENT = 2

# Loose plausibility windows. Anything outside these almost certainly
# means the parser misread a different number on the page.
MIN_FEE_EUR_YEAR = 5.0
MAX_FEE_EUR_YEAR = 500.0
MIN_RATE_EUR_M3 = 0.5
MAX_RATE_EUR_M3 = 20.0

# How far apart the Walloon cards of one year may put the SPGE figures,
# the thresholds the extractors hold a page to the constants with.
SPGE_TOLERANCE = {"CVA": 0.005, "FSE": 0.001}

# Fewest communes each lister may return before the list is called
# broken. A restyled dropdown the parser only half matches yields a short
# list rather than an error. Each floor sits some way under what the
# lister returned after its phantom filter in October 2026 (De Watergroep
# 699, Farys 266, Pidpa 63, Water-link 5), so an operator taking over or
# handing back a commune or two does not open an issue. A lister missing
# here only has to return something.
MIN_COMMUNES: dict[str, int] = {
    "de_watergroep": 600,
    "farys": 225,
    "pidpa": 50,
    "water_link": 4,
}

# Utilities whose live publication is unreachable from GitHub Actions
# runners. Water-link's CDN returns HTTP 403 to datacenter IP ranges
# (residential IPs work fine). Skipping in CI keeps the workflow's
# signal-to-noise clean -- the fixture-based unit tests still cover
# these parsers, and a maintainer can rerun this script from a
# residential IP on demand. The skip only applies on a runner, which
# sets GITHUB_ACTIONS; a shell that follows the "rerun locally" advice
# used to skip the same utility and check nothing.
CI_BLOCKED: dict[str, str] = {
    "water_link": (
        "Water-link's CDN blocks GitHub Actions IP ranges (HTTP 403). "
        "Reachable from residential IPs; rerun locally to live-check."
    ),
}


@dataclass
class CheckResult:
    extractor_id: str
    label: str
    region: str
    status: str  # "OK", "FAIL", "TRANSIENT", or "SKIP"
    detail: str


def _validate(tariff: WaterTariff, region: str) -> str | None:
    """Return ``None`` if sane, a complaint string if not."""
    if tariff.region != region:
        return f"region mismatch: extractor says {region!r}, tariff says {tariff.region!r}"
    if not (MIN_FEE_EUR_YEAR <= tariff.yearly_fixed_fee <= MAX_FEE_EUR_YEAR):
        return f"yearly_fixed_fee {tariff.yearly_fixed_fee:.2f} EUR outside [{MIN_FEE_EUR_YEAR}, {MAX_FEE_EUR_YEAR}]"

    rates = [
        r
        for r in (
            tariff.basis_eur_per_m3,
            tariff.comfort_eur_per_m3,
            tariff.linear_eur_per_m3,
            tariff.cvd_eur_per_m3 or None,
        )
        if r is not None
    ]
    if not rates:
        return "no volumetric component populated (basis / comfort / linear / cvd all None or 0)"
    for r in rates:
        if not (MIN_RATE_EUR_M3 <= r <= MAX_RATE_EUR_M3):
            return f"volumetric rate {r:.4f} EUR/m³ outside [{MIN_RATE_EUR_M3}, {MAX_RATE_EUR_M3}]"

    today = date.today()
    if tariff.valid_from.year not in (today.year - 1, today.year, today.year + 1):
        return f"valid_from year {tariff.valid_from.year} too far from today ({today.year})"
    return None


def _judge_tariff(tariff: WaterTariff, region: str) -> tuple[str, str]:
    complaint = _validate(tariff, region)
    if complaint is not None:
        return "FAIL", complaint
    return (
        "OK",
        f"valid {tariff.valid_from} → {tariff.valid_until}, fee {tariff.yearly_fixed_fee:.2f} EUR/yr ex-VAT",
    )


def _judge_communes(communes: tuple[CommuneOption, ...], floor: int) -> tuple[str, str]:
    if len(communes) < floor:
        return "FAIL", f"only {len(communes)} communes listed, expected at least {floor}"
    return "OK", f"{len(communes)} communes listed"


async def _check[T](
    session: aiohttp.ClientSession,
    extractor: WaterExtractor,
    *,
    check_id: str,
    label: str,
    call: Callable[[aiohttp.ClientSession], Awaitable[T]],
    judge: Callable[[T], tuple[str, str]],
) -> CheckResult:
    """Run one call against the utility and classify what it did."""
    region = extractor.region
    if extractor.id in CI_BLOCKED and os.environ.get("GITHUB_ACTIONS") == "true":
        return CheckResult(check_id, label, region, "SKIP", CI_BLOCKED[extractor.id])
    budget = asyncio.timeout(FETCH_BUDGET_S)
    try:
        async with budget:
            value = await call(session)
    except TransientFetchError as err:
        # Upstream hiccup (timeout / connection reset / HTTP 5xx): not a
        # regression, so it must not open an issue. Checked before the
        # ExtractorError branch because it is a subclass of it.
        return CheckResult(check_id, label, region, "TRANSIENT", str(err))
    except ExtractorError as err:
        return CheckResult(check_id, label, region, "FAIL", str(err))
    except Exception:  # top-level: report anything unexpected as a failure row
        # Out of time is a failure, not a hiccup: a refresh gives up on the
        # same budget and users stay on their held card, and a commune list
        # that slow leaves the config flow waiting as long. A PDF render it
        # gave up on ends when the reader kills its child process at
        # PDF_READER_TIMEOUT_S. A text or HTML parse thread runs on to its
        # end; only the job's own timeout bounds one that never returns.
        if budget.expired():
            detail = f"did not finish within {FETCH_BUDGET_S} s"
        else:
            detail = traceback.format_exc()
        return CheckResult(check_id, label, region, "FAIL", detail)
    status, detail = judge(value)
    return CheckResult(check_id, label, region, status, detail)


async def _check_one(
    session: aiohttp.ClientSession,
    extractor: WaterExtractor,
    seen: dict[str, WaterTariff] | None = None,
) -> CheckResult:
    """One extractor's card, kept in ``seen`` under its label when given."""

    def judge(tariff: WaterTariff) -> tuple[str, str]:
        if seen is not None:
            seen[extractor.label] = tariff
        return _judge_tariff(tariff, extractor.region)

    return await _check(
        session,
        extractor,
        check_id=extractor.id,
        label=extractor.label,
        call=extractor.fetch,
        judge=judge,
    )


def _check_spge(tariffs: dict[str, WaterTariff]) -> CheckResult | None:
    """The CVA and FSE the Walloon cards were priced on, side by side.

    A card past WALLONIA_SPGE_YEAR is priced on its own page's figures,
    so a misread there bills its entries wrong with no constant to catch
    it, and the constants in const.py are a year behind. Both fail here:
    a card past the year, until a release moves the constants and the
    year, and the cards of one year that disagree on either figure.
    None when no Walloon card came back to compare.
    """
    walloon = {label: t for label, t in tariffs.items() if t.region == REGION_WALLONIA}
    if not walloon:
        return None
    problems: list[str] = []
    later = sorted(label for label, t in walloon.items() if t.valid_from.year > WALLONIA_SPGE_YEAR)
    if later:
        problems.append(
            f"{', '.join(later)} on a card past {WALLONIA_SPGE_YEAR}, the year of the "
            f"constants in const.py: move WALLONIA_SPGE_YEAR and the figures"
        )
    for year in sorted({t.valid_from.year for t in walloon.values()}):
        cards = {label: t for label, t in walloon.items() if t.valid_from.year == year}
        for name, tolerance in SPGE_TOLERANCE.items():
            values = {
                label: t.cva_eur_per_m3 if name == "CVA" else t.fse_eur_per_m3
                for label, t in cards.items()
            }
            if max(values.values()) - min(values.values()) > tolerance:
                listed = ", ".join(f"{label} {value}" for label, value in sorted(values.items()))
                problems.append(f"{year} cards disagree on the {name}: {listed}")
    if problems:
        status, detail = "FAIL", "; ".join(problems)
    else:
        status, detail = "OK", f"{len(walloon)} Walloon cards agree on the CVA and FSE"
    return CheckResult("spge", "Walloon SPGE figures", REGION_WALLONIA, status, detail)


async def _check_communes(session: aiohttp.ClientSession, extractor: WaterExtractor) -> CheckResult:
    """The commune list the config flow offers. When it fails, the flow
    leaves the commune field out and the entry is billed on the
    operator's default commune, with nothing to show the user."""
    assert extractor.list_communes is not None
    # The label is the issue's fingerprint, so a broken list must not
    # read as the same problem as a broken default card.
    return await _check(
        session,
        extractor,
        check_id=f"{extractor.id}/communes",
        label=f"{extractor.label} (communes)",
        call=extractor.list_communes,
        judge=lambda communes: _judge_communes(communes, MIN_COMMUNES.get(extractor.id, 1)),
    )


def _exit_code(results: list[CheckResult]) -> int:
    """Fold the per-check statuses into the exit bitmask.

    SKIP rows do not flip any bit: a CI-unreachable utility is healthy
    from a residential IP, so flagging it as broken would be a false
    positive and quickly poison the workflow's signal. FAIL trips bit 1
    (real regression -> open an issue); TRANSIENT trips bit 2 (upstream
    hiccup -> retry but no issue).
    """
    rc = 0
    if any(r.status == "FAIL" for r in results):
        rc |= EXIT_REAL_FAIL
    if any(r.status == "TRANSIENT" for r in results):
        rc |= EXIT_TRANSIENT
    return rc


async def _run(texts: Path | None = None) -> tuple[list[CheckResult], int, StoredTexts | None]:
    """Every check, with the archive branch's texts as a render cache when
    a checkout is given: a PDF whose bytes the branch already holds is
    downloaded and measured as before, but its text is read from the
    branch instead of being rendered again."""
    cache = StoredTexts(texts) if texts is not None else None
    with ExitStack() as hooks:
        if cache is not None:
            hooks.enter_context(render_through(cache.render))
        async with aiohttp.ClientSession() as session:
            results = []
            seen: dict[str, WaterTariff] = {}
            for e in all_extractors():
                results.append(await _check_one(session, e, seen))
                if e.supports_communes:
                    results.append(await _check_communes(session, e))
            spge = _check_spge(seen)
            if spge is not None:
                results.append(spge)
    return results, _exit_code(results), cache


def _texts_line(cache: StoredTexts | None) -> str:
    """How much rendering the archive's texts saved this run."""
    if cache is None:
        return ""
    return (
        f"\n\n_{cache.unrendered} of {cache.unrendered + cache.rendered} PDFs were served from"
        f" the archive's texts; {cache.rendered} were rendered._"
    )


def _render(results: list[CheckResult], cache: StoredTexts | None = None) -> str:
    lines = ["# Water extractor live check", ""]
    lines.append("| utility | region | status | detail |")
    lines.append("|---|---|---|---|")
    for r in results:
        detail = r.detail.replace("\n", "<br>").replace("|", "\\|")
        lines.append(f"| {r.label} | {r.region} | {r.status} | {detail} |")
    failed = [r for r in results if r.status == "FAIL"]
    transient = [r for r in results if r.status == "TRANSIENT"]
    skipped = [r for r in results if r.status == "SKIP"]
    ok = len(results) - len(failed) - len(transient) - len(skipped)
    extras = []
    if transient:
        extras.append(f"{len(transient)} transient")
    if skipped:
        extras.append(f"{len(skipped)} skipped")
    lines.append("")
    if failed:
        # The banner names the failures; the extras give the rest of the
        # picture without burying the headline in an OK count.
        suffix = f" ({', '.join(extras)})" if extras else ""
        lines.append(f"**{len(failed)} of {len(results)} checks failed.**" + suffix)
    else:
        counts = ", ".join([f"{ok} OK", *extras])
        # A transient row is not "green", and it is not "checked" either.
        # Saying only "no regressions" reads as an all-clear for a utility
        # this run never got an answer out of, which is how a host that is
        # permanently 5xx or rate-limited stays invisible: nothing here
        # ever escalates, so the wording has to carry it.
        if transient:
            names = ", ".join(sorted(r.label for r in transient))
            headline = (
                f"No regressions, but {len(transient)} of {len(results)} could not be "
                f"checked this run ({names})"
            )
        else:
            headline = "All reachable extractors green"
        lines.append(f"{headline} ({counts}).")
    return "\n".join(lines) + _texts_line(cache)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch every utility's live tariff and check it.")
    parser.add_argument(
        "--texts",
        type=Path,
        default=None,
        metavar="DIR",
        help="a checkout of the archive branch; PDFs it already holds are not rendered again",
    )
    args = parser.parse_args()
    results, rc, cache = asyncio.run(_run(args.texts))
    print(_render(results, cache))
    return rc


if __name__ == "__main__":
    sys.exit(main())
