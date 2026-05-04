#!/usr/bin/env python3
"""Live end-to-end check of every water-utility extractor.

Walks every registered :class:`WaterExtractor`, hits the utility's real
publication, parses the result, and verifies the snapshot is structurally
sane (region matches, fee in plausible range, at least one volumetric
component populated). Prints a markdown report to stdout and exits
non-zero on the first failure.

Run by ``.github/workflows/live_check.yml`` daily; on persistent failure
the workflow opens or updates a GitHub issue with this report attached.

Exit code semantics (mirrors the sibling integration so the workflow
script is shared, even though water never trips the bit-1 path today):

    0 = all extractors green
    1 = at least one extractor failed (parse error, network error, sanity
        check missed) -- worth retrying in the workflow
"""

from __future__ import annotations

import asyncio
import sys
import traceback
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
)
from custom_components.be_water_prices.providers.base import ExtractorError  # noqa: E402

# Loose plausibility windows. Anything outside these almost certainly
# means the parser misread a different number on the page.
MIN_FEE_EUR_YEAR = 5.0
MAX_FEE_EUR_YEAR = 500.0
MIN_RATE_EUR_M3 = 0.5
MAX_RATE_EUR_M3 = 20.0


@dataclass
class CheckResult:
    extractor_id: str
    label: str
    region: str
    ok: bool
    detail: str  # human-readable summary on success, traceback on failure


def _validate(tariff: WaterTariff, extractor: WaterExtractor) -> str | None:
    """Return ``None`` if sane, a complaint string if not."""
    if tariff.region != extractor.region:
        return (
            f"region mismatch: extractor says {extractor.region!r}, tariff says {tariff.region!r}"
        )
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


async def _check_one(session: aiohttp.ClientSession, extractor: WaterExtractor) -> CheckResult:
    try:
        tariff = await extractor.fetch(session)
    except ExtractorError as err:
        return CheckResult(extractor.id, extractor.label, extractor.region, False, str(err))
    except Exception:  # top-level: report anything unexpected as a failure row
        return CheckResult(
            extractor.id,
            extractor.label,
            extractor.region,
            False,
            traceback.format_exc(),
        )

    complaint = _validate(tariff, extractor)
    if complaint is not None:
        return CheckResult(extractor.id, extractor.label, extractor.region, False, complaint)
    return CheckResult(
        extractor.id,
        extractor.label,
        extractor.region,
        True,
        f"valid {tariff.valid_from} → {tariff.valid_until}, fee {tariff.yearly_fixed_fee:.2f} EUR/yr ex-VAT",
    )


async def _run() -> tuple[list[CheckResult], int]:
    async with aiohttp.ClientSession() as session:
        results = [await _check_one(session, e) for e in all_extractors()]
    rc = 0 if all(r.ok for r in results) else 1
    return results, rc


def _render(results: list[CheckResult]) -> str:
    lines = ["# Water extractor live check", ""]
    lines.append("| utility | region | status | detail |")
    lines.append("|---|---|---|---|")
    for r in results:
        status = "OK" if r.ok else "FAIL"
        detail = r.detail.replace("\n", "<br>").replace("|", "\\|")
        lines.append(f"| {r.label} | {r.region} | {status} | {detail} |")
    failed = [r for r in results if not r.ok]
    if failed:
        lines.append("")
        lines.append(f"**{len(failed)} of {len(results)} extractors failed.**")
    else:
        lines.append("")
        lines.append(f"All {len(results)} extractors green.")
    return "\n".join(lines)


def main() -> int:
    results, rc = asyncio.run(_run())
    print(_render(results))
    return rc


if __name__ == "__main__":
    sys.exit(main())
