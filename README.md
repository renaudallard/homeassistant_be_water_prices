<p align="center">
  <img src="logo.svg" alt="BE water - integrale waterprijs" width="640"/>
</p>

<p align="center">
  <a href="https://github.com/renaudallard/homeassistant_be_water_prices/releases/latest">
    <img src="https://img.shields.io/github/v/release/renaudallard/homeassistant_be_water_prices?label=version&style=flat-square&sort=semver" alt="Latest release"/>
  </a>
  <a href="https://github.com/renaudallard/homeassistant_be_water_prices/actions/workflows/validate.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/renaudallard/homeassistant_be_water_prices/validate.yml?style=flat-square&label=hacs%20%2F%20hassfest" alt="Validate"/>
  </a>
  <a href="https://github.com/renaudallard/homeassistant_be_water_prices/actions/workflows/test.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/renaudallard/homeassistant_be_water_prices/test.yml?style=flat-square&label=tests" alt="Tests"/>
  </a>
  <a href="https://www.home-assistant.io/">
    <img src="https://img.shields.io/badge/Home%20Assistant-2026.4%2B-41BDF5?logo=home-assistant&logoColor=white&style=flat-square" alt="Home Assistant"/>
  </a>
  <a href="https://hacs.xyz">
    <img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square" alt="HACS"/>
  </a>
  <a href="./LICENSE">
    <img src="https://img.shields.io/github/license/renaudallard/homeassistant_be_water_prices?style=flat-square" alt="License"/>
  </a>
  <a href="https://www.paypal.me/RenaudAllard">
    <img src="https://img.shields.io/badge/PayPal-Donate-blue.svg?logo=paypal&style=flat-square" alt="PayPal"/>
  </a>
</p>

---

Home Assistant integration that exposes the **integrale waterprijs** for
Belgian drinking-water customers, taking into account every component of
a Belgian water bill (drinkwater + saneringsbijdragen + redevance + VAT)
and the regional bill structure of each operator (Brussels linear,
Flemish basis/comfort blocks, Walloon CWaPE tiers).

Tariffs are fetched **live** from each utility's own published page or
PDF. **No EUR values are hardcoded in the source.** Add a utility by
writing one Python module that knows where to find that utility's
publication and how to parse it.

> Targets Home Assistant **2026.4 or newer**.

## Highlights

- **Live tariff publications** — prices come straight from each utility's HTML page or PDF tariefplan; no EUR values live in this repo.
- **Whole-bill view** — drinkwater + sanering + redevance + VAT all add up to a single EUR/year sensor that mirrors what you actually pay.
- **Flemish integrale waterprijs** — VMM block structure (basis up to `30 + 30·persons` m³, comfort = 2× basis above), per-resident vastrecht korting, and the 80 % social-tariff reduction.
- **Walloon CWaPE tiers** — first 30 m³ at `0.5·CVD + FSE` (CVA exempt on the residential first block), above 30 m³ at full `CVD + CVA + FSE`, plus the regulator-defined `20·CVD + 30·CVA` redevance. Verified to the cent against inBW's published facture.
- **Brussels linear** — VIVAQUA's single-rate domestic tariff plus the annual fixed fee.
- **Postcode auto-resolution** — enter your postcode and the right utility is picked automatically. Fall through to a manual picker for the long tail.
- **Projected annual cost** — every entry has a `water_projected_annual_cost` sensor wired to your configured consumption (and household size + social-tariff opt-in for Flemish customers).
- **Year-to-date cost** — auto-detects your water meter from HA's Energy dashboard (Settings → Dashboards → Energy → Water consumption) and surfaces a `water_current_year_cost` sensor that reports your running bill since 1 January, computed from the recorder. Annual fees are pro-rated to the elapsed fraction of the year so the figure grows day by day instead of jumping to the full annual on Jan 1; the volumetric branch reuses the same regional bill math as the projected-cost sensor. The OptionsFlow exposes an explicit-override field for users who want to point at a different sensor than the Energy dashboard's choice.
- **Translated UI** — English, Dutch, French and German.
- **Self-healing** — last-known prices keep serving on outage; `snapshot_age_hours`, `snapshot_stale` and `last_error` are surfaced as attributes, and a stale snapshot (>35 days or past the published `valid_until`) raises a Repair issue you'll see under **Settings → Repairs**. The card auto-clears on the next successful, fresh fetch.
- **Daily live check** — a cron-driven workflow probes every utility and opens a GitHub issue if any extractor breaks (page restyled, wrong year, etc.).

