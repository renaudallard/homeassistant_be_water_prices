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

"""ZDE query error handling in the postcode refresh script."""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import patch

import pytest

from scripts.refresh_postcodes import ZdeQueryError, query_zde_for_centroid


class _Resp(io.BytesIO):
    """Minimal urlopen context manager over a canned body."""

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def _urlopen_returning(payload: dict[str, Any]) -> Any:
    return lambda *_a, **_kw: _Resp(json.dumps(payload).encode())


def test_hit_returns_the_distributor() -> None:
    payload = {"features": [{"attributes": {"DISTRIBUTEUR": "SWDE"}}]}
    with patch("urllib.request.urlopen", _urlopen_returning(payload)):
        assert query_zde_for_centroid(4.5, 50.5) == "SWDE"


def test_empty_feature_list_is_genuine_no_coverage() -> None:
    with patch("urllib.request.urlopen", _urlopen_returning({"features": []})):
        assert query_zde_for_centroid(4.5, 50.5) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"error": {"code": 400, "message": "Invalid or missing input parameters."}},
        {"error": {"code": 404, "message": "Service not found."}},
        {"error": {"code": 400, "message": "Failed to execute query."}},
    ],
)
def test_error_payload_is_not_read_as_no_coverage(payload: dict[str, Any]) -> None:
    """ArcGIS answers HTTP 200 with an error body, so urlopen does not raise.

    Left unchecked the missing "features" key reads as an empty list, i.e.
    the postcode falls outside every distribution zone, and a renamed layer
    would silently shrink the generated map instead of failing loudly.
    """
    with patch("urllib.request.urlopen", _urlopen_returning(payload)), pytest.raises(ZdeQueryError):
        query_zde_for_centroid(4.5, 50.5)


def test_an_interstitial_page_aborts_instead_of_emitting_an_empty_carveout() -> None:
    """A 200 that is not the tariff page must not become a committed answer.

    _fetch only raises on an HTTP error status, so a bot check or a
    consent wall reads as a page with no <option> elements. The carve-out
    then renders as a valid empty frozenset, and pasting it flips every
    postcode in it to the other operator.
    """
    from unittest.mock import patch

    import pytest

    from scripts import refresh_postcodes as R

    interstitial = "<html><body><h1>Just a moment...</h1></body></html>"
    with (
        patch.object(R, "_fetch", lambda _url: interstitial),
        pytest.raises(R.ScrapeTooThinError, match="De Watergroep"),
    ):
        R.build_dwg_flanders_carveout()


def test_a_thin_farys_scrape_aborts_too() -> None:
    """The mirror case: a good DWG page and a broken Farys one."""
    from pathlib import Path
    from unittest.mock import patch

    import pytest

    from scripts import refresh_postcodes as R

    real = (Path(__file__).parent / "fixtures" / "dewatergroep_tarieven_2026.html").read_text(
        encoding="utf-8", errors="replace"
    )

    def _fetch(url: str) -> str:
        return real if "dewatergroep" in url else "<html><body>nope</body></html>"

    with (
        patch.object(R, "_fetch", _fetch),
        pytest.raises(R.ScrapeTooThinError, match="Farys"),
    ):
        R.build_dwg_flanders_carveout()
