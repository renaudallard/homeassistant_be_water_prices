# AGSO Knokke-Heist

Module: `custom_components/be_water_prices/providers/agso_knokke.py`, the
card built by `build_flanders_tariff` in
`custom_components/be_water_prices/providers/_flanders.py`.
Tests: `tests/test_agso_knokke.py`, plus the AGSO cases in
`tests/test_parser_cross_checks.py` and `tests/test_prior_year_card.py`.
Fixture: `tests/fixtures/agso_knokke_2026.html`.

AGSO Knokke-Heist is the water utility of the single commune Knokke-Heist,
about 33 k inhabitants.

## Where the tariff is

- One HTML page:
  https://www.agsoknokke-heist.be/waterbedrijf/tarieven/tarieven-kleinverbruikers
  (`SOURCE_URL`), read with BeautifulSoup.
- The page shows the previous and the current year side by side, one
  "Integrale waterprijs" table each, under a dated heading. The two shapes
  seen so far are "OVERZICHT TARIEVEN 2025" and "OVERZICHT TARIEVEN PER
  1/1/2026" (`_YEAR_HEADING_RE`).
- The 2026 table on the fixture:

| Tariefschijf | Basis | comfort | tot 1.000m³ | >1.000m³ | Vast recht | Korting |
| --- | --- | --- | --- | --- | --- | --- |
| Drinkwater | € 2,3295 | € 4,6590 | € 2,7073 | € 2,1658 | € 50,00 | -€ 10,00 |
| Afvoer afvalwater | € 1,9572 | € 3,9144 | € 2,2173 | € 2,2173 | € 30,00 | -€ 6,00 |
| Zuivering afvalwater | € 1,7019 | € 3,4038 | € 1,9281 | € 1,9281 | € 20,00 | -€ 4,00 |
| Integrale waterprijs excl. BTW | €5,9886 | € 11,9772 | € 6,8527 | € 6,3112 | € 100,00 | -€ 20,00 |

The basis column is headed "Basis tot 30m³/WE + tot 30m³/gedomic.", the
two middle columns are the non-household rates, and an "Incl. BTW" row
follows.

- There is no fallback source.

## Region and communes

- Flanders. The resolver sends postcodes 8300 and 8301 here
  (`providers/_postcodes.py`), ahead of the Farys default for the
  8000-9999 block.
- One commune, so no commune picker.

## How each figure is read

Rows are found by a case-insensitive substring of their first cell
(`_row_first_amount`), and the first euro amount in the wanted column is
taken.

- `basis_eur_per_m3`: the "Drinkwater" row, basis column (2,3295 for 2026,
  2,2602 for 2025).
- `comfort_eur_per_m3`: the same row's comfort column, read rather than
  derived, and held to twice the basis within 0,01 (VMM 2x rule). Read by
  position alone, a swapped basis and comfort cell shipped twice the
  drinkwater rate with no error. A missing comfort cell raises.
- `sanering_gemeentelijk_eur_per_m3`: the "Afvoer" row, basis column
  (1,9572).
- `sanering_bovengemeentelijk_eur_per_m3`: the "Zuivering" row, basis
  column (1,7019). The comfort sanering rates are not read; `pricing.py`
  doubles the basis ones in the comfort block.
- Sum check: the page adds the three legs up itself in the "Integrale" row.
  Drinkwater + afvoer + zuivering must equal it within 0,0001, which
  catches a leg read from the wrong column where the 2x rule does not.
- `yearly_fixed_fee` 100 and per-resident discount 20: the VMM constants
  in `const.py`, not the page's Vast recht and Korting columns.
- VAT: ex-BTW figures; `vat_rate` is 6 %. `build_flanders_tariff` refuses
  a rate of zero or less, a rate outside 0.5 to 20 EUR/m³, and a negative
  sanering.

### Which table, and which year

The target is the year asked for, or the Belgian clock's year. Only tables
with an "Integrale" row take part; each is dated by `_year_for_table`, the
nearest heading above it, the walk stopping at the previous table so a
heading cannot date the wrong one.

1. Every table dated: the one dated with the target year, else the newest
   one dated at or before it. This is the year in force.
2. A heading that carries no year, or no dated table that has started yet:
   the table with the highest integrale price is taken, since rates only
   ever index up. Its year is its own heading's when it has one. An
   undated table is taken as the year after the newest dated one beside
   it, never later than the target; with no dated table on the page at
   all, the target. If the year arrived at is ahead of the target and no
   year was asked for, the page is refused ("dearest table is dated ...
   ahead of ...").
3. `carry_prior_year_card` lets a table of last year stand until 31 March.

So in January, while the page still shows last year's card, that card is
served dated last year and turns stale after 31 March. That holds when the
current heading has been reworded and no longer reads as a year:
`tests/test_agso_knokke.py` pins a 2026 table under "TARIEVEN VANAF 1
JANUARI 2026" staying 2026 in January and in May 2027.

## Failures

- Network errors, timeouts, HTTP 5xx and 429 raise `TransientFetchError`;
  other HTTP errors and a redirect off the site or off https raise
  `ExtractorError`.
- `ExtractorError`: no tables, no table with an Integrale row, the chosen
  table missing a drinkwater, afvoer or zuivering row, no comfort cell, a
  comfort rate that is not twice the basis, legs that do not add up to the
  printed total, a dearest table dated ahead of the target.

## Checks

- Live check: the default fetch, no CI skip.
- Drift check (weekly): "AGSO Knokke-Heist" against
  `agso_knokke_2026.html`.

## Quirks

- When every table is dated but none has started yet, step 2 takes the
  dearest table and its heading dates it ahead of the target, so the
  fetch is refused rather than served.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