## Supported utilities

| Utility | Region | Coverage | Source |
| --- | --- | --- | --- |
| **VIVAQUA** | Brussels | All 19 communes (~1.2 M) | [`providers/vivaqua.py`](./custom_components/be_water_prices/providers/vivaqua.py) — HTML table on [vivaqua.be/en/the-domestic-linear-rate](https://www.vivaqua.be/en/the-domestic-linear-rate/), picks the current-year section by header and divides by VAT to keep the ex-VAT convention |
| **De Watergroep** | Flanders | 167 communes (~3.3 M, ~49.5 % share) — full integrale waterprijs when a commune is configured | [`providers/de_watergroep.py`](./custom_components/be_water_prices/providers/de_watergroep.py) — two ingestion paths: the news-article `over-de-watergroep/nieuws/tarieven-<year>` for the no-commune fallback (drinkwater leg only), and the cookie-driven `/Tarief/UpdateDetailTariefJaar/<year>` endpoint with `dwg_l=<GUID>` for the full per-commune bill (drinkwater + gemeentelijke + bovengemeentelijke saneringsbijdragen). Pick your commune in the OptionsFlow to switch from drinkwater-only to full-bill |
| **Farys** (TMVW) | Flanders (Oost-Vl. + parts of West-Vl. & Vl-Br.) | 85 communes (~1.5 M, ~22 % share) | [`providers/farys.py`](./custom_components/be_water_prices/providers/farys.py) — POSTs to the Drupal AJAX form at `farys.be/nl/watertarieven?ajax_form=1` with the commune ID baked in (Gent-centrum = 25071 by default) and parses the per-commune integrale waterprijs out of the `insert` command's HTML payload. Pick a different commune in the OptionsFlow to switch (290+ options) |
| **Pidpa** | Flanders | Antwerp province (~1.2 M) | [`providers/pidpa.py`](./custom_components/be_water_prices/providers/pidpa.py) — the multi-year `Tariefplan_2025-2030_simulatie_type_gezin.pdf` parsed via `pdfplumber`. Pulls the year column for basistarief / comforttarief plus the gemeentelijke (afvoer) and bovengemeentelijke (zuivering) saneringsbijdragen |
| **Water-link** | Flanders (Antwerp city + ring) | ~200 k | [`providers/water_link.py`](./custom_components/be_water_prices/providers/water_link.py) — the per-year PDF `water-link.be/sites/default/files/<YYYY>-01/<YYYY>%20HH.pdf` parsed via `pdfplumber`. Drinkwater + zuivering are uniform across the service area; gemeentelijke afvoer differs per commune (Antwerpen at 1.3345 €/m³, ring communes at 1.9572). Defaults to Antwerpen; pick your commune in the OptionsFlow (Edegem, Hove, Mortsel, Beveren-Kruibeke-Zwijndrecht, …) to get the right sanering |
| **Aquaduin** | Flanders (Westkust) | 6 communes (~80 k year-round) | [`providers/aquaduin.py`](./custom_components/be_water_prices/providers/aquaduin.py) — gold-standard numeric PDF at `aquaduin.be/drinkwater/tarieven/overzicht-tarieven-<year>.pdf`. Publishes a single integrated basistarief (drinkwater + sanering combined) -- highest in Flanders |
| **AGSO Knokke-Heist** | Flanders (1 commune) | ~33 k | [`providers/agso_knokke.py`](./custom_components/be_water_prices/providers/agso_knokke.py) — bs4 walker over the per-component "Integrale waterprijs" table at `agsoknokke-heist.be/waterbedrijf/tarieven/tarieven-kleinverbruikers`. Page shows previous + current year side-by-side; parser picks the higher-priced table since rates only ever index up |
| **SWDE** | Wallonia | ~200 communes (~2.4 M, dominant Walloon distributor) | [`providers/swde.py`](./custom_components/be_water_prices/providers/swde.py) — bs4-anchored on the `<h3>` headings of [swde.be/en/water-prices-swde](https://www.swde.be/en/water-prices-swde) (the FR slug 4xxs, the EN one works). CVA / FSE come from the SPGE flat-Wallonia constants and drift-warn on divergence |
| **inBW** | Wallonia (Brabant Wallon) | 27 communes | [`providers/inbw.py`](./custom_components/be_water_prices/providers/inbw.py) — bs4 walker over the per-tier facture table on [eau.inbw.be/prix-de-leau](https://eau.inbw.be/prix-de-leau). The server's TLS chain is misconfigured (GoDaddy intermediate not sent), so we fetch with `verify_ssl=False`; risk note in the module docstring |
| **CILE** | Wallonia (Liège region) | 24 communes (~560 k) | [`providers/cile.py`](./custom_components/be_water_prices/providers/cile.py) — clean 4-row HTML table on [cile.be/facturation/le-prix-de-leau](https://www.cile.be/facturation/le-prix-de-leau). Same pattern as SWDE / inBW: CVD parsed live, CVA / FSE cross-checked against the SPGE constants |
| **INASEP** | Wallonia (Namur sud) | 10 communes (~38 k subscribers) | [`providers/inasep.py`](./custom_components/be_water_prices/providers/inasep.py) — INASEP states the CVD inline ("A l'INASEP, il est de N,NNNN €/m³") rather than in a table. Parser anchors on that exact phrase (tolerating Unicode quotes) |
| **IEG** | Wallonia (Mouscron) | ~50 k | [`providers/ieg.py`](./custom_components/be_water_prices/providers/ieg.py) — operator's own page at `ieg.be/eau/espace-client/facturation/structure-du-prix-de-leau/`. Uses the shared CWaPE residential tier math via [`_walloon_simple.py`](./custom_components/be_water_prices/providers/_walloon_simple.py) |
| **AIEM** | Wallonia (Molignée) | ~12 k connections (~25 k people) | [`providers/aiem.py`](./custom_components/be_water_prices/providers/aiem.py) — `aiem.be/prix-de-l-eau`. The page spells out formula examples (`0,5 x CVD (soit 1,435€)`) before listing the actual current value, so the shared parser anchors on `actuelle du CVD` to skip the example |
| **AIEC** | Wallonia (Condroz) | small | [`providers/aiec.py`](./custom_components/be_water_prices/providers/aiec.py) — pulled from `callmepower.be/fr/eau/distributeurs/aiec` (public aggregator) since the operator's own site doesn't carry the rate |
| **CIESAC** | Wallonia (Clavier / Durbuy / Ouffet / Tinlot) | 4 communes | [`providers/ciesac.py`](./custom_components/be_water_prices/providers/ciesac.py) — same Callmepower path; `ciesac.be` is intermittently unreachable and doesn't publish structured rates |
| **IDEN** | Wallonia (Nandrin / Tinlot / Modave) | 3 communes | [`providers/iden.py`](./custom_components/be_water_prices/providers/iden.py) — same Callmepower path; the operator's own site (`iden-eau.be`) carries CVD/CVA explainers but no numbers |

**Still deferred:**

- **~30 régies communales** (Chimay, Theux, Libramont, …) — no central publication channel; deferred indefinitely on dev-hours / customer ratio.

Adding another utility is a self-contained PR: drop a new module under
[`custom_components/be_water_prices/providers/`](./custom_components/be_water_prices/providers/),
register it in [`providers/__init__.py`](./custom_components/be_water_prices/providers/__init__.py),
extend the postcode resolver in [`providers/_postcodes.py`](./custom_components/be_water_prices/providers/_postcodes.py),
and ship a fixture-based unit test. SWDE is the cleanest reference for a
single-page HTML utility; Pidpa is the reference for a PDF-only utility.

### Postcode auto-resolution

The config flow's first step accepts a postcode and resolves it to the
dominant operator for that area:

| Postcode range | Region | Default utility |
| --- | --- | --- |
| 1000-1299 | Brussels-Capital | VIVAQUA |
| 1300-1499 | Brabant Wallon | inBW |
| 1500-1999, 3000-3999 | Vlaams-Brabant + Halle-Vilvoorde + Limburg | De Watergroep |
| 2000-2070 | Antwerp city core | Water-link |
| 2100-2999 | rest of Antwerp province | Pidpa *(Water-link's ring communes -- Edegem, Hove, Mortsel, Schoten, Beveren, etc. -- overlap with Pidpa territory; manual picker for those addresses)* |
| 4000-7999 | Wallonia | per-postcode table from the **Géoportail Wallonie ZDE** (496 postcodes mapped to SWDE / CILE / inBW / INASEP / AIEC / AIEM / CIESAC / IDEN / IEG; postcodes served by régies communales we don't ship return *unresolved* and drop into the manual picker rather than mis-defaulting to SWDE) |
| 8300, 8301 | Knokke-Heist | AGSO Knokke-Heist |
| 8430, 8450, 8620, 8630, 8660, 8670 | Westkust (Aquaduin communes) | Aquaduin |
| 8000-9999 (rest) | West-Vl. + Oost-Vl. (mostly Farys) | Farys |

Postcodes outside these mappings drop into the manual utility picker.
The Wallonia per-postcode table is generated by
[`scripts/refresh_postcodes.py`](./scripts/refresh_postcodes.py)
from the [ZDE GeoPackage](https://geoportail.wallonie.be/catalogue/d08e993e-bb1d-4322-a044-2aa7bfc03247.html)
(CC BY 4.0); re-run it annually since the underlying data is updated
continuously by the distributors via ZDEOnMap.

### How often the integration polls

Water tariffs are annual. The coordinator ticks **once a day**; that is
enough to catch the 1 January re-pricing within hours, and the rest of
the year is mostly a "did the page change shape" canary. There is no
spot-style hourly fetch, no separate probe path, and no shared cache
across entries — water utilities don't overlap, so a single HA instance
has at most one entry per address.

## What the integration computes

For every entry, the projected annual bill in EUR/year (VAT-incl) plus
the per-m³ rates that feed it. Each branch matches the actual structure
of the corresponding regional bill:

```
Brussels  : (consumption × (linear + sanering) + redevance) × (1 + VAT)
Flanders  : (basis_volume × (basis + sanering)
             + over × (comfort + 2·sanering)
             + max(0, vastrecht - persons·korting)) × (1 + VAT)
Wallonia  : (min(consumption, 30) × (0.5·CVD + FSE)
             + max(0, consumption - 30) × (CVD + CVA + FSE)
             + 20·CVD + 30·CVA) × (1 + VAT)
```

with `basis_volume = 30 + 30·persons` for Flanders. The math lives in
[`pricing.compute_annual_cost`](./custom_components/be_water_prices/pricing.py)
as a pure function so it stays unit-testable without a Home Assistant
install.

`sensor.<entry>_water_all_in_basis` reports the per-m³ price you
actually pay (basis or linear plus sanering, VAT-incl) so a dashboard
can surface "your water costs you X EUR per cubic metre" at a glance.

## Sensors

All sensors share one device per config entry. Up to eight entities
per entry: `water_comfort_rate` only appears for Flemish utilities, and
`water_current_year_cost` + `water_ytd_consumption` only appear when a
water meter is configured (explicit override in the OptionsFlow, or
auto-discovered from the Energy dashboard).

| Sensor | Description |
| --- | --- |
| `water_yearly_fee` | Vastrecht / redevance in EUR/year, ex-VAT, parsed from the utility's own publication. |
| `water_basis_rate` | First-block (Flanders) or single-rate (Brussels) or CVD (Wallonia) in EUR/m³, ex-VAT. |
| `water_comfort_rate` | Flanders block 2 in EUR/m³, ex-VAT. `unknown` outside Flanders. |
| `water_sanering_rate` | Sum of every sewerage / CVA / FSE component carried by the tariff in EUR/m³, ex-VAT. |
| `water_all_in_basis` | What you actually pay per m³ inside the first block: `(basis + sanering) × (1 + VAT)`. For Wallonia this is the **above-30 m³** headline; the first 30 m³ pays only `0.5·CVD + FSE` (use the projected-cost sensor for the actual bill). |
| `water_projected_annual_cost` | Projected VAT-incl annual bill in EUR for your configured consumption. Wired to your `consumption_m3_per_year`, plus `gedomicilieerd_persons` and `social_tariff` for Flemish entries. Updates immediately when you change options. |
| `water_current_year_cost` | Running VAT-incl bill in EUR **since 1 January** of the current year. Reads YTD m³ from the configured water meter sensor via HA's recorder daily statistics, applies the same regional bill math as the projected-cost sensor, and pro-rates annual fees by elapsed-fraction-of-year. Returns `unknown` until a water meter is configured in the options step. |
| `water_ytd_consumption` | Cumulative m³ consumed since 1 January, summed from the configured water meter sensor's daily deltas. Companion to `water_current_year_cost`. |

Each sensor exposes `valid_from`, `valid_until`, `publication_label`,
`source_url`, `snapshot_age_hours`, `snapshot_stale`, and `last_error`
as attributes for dashboards and automations.

## Installation

### HACS (recommended)

1. Open HACS, three-dot menu → **Custom repositories**.
2. Add `https://github.com/renaudallard/homeassistant_be_water_prices` as type **Integration**.
3. Install **Belgian Water Prices** and restart Home Assistant.
4. **Settings → Devices & services → Add integration → Belgian Water Prices**.

### Manual

Download the latest [release zip](https://github.com/renaudallard/homeassistant_be_water_prices/releases),
extract it under `<config>/custom_components/be_water_prices/`, and
restart Home Assistant.

`pypdf`, `pdfplumber` and `beautifulsoup4` are the only extra runtime
dependencies; Home Assistant installs them automatically from the
manifest.

## Configuration

The UI walks **two or three steps**, depending on whether your postcode
auto-resolves and whether the chosen utility is Flemish.

1. **Postcode** — 4-digit Belgian postcode. Brussels (1000-1299),
   Brabant Wallon (1300-1499), Antwerp (2000-2999), most of Flanders
   (1500-1999 + 3000-3999) and most of Wallonia (4000-7999) auto-resolve
   to their dominant utility. Anything else falls through to step 2.
2. **Utility** *(only if step 1 didn't resolve)* — pick from the dropdown
   of registered utilities.
3. **Options** — annual consumption (m³/yr, default 80). Flemish
   utilities additionally ask:
   - **Gedomicilieerd_persons** *(1-5)* — drives the basisvolume
     (`30 + 30·persons` m³) and the per-resident vastrecht korting
     (10 EUR for the drinkwater leg, 20 EUR for the integrale total).
     Default 1.
   - **Social tariff** — VMM means-tested 80 % reduction on the
     post-calc bill. Off by default.

   Per-commune utilities (De Watergroep, Farys, Water-link) also
   show:
   - **Commune** *(optional)* — pick your specific commune to get
     the full per-commune integrale waterprijs (correct
     saneringsbijdragen instead of the operator-wide default).
     Particularly important for **De Watergroep** users since the
     no-commune fallback only carries the drinkwater leg.

   All entries can additionally point at:
   - **Water meter sensor** *(optional override)* — any
     `device_class=water` cumulative-m³ sensor (e.g. from the
     [`watermeter`](https://github.com/Olen/homeassistant-watermeter)
     custom component, a P1 reader integration, or a Pulse counter).
     **Leave blank** to auto-pick the meter you already configured in
     HA's Energy dashboard (Settings → Dashboards → Energy → Water
     consumption); set it only when you want a different sensor than
     what the Energy dashboard sees. The
     `water_current_year_cost` and `water_ytd_consumption` entities
     are only created when *either* an override is set *or* the
     Energy dashboard has a water source -- otherwise they stay out
     of the device card entirely. Adding a meter via the OptionsFlow
     reloads the entry so the new entities appear without an HA
     restart.

### Reconfiguring later

**Settings → Devices & services → Belgian Water Prices → Configure**
re-prompts only the **Options** step. To switch utility (e.g. you
moved house or the postcode auto-resolver picked the wrong default),
remove the entry and add it again with the new postcode.

## Daily operation

### Refresh cadence

- **Tariff snapshot** — once every 24 h. Water tariffs are annual; a
  fresh January 1 publication is picked up within a day.
- **Projected cost** — recomputed every coordinator tick **and**
  immediately when you save new options, so changing your consumption
  or household size shows up without waiting for the next refresh.

### Failure mode

If a refresh fails, the coordinator keeps serving the last known
snapshot and surfaces `snapshot_age_hours`, `snapshot_stale` and
`last_error` as attributes on every sensor. Snapshots older than
**35 days**, or where the parsed `valid_until` has already passed,
flip `snapshot_stale` to `true`, raise a Repair issue under
**Settings → Repairs**, and the daily live-check workflow opens a
GitHub issue against the integration if the failure persists. The
Repair card auto-clears once a successful, fresh fetch lands.

### Diagnostics

**Settings → Devices & services → Belgian Water Prices →** three-dot
menu **→ Download diagnostics** dumps the active config, the last
parsed `WaterTariff` (every component plus validity window), the
fetch metadata, and the projected annual cost. Attach it when
reporting an issue.

## Known limitations

- **De Watergroep no-commune fallback is drinkwater-only.** When no
  commune is configured the extractor pulls the news-article snapshot,
  which only carries the drinkwater leg (sanering = 0). Pick your
  commune in the OptionsFlow to switch to the cookie-driven endpoint
  and get the full integrale waterprijs (drinkwater + gemeentelijke +
  bovengemeentelijke saneringsbijdragen).
- **Pidpa sanering frozen at 2024 values.** The Tariefplan PDF only
  refreshes drinkwater rates per year; the saneringsbijdragen line
  prints 2024 numbers. Drinkwater per-m³ is correct each year.
- **Wallonia régies communales** (~30 small operators -- Chimay,
  Theux, Libramont, ...) are deferred indefinitely. They have no
  central publication channel and the dev-hours / customer ratio
  doesn't justify per-régie extractors. Postcodes served by these
  operators are absent from the per-postcode table and drop into the
  manual picker rather than mis-defaulting to SWDE.
- **Aquaduin integrated rate.** Aquaduin's PDF only publishes one
  per-m³ figure (5.9908 €/m³ in 2026, the highest in Flanders) for
  the integrale waterprijs basistarief -- it does not split drinkwater
  from sanering. We store the integrated value in `basis_eur_per_m3`
  with sanering = 0; the bill total is correct but the
  `water_basis_rate` sensor shows the integrated rate rather than
  drinkwater alone. Pidpa and AGSO Knokke publish split components
  and surface drinkwater-only on `water_basis_rate`.

## Development

```bash
ruff check .
ruff format --check .
mypy --strict custom_components/be_water_prices
pytest tests/
python scripts/live_check.py    # hits real utility endpoints
```

Tests run against fixture HTML and PDF snippets in
[`tests/fixtures/`](./tests/fixtures/) (real 2026 publications from
every registered utility). Refresh a fixture with the utility's
current page or PDF to re-run against new data; the file naming
convention is `<utility>_<year>.<ext>`.

A daily GitHub Actions workflow
([`.github/workflows/live_check.yml`](./.github/workflows/live_check.yml))
runs every registered extractor against its real publication URL,
retries up to five times with exponential backoff, and opens or
updates a GitHub issue titled
`[live-check] water extractor broken …` on persistent failure.

## License

BSD 2-Clause. See [LICENSE](./LICENSE).
