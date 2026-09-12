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

"""fixture_drift: transient upstream failures must not be treated as errors."""

from __future__ import annotations

from datetime import date

import aiohttp
import pytest

from custom_components.be_water_prices.providers.base import (
    ExtractorError,
    TransientFetchError,
    WaterTariff,
)
from scripts.fixture_drift import FixtureCheck, _check_one


def _dummy_tariff(_payload: bytes) -> WaterTariff:
    return WaterTariff(
        utility="test",
        region="flanders",
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 12, 31),
        publication_label="test",
        source_url="https://example.invalid/",
        yearly_fixed_fee=0.0,
    )


async def test_transient_live_fetch_is_skipped_not_errored() -> None:
    async def _raise_transient(_session: aiohttp.ClientSession) -> WaterTariff:
        raise TransientFetchError("HTTP 503")

    chk = FixtureCheck(
        label="TEST",
        fixture="vivaqua_linear_2026.html",
        parse_fixture=_dummy_tariff,
        fetch_live=_raise_transient,
    )
    result = await _check_one(session=None, chk=chk)  # type: ignore[arg-type]
    # error is None -> not counted in the exit code -> no false GitHub issue.
    assert result.error is None
    assert result.skipped is not None
    assert "transient" in result.skipped.lower()


async def test_hard_live_fetch_is_errored() -> None:
    async def _raise_hard(_session: aiohttp.ClientSession) -> WaterTariff:
        raise ExtractorError("HTTP 404 gone")

    chk = FixtureCheck(
        label="TEST",
        fixture="vivaqua_linear_2026.html",
        parse_fixture=_dummy_tariff,
        fetch_live=_raise_hard,
    )
    result = await _check_one(session=None, chk=chk)  # type: ignore[arg-type]
    assert result.error is not None
    assert result.skipped is None


async def test_transient_skip_marks_the_run_incomplete() -> None:
    """A blip must not be reported as a clean, drift-free run.

    The workflow comments "Drift cleared. Safe to close." on any open drift
    issue when the run comes back clean. A utility that was merely
    unreachable was never actually checked, so that comment would be wrong.
    """

    async def _raise_transient(_session: aiohttp.ClientSession) -> WaterTariff:
        raise TransientFetchError("HTTP 503")

    chk = FixtureCheck(
        label="TEST",
        fixture="vivaqua_linear_2026.html",
        parse_fixture=_dummy_tariff,
        fetch_live=_raise_transient,
    )
    result = await _check_one(session=None, chk=chk)  # type: ignore[arg-type]
    assert result.transient is True


async def test_ci_blocked_skip_does_not_mark_the_run_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The permanently blocked utility is expected, so it stays quiet.

    Treating it as incomplete would suppress the cleared comment forever.
    """
    from scripts.fixture_drift import CI_BLOCKED

    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    async def _never_called(_session: aiohttp.ClientSession) -> WaterTariff:
        raise AssertionError("must not fetch a CI-blocked utility")

    label = next(iter(CI_BLOCKED))
    chk = FixtureCheck(
        label=label,
        fixture="vivaqua_linear_2026.html",
        parse_fixture=_dummy_tariff,
        fetch_live=_never_called,
    )
    result = await _check_one(session=None, chk=chk)  # type: ignore[arg-type]
    assert result.skipped is not None
    assert result.transient is False


async def test_a_ci_blocked_check_runs_off_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """The skip message says to rerun locally, so a local run must not skip too."""
    from scripts.fixture_drift import CI_BLOCKED

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    fetched = False

    async def _fetch(_session: aiohttp.ClientSession) -> WaterTariff:
        nonlocal fetched
        fetched = True
        return _dummy_tariff(b"")

    chk = FixtureCheck(
        label=next(iter(CI_BLOCKED)),
        fixture="vivaqua_linear_2026.html",
        parse_fixture=_dummy_tariff,
        fetch_live=_fetch,
    )
    result = await _check_one(session=None, chk=chk)  # type: ignore[arg-type]
    assert fetched
    assert result.skipped is None
    assert result.error is None
    assert result.deltas == []


def test_every_ci_blocked_label_matches_a_real_check() -> None:
    """The skip is keyed by a free-text label, so it can silently desync.

    Renaming a CHECKS label while tidying leaves CI_BLOCKED pointing at
    nothing: the utility is fetched on the runner after all, its CDN
    answers 403, and the weekly workflow opens a "fixtures need refresh"
    issue every Sunday for a utility that is not broken. The existing
    skip test builds its FixtureCheck from CI_BLOCKED itself, so it
    cannot notice.
    """
    from scripts.fixture_drift import CHECKS, CI_BLOCKED

    labels = {check.label for check in CHECKS}
    assert set(CI_BLOCKED) <= labels, (
        f"CI_BLOCKED names labels no check carries: {sorted(set(CI_BLOCKED) - labels)}"
    )


def test_diff_reports_a_rate_that_moved_and_nothing_that_did_not() -> None:
    from dataclasses import replace

    from scripts.fixture_drift import _diff

    fixture = _dummy_tariff(b"")
    assert _diff(fixture, fixture) == []
    moved = replace(fixture, basis_eur_per_m3=2.0)
    deltas = _diff(replace(fixture, basis_eur_per_m3=2.0111), moved)
    assert [d.field for d in deltas] == ["basis_eur_per_m3"]
    # A move inside the rounding threshold is noise.
    assert _diff(replace(fixture, basis_eur_per_m3=2.0005), moved) == []


@pytest.mark.parametrize(
    ("outcome", "expected_rc"),
    [("clean", 0), ("transient", 2), ("hard", 1)],
)
async def test_run_exit_code_follows_the_worst_outcome(
    monkeypatch: pytest.MonkeyPatch, outcome: str, expected_rc: int
) -> None:
    """Exit 2 keeps a blip from either opening or closing an issue."""
    from scripts import fixture_drift

    async def _fetch(_session: aiohttp.ClientSession) -> WaterTariff:
        if outcome == "transient":
            raise TransientFetchError("HTTP 503")
        if outcome == "hard":
            raise ExtractorError("gone")
        return _dummy_tariff(b"")

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        fixture_drift,
        "CHECKS",
        [
            FixtureCheck(
                label="TEST",
                fixture="vivaqua_linear_2026.html",
                parse_fixture=_dummy_tariff,
                fetch_live=_fetch,
            )
        ],
    )
    _results, rc, cache = await fixture_drift._run()
    assert rc == expected_rc
    assert cache is None
