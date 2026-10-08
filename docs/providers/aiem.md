# AIEM

Module: `custom_components/be_water_prices/providers/aiem.py`, a thin
wrapper over the shared prose parser in
`custom_components/be_water_prices/providers/_walloon_simple.py`.
Tests: `tests/test_walloon_small.py`. Fixture:
`tests/fixtures/aiem_2026.html`.

Association Intercommunale des Eaux de la Molignée: about 12 k connections
(about 25 k people) in the Molignée valley.

## Where the tariff is

- One HTML page on the operator's own site:
  https://www.aiem.be/prix-de-l-eau (`SOURCE_URL`).
- The page explains the bill in prose and then prints the current values:
  "Valeur actuelle du CVD : 2,87€ HTVA 6% (à partir du 01/02/2025)",
  "Valeur actuelle du CVA : 2,748€ HTVA 6% (à partir du 01/01/2026)" and,
  for the Fonds social, "Nombre de m³ x 0,0339€ HTVA 6%".
- There is no fallback source.

## Region and communes

- Wallonia. The postcode resolver's ZDE-derived table (`_PER_POSTCODE` in
  `providers/_postcodes.py`) maps 5520, 5521, 5522, 5523, 5524, 5537, 5640,
  5641, 5644 and 5646 to `aiem`.
- One card for the whole area, no commune picker.

## How each figure is read

The extractor is built by `build_extractor` and parses with
`_walloon_simple.parse_tariff`. Script and style tags are dropped and the
page is read as flat text.

- `cvd_eur_per_m3` (`parse_cvd`): the page spells out the formula with
  worked examples before it gives the value, "0,5 x CVD (soit 1,435€)", so a
  first-match read would take half the rate. The parser tries, in order:
  1. `_ACTUAL_CVD_RE`, "actuelle du CVD ... N,NNN €". This is the anchor
     AIEM's page answers to. The largest match inside the plausibility
     window wins.
  2. `_LABELED_DIST_CVD_RE`, "distribution (CVD) : N,NNN €" (the
     Callmepower phrasing, not present here).
  3. The generic scan `_CVD_RE`: every "CVD ... N,NNN €", the largest one
     inside the window, skipping a value equal to the CVA constant.

  The window is `_MIN_PLAUSIBLE_CVD` 1.5 to `_MAX_PLAUSIBLE_CVD` 6.0 EUR/m³.
  The floor is set to exclude the 1,435 example: a page left with only the
  example raises "no plausible CVD" rather than billing half the rate. The
  fixture reads 2,87.
- CVA and FSE: read off the page with `parse_cva` ("actuelle du CVA ...")
  and `parse_fse` ("Fonds social de l'eau ... x 0,0339€"), then handed to
  `spge_components` (see below).
- `yearly_fixed_fee`: the redevance, `20 x CVD + 30 x CVA`, computed by
  `build_tariff`. The page does not print it as a figure.
- VAT: the page prints ex-VAT ("HTVA") figures; `vat_rate` is
  `DEFAULT_VAT_RATE` (6 %).

### CVA and FSE

Both are the SPGE flat-Wallonia figures. `spge_components` in
`providers/_walloon_simple.py` decides by the card's year against
`WALLONIA_SPGE_YEAR` in `const.py`:

- A card of `WALLONIA_SPGE_YEAR` or earlier is priced on
  `WALLONIA_CVA_EUR_PER_M3` (2,748) and `WALLONIA_FSE_EUR_PER_M3` (0,0339).
  The printed figures are held to them: a CVA more than 0,005 away or an
  FSE more than 0,001 away raises `ExtractorError`, and so does a CVA the
  page no longer prints (`hold_to_constant`). An FSE the page does not print
  is not checked.
- A card of a later year is priced on the page's own CVA and FSE. If the
  page does not print one of them, the fetch fails, since the constants
  are not known to hold for that year.

### Year and dates

- The year comes from `detect_published_year`, which looks for "Tarifs
  YYYY", then "1er janvier YYYY", then "en YYYY", within a year of today.
  AIEM's page matches none of them (it writes "01/01/2026" and "01 janvier
  2026"), so the card is dated by the Belgian clock (`belgian_today`).
- `valid_from`: `effective_date` reads the "(à partir du DD/MM/YYYY)" next
  to "Valeur actuelle du CVD". It moves `valid_from` only when that day is
  in the card's own year. The fixture's 01/02/2025 is the previous change,
  so the 2026 card runs from 1 January.
- `valid_until` is 31 December, or 31 March of the current year for a card
  of an earlier year (`carry_prior_year_card`).

## Failures

- Network errors, timeouts, HTTP 5xx and 429 raise `TransientFetchError`
  (`fetch_text` in `providers/_pdf.py`); other HTTP errors and a redirect
  off the site or off https raise `ExtractorError`.
- `ExtractorError`: no CVD at all, no CVD inside the window, a CVA or FSE
  off the constants for a card of their year, a CVA no longer printed, a
  later card that prints no CVA or FSE.

## Checks

- Live check (`scripts/live_check.py`): the default fetch, no CI skip. It is
  part of the Walloon SPGE comparison, which fails while any Walloon card is
  past `WALLONIA_SPGE_YEAR` and when the Walloon cards of one year disagree
  on the CVA or the FSE.
- Drift check (`scripts/fixture_drift.py`, weekly): "AIEM" against
  `aiem_2026.html`.

## Quirks

- Because the page states no year that the parser reads, a page left on
  last year's rates in January is served as this year's card and does not
  go stale by date. In a new year past `WALLONIA_SPGE_YEAR` it is also
  priced on its own CVA and FSE, which the page prints, and the live check
  fails until the constants and the year are moved.
- `tests/test_walloon_small.py` pins the example trap: with a larger figure
  in an example, only the "actuelle du CVD" anchor still gets the value
  right.
- Tier math (half CVD on the first 30 m³, CVA exempt there) is in
  `pricing.py`; see [the pricing model](../pricing-model.md).

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
