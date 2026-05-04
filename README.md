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

**v0.2** -- Brussels (VIVAQUA), Flanders (De Watergroep, Pidpa),
Wallonia (SWDE, inBW). Farys (Oost-Vl. + parts of West-Vl. and Vl-Br.)
is intentionally not yet wired: its watertarieven page is JS-rendered
and the static HTML carries no per-m³ numbers; landing in a follow-up
once we discover the Drupal endpoint or use a per-commune fallback
URL. See `SCOPE.md` for the full roadmap.

## Supported utilities

| Region   | Utility       | Source                                              |
|----------|---------------|-----------------------------------------------------|
| Brussels | VIVAQUA       | https://www.vivaqua.be/en/the-domestic-linear-rate/ |
| Flanders | De Watergroep | https://www.dewatergroep.be/nl-be/over-de-watergroep/nieuws/tarieven-2026 (drinkwater leg only -- per-commune sanering coming in v0.4) |
| Flanders | Pidpa         | https://www.pidpa.be/sites/default/files/2024-05/Tariefplan_2025-2030_simulatie_type_gezin.pdf (PDF) |
| Wallonia | SWDE          | https://www.swde.be/en/water-prices-swde            |
| Wallonia | inBW          | https://eau.inbw.be/prix-de-leau (TLS chain misconfigured server-side; we fetch with `verify_ssl=False`) |

Postcode resolver:

* 1000-1299 → VIVAQUA
* 1300-1499 → inBW (Brabant Wallon)
* 1500-1999, 3000-3999 → De Watergroep
* 2000-2999 → Pidpa
* 4000-7999 → SWDE

West-/Oost-Vl (8000-9999) drops into the manual utility picker;
v0.4 will fold in the Géoportail Wallonie ZDE GeoPackage and the VMM
Waterloket Flanders dump for per-commune precision.

## Sensors

| Entity                           | Unit       | Notes                                     |
|----------------------------------|------------|-------------------------------------------|
| `sensor.water_yearly_fee`        | EUR/year   | Vastrecht / redevance, ex-VAT             |
| `sensor.water_basis_rate`        | EUR/m³     | First-block (Flanders), linear (Brussels), or CVD (Wallonia), ex-VAT |
| `sensor.water_comfort_rate`      | EUR/m³     | Flanders block 2 only; `None` elsewhere    |
| `sensor.water_sanering_rate`     | EUR/m³     | Sum of all sewerage / CVA / FSE components, ex-VAT |
| `sensor.water_all_in_basis`      | EUR/m³     | Basis + sanering + VAT. For Wallonia this is the *above-30 m³* headline; the first 30 m³ pay 50 % CVD, see the projected-cost sensor for the actual bill |
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

* **Postcode** -- used to auto-resolve your utility. Non-resolved
  postcodes drop into a manual utility picker.
* **Annual consumption (m³/yr)** -- defaults to 80; feeds the projected-
  cost sensor.
* **Gedomicilieerd_persons (Flanders only)** -- 1 to 5; sets the
  basisvolume (`30 + 30·persons` m³) and the per-resident vastrecht
  korting (10 EUR per persoon for the drinkwater leg, 20 EUR total
  when sanering is included). Defaults to 1.
* **Social tariff (Flanders only)** -- VMM means-tested 80 % reduction
  on the post-calc bill. Off by default.

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
