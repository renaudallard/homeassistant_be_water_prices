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

"""Compare each utility's live tariff against the parser's output on the
committed test fixture and report any numerical drift.

The live_check workflow already catches *parser breakage* (page restyled,
selector missed). It does not catch *silent rate drift*: a parser keeps
returning sane numbers, but the upstream page now publishes a different
rate while our fixture still carries the old one. That divergence is
how the Pidpa PDF projection (basis 2,0848 for 2026) ended up shipped
alongside the live HTML rate (2,1888) for several months.

For each (extractor, fixture) pair below, this script:

  1. Calls the extractor's live ``fetch`` (or ``fetch_for_commune``) to
     get today's published numbers.
  2. Runs the same parser on the committed fixture bytes.
  3. Diffs every numerical field of :class:`WaterTariff`.
  4. Prints a markdown report and exits non-zero when any field drifted
     by more than the per-class threshold (rates: 0.001 EUR/m³, fees:
     0.01 EUR/year). Tiny moves are noise; bigger ones mean either the
     utility revised its tariff or the parser regressed.

Run by ``.github/workflows/fixture_drift.yml`` weekly. On drift the
workflow opens or updates a single GitHub issue with this report.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Imports happen after sys.path mutation; the package's __init__ is lazy so
# this works without homeassistant being installed.
from custom_components.be_water_prices.providers import (  # noqa: E402
    WaterTariff,
    agso_knokke,
    aiec,
    aiem,
    aquaduin,
    ciesac,
    cile,
    de_watergroep,
    farys,
    get,
    iden,
    ieg,
    inasep,
    inbw,
    pidpa,
    swde,
    vivaqua,
    water_link,
)
from custom_components.be_water_prices.providers._pdf import (  # noqa: E402
    extract_pdf_text_layout,
)
from custom_components.be_water_prices.providers.base import (  # noqa: E402
    ExtractorError,
    TransientFetchError,
)

FIXTURES = ROOT / "tests" / "fixtures"

# Drift thresholds. Anything below is treated as rounding noise.
RATE_THRESHOLD_EUR_M3 = 0.001
FEE_THRESHOLD_EUR_YEAR = 0.01

# Field classification used by the diff threshold logic. Everything else
# (vat_rate, region, etc.) compares for equality.
_RATE_FIELDS = {
    "basis_eur_per_m3",
    "comfort_eur_per_m3",
    "linear_eur_per_m3",
    "sanering_bovengemeentelijk_eur_per_m3",
    "sanering_gemeentelijk_eur_per_m3",
    "cvd_eur_per_m3",
    "cva_eur_per_m3",
    "fse_eur_per_m3",
}
_FEE_FIELDS = {
    "yearly_fixed_fee",
    "yearly_fixed_fee_per_resident_discount",
}


@dataclass
class FixtureCheck:
    """One (extractor, fixture) pair we want to drift-check."""

    label: str
    fixture: str
    parse_fixture: Callable[[bytes], WaterTariff]
    fetch_live: Callable[[aiohttp.ClientSession], Awaitable[WaterTariff]]


def _t(b: bytes) -> str:
    return b.decode("utf-8")


CHECKS: list[FixtureCheck] = [
    FixtureCheck(
        "VIVAQUA",
        "vivaqua_linear_2026.html",
        lambda b: vivaqua.parse_tariff(_t(b), year=2026),
        lambda s: get("vivaqua").fetch(s),
    ),
    FixtureCheck(
        # The no-commune fetch hits the cookie-driven endpoint with the
        # Halle default GUID; the captured per-commune response for
        # Halle is the matching fixture. The legacy news-article fixture
        # (dewatergroep_2026.html) is only exercised by the fallback path
        # and the unit tests in test_per_commune.py / test_de_watergroep.py.
        "De Watergroep (default commune)",
        "dewatergroep_halle_2026.html",
        lambda b: de_watergroep.parse_commune_tariff(
            _t(b), year=2026, commune_label="Halle (DWG-served default)"
        ),
        lambda s: get("de_watergroep").fetch(s),
    ),
    FixtureCheck(
        "Pidpa (PDF fallback)",
        "pidpa_tariefplan_2025-2030.pdf",
        lambda b: pidpa.parse_tariff(extract_pdf_text_layout(b), year=2026),
        lambda s: get("pidpa").fetch(s),
    ),
    FixtureCheck(
        "Pidpa (per-commune Geel)",
        "pidpa_geel_2026.html",
        lambda b: pidpa.parse_commune_tariff(_t(b), commune_slug="geel", year=2026),
        lambda s: pidpa.fetch_for_commune(s, "geel"),
    ),
    FixtureCheck(
        "Water-link (Antwerpen default)",
        "water_link_2026.pdf",
        lambda b: water_link.parse_tariff(extract_pdf_text_layout(b), year=2026),
        lambda s: get("water_link").fetch(s),
    ),
    FixtureCheck(
        "Aquaduin",
        "aquaduin_2026.pdf",
        lambda b: aquaduin.parse_tariff(extract_pdf_text_layout(b), year=2026),
        lambda s: get("aquaduin").fetch(s),
    ),
    FixtureCheck(
        "AGSO Knokke-Heist",
        "agso_knokke_2026.html",
        lambda b: agso_knokke.parse_tariff(_t(b), year=2026),
        lambda s: get("agso_knokke").fetch(s),
    ),
    FixtureCheck(
        "Farys (Gent-centrum default)",
        "farys_gent_2026.json",
        lambda b: farys.parse_tariff(_t(b), year=2026, municipality_label="25071"),
        lambda s: get("farys").fetch(s),
    ),
    FixtureCheck(
        "SWDE",
        "swde_2026.html",
        lambda b: swde.parse_tariff(_t(b), year=2026),
        lambda s: get("swde").fetch(s),
    ),
    FixtureCheck(
        "inBW",
        "inbw_2026.html",
        lambda b: inbw.parse_tariff(_t(b), year=2026),
        lambda s: get("inbw").fetch(s),
    ),
    FixtureCheck(
        "CILE",
        "cile_2026.html",
        lambda b: cile.parse_tariff(_t(b), year=2026),
        lambda s: get("cile").fetch(s),
    ),
    FixtureCheck(
        "INASEP",
        "inasep_2026.html",
        lambda b: inasep.parse_tariff(_t(b), year=2026),
        lambda s: get("inasep").fetch(s),
    ),
    FixtureCheck(
        "IEG",
        "ieg_2026.html",
        lambda b: ieg.parse_tariff(_t(b), year=2026),
        lambda s: get("ieg").fetch(s),
    ),
    FixtureCheck(
        "AIEM",
        "aiem_2026.html",
        lambda b: aiem.parse_tariff(_t(b), year=2026),
        lambda s: get("aiem").fetch(s),
    ),
    FixtureCheck(
        "AIEC",
        "aiec_callmepower_2026.html",
        lambda b: aiec.parse_tariff(_t(b), year=2026),
        lambda s: get("aiec").fetch(s),
    ),
    FixtureCheck(
        "CIESAC",
        "ciesac_callmepower_2026.html",
        lambda b: ciesac.parse_tariff(_t(b), year=2026),
        lambda s: get("ciesac").fetch(s),
    ),
    FixtureCheck(
        "IDEN",
        "iden_callmepower_2026.html",
        lambda b: iden.parse_tariff(_t(b), year=2026),
        lambda s: get("iden").fetch(s),
    ),
]


@dataclass
class FieldDelta:
    field: str
    fixture: float | None
    live: float | None
    delta: float


@dataclass
class DriftResult:
    check: FixtureCheck
    deltas: list[FieldDelta]
    error: str | None
    skipped: str | None = None  # set when CI cannot reach this utility


# Utilities whose live publication is unreachable from GitHub Actions
# runners. Water-link's CDN returns HTTP 403 to datacenter IP ranges
# (residential IPs work fine; verified via local curl + r.jina.ai
# proxy attempt). Skipping in CI keeps the workflow's signal-to-noise
# clean -- the fixture-based unit tests still cover these parsers, and
# a maintainer can re-run the script from a residential IP on demand.
CI_BLOCKED: dict[str, str] = {
    "Water-link (Antwerpen default)": (
        "Water-link's CDN blocks GitHub Actions IP ranges (HTTP 403). "
        "Reachable from residential IPs; rerun locally to drift-check."
    ),
}


def _is_drifting(field_name: str, fixture_v: float, live_v: float) -> bool:
    delta = abs(live_v - fixture_v)
    if field_name in _RATE_FIELDS:
        return delta > RATE_THRESHOLD_EUR_M3
    if field_name in _FEE_FIELDS:
        return delta > FEE_THRESHOLD_EUR_YEAR
    return delta > 0.0


def _diff(fixture_t: WaterTariff, live_t: WaterTariff) -> list[FieldDelta]:
    deltas: list[FieldDelta] = []
    for f in fields(fixture_t):
        if f.name in {"valid_from", "valid_until", "publication_label", "source_url", "utility"}:
            # These rotate yearly or are cosmetic; the worth-flagging
            # signal lives in the numerical fields below.
            continue
        fx = getattr(fixture_t, f.name)
        lv = getattr(live_t, f.name)
        if isinstance(fx, int | float) and isinstance(lv, int | float):
            if not _is_drifting(f.name, fx, lv):
                continue
            deltas.append(FieldDelta(f.name, fx, lv, lv - fx))
        elif fx != lv:
            deltas.append(FieldDelta(f.name, fx, lv, 0.0))
    return deltas


async def _check_one(session: aiohttp.ClientSession, chk: FixtureCheck) -> DriftResult:
    fixture_path = FIXTURES / chk.fixture
    if not fixture_path.exists():
        return DriftResult(chk, [], error=f"fixture missing: {fixture_path}")
    try:
        fixture_t = chk.parse_fixture(fixture_path.read_bytes())
    except Exception:
        return DriftResult(chk, [], error=f"fixture parse failed:\n{traceback.format_exc()}")

    if chk.label in CI_BLOCKED:
        return DriftResult(chk, [], error=None, skipped=CI_BLOCKED[chk.label])

    try:
        live_t = await chk.fetch_live(session)
    except TransientFetchError as err:
        # A transient upstream blip (5xx / 429 / timeout) is not drift and
        # must not flip the exit code, or the weekly workflow would open a
        # false "fixtures need refresh" issue. Record it as a skip, like
        # live_check classifies its TRANSIENT results.
        return DriftResult(chk, [], error=None, skipped=f"transient upstream failure: {err}")
    except ExtractorError as err:
        return DriftResult(chk, [], error=f"live fetch failed: {err}")
    except Exception:
        return DriftResult(chk, [], error=f"live fetch crashed:\n{traceback.format_exc()}")
    return DriftResult(chk, _diff(fixture_t, live_t), error=None)


async def _run() -> tuple[list[DriftResult], int]:
    async with aiohttp.ClientSession() as session:
        results = [await _check_one(session, c) for c in CHECKS]
    drifted = sum(1 for r in results if r.deltas)
    errored = sum(1 for r in results if r.error is not None)
    # Skipped entries (CI-unreachable utilities) do not flip the exit
    # code: we cannot drift-check them from the runner, but a real
    # user is unaffected. The skip reason still appears in the report
    # so the maintainer can rerun locally.
    return results, 1 if (drifted or errored) else 0


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.4f}"


def _render(results: list[DriftResult]) -> str:
    drifted = [r for r in results if r.deltas]
    errored = [r for r in results if r.error is not None]
    skipped = [r for r in results if r.skipped is not None]
    clean = [r for r in results if not r.deltas and r.error is None and r.skipped is None]

    lines = ["# Water fixture drift report", ""]
    if not drifted and not errored:
        lines.append(
            f"All {len(clean)} fixtures match live within threshold "
            f"({len(skipped)} skipped: see below)."
            if skipped
            else f"All {len(results)} fixtures match live within threshold."
        )
        if skipped:
            lines.append("")
            lines.append("## Skipped (CI-unreachable, no issue raised)")
            lines.append("")
            for r in skipped:
                lines.append(f"- **{r.check.label}**: {r.skipped}")
        return "\n".join(lines)

    lines.append(
        f"**{len(drifted)} drifted, {len(errored)} errored, "
        f"{len(skipped)} skipped, {len(clean)} clean.**"
    )
    lines.append("")
    lines.append(
        "Drift threshold: rates "
        f"> {RATE_THRESHOLD_EUR_M3} EUR/m³, fees "
        f"> {FEE_THRESHOLD_EUR_YEAR} EUR/year."
    )

    if drifted:
        lines.append("")
        lines.append("## Drift")
        lines.append("")
        lines.append("| utility | field | fixture | live | delta |")
        lines.append("|---|---|---|---|---|")
        for r in drifted:
            for d in r.deltas:
                lines.append(
                    f"| {r.check.label} | `{d.field}` | {_fmt(d.fixture)} | {_fmt(d.live)} | {d.delta:+.4f} |"
                )

    if errored:
        lines.append("")
        lines.append("## Errors")
        lines.append("")
        for r in errored:
            lines.append(f"- **{r.check.label}**: {r.error}")

    if skipped:
        lines.append("")
        lines.append("## Skipped (CI-unreachable, no issue raised)")
        lines.append("")
        for r in skipped:
            lines.append(f"- **{r.check.label}**: {r.skipped}")

    if clean:
        lines.append("")
        lines.append(f"## Clean ({len(clean)})")
        lines.append("")
        lines.append(", ".join(r.check.label for r in clean))

    return "\n".join(lines)


def main() -> int:
    results, rc = asyncio.run(_run())
    print(_render(results))
    return rc


if __name__ == "__main__":
    sys.exit(main())
