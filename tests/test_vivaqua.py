"""VIVAQUA extractor against the captured 2026 fixture."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.vivaqua import (
    EXTRACTOR,
    parse_tariff,
)
from tests import fixture_html


def test_extractor_is_registered_with_the_right_metadata() -> None:
    assert EXTRACTOR.id == "vivaqua"
    assert EXTRACTOR.label == "VIVAQUA"
    assert EXTRACTOR.region == "brussels"


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
