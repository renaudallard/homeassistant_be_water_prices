# Architecture

The integration turns one water utility's published tariff into a set of
price and cost sensors. Each config entry is one household on one utility
(and, for the four utilities that bill sewerage per commune, one commune).
A coordinator fetches the utility's tariff once a day, prices the
household's configured consumption and, when a water meter is known, the
water it has drawn this year.

## Data flow

```
utility page / PDF ──> providers/<utility>.py ──> WaterTariff
                                                     │
card archive (archive branch) ─── on failure ────────┤
last good card (Store) ────────── on failure ────────┤
                                                     v
water meter, recorder, Energy dashboard ──> WaterCoordinator ──> CoordinatorData
                                                     │
                     sensor.py, button.py, statistics.py, repairs.py, diagnostics.py
```

1. `providers/<utility>.py` fetches and parses the utility's own
   publication into a `WaterTariff` (`providers/base.py`): every rate and
   fee ex-VAT, with its validity window. A figure the parser cannot read
   raises `ExtractorError` rather than defaulting to zero.
2. `WaterCoordinator` (`coordinator.py`) runs the fetch on a daily tick.
   On failure it serves the last good card, kept on disk with its fetch
   time, or the project's card archive when it holds a newer one or none
   is kept. It prices the year so far from the meter (live on each meter
   reading, anchored in the recorder's statistics) and the year's
   projection, and raises the Repairs cards.
3. `sensor.py` publishes the figures; `statistics.py` writes the price
   history into long-term statistics; `button.py` and the `refresh`
   service fetch again on demand.

The whole flow is per entry: entries share nothing but the extractor
registry.

## Module map

`custom_components/be_water_prices/`:

| Module | Role |
| --- | --- |
| `__init__.py` | Entry setup and unload, the services (`async_setup`), the setup-time commune sweeps, migration. Imports nothing from Home Assistant at module level, since the CI checks import the extractors without it; `CONFIG_SCHEMA` is built on first read. |
| `config_flow.py` | The setup wizard, the options and reconfigure ([config-flow.md](config-flow.md)) |
| `coordinator.py` | `WaterCoordinator`, the year-to-date cycle and its Stores, the recorder reads, the Repairs cards ([coordinator.md](coordinator.md)) |
| `pricing.py` | The bill math, without Home Assistant imports ([pricing-model.md](pricing-model.md)) |
| `sensor.py` | The sensors ([entities.md](entities.md)) |
| `button.py` | The refresh button |
| `statistics.py` | The price-history backfill and its service |
| `repairs.py` | The fix flows of the stale-snapshot and projection cards |
| `diagnostics.py` | The diagnostics dump |
| `_redact.py` | Commune redaction shared by the attributes, the log and diagnostics |
| `_phantom_blocklists.py` | Farys and Pidpa communes that are listed but not served, stdlib only so `scripts/refresh_postcodes.py` can import it |
| `const.py` | Configuration keys, defaults, the regulated constants |
| `services.yaml`, `strings.json`, `translations/`, `icons.json` | Service schema, UI text in English, French, Dutch and German, icons |
| `brand/` | The integration's icons and logos |

`providers/`:

| Module | Role |
| --- | --- |
| `__init__.py` | The registry: `_MODULE_NAMES`, built lazily (`async_load` off the event loop) |
| `base.py` | `WaterExtractor`, `WaterTariff`, `CommuneOption`, the errors, `belgian_today`, `carry_prior_year_card` |
| `_html.py`, `_pdf.py` | Fetching and reading pages and PDFs, with their guards |
| `_flanders.py` | The Flemish integrale waterprijs builder |
| `_walloon_simple.py` | The Walloon CWaPE builder, the CVD scan and the SPGE figures |
| `_postcodes.py` | Postcode to utility |
| one module per utility | See [providers/](providers/) |

The framework is in [provider-framework.md](provider-framework.md), where
each input comes from in [data-sources.md](data-sources.md).

Outside the integration, `scripts/` holds the live check, the drift check,
the card archiver and the postcode map refresher, and `tests/` the suite
([ci-and-testing.md](ci-and-testing.md)).

## Design rules

- **No price in the source.** Every per-utility rate is read from the
  utility's publication on each fetch, bar AIEC's, whose card is a picture
  transcribed against the date its file name carries
  ([providers/aiec.md](providers/aiec.md)). What is uniform by decree
  (the Flemish vastrecht and korting, the Walloon CVA and FSE for the year
  they are in force, VAT) is a constant in `const.py`.
- **Fail closed, keep serving.** A page that changed shape, prints a
  figure off its cross-check or misses a mandatory row fails the fetch,
  and the entry keeps its last good card behind the stale-snapshot
  Repairs card. The daily live check is what turns that into an issue.
- **Keep the household out of shared surfaces.** The postcode, the commune
  and the meter id are redacted from diagnostics and scrubbed from the
  attributes and the log; the card archive request names only the utility
  and the commune id, and can be switched off.
- **Never let the running bill walk down for no reason.** The year to date
  is held to a high-water mark and falls only for a reason the coordinator
  can name: the 1 January rollover, a different or replaced meter, or the
  recorder taking back a misread ([coordinator.md](coordinator.md)).

## Adding a utility

See [provider-framework.md](provider-framework.md#adding-a-utility).
