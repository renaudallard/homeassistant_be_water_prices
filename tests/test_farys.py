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

"""Farys extractor against the captured Drupal AJAX response."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.farys import parse_tariff
from tests import fixture_html


def test_parses_2026_gent_centrum_rates() -> None:
    t = parse_tariff(fixture_html("farys_gent_2026.json"), year=2026)
    assert t.basis_eur_per_m3 == 3.0058  # Gent-centrum drinkwater
    assert t.comfort_eur_per_m3 == 6.0116  # = 2× basis
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.9572  # afvoer
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019  # zuivering
    assert t.yearly_fixed_fee == 100.0
    assert t.yearly_fixed_fee_per_resident_discount == 20.0


def test_raises_when_response_is_not_json() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("not json at all")


def test_raises_when_no_insert_command_present() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff('[{"command":"settings","settings":{}}]')


@pytest.mark.parametrize("body", ['{"error": "nope"}', "42", '"a bare string"'])
def test_raises_when_response_is_not_a_command_list(body: str) -> None:
    # Valid JSON that is not a list of command dicts must surface as
    # ExtractorError, not a raw AttributeError / TypeError.
    with pytest.raises(ExtractorError):
        parse_tariff(body)


def test_unservable_labels_dropped_from_list_communes() -> None:
    # Farys's dropdown carries 23 "phantom" commune options at split
    # postcodes where DWG is the actual operator; picking one crashes
    # because the AJAX endpoint returns no tariff data. list_communes
    # must drop them so the dropdown never offers a crashing option.
    import asyncio
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers.farys import (
        _UNSERVABLE_COMMUNE_LABELS,
        list_communes,
    )

    # Build a synthetic dropdown that contains one phantom, one valid.
    phantom = next(iter(_UNSERVABLE_COMMUNE_LABELS))
    fake_html = (
        '<select name="municipality">'
        f'<option value="1">{phantom}</option>'
        '<option value="2">9000 - Gent (Gent)</option>'
        "</select>"
    )
    with patch(
        "custom_components.be_water_prices.providers.farys.fetch_text",
        new=AsyncMock(return_value=fake_html),
    ):
        out = asyncio.run(list_communes(session=None))  # type: ignore[arg-type]
    labels = {c.label for c in out}
    assert phantom not in labels
    assert "9000 - Gent (Gent)" in labels


def test_unservable_labels_blocklist_holds_known_phantoms() -> None:
    # Pin the floor: the blocklist must keep these specific entries
    # that the smoke test against the live Farys AJAX endpoint flagged
    # as "no insert command with tariff data".
    from custom_components.be_water_prices.providers.farys import _UNSERVABLE_COMMUNE_LABELS

    must_include = {
        "1500 - Halle (Halle)",
        "8020 - Hertsberge (Oostkamp)",
        "8450 - Bredene (Bredene)",
        "9080 - Beervelde (Lochristi)",
        "9550 - Sint-Antelinks (Herzele)",
    }
    missing = must_include - _UNSERVABLE_COMMUNE_LABELS
    assert not missing, f"blocklist regression: {missing} disappeared"


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status

    async def text(self) -> str:
        return ""


class _FakeAjaxCtx:
    def __init__(self, *, status: int | None = None, exc: BaseException | None = None) -> None:
        self._status = status
        self._exc = exc

    async def __aenter__(self) -> _FakeResp:
        if self._exc is not None:
            raise self._exc
        assert self._status is not None
        return _FakeResp(self._status)

    async def __aexit__(self, *_a: object) -> bool:
        return False


class _FakePostSession:
    def __init__(self, *, status: int | None = None, exc: BaseException | None = None) -> None:
        self._status = status
        self._exc = exc

    def post(self, *_a: object, **_k: object) -> _FakeAjaxCtx:
        return _FakeAjaxCtx(status=self._status, exc=self._exc)


async def test_post_for_commune_maps_5xx_to_transient() -> None:
    from custom_components.be_water_prices.providers import farys
    from custom_components.be_water_prices.providers.base import TransientFetchError

    with pytest.raises(TransientFetchError):
        await farys._post_for_commune(_FakePostSession(status=503), "x")  # type: ignore[arg-type]


async def test_post_for_commune_maps_timeout_to_transient() -> None:
    from custom_components.be_water_prices.providers import farys
    from custom_components.be_water_prices.providers.base import TransientFetchError

    with pytest.raises(TransientFetchError):
        await farys._post_for_commune(  # type: ignore[arg-type]
            _FakePostSession(exc=TimeoutError()), "x"
        )


async def test_post_for_commune_4xx_stays_permanent() -> None:
    from custom_components.be_water_prices.providers import farys
    from custom_components.be_water_prices.providers.base import (
        ExtractorError,
        TransientFetchError,
    )

    with pytest.raises(ExtractorError) as exc:
        await farys._post_for_commune(_FakePostSession(status=404), "x")  # type: ignore[arg-type]
    assert not isinstance(exc.value, TransientFetchError)
