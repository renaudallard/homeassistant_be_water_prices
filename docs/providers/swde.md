# SWDE

Module: `custom_components/be_water_prices/providers/swde.py`, using
`build_tariff` and `spge_components` from
`custom_components/be_water_prices/providers/_walloon_simple.py`.
Tests: `tests/test_swde.py`, plus the SWDE case in
`tests/test_walloon_small.py`. Fixture: `tests/fixtures/swde_2026.html`.

Société wallonne des eaux, the dominant Walloon distributor: about 2.4 M
inhabitants across about 200 communes.

## Where the tariff is

- The English tariff page: https://www.swde.be/en/water-prices-swde
  (`SOURCE_URL`). The French slug (`prix-de-l-eau-swde`) answers 404; the
  English one works.
- The page puts each bill component under its own `<h3>` heading, followed
  by a `<p>` holding one `<strong>` amount:

| `<h3>` | `<p>` |
| --- | --- |
| 1. True-cost of supply (CVD) | The current CVD amounts to € 3.24/m³. |
| 2. True cost of sanitation (CVA) | The current CVA is € 2.748/m³. |
| 3. VAT | For water supply, it amounts to 6 %. |
| 4. Social Water Fund | The current Social Water Fund amounts to € 0.0339/m³. |

- SWDE writes dot decimals ("€ 2.748"); `extract_amounts` in
  `providers/_html.py` leaves dot-grouped thousands alone for that reason.
- There is no fallback source.

## Region and communes

- Wallonia. The resolver's ZDE-derived table (`_PER_POSTCODE` in
  `providers/_postcodes.py`) maps 427 of its 540 Walloon postcodes to
  `swde`.
- One card for the whole area, no commune picker.

## How each figure is read

`parse_tariff` in the module. `_find_component` takes the first `<h2>`,
`<h3>` or `<h4>` whose accent-folded text holds one of the keywords, then
the first euro amount in the siblings after it, stopping at the next
heading (`_amounts_after`).

- `cvd_eur_per_m3`: headings with "cvd", "true-cost of supply" or "true
  cost of supply" (3,24 on the fixture).
- CVA: "cva" or "true cost of sanitation".
- FSE: "social water fund", "fonds social de l'eau" or "fonds social".
- `yearly_fixed_fee`: `20 x CVD + 30 x CVA`, computed by `build_tariff`,
  which also holds the CVD to the 1.5 to 6.0 EUR/m³ window.
- VAT: ex-VAT figures; `vat_rate` is the rate the page states ("VAT For
  water supply, it amounts to 6 %", read by `stated_vat_rate`).

Two choices in the section walk are deliberate:

- Only true siblings are walked. Walking the rest of the document went
  into the next section's wrapper, so a CVD section that lost its euro
  sign answered with the CVA.
- The first amount is taken, not the largest. Taking the largest was
  tried, to serve a current rate printed after a historic one, and
  reverted: a VAT-inclusive twin is only 6 % above the rate and a year of
  indexation a few percent, so no threshold separates them, and reading
  the TVAC figure would apply the VAT twice (3,24 billed as 3,43).

### CVA and FSE

The CVA and FSE sections are read off the page and checked by
`spge_components` in `providers/_walloon_simple.py`, with the label
"SWDE": the card is priced on them as printed. A CVA or FSE section that
is gone fails the fetch, as does a figure outside its window (1 to 10
EUR/m³ for the CVA, 0,01 to 0,2 for the FSE). The live check holds the
Walloon pages of one year to each other on both figures.

### Year and dates

- The page states no year. The card is dated by the Belgian clock
  (`belgian_today`), and the publication label says so: "SWDE water
  prices 2026 (page states no year)".
- `valid_from` 1 January, `valid_until` 31 December of the clock's year.

## Failures

- Network errors, timeouts, HTTP 5xx and 429 raise `TransientFetchError`;
  other HTTP errors and a redirect off the site or off https raise
  `ExtractorError`.
- `ExtractorError`: no CVD section with an amount, a CVD outside the
  window, a CVA or FSE section gone or outside its window.

## Checks

- Live check: the default fetch, no CI skip, and part of the Walloon SPGE
  comparison.
- Drift check (weekly): "SWDE" against `swde_2026.html`.

## Quirks

- Because the card is dated by the clock, a page left on last year's
  rates in January is served as this year's and is never stale by date.
  Nothing detects a CVD left on last year's value; the live check
  validates ranges, and holds the CVA and FSE to the other Walloon pages
  of the year the clock gives the card.
- SWDE is the README's reference for a single-page HTML utility.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
