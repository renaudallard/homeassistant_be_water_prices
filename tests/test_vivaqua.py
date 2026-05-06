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

"""VIVAQUA extractor against the captured 2026 fixture."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.vivaqua import parse_tariff
from tests import fixture_html


def test_parses_2026_rates_ex_vat() -> None:
    html = fixture_html("vivaqua_linear_2026.html")
    t = parse_tariff(html, year=2026)

    # Ex-VAT components should reconstruct the published VAT-incl headlines
    # to the cent.
    assert round(t.linear_eur_per_m3 * 1.06, 2) == 2.62
    assert round(t.sanering_gemeentelijk_eur_per_m3 * 1.06, 2) == 2.73
    assert round((t.linear_eur_per_m3 + t.sanering_gemeentelijk_eur_per_m3) * 1.06, 2) == 5.35
    assert round(t.yearly_fixed_fee * 1.06, 2) == 40.23

    assert t.utility == "vivaqua"
    assert t.region == "brussels"
    assert t.valid_from.year == 2026
    assert t.valid_until is not None and t.valid_until.year == 2026
    assert t.basis_eur_per_m3 is None and t.comfort_eur_per_m3 is None
    assert t.vat_rate == 0.06
    assert t.publication_label.startswith("Price from January 1st 2026")
    assert t.source_url.startswith("https://www.vivaqua.be/")


def test_falls_back_to_previous_year_when_target_missing() -> None:
    html = fixture_html("vivaqua_linear_2026.html")
    # Asking for 2027 should fall back to 2026 (the most recent year present).
    t = parse_tariff(html, year=2027)
    assert t.valid_from.year == 2026


def test_raises_when_no_year_table_present() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>nothing here</body></html>", year=2026)
