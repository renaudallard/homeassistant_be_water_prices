# Belgian Water Prices for Home Assistant

A custom integration that publishes the official tariffs of Belgian
drinking-water utilities as Home Assistant sensors, so dashboards and
automations can show the per-m³ price, the yearly fixed fee, and the
projected annual cost based on your household's consumption.

This is a sibling to
[`homeassistant_be_electricity_prices`](https://github.com/renaudallard/homeassistant_be_electricity_prices)
and follows the same architecture (registry of per-utility extractors,
daily-refresh `DataUpdateCoordinator`, no Python-source EUR values).

## Status

**v0.1** -- Brussels-Capital Region only (VIVAQUA). Flemish utilities
land in v0.2; Wallonia in v0.3. See `SCOPE.md` for the full roadmap.

## Supported utilities

| Region   | Utility   | Source                                           |
|----------|-----------|--------------------------------------------------|
| Brussels | VIVAQUA   | https://www.vivaqua.be/en/the-domestic-linear-rate/ |

## Sensors

| Entity                           | Unit       | Notes                                     |
|----------------------------------|------------|-------------------------------------------|
| `sensor.water_yearly_fee`        | EUR/year   | Vastrecht / redevance, ex-VAT             |
| `sensor.water_basis_rate`        | EUR/m³     | First-block (Flanders) or linear (Brussels), ex-VAT |
| `sensor.water_comfort_rate`      | EUR/m³     | Flanders block 2 only; `None` elsewhere    |
| `sensor.water_sanering_rate`     | EUR/m³     | Sum of all sewerage components, ex-VAT     |
| `sensor.water_all_in_basis`      | EUR/m³     | Basis + sanering + VAT (what you pay)      |
| `sensor.water_projected_annual_cost` | EUR/year | VAT-incl projection from your options    |

Each sensor exposes `valid_from`, `valid_until`, `publication_label`,
`source_url`, `snapshot_age_hours`, and `snapshot_stale` as attributes.

## Installation

1. Add this repo as a custom HACS repository:
   `https://github.com/renaudallard/homeassistant_be_water_prices`
2. Install **Belgian Water Prices** from HACS.
3. Restart Home Assistant.
4. Settings → Devices & services → Add integration → Belgian Water
   Prices, enter your postcode (or pick the utility manually).

## Configuration

* **Postcode** -- used to auto-resolve your utility. v0.1 maps Brussels
  postcodes (1000-1299) to VIVAQUA; non-resolved postcodes drop into a
  manual utility picker.
* **Annual consumption (m³/yr)** -- defaults to 80; feeds the projected-
  cost sensor.

The Flanders-only `gedomicilieerd_persons` and `social_tariff` options
arrive in v0.2 along with the block-tariff math.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install beautifulsoup4 lxml pypdf pdfplumber aiohttp \
    pytest pytest-asyncio voluptuous ruff mypy
.venv/bin/pytest tests/ -q
.venv/bin/ruff check custom_components/ tests/
.venv/bin/ruff format --check custom_components/ tests/
.venv/bin/mypy custom_components/be_water_prices
```

Tariff fixtures live under `tests/fixtures/` (raw HTML / PDF captures
with the year baked into the filename). Refresh annually before the
new card lands.

## License

3-clause BSD.
