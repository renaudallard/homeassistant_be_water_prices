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
component populated). Prints a markdown report to stdout and exits
non-zero on the first failure.

Run by ``.github/workflows/live_check.yml`` daily; on persistent failure
the workflow opens or updates a GitHub issue with this report attached.

Exit code semantics (a bitmask so the workflow can retry on any failure
but only open an issue for a real one):

    0 = all reachable extractors green
    1 = at least one extractor really failed (parse error, sanity check
        missed, HTTP 4xx); worth retrying and opening an issue
    2 = at least one extractor hit a transient infrastructure failure
        (timeout, connection reset, HTTP 5xx / 429); worth retrying but
        not worth an issue. A brief upstream hiccup is not a regression

The two bits combine (3 = both). The workflow retries while either bit
is set and opens the broken-extractor issue only when bit 1 is set.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Imports happen after sys.path mutation; the package's __init__ is lazy so
# this works without homeassistant being installed.
from custom_components.be_water_prices.providers import (  # noqa: E402
    WaterExtractor,
    WaterTariff,
    all_extractors,
    de_watergroep,
    pidpa,
)
from custom_components.be_water_prices.providers.base import (  # noqa: E402
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


async def _check_fetch(
    session: aiohttp.ClientSession,
    *,
    check_id: str,
    label: str,
    region: str,
    fetch: Callable[[aiohttp.ClientSession], Awaitable[WaterTariff]],
) -> CheckResult:
    """Run one fetch and classify what it did."""
    try:
        tariff = await fetch(session)
    except TransientFetchError as err:
        # Upstream hiccup (timeout / connection reset / HTTP 5xx): not a
        # regression, so it must not open an issue. Checked before the
        # ExtractorError branch because it is a subclass of it.
        return CheckResult(check_id, label, region, "TRANSIENT", str(err))
    except ExtractorError as err:
        return CheckResult(check_id, label, region, "FAIL", str(err))
    except Exception:  # top-level: report anything unexpected as a failure row
        return CheckResult(check_id, label, region, "FAIL", traceback.format_exc())

    complaint = _validate(tariff, region)
    if complaint is not None:
        return CheckResult(check_id, label, region, "FAIL", complaint)
    return CheckResult(
        check_id,
        label,
        region,
        "OK",
        f"valid {tariff.valid_from} → {tariff.valid_until}, fee {tariff.yearly_fixed_fee:.2f} EUR/yr ex-VAT",
    )


async def _check_one(session: aiohttp.ClientSession, extractor: WaterExtractor) -> CheckResult:
    if extractor.id in CI_BLOCKED and os.environ.get("GITHUB_ACTIONS") == "true":
        return CheckResult(
            extractor.id, extractor.label, extractor.region, "SKIP", CI_BLOCKED[extractor.id]
        )
    return await _check_fetch(
        session,
        check_id=extractor.id,
        label=extractor.label,
        region=extractor.region,
        fetch=extractor.fetch,
    )


@dataclass(frozen=True)
class PrimaryPathProbe:
    """A page a no-commune fetch reads first, checked without its fallback.

    De Watergroep and Pidpa answer a no-commune install from a default
    commune page and fall back to a stand-in when that page cannot be
    read, so probing ``fetch`` alone reported OK while every such
    household was billed on the fallback. These read the page itself and
    fail loudly.
    """

    check_id: str
    label: str
    region: str
    fetch: Callable[[aiohttp.ClientSession], Awaitable[WaterTariff]]


PRIMARY_PATH_PROBES: tuple[PrimaryPathProbe, ...] = (
    PrimaryPathProbe(
        "de_watergroep:default-page",
        "De Watergroep (default commune page)",
        "flanders",
        lambda s: de_watergroep.fetch_for_commune(s, de_watergroep._DEFAULT_COMMUNE_GUID),
    ),
    PrimaryPathProbe(
        "pidpa:default-page",
        "Pidpa (default commune page)",
        "flanders",
        lambda s: pidpa.fetch_for_commune(s, pidpa._DEFAULT_COMMUNE_SLUG),
    ),
)


async def _check_probe(session: aiohttp.ClientSession, probe: PrimaryPathProbe) -> CheckResult:
    return await _check_fetch(
        session,
        check_id=probe.check_id,
        label=probe.label,
        region=probe.region,
        fetch=probe.fetch,
    )


def _exit_code(results: list[CheckResult]) -> int:
    """Fold the per-extractor statuses into the exit bitmask.

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


async def _run() -> tuple[list[CheckResult], int]:
    async with aiohttp.ClientSession() as session:
        results = [await _check_one(session, e) for e in all_extractors()]
        results += [await _check_probe(session, p) for p in PRIMARY_PATH_PROBES]
    return results, _exit_code(results)


def _render(results: list[CheckResult]) -> str:
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
        lines.append(f"**{len(failed)} of {len(results)} extractors failed.**" + suffix)
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
    return "\n".join(lines)


def main() -> int:
    results, rc = asyncio.run(_run())
    print(_render(results))
    return rc


if __name__ == "__main__":
    sys.exit(main())
