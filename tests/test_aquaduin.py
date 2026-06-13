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

"""Aquaduin extractor against the captured 2026 PDF fixture."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
from custom_components.be_water_prices.providers.aquaduin import parse_tariff
from tests import fixture_bytes


def _pdf_text() -> str:
    return extract_pdf_text_layout(fixture_bytes("aquaduin_2026.pdf"))


def test_parses_2026_basis_and_comfort() -> None:
    t = parse_tariff(_pdf_text(), year=2026)
    assert t.basis_eur_per_m3 == 5.9908
    assert t.comfort_eur_per_m3 == 11.9816  # = 2× basis (VMM)
    assert t.yearly_fixed_fee == 100.0
    assert t.yearly_fixed_fee_per_resident_discount == 20.0
    # Sanering stays 0 -- the published 5.9908 is the integrated rate.
    assert t.sanering_gemeentelijk_eur_per_m3 == 0.0
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 0.0


def test_raises_when_pdf_text_is_garbage() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("nothing here", year=2026)


async def test_transient_error_propagates_not_masked() -> None:
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import aquaduin
    from custom_components.be_water_prices.providers.base import TransientFetchError

    with (
        patch.object(
            aquaduin,
            "fetch_pdf_text_layout",
            new=AsyncMock(side_effect=TransientFetchError("timeout")),
        ) as mock,
        pytest.raises(TransientFetchError),
    ):
        await aquaduin.fetch(session=None)  # type: ignore[arg-type]
    # Must not have masked it by falling back to last year's PDF.
    assert mock.await_count == 1


async def test_hard_error_falls_back_to_prior_year() -> None:
    from datetime import date
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import aquaduin

    text = extract_pdf_text_layout(fixture_bytes("aquaduin_2026.pdf"))
    with patch.object(
        aquaduin,
        "fetch_pdf_text_layout",
        new=AsyncMock(side_effect=[ExtractorError("HTTP 404"), text]),
    ) as mock:
        tariff = await aquaduin.fetch(session=None)  # type: ignore[arg-type]
    assert mock.await_count == 2
    # Prior-year fallback pushes valid_until to March 31 of the target year.
    assert tariff.valid_until == date(date.today().year, 3, 31)
