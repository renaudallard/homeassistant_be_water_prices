"""Unit tests for the vendored PDF helpers (pure functions only)."""

from __future__ import annotations

from datetime import date

from custom_components.be_water_prices.providers._pdf import (
    fold_accents,
    parse_valid_until,
    to_float,
)


def test_to_float_handles_belgian_comma() -> None:
    assert to_float("15,93") == 15.93
    assert to_float("0.102") == 0.102


def test_to_float_strips_unicode_separators() -> None:
    # NBSP-separated thousands: Belgian PDFs use this for "5 029" etc.
    assert to_float("5 029,5") == 5029.5


def test_fold_accents_lowercases_and_strips_diacritics() -> None:
    assert fold_accents("Août 2026") == "aout 2026"
    assert fold_accents("Décembre") == "decembre"


def test_parse_valid_until_spelled_out_dutch() -> None:
    assert parse_valid_until("Geldig tot 30 april 2026") == date(2026, 4, 30)


def test_parse_valid_until_numeric_french() -> None:
    assert parse_valid_until("Valable jusqu'au 31/12/2026") == date(2026, 12, 31)


def test_parse_valid_until_returns_none_without_keyword() -> None:
    assert parse_valid_until("Random date 30 april 2026 unrelated") is None
