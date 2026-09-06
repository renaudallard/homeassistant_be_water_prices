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


def test_empty_sanering_row_raises_instead_of_taking_the_comforttarief() -> None:
    """A row with no amount must not be filled in from the row below.

    A commune with no municipal sewerage line renders the label with an
    empty amount block. Bridging the gap to the next euro sign lands on
    the comforttarief, which is exactly twice the basistarief and
    plausible enough to ship as a real rate.
    """
    from custom_components.be_water_prices.providers.base import ExtractorError

    raw = fixture_html("farys_gent_2026.json")
    # Empty the gemeentelijke basistarief cells, euro signs included.
    blanked = raw.replace("\\u0026euro; 1,9572", "").replace("\\u0026euro; 2,0746", "")
    assert blanked != raw
    with pytest.raises(ExtractorError, match="gemeentelijke saneringsbijdrage"):
        parse_tariff(blanked, year=2026)


def test_the_year_comes_from_the_page_not_the_clock() -> None:
    """Farys flags the period it is showing; that is the tariff's year.

    Stamping the clock's year meant a page serving next year's card
    early was dated as this year's, and a page still stuck on last
    year's looked current -- so the stale-snapshot check could never
    fire on either.
    """
    raw = fixture_html("farys_gent_2026.json")
    assert parse_tariff(raw).valid_from.year == 2026
    # Move the active flag onto the other period button.
    active = "\\u003Cli class=\\u0022active\\u0022\\u003E"
    inactive = "\\u003Cli class=\\u0022\\u0022\\u003E"
    assert raw.count(active) == 1 and raw.count(inactive) == 1
    switched = (
        raw.replace(active, "<<TMP>>", 1).replace(inactive, active, 1).replace("<<TMP>>", inactive)
    )
    assert parse_tariff(switched).valid_from.year == 2025

    # An explicit year still wins over the page.
    assert parse_tariff(raw, year=2030).valid_from.year == 2030


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


def test_a_card_served_ahead_of_the_calendar_is_priced_and_flagged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The page dates the card; a card published early is applied, and said so."""
    import logging

    raw = fixture_html("farys_gent_2026.json")
    needle = "value=\\u00222026\\u0022\\u003E2026"
    assert raw.count(needle) == 1, "the active period button moved; the test needs updating"
    early = raw.replace(needle, "value=\\u00222027\\u0022\\u003E2027")
    with caplog.at_level(logging.WARNING):
        t = parse_tariff(early)
    assert t.valid_from.year == 2027
    assert "2027 card while the calendar says" in caplog.text
