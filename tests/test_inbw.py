"""inBW extractor against the captured 2026 fixture."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.const import (
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from custom_components.be_water_prices.pricing import compute_annual_cost
from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.inbw import EXTRACTOR, parse_tariff
from tests import fixture_html


def test_extractor_metadata() -> None:
    assert EXTRACTOR.id == "inbw"
    assert EXTRACTOR.label == "inBW"
    assert EXTRACTOR.region == "wallonia"


def test_parses_2026_components() -> None:
    t = parse_tariff(fixture_html("inbw_2026.html"), year=2026)
    assert t.cvd_eur_per_m3 == 2.6
    assert t.cva_eur_per_m3 == WALLONIA_CVA_EUR_PER_M3
    assert t.fse_eur_per_m3 == WALLONIA_FSE_EUR_PER_M3
    # Redevance = 20·CVD + 30·CVA = 52 + 82.44 = 134.44
    assert round(t.yearly_fixed_fee, 2) == round(20 * 2.6 + 30 * WALLONIA_CVA_EUR_PER_M3, 2)


def test_matches_published_facture_for_100_m3() -> None:
    # The inBW page itself shows 584.261 € TVAC for a 100 m³ sample bill.
    # Our cost engine has to reproduce that to the cent or we know either
    # the parser or the Wallonia tier math is off.
    t = parse_tariff(fixture_html("inbw_2026.html"), year=2026)
    assert compute_annual_cost(t, 100, 1) == 584.26


def test_raises_when_table_missing() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>no table here</body></html>")
