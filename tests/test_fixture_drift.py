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
