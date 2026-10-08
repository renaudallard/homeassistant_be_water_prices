# Pidpa

Module: `custom_components/be_water_prices/providers/pidpa.py`, the card
built by `build_flanders_tariff` in
`custom_components/be_water_prices/providers/_flanders.py`.
Tests: `tests/test_pidpa.py`, plus the Pidpa cases in
`tests/test_parser_cross_checks.py`, `tests/test_per_commune.py` and
`tests/test_prior_year_card.py`. Fixtures:
`tests/fixtures/pidpa_geel_2026.html` (a commune page) and
`tests/fixtures/pidpa_tariefplan_2025-2030.pdf` (the projection).

Pidpa supplies most of the Antwerp province, about 1.2 M inhabitants.

## Where the tariff is

Two paths; only the first one serves a card:

- **The per-commune page**,
  `https://www.pidpa.be/ons-aanbod/je-gemeente/<slug>`
  (`COMMUNE_URL_FMT`). One `<table>` per year (2018 to 2026 on the fixture)
  inside a tabbed widget, carrying the current published rates. The
  no-commune fetch reads it too, for a fixed default commune (Geel). This
  is the only path that serves a card.
- **The Tariefplan PDF**,
  https://www.pidpa.be/sites/default/files/2024-05/Tariefplan_2025-2030_simulatie_type_gezin.pdf
  (`SOURCE_URL`), parsed with pdfplumber by `fetch_tariefplan` and
  `parse_tariff`. It is a May 2024 projection: the drinkwater column was
  never indexed and the saneringsbijdragen paragraph is frozen at 2024,
  so its 2026 column runs about 14 % under the commune pages (2,0848,
  1,6533 and 1,1809 against 2,1888, 1,9572 and 1,7019, which bills
  606,21 EUR a year at 80 m³ against 704,68). It used to be the default
  and then the fallback, which is how that gap shipped for months. It is
  no longer served: a card that short is worse than none, so an
  unreadable commune page leaves the last good snapshot in place and
  raises the stale-snapshot Repair. Only the drift check still parses it,
  against itself.

## Region and communes

- Flanders. The resolver sends 2000 to 2999 here
  (`providers/_postcodes.py`), except 2000 to 2070 and the other postcodes
  Water-link bills on a row of its own. Ranst, Hemiksem and Schoten stay
  with Pidpa: Water-link lists them without billing them.
- Commune list: `list_communes` reads the slugs out of Pidpa's public
  sitemap, https://www.pidpa.be/sitemap.xml (`SITEMAP_URL`), 63 communes.
  The label is the slug with each hyphenated token capitalised.
- Phantom filter: `PIDPA_UNSERVABLE_SLUGS` in
  `custom_components/be_water_prices/_phantom_blocklists.py` holds
  `antwerpen`, which the sitemap lists although the city is Water-link's
  and its page has no household table. It is dropped from the list, and an
  entry that saved it has it removed on load
  (`_drop_phantom_commune_if_blocked` in `__init__.py`).
- Default commune: `geel` (`_DEFAULT_COMMUNE_SLUG`), labelled "Geel (Pidpa
  default)". Its page carries the rate 60 of the 63 communes pay.
- Pre-selected postcodes (`commune_for_postcode`): Nijlen (2560),
  Wommelgem (2160) and Kasterlee (2460) publish a lower gemeentelijke
  saneringsbijdrage, 1,7019, 1,7814 and 1,9078 against 1,9572 for the
  other 60 (checked against all 63 pages on 2026-09-08). Left on the Geel
  default they were over-charged 27,06, 18,64 and 5,24 EUR a year, so the
  config flow pre-selects their commune, and an entry with no commune
  gets it on load (`_adopt_commune_for_postcode` in `__init__.py`).

## How each figure is read

### The commune page

`parse_commune_tariff`:

- The table (`_find_year_table`): a `tariff-tab-content` div whose id ends
  in `-tab-<year>`, nested in the outer `tariff-tab-content` whose id ends
  in `-tab-0` (Huishoudelijk), holding a table with "Integrale
  waterprijs". The walk up to the outer tab keeps the business table out.
- Layout: five rows (header, vastrecht, korting, basistarief,
  comforttarief) and five columns (label, drinkwater, afvoer, zuivering,
  integrale). Cells read "2,1888 euro excl. btw - 2,3201 euro incl. btw";
  `_EX_VAT_AMOUNT_RE` takes the ex-VAT half.
- `basis_eur_per_m3`: basistarief row, drinkwater cell (2,1888 for Geel).
- `comfort_eur_per_m3`: comforttarief row, drinkwater cell (4,3776), held
  to twice the basis within 0,01 (VMM 2x rule).
- `sanering_gemeentelijk_eur_per_m3`: basistarief row, afvoer cell
  (1,9572).
- `sanering_bovengemeentelijk_eur_per_m3`: basistarief row, zuivering cell
  (1,7019).
- Sum check: the fifth cell of the basistarief row is the page's own sum,
  and the three legs must make it within 0,0001.
- `yearly_fixed_fee` 100 and per-resident discount 20: the VMM constants
  in `const.py`; the page's vastrecht and korting rows are not read.
- VAT: ex-VAT figures; `vat_rate` is 6 %. `build_flanders_tariff` refuses
  a rate of zero or less, a rate outside 0.5 to 20 EUR/m³, and a negative
  sanering.
- Year: the Belgian clock's year. When the page has no tab for it (the
  1 January window before Pidpa publishes the new tab), the newest
  household tab up to that year is used, logged as a warning, and the
  card stands until 31 March (`carry_prior_year_card`). A tab dated past
  the year asked for is never a fallback. An explicitly asked year that is
  missing raises.
- Label: "Pidpa per-commune tarieven `<year>` (`<commune>`)"; `source_url`
  is the commune page.

### The Tariefplan PDF

`parse_tariff`, for the drift check only:

- The year header after "Drinkwatertarief" and the "basistarief
  huishoudelijk" and "comforttarief huishoudelijk" rows; the column for the
  year asked for (2,0848 and 4,1696 for 2026). A header and row of
  different lengths raise. Past 2030 the last column is served with a
  warning.
- The sanering lines "(afvoer) ... : N €/m³" and "(zuivering) ... : N
  €/m³" (`_sanering_for_year`): this year's line first, then an undated
  one, then the latest dated up to this year. The PDF dates its sanering
  2024 (1,6533 and 1,1809).
- The 2x rule applies here too.

## Failures

- The no-commune fetch has nothing under it. `TransientFetchError`
  (network error, timeout, HTTP 5xx or 429) and every `ExtractorError`
  reach the coordinator as they are, and the PDF is not fetched.
- `ExtractorError`: no household table for the year, a table short of rows
  or columns, an unreadable ex-VAT cell, a comfort rate that is not twice
  the basis, legs that do not make the printed total, a sitemap with no
  commune slugs.

## Checks

- Live check: the default fetch, no CI skip. The commune list floor
  (`MIN_COMMUNES`) is 50; the lister returned 63 in October 2026.
- Drift check (weekly), two rows: "Pidpa (Geel default)" against
  `pidpa_geel_2026.html` through `fetch_for_commune(s, "geel")`, and
  "Pidpa (PDF fallback)" against `pidpa_tariefplan_2025-2030.pdf` through
  `fetch_tariefplan`, so a change in the projection is still noticed.

## Quirks

- `scripts/fixture_drift.py` gives this utility as the case it catches:
  the PDF projection (basis 2,0848 for 2026) shipped beside the live
  page's rate (2,1888) for several months, a log line the only signal.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
