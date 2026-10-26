# VIVAQUA

Module: `custom_components/be_water_prices/providers/vivaqua.py`.
Tests: `tests/test_vivaqua.py`, plus the VIVAQUA cases in
`tests/test_parser_cross_checks.py`. Fixtures:
`tests/fixtures/vivaqua_linear_2026.html` and
`tests/fixtures/vivaqua_intervention_2026.html`.

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
- The Region's intervention sociale for households with BIM status is on
  a second page, https://www.vivaqua.be/fr/intervention-sociale/
  (`INTERVENTION_URL`). In 2026 it reads:

  > En 2026, l'intervention sociale s'élève à
  > - 58,70 € pour un ménage d'1 personne
  > - 53,36 € par personne supplémentaire composant le ménage

  The French page is read because the English one has lagged behind it
  (its FAQ still worked the 2026 example on the 2025 figures).
- There is no other source.

## Region and communes

- Brussels, all 19 communes, about 1.2 M people. The resolver sends
  postcodes 1000 to 1299 here (`providers/_postcodes.py`).
- One card, no commune picker.

## How each figure is read

`parse_tariff` in the module.

- The table (`_residential_table`): a `<table>` whose accent-folded text
  holds the year, "vat" and a rate in per cent (`_VAT_PERCENT_RE`). Where
  more than one card of the year does, the one at the lowest rate is the
  household's, so a 21 % card for the same year is not taken. The header
  wording is not matched, so "6 % VAT included" or "incl. 6% VAT" still
  finds it, and the rate is read off it.
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
  ex-VAT ones, so every figure is divided by 1 plus the rate the header
  states (6 %, so 1,06):
  - `yearly_fixed_fee`: the fixed charge total / 1,06.
  - `linear_eur_per_m3`: the variable supply / 1,06.
  - `sanering_gemeentelijk_eur_per_m3`: the variable sanitation / 1,06
    (sanitation is billed on behalf of Hydria).
  - `vat_rate`: the header's rate, 6 %.

  The Brussels branch of `pricing.py` bills `linear + sanering` per m³ plus
  the fixed charge, then adds the VAT back (see
  [the pricing model](../pricing-model.md)).
- Year: the year asked for, or the Belgian clock's year. `valid_from` 1
  January, `valid_until` 31 December, label "Price from January 1st
  `<year>` (VAT included `<rate>` %)".

### The intervention sociale

`parse_intervention` in the module, called by `fetch` after the tariff,
whose card it completes.

- The page's text is accent-folded and searched for the sentence "En
  `<year>`, l'intervention sociale s'élève à" (`_INTERVENTION_YEAR_RE`).
  The first person's amount is the one followed by "pour un ménage d'1
  personne", the further persons' the one followed by "par personne
  supplémentaire". Both are looked for in the 200 characters after the
  sentence only: the worked example below them and the FAQ further down
  repeat the figures, and a FAQ left on last year's would otherwise stand
  in for a block the parser no longer matches.
- The amounts go on the card as `social_intervention_first_person` and
  `social_intervention_per_extra_person`, as printed: the payment carries
  no VAT.
- Year: the page's own. A year either side of the Belgian clock's is
  taken with a warning, since the page turns over on its own schedule
  and the amounts move by indexation only; anything further off raises.

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
  sanitation sum more than a cent off its total; an intervention page
  with no year sentence, one stating a year more than one off, or one
  whose two amounts cannot be read. Either page failing fails the fetch,
  and the last good snapshot keeps serving.

## Checks

- Live check: the default fetch, no CI skip.
- Drift check (weekly): "VIVAQUA" against `vivaqua_linear_2026.html`
  and `vivaqua_intervention_2026.html` together, the intervention's two
  figures compared like the fixed fee.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
