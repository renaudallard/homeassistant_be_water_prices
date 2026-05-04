"""De Watergroep extractor against the captured 2026 news article."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.de_watergroep import (
    EXTRACTOR,
    parse_tariff,
)
from tests import fixture_html


def test_extractor_metadata() -> None:
    assert EXTRACTOR.id == "de_watergroep"
    assert EXTRACTOR.label == "De Watergroep"
    assert EXTRACTOR.region == "flanders"


def test_parses_2026_basistarief() -> None:
    t = parse_tariff(fixture_html("dewatergroep_2026.html"), year=2026)
    assert t.basis_eur_per_m3 == 2.9521
    assert t.comfort_eur_per_m3 == 5.9042  # 2× basis


def test_uses_drinkwater_only_vastrecht() -> None:
    # The news article only covers the drinkwater leg, so vastrecht is
    # 50 EUR / 10 EUR-per-persoon (not the full 100/20 integrale fee).
    t = parse_tariff(fixture_html("dewatergroep_2026.html"), year=2026)
    assert t.yearly_fixed_fee == 50.0
    assert t.yearly_fixed_fee_per_resident_discount == 10.0
    # Sanering stays at 0 -- per-commune data is a v0.4 polish.
    assert t.sanering_gemeentelijk_eur_per_m3 == 0.0
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 0.0


def test_raises_on_missing_basistarief_phrase() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>nothing here</body></html>", year=2026)
