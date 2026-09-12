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
PDF. **Every per-distributor rate is fetched bar one**: AIEC publishes
its card only as a picture, so that one rate is transcribed from the
picture against the date its file name carries, and the card AIEC's page
is showing decides which transcription applies. The
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
- **Walloon CWaPE tiers** — first 30 m³ at `0.5·CVD + FSE` (CVA exempt on the residential first block), 31 to 5 000 m³ at full `CVD + CVA + FSE`, above 5 000 m³ at `0.9·CVD + CVA + FSE`, plus the regulator-defined `20·CVD + 30·CVA` redevance. Verified to the cent against inBW's published facture and AIEC's own worked table.
- **Brussels linear** — VIVAQUA's single-rate domestic tariff plus the annual fixed fee.
- **Postcode auto-resolution** — enter your postcode and the right utility is picked automatically. Fall through to a manual picker for the long tail.
- **Projected annual cost** — every entry has a `projected_annual_cost` sensor wired to your configured consumption (and household size + social-tariff opt-in for Flemish customers).
- **Year-to-date cost** — auto-detects your water meter from HA's Energy dashboard (Settings → Dashboards → Energy → Water consumption) and surfaces a `current_year_cost` sensor that reports your running bill since 1 January, computed from the recorder. Annual fees are pro-rated to the elapsed fraction of the year so the figure grows day by day instead of jumping to the full annual on Jan 1; the volumetric branch reuses the same regional bill math as the projected-cost sensor. The OptionsFlow exposes an explicit-override field for users who want to point at a different sensor than the Energy dashboard's choice. Only one meter is billed: if the dashboard lists several water sources the first is used, a warning says how many are being ignored, and the override field is how you pick.
- **Translated UI** — English, Dutch, French and German.
- **Projection kept honest** — once your meter has measured a whole calendar year, a Repair offers that real figure in place of the consumption you typed at setup, with both numbers shown. It never overwrites the setting on its own, and a year holding a bucket that claims more than the days behind it could hold is not offered at all: that is a re-based or reset counter rather than water, and it would otherwise be proposed as your yearly consumption. The days behind it count because a meter that was away comes back with the whole absence in one bucket, and a year that really did have an outage is still a year worth offering.
- **Postcode kept honest** — v2 entries store the postcode you typed, and the resolver is re-run on every refresh. If a later release corrects the operator your postcode resolves to, a Repair says so and names both operators rather than the correction only reaching new installs. It does not switch for you: changing operator also clears the commune and the Flemish household settings, so that stays a Reconfigure you drive.
- **Self-healing** — last-known prices keep serving on outage; `snapshot_age_hours`, `snapshot_stale` and `last_error` are surfaced as attributes, and a stale snapshot (>35 days or past the published `valid_until`) raises a Repair issue you'll see under **Settings → Repairs**. The card carries a **Retry** button that triggers an immediate refresh, and auto-clears on the next successful, fresh fetch. When a utility has not published its new card by 1 January, last year's card is served until 31 March before the snapshot counts as stale. A restart while the utility is down loads the entry on the last card the project's daily archive captured (see *The card archive* below) instead of retrying setup until the utility is back; a box in the options switches that off.
- **Price-history backfill** — on the first setup of each entry, a flat-line of hourly long-term-statistics rows is imported from 1 January of the current year up to now, so the History dashboard and Energy dashboard tariff overlays show a price line going back further than the install moment. It runs again by itself when anything the line is drawn from moves: the calendar year, the operator, the year of the card the rates came off, or the commune, since the gemeentelijke saneringsbijdrage is a commune's own number. Re-run on demand via the `be_water_prices.backfill_prices` service (start date and clear-first toggle).
- **Daily live check** — a cron-driven workflow probes every utility and opens a GitHub issue if any extractor breaks (page restyled, wrong year, etc.).
- **Weekly fixture drift check** — a second cron parses each utility's live publication and diffs the result against the parser's output on the committed test fixture; if any tariff field drifted by more than the threshold (rates `> 0.001` €/m³, fees `> 0.01` €/year), an issue is opened with the field-by-field deltas so the fixture can be re-captured. It only sees fields we read off a page, so it cannot notice a move in the decreed constants above; every Walloon parser fails outright if the CVA or FSE its page publishes leaves the constant behind, or if its page stops printing a CVA at all, which is what puts that move on the live check instead. Note what that means each 1 January: when CWaPE moves the CVA, every Walloon page picks it up at once and all nine Walloon extractors stop together, each entry holding its last good snapshot behind a stale-snapshot Repair, until a release carries the new `WALLONIA_CVA_EUR_PER_M3` / `WALLONIA_FSE_EUR_PER_M3` in `const.py`. That is deliberate: the constants are what the household is billed on, so failing open would bill everyone on last year's figure with nothing to show for it.

## Supported utilities

