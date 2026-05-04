"""Pidpa extractor against the captured Tariefplan PDF fixture."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
from custom_components.be_water_prices.providers.pidpa import EXTRACTOR, parse_tariff
from tests import fixture_bytes


def _pdf_text() -> str:
    return extract_pdf_text_layout(fixture_bytes("pidpa_tariefplan_2025-2030.pdf"))


def test_extractor_metadata() -> None:
    assert EXTRACTOR.id == "pidpa"
    assert EXTRACTOR.label == "Pidpa"
    assert EXTRACTOR.region == "flanders"


def test_parses_2026_drinkwater_block() -> None:
    t = parse_tariff(_pdf_text(), year=2026)
    assert t.basis_eur_per_m3 == 2.0848
    assert t.comfort_eur_per_m3 == 4.1696  # exactly 2× basis per VMM
    assert t.region == "flanders"
    assert t.valid_from.year == 2026
    assert t.valid_until is not None and t.valid_until.year == 2026


def test_parses_saneringsbijdragen() -> None:
    t = parse_tariff(_pdf_text(), year=2026)
    # gemeentelijke (afvoer) > bovengemeentelijke (zuivering) -- the parser
    # used to swap them when anchoring on the substring "gemeentelijke".
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.6533
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.1809


def test_uses_standard_flemish_vastrecht_structure() -> None:
    t = parse_tariff(_pdf_text(), year=2026)
    assert t.yearly_fixed_fee == 100.0  # 50 + 30 + 20
    assert t.yearly_fixed_fee_per_resident_discount == 20.0  # 10 + 6 + 4


def test_falls_back_to_last_year_when_target_outside_window() -> None:
    # Tariefplan covers 2025-2030; asking for 2031 should clamp to 2030.
    t = parse_tariff(_pdf_text(), year=2031)
    assert t.valid_from.year == 2030


def test_raises_when_pdf_text_is_garbage() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("nothing here", year=2026)
