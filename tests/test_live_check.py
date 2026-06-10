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

"""Tests for the live-check transient-vs-real failure classification.

A brief upstream hiccup (timeout, connection reset, HTTP 5xx) must be
reported as TRANSIENT so the daily workflow retries it but does not open
a false GitHub issue; only a genuine parse / shape / 4xx failure is FAIL.
"""

from __future__ import annotations

from types import SimpleNamespace

import aiohttp
import pytest

from custom_components.be_water_prices.providers._pdf import _http_error, fetch_text
from custom_components.be_water_prices.providers.base import (
    ExtractorError,
    TransientFetchError,
)
from scripts.live_check import (
    EXIT_REAL_FAIL,
    EXIT_TRANSIENT,
    CheckResult,
    _check_one,
    _exit_code,
    _render,
)

# --- HTTP status mapping -----------------------------------------------------


def test_http_error_5xx_and_429_are_transient() -> None:
    for status in (500, 502, 503, 504, 429):
        assert isinstance(_http_error("https://x", status), TransientFetchError)


def test_http_error_4xx_is_real_not_transient() -> None:
    # 404 (moved) and 403 (forbidden) mean the page changed: a real
    # failure that should still open an issue.
    for status in (400, 403, 404, 410):
        err = _http_error("https://x", status)
        assert isinstance(err, ExtractorError)
        assert not isinstance(err, TransientFetchError)


# --- network errors are wrapped as transient ---------------------------------


class _RaisingCtx:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def __aenter__(self) -> object:
        raise self._exc

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _RaisingSession:
    """Minimal stand-in whose ``get`` blows up inside the request."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def get(self, *args: object, **kwargs: object) -> _RaisingCtx:
        return _RaisingCtx(self._exc)


async def test_fetch_text_wraps_timeout_as_transient() -> None:
    session = _RaisingSession(TimeoutError())
    with pytest.raises(TransientFetchError):
        await fetch_text(session, "https://example.test")  # type: ignore[arg-type]


async def test_fetch_text_wraps_connection_error_as_transient() -> None:
    session = _RaisingSession(aiohttp.ClientConnectionError("reset"))
    with pytest.raises(TransientFetchError):
        await fetch_text(session, "https://example.test")  # type: ignore[arg-type]


# --- _check_one status classification ----------------------------------------


def _fake_extractor(fetch: object) -> SimpleNamespace:
    return SimpleNamespace(id="x", label="X", region="flanders", fetch=fetch)


async def test_check_one_transient_fetch_error_is_transient() -> None:
    async def fetch(_session: object) -> None:
        raise TransientFetchError("HTTP 503 fetching https://x")

    result = await _check_one(None, _fake_extractor(fetch))  # type: ignore[arg-type]
    assert result.status == "TRANSIENT"


async def test_check_one_extractor_error_is_fail() -> None:
    async def fetch(_session: object) -> None:
        raise ExtractorError("could not locate the year header")

    result = await _check_one(None, _fake_extractor(fetch))  # type: ignore[arg-type]
    assert result.status == "FAIL"


async def test_check_one_unexpected_exception_is_fail() -> None:
    async def fetch(_session: object) -> None:
        raise ValueError("boom")

    result = await _check_one(None, _fake_extractor(fetch))  # type: ignore[arg-type]
    assert result.status == "FAIL"


# --- exit-code bitmask -------------------------------------------------------


def _result(status: str) -> CheckResult:
    return CheckResult("x", "X", "flanders", status, "detail")


def test_exit_code_transient_only_sets_bit_2_not_bit_1() -> None:
    rc = _exit_code([_result("OK"), _result("TRANSIENT"), _result("SKIP")])
    assert rc & EXIT_REAL_FAIL == 0
    assert rc & EXIT_TRANSIENT


def test_exit_code_real_fail_sets_bit_1() -> None:
    rc = _exit_code([_result("FAIL"), _result("TRANSIENT")])
    assert rc & EXIT_REAL_FAIL
    assert rc & EXIT_TRANSIENT


def test_exit_code_all_clean_is_zero() -> None:
    assert _exit_code([_result("OK"), _result("OK"), _result("SKIP")]) == 0


# --- report rendering --------------------------------------------------------


def test_render_reports_transient_without_a_failure_banner() -> None:
    out = _render([_result("OK"), _result("TRANSIENT")])
    assert "No regressions" in out
    assert "1 transient" in out
    assert "extractors failed" not in out
