# VIVAQUA

Module: `custom_components/be_water_prices/providers/vivaqua.py`.
Tests: `tests/test_vivaqua.py`, plus the VIVAQUA cases in
`tests/test_parser_cross_checks.py`. Fixture:
`tests/fixtures/vivaqua_linear_2026.html`.

VIVAQUA is the water utility for the whole Brussels-Capital Region. Its
domestic tariff has been linear since 2022 (no consumption blocks); Brugel
approves the multi-year period, and the module notes the current card runs
through 2026 with a new one expected for 2027.

## Where the tariff is

- One HTML page: https://www.vivaqua.be/en/the-domestic-linear-rate/
  (`SOURCE_URL`).
- The page carries one `<table>` per year. The 2026 card on the fixture:

| Price from January 1st 2026 (VAT included 6 %) | |
| --- | --- |
| Fixed charge (per year) | € 40,23 |
| supply | € 19,69 |
| sanitation | € 20,54 |
| Variable charge (per m³) | € 5,35 |
| supply | € 2,62 |
| sanitation | € 2,73 |

- The fixture also carries the 2025 card below it.
- There is no other source.

## Region and communes

- Brussels, all 19 communes, about 1.2 M people. The resolver sends
  postcodes 1000 to 1299 here (`providers/_postcodes.py`).
- One card, no commune picker.

## How each figure is read

`parse_tariff` in the module.

- The table (`_residential_table`): the first `<table>` whose accent-folded
  text holds the year, "vat" and a 6 % marker (`_RESIDENTIAL_VAT_RE`). The
  6 % is what pins the residential card; a 21 % card for the same year is
  not taken. The header wording is not matched, so "6 % VAT included" or
  "incl. 6% VAT" still finds it.
- Rows are matched by label, not position: "fixed charge", "variable
  charge", and rows labelled exactly "supply" or "sanitation". The first
  "supply" and "sanitation" rows belong to the fixed charge, the second to
  the variable charge.
- A matched row must have exactly one label and one value. A wider matched
  row is refused: a merged multi-year table with the current year first
  would otherwise price the previous year, with the supply plus sanitation
  check passing because all three figures come from the same wrong column.
  A wide row the parser does not read (a footnote) is skipped.
- Cross-checks, in whole cents with one cent of rounding allowed:
  variable supply + sanitation must make the variable total, and fixed
  supply + sanitation the fixed total (when both fixed rows are there).
- VAT: VIVAQUA publishes VAT-inclusive figures, and `WaterTariff` holds
  ex-VAT ones, so every figure is divided by `1 + DEFAULT_VAT_RATE` (1,06):
  - `yearly_fixed_fee`: the fixed charge total / 1,06.
  - `linear_eur_per_m3`: the variable supply / 1,06.
  - `sanering_gemeentelijk_eur_per_m3`: the variable sanitation / 1,06
    (sanitation is billed on behalf of Hydria).
  - `vat_rate`: 6 %.

  The Brussels branch of `pricing.py` bills `linear + sanering` per m³ plus
  the fixed charge, then adds the VAT back (see
  [the pricing model](../pricing-model.md)).
- Year: the year asked for, or the Belgian clock's year. `valid_from` 1
  January, `valid_until` 31 December, label "Price from January 1st
  `<year>` (VAT included 6 %)".

### When this year's card is not up yet

If no residential table for the current year is found, the previous
year's is parsed instead, logged as a warning, and served until 31 March
(`carry_prior_year_card`). A current-year table that is present but whose
rows do not parse raises instead: that is a broken parser, and serving
last year's rates under the grace period would hide it.

## Failures

- Network errors, timeouts, HTTP 5xx and 429 raise `TransientFetchError`;
  other HTTP errors and a redirect off the site or off https raise
  `ExtractorError`.
- `ExtractorError`: no table for this year or last, a table found whose
  rows cannot be parsed, a matched row wider than two cells, a supply plus
  sanitation sum more than a cent off its total.

## Checks

- Live check: the default fetch, no CI skip.
- Drift check (weekly): "VIVAQUA" against `vivaqua_linear_2026.html`.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
