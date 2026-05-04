"""SWDE extractor against the captured 2026 fixture."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.const import (
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.swde import EXTRACTOR, parse_tariff
from tests import fixture_html


def test_extractor_metadata() -> None:
    assert EXTRACTOR.id == "swde"
    assert EXTRACTOR.label == "SWDE"
    assert EXTRACTOR.region == "wallonia"


def test_parses_2026_components() -> None:
    t = parse_tariff(fixture_html("swde_2026.html"), year=2026)

    assert t.cvd_eur_per_m3 == 3.24
    # CVA / FSE are stored from const.py (not the page) but we still verify
    # the page hadn't drifted: the parser warns on drift > 0.005, and the
    # constants are the source of truth.
    assert t.cva_eur_per_m3 == WALLONIA_CVA_EUR_PER_M3
    assert t.fse_eur_per_m3 == WALLONIA_FSE_EUR_PER_M3

    # Redevance is the regulator-defined 20·CVD + 30·CVA.
    assert round(t.yearly_fixed_fee, 2) == round(20 * 3.24 + 30 * WALLONIA_CVA_EUR_PER_M3, 2)

    assert t.utility == "swde"
    assert t.region == "wallonia"
    assert t.valid_from.year == 2026
    assert t.valid_until is not None and t.valid_until.year == 2026
    # Wallonia uses CVD-based pricing; the Flanders / Brussels rate slots
    # stay None.
    assert t.linear_eur_per_m3 is None
    assert t.basis_eur_per_m3 is None
    assert t.comfort_eur_per_m3 is None
    assert t.vat_rate == 0.06
    assert t.source_url.startswith("https://www.swde.be/")


def test_raises_when_cvd_missing() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>nothing here</body></html>")
