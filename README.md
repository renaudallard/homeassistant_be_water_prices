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
    <img src="https://img.shields.io/badge/Home%20Assistant-2026.2.3%2B-41BDF5?logo=home-assistant&logoColor=white&style=flat-square" alt="Home Assistant"/>
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

Home Assistant integration that exposes the **total annual water bill**
for Belgian drinking-water customers, taking into account every component
(drinkwater + saneringsbijdragen + redevance + VAT) and the regional bill
structure of each operator: Brussels linear, Flemish *integrale
waterprijs* (basis/comfort blocks), and Walloon CWaPE tiers.

Tariffs are fetched **live** from each utility's own published page or
PDF. **Every per-distributor rate is fetched, none is hardcoded.** The
components that are uniform by decree are carried as constants and
reviewed annually: the flat-Wallonia SPGE CVA and FSE, and the Flemish
vastrecht and korting (50+30+20 and 10+6+4 EUR). Add a utility by
writing one Python module that knows where to find that utility's
publication and how to parse it.

> Targets Home Assistant **2026.2.3 or newer** -- the version the test
> matrix installs, so the floor is one CI proves rather than one nobody
> has run.

## Highlights

- **Live tariff publications** — prices come straight from each utility's HTML page or PDF tariefplan; no EUR values live in this repo.
- **Whole-bill view** — drinkwater + sanering + redevance + VAT all add up to a single EUR/year sensor that mirrors what you actually pay.
- **Flemish integrale waterprijs** — VMM block structure (basis up to `30 + 30·persons` m³, comfort = 2× basis above), per-resident vastrecht korting, and the 80 % social-tariff reduction.
- **Walloon CWaPE tiers** — first 30 m³ at `0.5·CVD + FSE` (CVA exempt on the residential first block), above 30 m³ at full `CVD + CVA + FSE`, plus the regulator-defined `20·CVD + 30·CVA` redevance. Verified to the cent against inBW's published facture.
- **Brussels linear** — VIVAQUA's single-rate domestic tariff plus the annual fixed fee.
- **Postcode auto-resolution** — enter your postcode and the right utility is picked automatically. Fall through to a manual picker for the long tail.
- **Projected annual cost** — every entry has a `projected_annual_cost` sensor wired to your configured consumption (and household size + social-tariff opt-in for Flemish customers).
- **Year-to-date cost** — auto-detects your water meter from HA's Energy dashboard (Settings → Dashboards → Energy → Water consumption) and surfaces a `current_year_cost` sensor that reports your running bill since 1 January, computed from the recorder. Annual fees are pro-rated to the elapsed fraction of the year so the figure grows day by day instead of jumping to the full annual on Jan 1; the volumetric branch reuses the same regional bill math as the projected-cost sensor. The OptionsFlow exposes an explicit-override field for users who want to point at a different sensor than the Energy dashboard's choice. Only one meter is billed: if the dashboard lists several water sources the first is used, a warning names the ones being ignored, and the override field is how you pick.
- **Translated UI** — English, Dutch, French and German.
- **Projection kept honest** — once your meter has measured a whole calendar year, a Repair offers that real figure in place of the consumption you typed at setup, with both numbers shown. It never overwrites the setting on its own.
- **Self-healing** — last-known prices keep serving on outage; `snapshot_age_hours`, `snapshot_stale` and `last_error` are surfaced as attributes, and a stale snapshot (>35 days or past the published `valid_until`) raises a Repair issue you'll see under **Settings → Repairs**. The card carries a **Retry** button that triggers an immediate refresh, and auto-clears on the next successful, fresh fetch. When a utility has not published its new card by 1 January, last year's card is served until 31 March before the snapshot counts as stale.
- **Price-history backfill** — on the first setup of each entry, a flat-line of hourly long-term-statistics rows is imported from 1 January of the current year up to now, so the History dashboard and Energy dashboard tariff overlays show a price line going back further than the install moment. Re-run on demand via the `be_water_prices.backfill_prices` service (start date and clear-first toggle).
- **Daily live check** — a cron-driven workflow probes every utility and opens a GitHub issue if any extractor breaks (page restyled, wrong year, etc.).
- **Weekly fixture drift check** — a second cron parses each utility's live publication and diffs the result against the parser's output on the committed test fixture; if any tariff field drifted by more than the threshold (rates `> 0.001` €/m³, fees `> 0.01` €/year), an issue is opened with the field-by-field deltas so the fixture can be re-captured. It only sees fields we read off a page, so it cannot notice a move in the decreed constants above; SWDE, CILE and inBW publish the CVA and FSE and their parsers now fail outright if the published figure leaves the constant behind, which is what puts that move on the live check instead.

## Supported utilities

