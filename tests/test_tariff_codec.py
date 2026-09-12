"""providers/base.py: the tariff as the card archive writes and reads it."""

from __future__ import annotations

import json
from datetime import date

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
