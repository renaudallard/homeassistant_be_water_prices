"""providers/base.py: the tariff as the card archive writes and reads it."""

from __future__ import annotations

import json
from datetime import date

import pytest

from custom_components.be_water_prices.providers.base import (
    WaterTariff,
    tariff_from_dict,
    tariff_to_dict,
)


def _tariff(**overrides: object) -> WaterTariff:
    values: dict[str, object] = {
        "utility": "inbw",
        "region": "wallonia",
        "valid_from": date(2026, 1, 1),
        "valid_until": date(2026, 12, 31),
        "publication_label": "Tarifs 2026",
        "source_url": "https://inbw.test/tarifs",
        "yearly_fixed_fee": 100.0,
        "cvd_eur_per_m3": 2.5,
        "cva_eur_per_m3": 2.748,
        "fse_eur_per_m3": 0.0339,
    }
    values.update(overrides)
    return WaterTariff(**values)  # type: ignore[arg-type]


def test_a_tariff_survives_the_round_trip_through_json() -> None:
    tariff = _tariff()
    stored = json.loads(json.dumps(tariff_to_dict(tariff)))
    assert stored["valid_from"] == "2026-01-01"
    assert stored["valid_until"] == "2026-12-31"
    assert tariff_from_dict(stored) == tariff
    open_ended = _tariff(valid_until=None)
    assert tariff_from_dict(tariff_to_dict(open_ended)) == open_ended


def test_a_rows_own_keys_are_left_out() -> None:
    row = {**tariff_to_dict(_tariff()), "_seen_on": "2026-09-12", "_sources": []}
    assert tariff_from_dict(row) == _tariff()


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("cvd_eur_per_m3", "NaN"),
        ("cvd_eur_per_m3", "Infinity"),
        ("cvd_eur_per_m3", "1" + "0" * 400),
        ("cvd_eur_per_m3", '"2.5"'),
        ("cvd_eur_per_m3", "true"),
        ("cvd_eur_per_m3", "null"),
        ("linear_eur_per_m3", "NaN"),
        ("publication_label", "2026"),
    ],
    ids=["NaN", "infinity", "huge", "string", "true", "null", "optional NaN", "label"],
)
def test_a_row_whose_figures_are_no_finite_numbers_is_refused(name: str, raw: str) -> None:
    """The card archive's rows are read with json.loads, which takes NaN,
    Infinity and an integer past any float. Such a card was served while
    the utility was down: priced as NaN and written back to the Store as
    null, or raising out of every refresh for a string or a huge integer."""
    row = tariff_to_dict(_tariff())
    body = json.dumps({**row, name: "@"}).replace('"@"', raw)
    with pytest.raises(ValueError):
        tariff_from_dict(json.loads(body))


def test_a_rate_a_card_does_not_have_reads_back_as_none() -> None:
    row = {**tariff_to_dict(_tariff()), "basis_eur_per_m3": None, "yearly_fixed_fee": 100}
    tariff = tariff_from_dict(row)
    assert tariff.basis_eur_per_m3 is None
    assert tariff.yearly_fixed_fee == 100.0
    assert isinstance(tariff.yearly_fixed_fee, float)