| Utility | Region | Coverage | Source |
| --- | --- | --- | --- |
| **AGSO Knokke-Heist** | Flanders (1 commune) | ~33 k | [`providers/agso_knokke.py`](./custom_components/be_water_prices/providers/agso_knokke.py) — bs4 walker over the per-component "Integrale waterprijs" table at `agsoknokke-heist.be/waterbedrijf/tarieven/tarieven-kleinverbruikers`. Page shows previous + current year side-by-side; parser picks the higher-priced table since rates only ever index up |
| **AIEC** | Wallonia (Condroz) | small | [`providers/aiec.py`](./custom_components/be_water_prices/providers/aiec.py) — pulled from `callmepower.be/fr/eau/distributeurs/aiec` (public aggregator) since the operator's own site doesn't carry the rate |
| **AIEM** | Wallonia (Molignée) | ~12 k connections (~25 k people) | [`providers/aiem.py`](./custom_components/be_water_prices/providers/aiem.py) — `aiem.be/prix-de-l-eau`. The page spells out formula examples (`0,5 x CVD (soit 1,435€)`) before listing the actual current value, so the shared parser anchors on `actuelle du CVD` to skip the example |
| **Aquaduin** | Flanders (Westkust) | 6 communes (~80 k year-round) | [`providers/aquaduin.py`](./custom_components/be_water_prices/providers/aquaduin.py) — gold-standard numeric PDF linked from `aquaduin.be/nl/zelf-regelen/tarieven/tarieven-<year>` (the PDF URL is scraped from that page). Publishes a single integrated basistarief (drinkwater + sanering combined) rather than the usual split |
| **CIESAC** | Wallonia (Clavier / Durbuy / Ouffet / Tinlot) | 4 communes | [`providers/ciesac.py`](./custom_components/be_water_prices/providers/ciesac.py) — same Callmepower path; `ciesac.be` is intermittently unreachable and doesn't publish structured rates |
| **CILE** | Wallonia (Liège region) | 24 communes (~560 k) | [`providers/cile.py`](./custom_components/be_water_prices/providers/cile.py) — clean 4-row HTML table on [cile.be/facturation/le-prix-de-leau](https://www.cile.be/facturation/le-prix-de-leau). Same pattern as SWDE / inBW: CVD parsed live, CVA / FSE cross-checked against the SPGE constants |
| **De Watergroep** | Flanders | 167 communes (~3.3 M, ~49.5 % share) — full integrale waterprijs out of the box, exact per-commune numbers when a commune is configured | [`providers/de_watergroep.py`](./custom_components/be_water_prices/providers/de_watergroep.py) — two ingestion paths: the cookie-driven `/Tarief/UpdateDetailTariefJaar/<year>` endpoint with `dwg_l=<GUID>` (default GUID = Halle / 1500 if no commune is configured, otherwise the user-picked one) returns the full per-commune bill (drinkwater + gemeentelijke + bovengemeentelijke saneringsbijdragen). Falls back to the news-article `over-de-watergroep/nieuws/tarieven-<year>` (drinkwater leg only) if the cookie endpoint fails |
| **Farys** (TMVW) | Flanders (Oost-Vl. + parts of West-Vl. & Vl-Br.) | 85 communes (~1.5 M, ~22 % share) | [`providers/farys.py`](./custom_components/be_water_prices/providers/farys.py) — POSTs to the Drupal AJAX form at `farys.be/nl/watertarieven?ajax_form=1` with the commune ID baked in (Gent-centrum = 25071 by default) and parses the per-commune integrale waterprijs out of the `insert` command's HTML payload. Pick a different commune in the OptionsFlow to switch (~265 options; 23 phantom entries that Farys lists but doesn't actually serve are filtered out so users can't pick a crashing option) |
| **IDEN** | Wallonia (Nandrin / Tinlot / Modave) | 3 communes | [`providers/iden.py`](./custom_components/be_water_prices/providers/iden.py) — same Callmepower path; the operator's own site (`iden-eau.be`) carries CVD/CVA explainers but no numbers |
| **IEG** | Wallonia (Mouscron) | ~50 k | [`providers/ieg.py`](./custom_components/be_water_prices/providers/ieg.py) — operator's own page at `ieg.be/eau/espace-client/facturation/structure-du-prix-de-leau/`. Uses the shared CWaPE residential tier math via [`_walloon_simple.py`](./custom_components/be_water_prices/providers/_walloon_simple.py) |
| **INASEP** | Wallonia (Namur sud) | 10 communes (~38 k subscribers) | [`providers/inasep.py`](./custom_components/be_water_prices/providers/inasep.py) — INASEP lists the CVD on the *Prix de l'eau et évolution* page under the heading "Coût-Vérité Distribution (CVD) = N,NNNN €/m³". Parser anchors on that heading (tolerating accent-stripped variants) and dates the tariff from the day the page says the CVD applies (27 April for the 2026 card) |
| **inBW** | Wallonia (Brabant Wallon) | 27 communes | [`providers/inbw.py`](./custom_components/be_water_prices/providers/inbw.py) — bs4 walker over the per-tier facture table on [eau.inbw.be/prix-de-leau](https://eau.inbw.be/prix-de-leau). The server's TLS chain is misconfigured (GoDaddy intermediate not sent). The fetch verifies first and only retries with `verify_ssl=False` after a TLS-specific failure, logging a warning when it does; risk note in the module docstring |
| **Pidpa** | Flanders | Antwerp province (~1.2 M) | [`providers/pidpa.py`](./custom_components/be_water_prices/providers/pidpa.py) — two paths: the per-commune `/ons-aanbod/je-gemeente/<slug>` HTML page, which carries the current published rates and which the no-commune fetch also reads for a fixed default commune (Geel) since Pidpa charges one rate province-wide; and the multi-year `Tariefplan_2025-2030_simulatie_type_gezin.pdf` parsed via `pdfplumber`, a May-2024 projection kept only as the fallback when that page cannot be read. Commune list comes from Pidpa's public sitemap (63 communes) |
| **SWDE** | Wallonia | ~200 communes (~2.4 M, dominant Walloon distributor) | [`providers/swde.py`](./custom_components/be_water_prices/providers/swde.py) — bs4-anchored on the `<h3>` headings of [swde.be/en/water-prices-swde](https://www.swde.be/en/water-prices-swde) (the FR slug 4xxs, the EN one works). CVA / FSE come from the SPGE flat-Wallonia constants and the fetch fails if the page has moved off them |
| **VIVAQUA** | Brussels | All 19 communes (~1.2 M) | [`providers/vivaqua.py`](./custom_components/be_water_prices/providers/vivaqua.py) — HTML table on [vivaqua.be/en/the-domestic-linear-rate](https://www.vivaqua.be/en/the-domestic-linear-rate/), picks the current-year section by header and divides by VAT to keep the ex-VAT convention |
| **Water-link** | Flanders (Antwerp city + ring) | ~200 k | [`providers/water_link.py`](./custom_components/be_water_prices/providers/water_link.py) — the per-year PDF `water-link.be/sites/default/files/<YYYY>-<MM>/<YYYY>%20HH.pdf` parsed via `pdfplumber`, its link discovered from the Antwerpen tariff page since the upload directory carries the publication month. Drinkwater + zuivering are uniform across the service area; gemeentelijke afvoer differs per commune (Antwerpen at 1.3345 €/m³, ring communes at 1.9572). Defaults to Antwerpen; pick your commune in the OptionsFlow (Edegem, Hove, Mortsel, Beveren-Kruibeke-Zwijndrecht, …) to get the right sanering |

**Still deferred:**

- **~30 régies communales** (Chimay, Theux, Libramont, …) — no central publication channel; deferred indefinitely on dev-hours / customer ratio.

Adding another utility is a self-contained PR: drop a new module under
[`custom_components/be_water_prices/providers/`](./custom_components/be_water_prices/providers/),
register it in [`providers/__init__.py`](./custom_components/be_water_prices/providers/__init__.py),
extend the postcode resolver in [`providers/_postcodes.py`](./custom_components/be_water_prices/providers/_postcodes.py),
and ship a fixture-based unit test. SWDE is the cleanest reference for a
single-page HTML utility; Aquaduin is the reference for a PDF-based one.

### Postcode auto-resolution

The config flow's first step accepts a postcode and resolves it to the
dominant operator for that area:

| Postcode range | Region | Default utility |
| --- | --- | --- |
| 1000-1299 | Brussels-Capital | VIVAQUA |
| 1500-1999 (17 Farys-served postcodes) | Halle-Vilvoorde (Beersel, Asse, Zaventem, Machelen, Drogenbos, etc.) | Farys |
| 1500-1999, 3000-3999 (rest) | Vlaams-Brabant + Halle-Vilvoorde + Limburg | De Watergroep |
| 2000-2070 | Antwerp city core | Water-link |
| 2100-2999 | rest of Antwerp province | Pidpa *(Water-link's ring communes -- Edegem, Hove, Mortsel, Schoten, Beveren, etc. -- overlap with Pidpa territory; manual picker for those addresses)* |
| 1300-1499, 4000-7999 | Brabant Wallon + Wallonia | per-postcode table from the **Géoportail Wallonie ZDE** (540 postcodes mapped to SWDE / CILE / inBW / INASEP / AIEC / AIEM / CIESAC / IDEN / IEG; postcodes served by régies communales we don't ship return *unresolved* and drop into the manual picker rather than mis-defaulting to SWDE) |
| 8300, 8301 | Knokke-Heist | AGSO Knokke-Heist |
| 8620, 8630, 8660, 8670, 8690, 8691 | Westkust (Aquaduin communes) | Aquaduin |
| 8000-9999 (122 DWG-served postcodes) | Kortrijk, Harelbeke, Roeselare, Waregem, Ieper, Sint-Niklaas outer parishes, Eeklo, Maldegem, etc. | De Watergroep |
| 1770, 8020, 8400, 8490, 9080, 9550, 9570 | Postcodes genuinely split at street level (e.g. 8400 Stene/Mariakerke, 9080 Zaffelare/Lochristi) | **Multi-choice**: the config flow asks the user to pick between the candidate operators |
| 8000-9999 (rest) | West-Vl. + Oost-Vl. (mostly Farys) | Farys |

Postcodes outside these mappings drop into the manual utility picker.
The Wallonia per-postcode table and the DWG carve-out are generated by
[`scripts/refresh_postcodes.py`](./scripts/refresh_postcodes.py)
(Wallonia from the [ZDE GeoPackage](https://geoportail.wallonie.be/catalogue/d08e993e-bb1d-4322-a044-2aa7bfc03247.html)
under CC BY 4.0; DWG carve-out from DWG's and Farys's commune
dropdowns); re-run it annually since the underlying data shifts when
intercommunales merge or distributors update ZDEOnMap.

### How often the integration polls

Water tariffs are annual. The coordinator ticks **once a day**; that is
enough to catch the 1 January re-pricing within hours, and the rest of
the year is mostly a "did the page change shape" canary. There is no
spot-style hourly fetch, no separate probe path, and no shared cache
across entries — water utilities don't overlap, so a single HA instance
has at most one entry per address. This daily cadence governs only the
network fetch; the meter-derived running-cost and YTD-consumption
sensors update live from your local water meter (see
[Refresh cadence](#refresh-cadence)).

## What the integration computes

For every entry, the projected annual bill in EUR/year (VAT-incl) plus
the per-m³ rates that feed it. Each branch matches the actual structure
of the corresponding regional bill:

```
Brussels  : (consumption × (linear + sanering) + redevance) × (1 + VAT)
Flanders  : (min(consumption, basis_volume) × (basis + sanering)
             + max(0, consumption - basis_volume) × (comfort + 2·sanering)
             + max(0, vastrecht - persons·korting)) × (1 + VAT)
Wallonia  : (min(consumption, 30) × (0.5·CVD + FSE)
             + max(0, consumption - 30) × (CVD + CVA + FSE)
             + 20·CVD + 30·CVA) × (1 + VAT)
```

with `basis_volume = 30 + 30·persons` for Flanders. The math lives in
[`pricing.compute_annual_cost`](./custom_components/be_water_prices/pricing.py)
as a pure function so it stays unit-testable without a Home Assistant
install.

The all-in basis rate sensor reports the first-block tariff per m³
(basis or linear plus sanering, VAT-incl) so a dashboard can surface
what a cubic metre costs at a glance. It is the published rate: the
social tariff and the vastrecht korting only enter the two cost
sensors.

## Sensors

All sensors share one device per config entry, named after the
utility (`VIVAQUA`, `De Watergroep`, ...). Home Assistant builds the
entity id from the device name plus the sensor name, so the projected
cost lands on `sensor.vivaqua_projected_annual_cost` for a VIVAQUA
entry and `sensor.de_watergroep_projected_annual_cost` for a De
Watergroep one. The table below lists the suffix; rename the device
and the prefix follows.

Up to eight entities per entry: `comfort_rate` only appears for
Flemish utilities; `current_year_cost` and `year_to_date_consumption`
are always created and report `unknown` until a water meter is wired up
(explicit override in the OptionsFlow, or auto-discovered from the
Energy dashboard). The next coordinator tick after the meter shows
up fills in the values without an HA restart. Re-pointing the Energy
dashboard at a different water meter is picked up the same way: the
following tick re-anchors on the new meter and live tracking moves
with it, again without a restart.

| Entity id suffix | Description |
| --- | --- |
| `yearly_fixed_fee` | Vastrecht / redevance in EUR/year, ex-VAT. Parsed from the publication for VIVAQUA only. Flemish entries carry the decreed uniform vastrecht (50+30+20, or 50 alone on the drinkwater-only fallback); Walloon entries use the regulator's formula `20·CVD + 30·CVA`, where the CVD is parsed and the CVA is the flat SPGE constant. |
| `basis_rate` | First-block (Flanders) or single-rate (Brussels) or CVD (Wallonia) in EUR/m³, ex-VAT. |
| `comfort_rate` | Flanders block 2 in EUR/m³, ex-VAT. Not created for Brussels or Wallonia entries (the concept is Flemish-only). |
| `sewerage_rate` | Sum of every sewerage / CVA / FSE component carried by the tariff in EUR/m³, ex-VAT. |
| `all_in_basis_rate` | The first-block tariff per m³, VAT-incl: `(basis + sanering) × (1 + VAT)`, before any social-tariff reduction. For Wallonia this is the **above-30 m³** headline; the first 30 m³ pays only `0.5·CVD + FSE` (use the projected-cost sensor for the actual bill). |
| `projected_annual_cost` | Projected VAT-incl annual bill in EUR for your configured consumption. Wired to your `consumption_m3_per_year`, plus `gedomicilieerd_persons` and `social_tariff` for Flemish entries. Updates immediately when you change options. |
| `current_year_cost` | Running VAT-incl bill in EUR **since 1 January** of the current year. Anchors the January 1 meter reading once from HA's recorder daily statistics and **persists it across restarts**, then tracks the configured water meter sensor **live** as `live − baseline` — recomputing on each meter reading — applies the same regional bill math as the projected-cost sensor, and pro-rates annual fees by elapsed-fraction-of-year. The figure is **monotonic within the year**: the EUR cost carries its own year-to-date high-water mark on top of the consumption clamp, so neither a momentary low meter reading nor a transiently lower tariff fetch is ever published as a decrease — the bill only drops to ~0 when the cycle restarts, which is the 1 January rollover, a confirmed meter swap, or pointing the integration at a different meter. Returns `unknown` until a water meter is configured in the options step. |
| `year_to_date_consumption` | Cumulative m³ consumed since 1 January. Tracks the configured water meter sensor live (recorder-anchored baseline plus the live reading), clamped to the year's high-water mark so it never decreases mid-year. Companion to `current_year_cost`. |

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

`pdfplumber` and `beautifulsoup4` are the only extra runtime
dependencies; Home Assistant installs them automatically from the
manifest.

## Configuration

The UI walks **two or three steps**, depending on whether your postcode
auto-resolves cleanly.

1. **Postcode** — 4-digit Belgian postcode. Every postcode a supported
   operator serves auto-resolves: Brussels (1000-1299) to VIVAQUA, the
   Antwerp province (2000-2999) to Pidpa, the rest of Flanders
   (1500-1999, 3000-3999 and 8000-9999) to De Watergroep, Farys,
   Aquaduin or AGSO Knokke-Heist by commune, and Wallonia (1300-1499
   and 4000-7999) per the regulator's distribution zones. A postcode no
   supported operator serves falls through to step 2.
2. **Utility** *(only if step 1 didn't resolve)* — pick from the dropdown
   of registered utilities. For the seven postcodes genuinely split
   between two or three operators at street level (1770 Liedekerke,
   8020 Oostkamp, 8400 Oostende, 8490 Jabbeke, 9080 Lochristi, 9550
   Herzele, 9570 Lierde), the picker is pre-narrowed
   to just the candidate operators so you only see the choices that
   actually serve your address.
3. **Options** — annual consumption (m³/yr, default 80). Flemish
   utilities additionally ask:
   - **Gedomicilieerd_persons** *(0-20)* — drives the basisvolume
     (`30 + 30·persons` m³, which the decree does not cap) and the
     per-resident vastrecht korting (10 EUR for the drinkwater leg,
     20 EUR for the integrale total). The korting stops mattering past
     five residents, where it has cancelled the vastrecht outright.
     Default 1; 0 is right for a second home or a rental between
     tenants.
   - **Social tariff** — VMM means-tested 80 % reduction on the
     post-calc bill. Off by default.

   Per-commune utilities (De Watergroep, Farys, Pidpa, Water-link)
   also show:
   - **Commune** *(optional)* — pick your specific commune to get
     the exact per-commune integrale waterprijs. Without a commune,
     each of those four utilities falls back to a representative
     default (Halle for De Watergroep, Gent-centrum for Farys,
     Antwerpen for Water-link, Geel for Pidpa). Pidpa charges one
     rate province-wide, so its default is exact; for the other
     three the projected-cost sensor is in the right ballpark but
     the saneringsbijdragen may differ from your actual bill.

   All entries can additionally point at:
   - **Water meter sensor** *(optional override)* — any
     `device_class=water` cumulative-volume sensor (e.g. from the
     [`watermeter`](https://github.com/Olen/homeassistant-watermeter)
     custom component, a P1 reader integration, or a Pulse counter).
     Meters reporting litres, gallons or ft³ are converted to m³, so
     the unit your meter uses does not matter.
     **Leave blank** to auto-pick the meter you already configured in
     HA's Energy dashboard (Settings → Dashboards → Energy → Water
     consumption); set it only when you want a different sensor than
     what the Energy dashboard sees. The
     `current_year_cost` and `year_to_date_consumption` entities
     are always created -- they report `unknown` until a meter is
     wired up through either path, and the next coordinator tick
     after that fills them in without an HA restart.

### Reconfiguring later

**Settings → Devices & services → Belgian Water Prices → Configure**
re-prompts only the **Options** step (consumption, persons, social
tariff, commune, water meter sensor).

To switch utility (you moved house, or the postcode auto-resolver
picked the wrong default the first time), open the entry's three-dot
menu and pick **Reconfigure**. You get a menu with two options:

- **Update postcode** re-prompts the postcode and re-runs the resolver
  -- the right choice after a move.
- **Pick the utility directly** jumps straight to the operator
  dropdown, bypassing the resolver -- the right choice when your
  postcode resolves to the wrong operator (e.g. a Pidpa ring commune
  actually served by Water-link).

If either path lands on a per-commune operator (De Watergroep / Farys
/ Pidpa / Water-link), a commune dropdown follows so you can pick the
exact commune in the same flow. When the postcode resolves to the
same operator you already have, the dropdown is pre-filled with your
current commune so you can submit as-is to just refresh.

Either path reloads the integration in place. Annual consumption and the
water-meter sensor carry over. Registered residents and the social
tariff are Flemish-only settings: they survive a move to another Flemish
operator and are dropped when the new one is not Flemish, since the
options step stops offering them. The saved commune is cleared when the
utility changes (the new operator uses different commune IDs, so a stale
value would silently fail at fetch time).

Switching operators can also strand price history: a Flemish comfort
rate has no counterpart in Wallonia, so its statistics would sit there
as a line that never moves again. Rows written earlier in the current
year are dropped, but anything from a previous year is kept -- the
recorder can only delete a statistic whole, and a real record of what
you paid is worth more than a tidy chart.

## Daily operation

### Refresh cadence

- **Tariff snapshot** — once every 24 h. Water tariffs are annual; a
  fresh January 1 publication is picked up within a day.
- **Projected cost** — recomputed every coordinator tick **and**
  immediately when you save new options, so changing your consumption
  or household size shows up without waiting for the next refresh.
- **Running cost / consumption** — `current_year_cost` and
  `year_to_date_consumption` update **live**: each time your configured
  water-meter sensor reports new usage, the running bill and YTD volume
  recompute immediately (in-memory, no extra recorder or network call).
  A live update never defers the 24 h tariff refresh above, however often
  the meter reports.
  The January 1 meter reading is anchored once from the recorder and
  then **persisted across restarts**, so the figure tracks the live
  meter as `live − baseline` and is not pulled back down to the
  recorder's lagging daily total on every restart or reload. A persisted
  record that cannot be read is dropped for a fresh anchor rather than
  blocking the entry. The figure
  is **monotonic within the year**: both the consumption and the EUR
  cost are clamped to their year-to-date high-water mark, so a momentary
  low meter reading, a transiently lower tariff fetch, or a mid-year
  options change that would lower the bill is never published as a
  decrease. The baseline only re-anchors on a genuine reset: the Jan 1
  rollover, a meter swap confirmed by several consecutive readings too low
  to belong to the year (a single low reading is held as a glitch), or
  pointing the integration at a different meter, which restarts the year's
  figure because the new meter's reading says nothing about the old one's.
  The same applies upward: a reading that climbs more than 100 m³ in one
  report is held until the next reading confirms it, so one garbage value
  cannot pin the year's figure while a real catch-up after a long outage
  still lands.
  If the recorder cannot be read at the moment the year rolls over, neither
  sensor starts the new year at 0: a query that failed is not the same as a
  year that is genuinely empty, and anchoring on it would discard
  consumption already recorded. The daily tick reports `unknown` in that
  state. A meter reading arriving before the tick has placed the new year
  cannot move the figure either, so the sensors hold the last value they
  published -- December's -- until the tick gets an answer. An install with
  no recorder at all is a genuinely empty year and does start from 0.
  The figure also heals itself upward. If the meter drops out and the
  recorder reports more consumption for the year than the anchor accounts
  for, the next daily tick re-reads the meter and the recorder together and
  moves the anchor under that figure, so the meter counts on from the larger
  number rather than having to climb back up to the old anchor first. It
  takes a tick rather than happening on the next reading, because only a
  meter reading and a recorder figure read at the same moment can say where
  the meter stood when that water was used.
  Responsiveness is bounded by how often your meter entity itself pushes
  a new state.

  Because the running cost is a high-water mark, a mid-year change that
  *lowers* your bill — enabling the social tariff, reducing the
  household size, or switching to a cheaper commune — is **not**
  reflected in `current_year_cost` until the next January 1; the
  `projected_annual_cost` sensor reflects it immediately.

### Keeping the projection honest

`projected_annual_cost` runs off the consumption figure you typed at
setup, which is a guess until your meter has measured a real year. Once
it has, the daily tick compares the two and, when the typed figure is
**10 %** or more off, raises a Repair under **Settings → Repairs**
showing both numbers. Its button writes the measured figure into the
options; ignoring the card keeps what you typed. Nothing is overwritten
without you pressing it.

A year only counts if the meter has statistics on both sides of it: a
bucket before 1 January proves it was already running when the year
started, one in that year's December proves it was still running at the
end. A meter installed in June therefore never produces a prompt. That
check is also why the prompt can appear the first day you wire up a
meter that has been recording in Home Assistant since before last
January, rather than only after a January 1 rollover.

A year in which the recorded register went backwards is skipped too,
which is what replacing a `total` meter looks like. A
`total_increasing` meter never gets that far: Home Assistant reads the
drop as a new cycle and its running sum climbs through the swap, which
already yields the figure this wants.

The year-to-date sensors are unaffected either way: they always read the
meter, never this setting.

### Failure mode

If a refresh fails, or does not finish within three minutes, the
coordinator keeps serving the last known snapshot and surfaces
`snapshot_age_hours`, `snapshot_stale` and `last_error` as attributes
on every sensor. Snapshots older than
**35 days**, or where the parsed `valid_until` has already passed,
flip `snapshot_stale` to `true` and raise a Repair issue under
**Settings → Repairs**. The Repair card carries a **Retry** button
that triggers an immediate coordinator refresh; the issue
auto-clears as soon as the next fetch returns a fresh snapshot. The
daily live-check workflow opens a GitHub issue against the
integration if the failure persists across CI runs.

### Price-history backfill

Long-term statistics back the price line you see in the History card
and the Energy dashboard's tariff overlays. A fresh entry has no
historical statistics, so the line normally starts at the install
moment.

On every first setup of a calendar year, the integration imports
hourly flat-line rows from **1 January of the current year up to the
previous full hour** for the five flat-line price sensors on the entry
(`yearly_fixed_fee`, `basis_rate`, `comfort_rate` on Flemish entries,
`sewerage_rate`, `all_in_basis_rate`). `projected_annual_cost` is
`MEASUREMENT`-class too but is deliberately not among them: it moves
with your options rather than with the tariff, so a flat line back to
January would be fiction. The start is clamped to the
tariff snapshot's `valid_from` so periods with no published source
are not invented. The auto-once gate is stamped onto the config
entry's data; when the calendar year rolls over the gate trips and
the next setup extends the line into the new year, unless the snapshot
is stale -- then it waits, so a year is never filled in with rates that
had already expired. The window also stops at the tariff's own
`valid_until`. The YTD sensors
(`current_year_cost`, `year_to_date_consumption`) are intentionally excluded
because their values come from the user's actual meter history.

To re-run the backfill on demand (e.g. after fixing a wrong tariff
or extending coverage to an earlier date), call the
`be_water_prices.backfill_prices` service:

```yaml
service: be_water_prices.backfill_prices
data:
  entry_id: 01H8WZK0WZK0WZK0WZK0WZK0WZ   # optional; default: every loaded entry
  start_date: "2026-01-01"                # optional; default: 1 January of the current year
  clear: false                            # optional; if true, wipe existing stats first
```

`clear: true` calls the recorder's `async_clear_statistics` on the
targeted entities before re-importing. That deletes those sensors'
statistics **in full** — every year of them, not just the window being
re-imported — because the recorder has no windowed delete. Whatever
sat before `start_date` is gone and is not written back. It
**requires** an explicit `entry_id`; the service rejects the blanket
combination (`clear: true` without `entry_id`) to keep one careless
call from wiping long-term statistics across every loaded entry.

You rarely need it: the default gap-fill upserts on
`(statistic_id, start)`, so re-running without `clear` overwrites the
window in place and is safely idempotent. Reach for `clear` only when
rows outside the window are themselves wrong.

### Diagnostics

**Settings → Devices & services → Belgian Water Prices →** three-dot
menu **→ Download diagnostics** dumps the active config, the last
parsed `WaterTariff` (every component plus validity window), the
fetch metadata, and the projected annual cost. Attach it when
reporting an issue.

## Known limitations

- **De Watergroep no-commune fallback uses Halle as a default commune.**
  Without a commune configured the extractor hits the cookie-driven
  endpoint with Halle's GUID (postcode 1500) and returns the full
  integrale waterprijs there. Saneringsbijdragen vary by commune in
  Flanders, so the result still under- or over-estimates slightly for
  users in other communes (typically off by tens of EUR/year vs ~200
  EUR/year before). Pick your commune in the OptionsFlow for exact
  numbers. If the cookie endpoint fails the integration falls back to
  the news-article snapshot (drinkwater leg only) so it never goes
  completely dark. That article is also less reliable than the
  endpoint: its 2026 figure (2,9521 €/m³) disagrees with the 2,9251
  the tariff pages publish.
- **Pidpa falls back to the May-2024 Tariefplan PDF** when the default
  commune page cannot be read. That PDF is a projection: its drinkwater
  column was never indexed and its saneringsbijdragen are frozen at
  2024, so its 2026 column sits about 14 % under the published rate
  (606 vs 705 EUR/year at 80 m³ for one resident). The fallback logs a
  warning and labels the snapshot `Tariefplan 2025-2030`. It was the
  default up to v0.7.3, and the weekly drift check could not see the
  gap because the PDF never changes. Pidpa charges the same rate in
  every commune, so the Geel default is exact; the OptionsFlow exposes
  the full sitemap-derived commune list anyway in case Pidpa starts
  varying rates per commune.
- **Mid-year tariff revisions are billed for the whole year.** A
  snapshot carries one rate and the previous one is not published, so
  when a utility changes a rate mid-year (INASEP moved its CVD on
  27 April 2026) the year-to-date cost bills the whole year's volume at
  the current rate. The tariff's `valid_from` attribute and the price
  backfill do follow the published date.
- **Wallonia régies communales** (~30 small operators -- Chimay,
  Theux, Libramont, ...) are deferred indefinitely. They have no
  central publication channel and the dev-hours / customer ratio
  doesn't justify per-régie extractors. Postcodes served by these
  operators are absent from the per-postcode table and drop into the
  manual picker rather than mis-defaulting to SWDE.
- **Aquaduin integrated rate.** Aquaduin's PDF only publishes one
  per-m³ figure (5.9908 €/m³ in 2026) for
  the integrale waterprijs basistarief -- it does not split drinkwater
  from sanering. We store the integrated value in `basis_eur_per_m3`
  with sanering = 0; the bill total is correct but the
  `basis_rate` sensor shows the integrated rate rather than
  drinkwater alone. Pidpa and AGSO Knokke publish split components
  and surface drinkwater-only on `basis_rate`.

## Development

```bash
ruff check .
ruff format --check .
mypy --strict custom_components/be_water_prices
mypy --strict scripts
pytest tests/
python scripts/live_check.py    # hits real utility endpoints
```

Tests run against fixture HTML and PDF snippets in
[`tests/fixtures/`](./tests/fixtures/) (real 2026 publications from
every registered utility). Refresh a fixture with the utility's
current page or PDF to re-run against new data; the file naming
convention is `<utility>_<year>.<ext>`.

Two cron workflows guard against silent regressions:

- [`.github/workflows/live_check.yml`](./.github/workflows/live_check.yml)
  runs daily, hits every registered extractor against its real
  publication URL, retries up to five times with exponential
  backoff, and opens / updates a GitHub issue titled
  `[live-check] water extractor broken …` on persistent parser
  failure. Transient upstream hiccups (timeout, connection reset,
  HTTP 5xx / 429) are reported as a `TRANSIENT` row and retried but
  never open an issue — only a real regression (parse / shape error,
  HTTP 3xx / 4xx) does, so a brief outage at a utility is not mistaken for a
  broken extractor. De Watergroep and Pidpa answer a no-commune install
  from a default commune page and fall back to a stand-in when that page
  cannot be read, so the check also probes those two pages directly:
  a broken page fails the run even though users keep getting a tariff.
- [`.github/workflows/fixture_drift.yml`](./.github/workflows/fixture_drift.yml)
  runs weekly, parses each utility's live publication and diffs
  the result against the parser's output on the committed test
  fixture. Opens / updates `[fixture-drift] water fixtures need
  refresh …` when any tariff field drifts above the threshold
  (rates `> 0.001` €/m³, fees `> 0.01` €/year). Catches *silent
  rate drift* that the live check misses. A run where a utility was
  unreachable on a blip exits 2 rather than 0, so it neither opens an
  issue nor comments "drift cleared" on an open one it did not recheck.

Both scripts skip Water-link in CI: its CDN HTTP-403s GitHub
Actions IP ranges. Reachable from residential IPs; rerun either
script locally to drift-check Water-link. The skip is keyed on the
runner's `GITHUB_ACTIONS` variable, so a local run does check it.

## License

BSD 2-Clause. See [LICENSE](./LICENSE).