| Utility | Region | Coverage | Source |
| --- | --- | --- | --- |
| **AGSO Knokke-Heist** | Flanders (1 commune) | ~33 k | [`providers/agso_knokke.py`](./custom_components/be_water_prices/providers/agso_knokke.py) — bs4 walker over the per-component "Integrale waterprijs" table at `agsoknokke-heist.be/waterbedrijf/tarieven/tarieven-kleinverbruikers`. Page shows previous + current year side-by-side, each under a dated heading; parser picks the table dated with the year in force and falls back to the higher-priced one when a heading carries no year or no dated table has started yet, since rates only ever index up |
| **AIEC** | Wallonia (Condroz) | small | [`providers/aiec.py`](./custom_components/be_water_prices/providers/aiec.py) — AIEC publishes its card only as a picture on `eauxducondroz.be/Prix.htm`, and the picture's name carries the day it took effect (`Tarif-2026-04-1-*.jpg`). Its rate is transcribed in the module against that date: while the page shows a card that has been read, that rate is served and the aggregator is not consulted. A card nobody has read yet, or a page that has moved, falls back to `callmepower.be/fr/eau/distributeurs/aiec` dated from the picture, with a warning; a transient failure is raised instead, so a blip on that one-page site cannot quietly swap the operator's rate for the aggregator's. This is the one hardcoded per-distributor rate, and it earns it: the aggregator sat on 2,460 for the five months after AIEC moved to 3,050, under-billing an 80 m³ household by 53.16 €/year with nothing to show it. The weekly drift check compares against AIEC's own page, so a new picture fails it |
| **AIEM** | Wallonia (Molignée) | ~12 k connections (~25 k people) | [`providers/aiem.py`](./custom_components/be_water_prices/providers/aiem.py) — `aiem.be/prix-de-l-eau`. The page spells out formula examples (`0,5 x CVD (soit 1,435€)`) before listing the actual current value, so the shared parser anchors on `actuelle du CVD` to skip the example, and reads the `à partir du DD/MM/YYYY` next to it as the card's `valid_from` when that day is in the card's year |
| **Aquaduin** | Flanders (Westkust) | 6 communes (~80 k year-round) | [`providers/aquaduin.py`](./custom_components/be_water_prices/providers/aquaduin.py) — gold-standard numeric PDF linked from `aquaduin.be/nl/zelf-regelen/tarieven/tarieven-<year>` (the PDF URL is scraped from that page). Publishes a single integrated basistarief (drinkwater + sanering combined) rather than the usual split — the card also states the year it applies from ("Overzicht tarieven per 1 januari `<year>`"), and the parser holds the page URL's year to it |
| **CIESAC** | Wallonia (Clavier / Durbuy / Ouffet / Tinlot) | 4 communes | [`providers/ciesac.py`](./custom_components/be_water_prices/providers/ciesac.py) — same Callmepower path. **The rate is unverified**: `ciesac.be` has answered `ERR_UPDATING_SERVER` on every path for months, so there is no second source, and Callmepower prints this CVD with one decimal where every other Walloon operator publishes two or four |
| **CILE** | Wallonia (Liège region) | 24 communes (~560 k) | [`providers/cile.py`](./custom_components/be_water_prices/providers/cile.py) — clean 4-row HTML table on [cile.be/facturation/le-prix-de-leau](https://www.cile.be/facturation/le-prix-de-leau). Same pattern as SWDE / inBW: CVD parsed live, CVA / FSE cross-checked against the SPGE constants |
| **De Watergroep** | Flanders | 167 communes (~3.3 M, ~49.5 % share) — full integrale waterprijs out of the box, exact per-commune numbers when a commune is configured | [`providers/de_watergroep.py`](./custom_components/be_water_prices/providers/de_watergroep.py) — the cookie-driven `/Tarief/UpdateDetailTariefJaar/<year>` endpoint with `dwg_l=<GUID>` (default GUID = Halle / 1500 if no commune is configured, otherwise the user-picked one) returns the full per-commune bill (drinkwater + gemeentelijke + bovengemeentelijke saneringsbijdragen); the year is read off the answer's active tab, and while the new year's endpoint has nothing, last year's card is fetched from its own endpoint and stands until 31 March. There is no fallback under it: the news article `over-de-watergroep/nieuws/tarieven-<year>` used to be one and carries the drinkwater leg alone, which bills € 355,32 a year at 80 m³ where the Halle card bills € 782,73, with nothing on the entry to say which was served. When De Watergroep prints "De kostprijs kan momenteel niet getoond worden" in place of a saneringsbijdrage the fetch does not bill that leg at zero, and neither does a card that simply omits the row: all 699 commune pages carry both legs, so an absent one is a parser problem and never a commune that levies nothing. A commune whose page will not show a leg is served the operator default instead of nothing, labelled so you can see it (3660 Opglabbeek has printed that sentence for months and could not be set up at all); an omitted row still fails |
| **Farys** (TMVW) | Flanders (Oost-Vl. + parts of West-Vl. & Vl-Br.) | 85 communes (~1.5 M, ~22 % share) | [`providers/farys.py`](./custom_components/be_water_prices/providers/farys.py) — POSTs to the Drupal AJAX form at `farys.be/nl/watertarieven?ajax_form=1` with the commune ID baked in (Gent-centrum = 25071 by default) and parses the per-commune integrale waterprijs out of the `insert` command's HTML payload. Pick a different commune in the OptionsFlow to switch (~265 options; 23 phantom entries that Farys lists but doesn't actually serve are filtered out so users can't pick a crashing option). Five of the 266 communes are not on the default card, so their postcodes are pre-selected in the config flow: Drogenbos (1620) levies a lower gemeentelijke saneringsbijdrage, and 1930 / 1932 / 1933 / 1935 in Zaventem carry the tussenkomst below. A commune that pays part of the drinkwater leg for its residents has it printed as a negative `Gemeentelijke tussenkomst` row under the tarief it applies to, and the rate is netted before the card is checked against the integrale waterprijs Farys prints two rows further down (Zaventem covers € 0,0807/m³ of the basistarief) |
| **IDEN** | Wallonia (Nandrin / Tinlot / Modave) | 3 communes | [`providers/iden.py`](./custom_components/be_water_prices/providers/iden.py) — the operator's own card on `iden-eau.be/iden_web/fr/Tarification.awp`: three read-only fields headed "Depuis le 1er janvier `<year>`", carrying the CVD, the CVA and the Fonds social. The CVA and FSE rows are held to the SPGE constants like every other Walloon page |
| **IEG** | Wallonia (Mouscron) | ~50 k | [`providers/ieg.py`](./custom_components/be_water_prices/providers/ieg.py) — operator's own page at `ieg.be/eau/espace-client/facturation/structure-du-prix-de-leau/`. Uses the shared CWaPE residential tier math via [`_walloon_simple.py`](./custom_components/be_water_prices/providers/_walloon_simple.py) |
| **INASEP** | Wallonia (Namur sud) | 10 communes (~38 k subscribers) | [`providers/inasep.py`](./custom_components/be_water_prices/providers/inasep.py) — INASEP lists the CVD on the *Prix de l'eau et évolution* page under the heading "Coût-Vérité Distribution (CVD) = N,NNNN €/m³". Parser anchors on that heading (tolerating accent-stripped variants) and dates the tariff from the day the page says the CVD applies (27 April for the 2026 card) |
| **inBW** | Wallonia (Brabant Wallon) | 27 communes | [`providers/inbw.py`](./custom_components/be_water_prices/providers/inbw.py) — bs4 walker over the per-tier facture table on [eau.inbw.be/prix-de-leau](https://eau.inbw.be/prix-de-leau). The server's TLS chain is misconfigured (GoDaddy intermediate not sent). The fetch verifies first and only retries with `verify_ssl=False` after a TLS-specific failure, logging a warning when it does; risk note in the module docstring |
| **Pidpa** | Flanders | Antwerp province (~1.2 M) | [`providers/pidpa.py`](./custom_components/be_water_prices/providers/pidpa.py) — two paths: the per-commune `/ons-aanbod/je-gemeente/<slug>` HTML page, which carries the current published rates and which the no-commune fetch also reads for a fixed default commune (Geel), the rate 60 of the 63 communes pay. Nijlen, Wommelgem and Kasterlee publish a lower gemeentelijke saneringsbijdrage, so their postcodes are pre-selected in the config flow; and the multi-year `Tariefplan_2025-2030_simulatie_type_gezin.pdf` parsed via `pdfplumber`, a May-2024 projection whose 2026 column runs 14 % under the commune pages. It is no longer served as a fallback: a card that short is worse than none, so an unreadable page leaves the last good snapshot in place and raises the stale-snapshot Repair. The drift check still parses the PDF against itself. Commune list comes from Pidpa's public sitemap (63 communes) |
| **SWDE** | Wallonia | ~200 communes (~2.4 M, dominant Walloon distributor) | [`providers/swde.py`](./custom_components/be_water_prices/providers/swde.py) — bs4-anchored on the `<h3>` headings of [swde.be/en/water-prices-swde](https://www.swde.be/en/water-prices-swde) (the FR slug 4xxs, the EN one works). CVA / FSE come from the SPGE flat-Wallonia constants and the fetch fails if the page has moved off them. The page states no year, so the card is dated by the clock: a page left on last year's rates is never stale by date, and nothing detects a CVD left on last year's value (the CVA / FSE hold only fails the fetch when those two move) |
| **VIVAQUA** | Brussels | All 19 communes (~1.2 M) | [`providers/vivaqua.py`](./custom_components/be_water_prices/providers/vivaqua.py) — HTML table on [vivaqua.be/en/the-domestic-linear-rate](https://www.vivaqua.be/en/the-domestic-linear-rate/), picks the current-year section by header and divides by VAT to keep the ex-VAT convention |
| **Water-link** | Flanders (all of Antwerp city + Hove, Mortsel, Edegem, Beveren-Kruibeke-Zwijndrecht) | ~200 k | [`providers/water_link.py`](./custom_components/be_water_prices/providers/water_link.py) — the per-year PDF `water-link.be/sites/default/files/<YYYY>-<MM>/<YYYY>%20HH.pdf` parsed via `pdfplumber`, its link discovered from the Antwerpen tariff page since the upload directory carries the publication month. Drinkwater + zuivering are uniform across the service area; gemeentelijke afvoer differs per commune (Antwerpen at 1.3345 €/m³, ring communes at 1.9572). Defaults to Antwerpen; pick your commune in the OptionsFlow (Edegem, Hove, Mortsel, Beveren-Kruibeke-Zwijndrecht, …) to get the right sanering. Postcodes 2070 (Zwijndrecht / Burcht), 2540 (Hove), 2640 (Mortsel) and 2650 (Edegem) sit in the ring group on Water-link's own card, not in Antwerpen, so the config flow pre-selects their commune rather than letting the Antwerpen default stand; the city's own districts need no pre-selection because the card bills all of them on the Antwerpen row — the card also states the year it applies from ("Geldig vanaf 1 januari `<year>`"), and the parser holds the link's year to it |

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
| 1733, 1931, 1934, 1935, 9451 | secondary postcodes of a commune the other operator serves (Asse, Machelen, Zaventem, Haaltert) | the operator whose dropdown names that commune, since neither names the postcode itself |
| 2000-2070 | Antwerp city core | Water-link |
| 2099, 2100, 2140, 2150, 2170, 2180, 2600, 2610, 2660 | the city of Antwerp's other districts (Deurne, Borgerhout, Borsbeek, Merksem, Ekeren, Berchem, Wilrijk, Hoboken) | Water-link |
| 2540, 2640, 2650 | Hove, Mortsel, Edegem: ring communes with a billing row of their own on Water-link's card | Water-link |
| 2100-2999 (rest) | rest of Antwerp province | Pidpa *(Water-link is also active in Ranst 2520, Hemiksem 2620 and Schoten 2900 but bills none of them, and Pidpa lists all three, so those stay Pidpa)* |
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
intercommunales merge or distributors update ZDEOnMap. It aborts rather
than print a Walloon map or a carve-out that came back thin, so a walk
cut short by an outage or an interstitial page is never pasted in.

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
             + max(0, min(consumption, 5000) - 30) × (CVD + CVA + FSE)
             + max(0, consumption - 5000) × (0.9·CVD + CVA + FSE)
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
| `yearly_fixed_fee` | Vastrecht / redevance in EUR/year, ex-VAT, **as the card prints it** — before the per-resident korting and before VAT, which is how the operators publish it too. What a Flemish household is actually charged is `max(0, fee − persons × korting)`: € 80,00 ex-VAT at the default one resident against the € 100,00 shown here, and € 0,00 from five residents up. Parsed from the publication for VIVAQUA only; Flemish entries carry the decreed uniform vastrecht (50+30+20); Walloon entries use the regulator's formula `20·CVD + 30·CVA`, where the CVD is parsed and the CVA is the flat SPGE constant. |
| `basis_rate` | First-block (Flanders) or single-rate (Brussels) or CVD (Wallonia) in EUR/m³, ex-VAT. |
| `comfort_rate` | Flanders block 2 in EUR/m³, ex-VAT. Not created for Brussels or Wallonia entries (the concept is Flemish-only). |
| `sewerage_rate` | Sum of every sewerage / CVA / FSE component carried by the tariff in EUR/m³, ex-VAT. |
| `all_in_basis_rate` | The first-block tariff per m³, VAT-incl: `(basis + sanering) × (1 + VAT)`, before any social-tariff reduction. For Wallonia this is the **above-30 m³** headline; the first 30 m³ pays only `0.5·CVD + FSE` (use the projected-cost sensor for the actual bill). |

Between 1 January and the day your operator publishes the new card, both
cost sensors run at **last year's rates**, and `snapshot_stale` stays
`false` because the card is deliberately held valid until 31 March (see
the grace window above). The `valid_from` attribute is what tells you
which year is on screen. Once the new card lands the whole year to date
is re-billed at it, which is correct: Belgian tariffs apply from
1 January whatever day they are published. On the 2025 to 2026 Pidpa step, the running bill is corrected by about
€ 4 when the new card lands at the end of the grace window.

All five of the rows above are **tariff-card figures**: they are what your operator publishes, not what your household is charged. None of them applies the per-resident korting or the social tariff, and for Wallonia `basis_rate` / `sewerage_rate` / `all_in_basis_rate` describe the above-30 m³ tranche. On a Flemish social-tariff entry the charged figures are a fifth of these. Use `projected_annual_cost` and `current_year_cost` for what you owe.

| Entity id suffix | Description |
| --- | --- |
| `projected_annual_cost` | Projected VAT-incl annual bill in EUR for your configured consumption. Wired to your `consumption_m3_per_year`, plus `gedomicilieerd_persons` and `social_tariff` for Flemish entries. Updates immediately when you change options. |
| `current_year_cost` | Running VAT-incl bill in EUR **since 1 January** of the current year. Anchors the January 1 meter reading once from HA's recorder daily statistics and **persists it across restarts**, then tracks the configured water meter sensor **live** as `live − baseline` — recomputing on each meter reading — applies the same regional bill math as the projected-cost sensor, and pro-rates annual fees by elapsed-fraction-of-year. The figure only goes **down for a reason**: the EUR cost carries its own year-to-date high-water mark on top of the consumption clamp, so neither a momentary low meter reading nor a transiently lower tariff fetch is ever published as a decrease. The one thing that can lower it is the recorder, and only when it has already reported for this year and its latest answer is at or above every earlier one, so its history is intact. That is what takes back a meter spike small enough to have been admitted, which used to stand until January — the bill only drops to ~0 when the cycle restarts, which is the 1 January rollover, a confirmed meter swap, or pointing the integration at a different meter. The mark is measured for a household, so changing your commune, `gedomicilieerd_persons` or `social_tariff` rebuilds it from what you owe now rather than holding the old figure. Returns `unknown` until a water meter is configured in the options step. The meter's unit must be one Home Assistant can convert to m³ (`m³`, `L`, `gal`, `ft³`, `CCF`, …), or one that needs no converting because it already is cubic metres: no unit at all, or the ASCII spelling `m3`. A sensor labelled with anything else is refused rather than read as cubic metres, which is what a lowercase `l` used to do at 1000× the bill. |
| `year_to_date_consumption` | Cumulative m³ consumed since 1 January. Tracks the configured water meter sensor live (recorder-anchored baseline plus the live reading), clamped to the year's high-water mark, so the only thing that lowers it mid-year is a recorder answer with the year's own statistics behind it. Companion to `current_year_cost`. A daily statistics bucket claiming more than 100 m³ is ignored: no household draws that in a day, so it reads as a counter re-based onto the real meter reading or a register reset rather than water. That bound grows with the days behind the bucket, by the same cubic metre a day, because a bucket is not always one day of water: Home Assistant compiles nothing while a meter is away and puts the whole absence in the bucket it comes back in, so read as one day a real catch-up looked like a re-based register and was thrown away. The live meter is bounded too, and more tightly: one report may advance the year by 30 m³ before it is held for confirmation and put to the recorder, since an hour of a domestic connection at full bore is about 20 m³ and a household uses 80-100 m³ in a year. A meter that has been out of sight gets more room, in proportion to how long it was away: it comes back showing everything drawn while it was down, and the allowance grows by a cubic metre for each day of the gap, which is several times what any household draws in one. That gap is measured across restarts, since Home Assistant being down is the longest a meter goes unseen. The recorder is not short of that water, whatever the gap: Home Assistant carries the running total forward and attributes the whole increase to the moment the meter comes back, so the two agree again as soon as it does. A recorder answer read in the same round still settles a step that goes beyond what the gap allows. And a recorder answer that still has the year behind it settles the round outright: it reads the same meter's own statistics for the whole year, so water it has no record of did not flow. The figure resumes as soon as a reading and the recorder agree. |

Each sensor exposes `utility`, `region`, `valid_from`, `valid_until`,
`publication_label`, `source_url`, `snapshot_age_hours`, `snapshot_stale`
and `last_error` as attributes for dashboards and automations. The
publication label, the source URL and the error text are scrubbed of the
commune before they are published, so an entry configured for one commune
does not name it on every sensor.

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
   Antwerp province (2000-2999) to Pidpa except the city and the three
   ring communes with their own row on Water-link's card, the rest of
   Flanders (1500-1999, 3000-3999 and 8000-9999) to De Watergroep, Farys,
   Aquaduin or AGSO Knokke-Heist by commune, and Wallonia (1300-1499
   and 4000-7999) per the regulator's distribution zones. A postcode no
   supported operator serves falls through to step 2.
2. **Utility** *(only if step 1 didn't resolve)* — pick from the dropdown
   of registered utilities. For the seven postcodes genuinely split
   between two operators at street level (1770 Liedekerke,
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
     Antwerpen for Water-link, Geel for Pidpa). Where an operator's
     card puts a postcode somewhere other than its default, the flow
     pre-selects that commune for you: Water-link's 2070, 2540, 2640
     and 2650, Pidpa's Nijlen, Wommelgem and Kasterlee, and Farys's
     Drogenbos and four Zaventem postcodes. An entry that already
     exists picks the same commune up on its next load, so upgrading is
     enough and no reconfigure is needed. Everywhere else the default is
     what 60 of Pidpa's 63 communes pay and what 96 % of De
     Watergroep's communes pay, but the saneringsbijdragen do vary,
     so the projected-cost sensor can be out by up to ~59 EUR/year
     (De Watergroep's widest gap, Overijse) until you pick your own.

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
   - **Read the project's card archive** — on by default. When a
     refresh fails and the entry has nothing cached to serve (a restart
     while the utility's site is down), the last card the project's
     daily archive holds for your utility and commune is served instead
     of failing setup. The request to GitHub names the utility and the
     commune id and nothing else; untick the box and the integration
     never contacts GitHub.

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
operator, are dropped when the new one is not Flemish, since the
options step stops offering them, and are asked for on a move into
Flanders from Brussels or Wallonia, since the Flemish tariff prices on
them. The saved commune is cleared when the
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
  **only goes down for a reason**: both the consumption and the EUR cost
  are clamped to their year-to-date high-water mark, so a momentary low
  meter reading, a transiently lower tariff fetch, or a mid-year options
  change that would lower the bill is never published as a decrease.

  The exception is the recorder, and only under two conditions: it has
  already reported for this year, and its latest answer is at or above
  every earlier one, which is what says its history is whole rather than
  half purged. There used to be a third, about water the recorder could
  not account for, and it turned out to be answering a question that never
  arises: Home Assistant carries a meter's running total across any gap and
  attributes the whole increase to the moment it comes back, so an outage
  costs the recorder nothing and a real catch-up needs no protecting from
  it. Then it is not a dip but the year's own statistics, and the figure is
  corrected to them, frame and cost floor included. That is what takes back
  a meter spike small enough to have been admitted in the first place,
  which otherwise stood until January.

  The baseline only re-anchors on a genuine reset: the Jan 1
  rollover, a meter swap confirmed by several consecutive readings below
  the water the year has already used that also agree with each other (a
  single low reading is held as a glitch, and a reading standing nowhere
  near the ones before it begins a fresh run rather than completing
  theirs), or pointing the integration at a different meter, which restarts
  the year's figure because the new meter's reading says nothing about the
  old one's. A sustained run of readings that stay above the year's own
  consumption but below the baseline is read the other way round: no
  replacement register could show that much water, so the baseline is what
  is wrong. It was set too high by an overstated reading, and it is rebuilt
  under the meter with the year's figure left untouched, which puts live
  tracking back to work instead of restarting the year at zero.
  The same applies upward: a reading that climbs more than 30 m³ in one
  report is held until the next reading confirms it, the bound growing by
  a cubic metre for each day the meter was out of sight, so one garbage
  value cannot pin the year's figure while a real catch-up after a long
  outage still lands. Two readings agreeing say where the meter is,
  though, not where it stood in January, so a confirmed climb that still
  stands that far above the year's own figure is read as a baseline
  sitting under the meter: with a recorder answer in hand the baseline is
  rebuilt under the reading instead of the difference being billed.
  A genuine catch-up is not affected: the recorder sees the same water and
  agrees. With no recorder answer in that round there is nothing to weigh
  the baseline against, so the figure holds where it is and the next daily
  tick brings one.
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

  The high-water mark is measured for a *household*: your operator, your
  commune, and the `gedomicilieerd_persons` / `social_tariff` options.
  Change any of those and the floor is rebuilt from what you owe now, so
  enabling the social tariff or picking your commune lowers
  `current_year_cost` straight away.

  It is also rebuilt once by any release that changes it, so a rate this
  integration itself corrects downwards reaches `current_year_cost` the
  day the fix ships rather than on the next January 1. That is safe
  because the m³ figure carries its own high-water mark, so a recomputed
  bill can only come out lower when the rates really are lower.

  It is rebuilt once more when your operator finally publishes the new
  year's card. Until 31 March a late publisher's entry runs on last year's
  card, so January onwards accrues at a stand-in's rates; those are not a
  transient fetch, and if the real card comes in cheaper the floor would
  otherwise hold you on the stand-in until the year turned.

  What the floor still holds against is a **tariff fetch that comes back
  cheaper** — a transient bad parse, or a fallback card. A genuine
  mid-year price cut by your operator therefore only reaches
  `current_year_cost` on the next January 1, while
  `projected_annual_cost` reflects it immediately.

  Registering *more* residents raises a Flemish bill rather than lowering
  it, since the vastrecht korting is capped at five but the basisvolume
  is not: 80 m³ on Farys/Gent costs € 586,38 at four residents and
  € 791,28 at one.

  Four Flemish operators print the sum of their own three legs next to
  them, and each of those parsers checks its legs against that sum:
  Water-link's fourth column, AGSO Knokke's "Integrale waterprijs excl.
  BTW" row, Pidpa's fifth column and Farys's "Integrale waterprijs
  basistarief". It catches a cell read from the wrong column even when
  the VMM 2× rule still holds, and never goes stale the way a pinned
  Flanders-wide constant would.

  If your Energy dashboard lists more than one water source, the
  integration bills whichever it stores first, which reflects the order
  they were added and nothing about which meter your utility invoices.
  There is no safe automatic answer (summing would double-count a
  sub-meter and over-bill a rainwater or well meter), so a Repair card
  asks you to pick one in the options; it clears once you do.

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
end, and the year needs a bucket on at least two days in three in
between, so a meter that was unavailable for most of it does not pass
off what it saw as the year. A meter installed in June therefore never
produces a prompt. That
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

The cached snapshot lives in memory, so a restart while the utility is
unreachable used to leave the entry retrying setup until the site was
back. Now the coordinator asks the project's card archive (the
[`archive`](https://github.com/renaudallard/homeassistant_be_water_prices/tree/archive)
branch, one JSON per utility, commune and month; this month's row, then
last month's) and loads on the card it captured, dated the day of the
capture so the 35-day staleness clock runs from there, with the failure
in `last_error`. Later failures serve that card as the cached snapshot;
the next successful fetch replaces it. The request names the utility
and the commune id and nothing else, and the *Read the project's card
archive* box in the options turns it off.

### Price-history backfill

Long-term statistics back the price line you see in the History card
and the Energy dashboard's tariff overlays. A fresh entry has no
historical statistics, so the line normally starts at the install
moment.

On the first setup of a calendar year, and on any later daily tick that
finds the gate below has moved, the integration imports
hourly flat-line rows from **1 January of the current year up to the
previous full hour** for the five flat-line price sensors on the entry
(`yearly_fixed_fee`, `basis_rate`, `comfort_rate` on Flemish entries,
`sewerage_rate`, `all_in_basis_rate`). `projected_annual_cost` is
`MEASUREMENT`-class too but is deliberately not among them: it moves
with your options rather than with the tariff, so a flat line back to
January would be fiction. The start is clamped to the
tariff snapshot's `valid_from` so periods with no published source
are not invented. The auto-once gate is stamped onto the config
entry's data and carries the calendar year, the operator, the year of
the card the rates came off, and the commune. It trips when any of the
four changes, so the line extends into a new year, follows an operator
change, is redrawn when you pick your own commune (the gemeentelijke
saneringsbijdrage is a commune's own number, so `sewerage_rate` and
`all_in_basis_rate` both carry it), and is rewritten once your utility
finally publishes the new card: publishers run late, last year's card
stands until 31 March, and without the card year in the gate January's
line kept last year's rate for good. The
daily tick checks the gate too, so an install that never restarts
between January and the card landing still gets the rewrite. If the
snapshot is stale it waits instead, so a year is never filled in with
rates that had already expired. The window also stops at the tariff's own
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
rows outside the window are themselves wrong. A `start_date` at or
past the end of the window (now, or the card's `valid_until`) writes
and clears nothing, and says so in the log at info level.

### Diagnostics

**Settings → Devices & services → Belgian Water Prices →** three-dot
menu **→ Download diagnostics** dumps the active config, the last
parsed `WaterTariff` (every component plus validity window), the
fetch metadata, and the projected annual cost. An entry that has not
loaded dumps its state and config with an empty snapshot. Attach it
when reporting an issue.

## Known limitations

- **Water drawn while Home Assistant is down lands on the day it comes
  back, and across 1 January that means the wrong year.** Home Assistant
  measures a period's consumption against the last statistic it compiled
  before that period, so a host that is off from 20 December to 10 January
  attributes everything drawn in between to 10 January. The year-to-date
  sensors then carry three weeks of December in their January figure,
  about 4,5 m³ or € 60 on a Flemish bill at 80 m³/year. Nothing in the
  data says when during the gap the water actually flowed, so splitting it
  would be a guess dressed up as a measurement, and the figure corrects
  itself the following January. The projected-cost sensor is unaffected.
- **De Watergroep no-commune fallback uses Halle as a default commune.**
  Without a commune configured the extractor hits the cookie-driven
  endpoint with Halle's GUID (postcode 1500) and returns the full
  integrale waterprijs there. Saneringsbijdragen vary by commune in
  Flanders, so the result still under- or over-estimates slightly for
  users in other communes. Measured across all 699 commune pages the
  mean error is € 0,43/year and the worst real case € 58,65 (Overijse,
  gemeentelijke 1,4039). Pick your commune in the OptionsFlow for exact
  numbers. There is no fallback under the cookie endpoint: the
  news-article snapshot used to be one, it carries the drinkwater leg
  alone, and it disagrees with the tariff pages anyway (2,9521 €/m³
  against 2,9251). If the endpoint cannot be read the last good
  snapshot keeps serving behind the stale-snapshot Repair.
- **Aquaduin's January fallback depends on last year's page.** When the
  new year's tariff page is not up yet, the extractor reads the PDF
  link off last year's page, and Aquaduin strips that link once a year
  is over (the 2025 page carried none by September 2026). If the link
  is already gone when the new page is late, the fetch fails and the
  cached card keeps serving with the stale-snapshot Repair up until the
  new card is published.
- **Pidpa's default commune page has nothing under it either.** The
  May-2024 Tariefplan PDF used to stand in when the page could not be
  read; it is a projection whose drinkwater column was never indexed
  and whose saneringsbijdragen are frozen at 2024, so its 2026 column
  sits about 14 % under the published rate (606 vs 705 EUR/year at
  80 m³ for one resident). A card that short is worse than none, so an
  unreadable page now leaves the last good snapshot in place. The
  weekly drift check still parses the PDF against itself. **Pidpa does
  not charge one rate province-wide**: Nijlen, Wommelgem and Kasterlee
  publish a lower gemeentelijke saneringsbijdrage than the other 60
  communes, and their postcodes are pre-selected for you. Anywhere else
  in the province the Geel default is the rate you pay.
- **Mid-year tariff revisions are billed for the whole year.** A
  snapshot carries one rate and the previous one is not published, so
  when a utility changes a rate mid-year the year-to-date cost bills
  the whole year's volume at the current rate. INASEP moved its CVD
  from 2,9952 to 3,6734 on 27 April 2026, which is € 13,71/year too
  much on an 80 m³ bill and € 28,05 at 150 m³. Pricing the two periods
  apart would need the superseded rate, which no page publishes and a
  fresh install has never seen. The tariff's `valid_from` attribute and
  the price backfill do follow the published date.
- **Consumption above the tranches the engine models is not the
  operator's tariff.** The Walloon branch prices the CWaPE residential
  structure up to `0.9·CVD` above 5 000 m³. SWDE publishes a further
  `0.7·CVD` above 25 000 m³ and CILE 85 / 80 / 70 % above 50 000, and
  neither figure is on the page the extractor reads. No household meter
  reaches those volumes; a building-wide meter that does is on a
  professional tariff with a different redevance and a meter-diameter
  charge, so the card we hold is the wrong card for it either way.
- **The decreed constants are only as current as the release.** When
  CWaPE moves the CVA on 1 January, every Walloon household is billed
  on last year's figure until a release carries the new one. The
  operators' own pages lag too (INASEP still printed the 2025 CVA on
  day 117 of 2026), so they cannot be used to detect the move: reading
  the CVA off the page instead of the constant is the fail-open this
  integration deliberately rejects.
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
pip install -r requirements-dev.txt
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
  backoff, and opens or updates one GitHub issue, found by its
  `live-check` label (`[live-check] water extractor broken …`), on
  persistent parser failure. The failing utilities are the fingerprint:
  a utility that stays broken gets one comment a week rather than one a
  day, and a failure that changes shape is posted at once. Transient
  upstream hiccups (timeout, connection reset,
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
  fixture. Opens or updates one issue, found by its `fixture-drift`
  label (`[fixture-drift] water fixtures need refresh …`), when any
  tariff field drifts above the threshold (rates `> 0.001` €/m³, fees
  `> 0.01` €/year), the drifted fields being the fingerprint so an
  unchanged drift gets one comment a week. Catches *silent
  rate drift* that the live check misses. A run where a utility was
  unreachable on a blip exits 2 rather than 0, so it neither opens an
  issue nor comments "drift cleared" on an open one it did not recheck.

Both scripts skip Water-link in CI: its CDN HTTP-403s GitHub
Actions IP ranges. Reachable from residential IPs; rerun either
script locally to drift-check Water-link. The skip is keyed on the
runner's `GITHUB_ACTIONS` variable, so a local run does check it.

Both checks read the archive branch's texts when a checkout is given
(`--texts tmp/archive`, which the workflows pass after fetching the
branch): a PDF whose bytes the archive already holds is downloaded and
parsed as before, but its text is taken from the branch instead of being
rendered again, and the report ends with how many were served that way.

The readers in `providers/_pdf.py` carry two seams for scripts that walk
every utility in one go, both off in Home Assistant itself: a text memo
(`memoise_text_fetches`) that serves a page or a rendered PDF read twice
in one walk from memory, and a render hook (`render_through`) that is
handed the bytes of every downloaded PDF before they are rendered, so a
caller can skip the render of a card it has already seen or keep the
bytes. A provider that obtains a PDF some other way than through
`fetch_pdf_text_layout` renders it with `render_pdf`, so the hook still
sees it.

### The card archive

`scripts/archive_cards.py` walks every registered utility, fetches the
tariff the integration would price on right now, once for the utility's
default and once for each commune a per-commune utility lists, and
writes what it parsed into a checkout of the `archive` branch:

```bash
python scripts/archive_cards.py --out tmp/archive --pdfs tmp/pdfs [--only pidpa]
```

The branch holds, per utility and commune, one JSON file per month
(`<utility>/<commune>/<YYYY-MM>.json`, `default` being the no-commune
fetch, otherwise the commune id the integration uses with the commune's
label inside the file), the text of every page and rendered PDF a parse
read (`texts/<sha256>.txt`, stored once and shared between the rows that
read it), and a manifest of where each PDF is kept. A water tariff is
annual, so a month whose card is the same as the previous month's points
at the texts that month already holds rather than storing the page again.
A day on which nothing changed writes nothing; months older than three
years are removed, with the texts nothing refers to any more.

The PDFs themselves (Aquaduin's, Pidpa's and Water-link's cards) are kept
under `--pdfs` for upload to the releases of the shared cards repository,
named by their SHA-256; a card whose bytes have not changed is served the
text the branch already holds instead of being rendered again. When the
parser sources change, every stored month is replayed offline through the
current parser from its stored texts, the clock pinned to the day the
row was captured, and rewritten where the parse came out differently
(`--reparse` forces it, `--rerender` also renders every kept PDF afresh
for a reader upgrade). `--index-only` rewrites the listing on the branch,
`coverage.md` and one sheet per utility under `coverage/` (per commune,
the months held, each linking to the PDF or the page it was parsed from
and to the parsed JSON), without fetching anything.

[`.github/workflows/archive_cards.yml`](./.github/workflows/archive_cards.yml)
runs the archiver every morning at 05:23 UTC against the `archive`
branch, walking the communes of the per-commune utilities on Sundays
and the sixteen default rows only on the other days (the tariffs are
annual, and De Watergroep alone lists about seven hundred communes; a
manual run walks them unless its `communes` input is unticked, and
`--defaults-only` is the local equivalent), uploads the PDFs of the day
to the releases of the shared cards
repository [`be_price_cards`](https://github.com/renaudallard/be_price_cards)
(`water-<YYYY-MM>`, one per month the cards were seen in; the electricity
integration's live beside them as `electricity-<YYYY-MM>`), rewrites the
index and the per-utility sheets so each month links to a file that
exists, publishes them under `water/` in that repository, and commits
the branch when anything changed. The upload needs a fine-grained token
with contents read and write on the cards repository in the
`BE_WATER_CARDS` secret; without it the branch still gets the parsed
cards and their texts and the step says so. A run that stores nothing, a
refused push or an expired token files an issue labelled `archive-cards`,
one per problem with a comment per further failing run, and the token's
expiry is announced two weeks ahead the same way. A manual run can ask
for `--reparse` or `--rerender`.
Water-link is skipped on a runner, as the checks skip it; an archive run
from a residential address stores it.

**Finding a stored card by hand.** Everything on the
[`archive`](https://github.com/renaudallard/homeassistant_be_water_prices/tree/archive)
branch is addressed by the two ids the integration uses, which are the
directory names: the utility (`pidpa`, `farys`, `inbw`, ...) and the
commune, as the id the integration passes to the extractor (`geel` for
Pidpa, the numeric id for Farys, the GUID for De Watergroep, the name for
Water-link), or `default` for the no-commune fetch. Browse the branch to
see them; the commune's label is inside each file.

1. **The parsed card** is one JSON per month at
   `<utility>/<commune>/<YYYY-MM>.json`, for example
   [`pidpa/geel/2026-09.json`](https://github.com/renaudallard/homeassistant_be_water_prices/blob/archive/pidpa/geel/2026-09.json).
   It holds the tariff exactly as the integration parsed it, plus
   `_seen_on` (the day it was captured), `_commune` and `_commune_label`
   for a commune row, and `_sources`: every page or document the parse
   read, each with its text file under `texts/` and, for a PDF, the
   digest of the file.
2. **The page or the PDF the parser read** is easiest through
   [`coverage.md`](https://github.com/renaudallard/homeassistant_be_water_prices/blob/archive/coverage.md)
   at the branch root, which names one sheet per utility under
   `coverage/`: a row per commune, a column per month. Each month cell
   carries two links: `page` opens the text of
   the page as it was read, on the branch (`pdf` downloads the card from
   the cards repository's releases instead, for a card parsed from a PDF),
   and `json` opens the parsed row above. The same sheets are published
   under
   [`water/`](https://github.com/renaudallard/be_price_cards/tree/main/water)
   in the cards repository itself, and each release's notes point there,
   so a file seen on the releases page can be named too: search that
   repository for the file's name. Behind them is `pdfs.json`, which maps a digest to
   `water-<YYYY-MM>/<digest>.pdf` in those releases; the digest in a
   JSON's `_sources` is the same key.
3. **The text the parser read** is under `texts/`, named by the digest
   of the text itself and listed in the JSON's `_sources`, for checking a
   figure against the page without fetching it again. A month whose card
   is the same as the previous month's names that month's text, so the
   same page is not stored twelve times a year.

The integration itself reads the branch in one case: a refresh that
fails with nothing cached to serve, which is a restart while the
utility's site is down. It asks for this month's row of its utility and
commune (`default` without a commune), then last month's, and loads on
the card it finds, dated the day it was captured, rather than retrying
setup until the site is back. Every other refresh goes to the utility.

## License

BSD 2-Clause. See [LICENSE](./LICENSE).
