# CILE

Module: `custom_components/be_water_prices/providers/cile.py`, using
`build_tariff`, `detect_published_year` and `spge_components` from
`custom_components/be_water_prices/providers/_walloon_simple.py`.
Tests: `tests/test_cile.py`, plus the CILE cases in
`tests/test_walloon_small.py`. Fixture: `tests/fixtures/cile_2026.html`.

Compagnie Intercommunale Liégeoise des Eaux: the Liège region, about
560 k inhabitants across 24 communes, the largest Walloon distributor
after SWDE.

## Where the tariff is

- One HTML page: https://www.cile.be/facturation/le-prix-de-leau
  (`SOURCE_URL`).
- A clean four-row table:

| Poste | Tarif au 1er janvier 2026 |
| --- | --- |
| C.V.A | 2,7480 €/m³ |
| C.V.D | 3,5552 €/m³ |
| Fonds social | 0,0339 €/m³ |
| TVA 6% | |

- There is no fallback source.

## Region and communes

- Wallonia. The resolver's ZDE-derived table (`_PER_POSTCODE` in
  `providers/_postcodes.py`) maps 50 postcodes to `cile`, from 4000 to
  4672.
- One card, no commune picker.

## How each figure is read

`parse_tariff` in the module, the same pattern as SWDE and inBW: the CVD
parsed live, the CVA and FSE read off the table.

- The first `<table>` on the page is the card; none raises.
- Year: `detect_published_year` over the page text, which reads "Tarif au
  1er janvier 2026" (an older "à partir du 1er janvier 2022" elsewhere on
  the page is too far from today to count). With no year it can read, the
  clock's year is used.
- Column (`_value_column`): the value column whose heading names that
  year. With a single value column that one is read. With two or more and
  none naming the year the table is refused, since "new | old" and "old |
  new" put last year's figure in the last cell half the time.
- Rows (`_row_amount`): the first cell contains "c.v.d", "c.v.a" or "fonds
  social"; the first euro amount in the chosen column is taken.
- `cvd_eur_per_m3`: the C.V.D row (3,5552 on the fixture). A missing row
  raises.
- `yearly_fixed_fee`: `20 x CVD + 30 x CVA`, computed by `build_tariff`,
  which also holds the CVD to the 1.5 to 6.0 EUR/m³ window.
- VAT: ex-VAT figures; `vat_rate` is 6 %.

### CVA and FSE

The C.V.A and Fonds social rows are read off the table and checked by
`spge_components` in `providers/_walloon_simple.py`, with the label
"CILE": the card is priced on them as printed. A row that is gone fails
the fetch ("CVA is not printed on its card"), as does a figure outside
its window (1 to 10 EUR/m³ for the CVA, 0,01 to 0,2 for the FSE).

### Dates

- `valid_from` 1 January of the card's year, `valid_until` 31 December.
- A page still on last year's card in January is dated last year and
  stands until 31 March (`carry_prior_year_card`); `tests/test_cile.py`
  pins it with the clock at 5 January 2027.

## Failures

- Network errors, timeouts, HTTP 5xx and 429 raise `TransientFetchError`;
  other HTTP errors and a redirect off the site or off https raise
  `ExtractorError`.
- `ExtractorError`: no table, no C.V.D row, a multi-column table with no
  column naming the year, a CVD outside the window, a C.V.A or Fonds
  social row gone or outside its window.

## Checks

- Live check: the default fetch, no CI skip, and part of the Walloon SPGE
  comparison.
- Drift check (weekly): "CILE" against `cile_2026.html`.

## Quirks

- CILE publishes further tranches above 50 000 m³; `pricing.py` does not
  model them (see [the pricing model](../pricing-model.md)).

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
