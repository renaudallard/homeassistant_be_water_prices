# INASEP

Module: `custom_components/be_water_prices/providers/inasep.py`, using
`build_tariff`, `check_spge_constants` and `detect_published_year` from
`custom_components/be_water_prices/providers/_walloon_simple.py`.
Tests: `tests/test_inasep.py`. Fixture: `tests/fixtures/inasep_2026.html`.

Intercommunale Namuroise de Services Publics: Namur sud, 10 communes,
about 38 k subscribers (38 600 abonnés, 80 to 100 k people per the module
docstring).

## Where the tariff is

- The "Prix de l'eau et évolution" page:
  https://www.inasep.be/prix-de-leau-et-evolution (`SOURCE_URL`).
- The three components sit under a "Tarifs YYYY" heading:

  ```
  Tarifs 2026
  Coût-Vérité Distribution (CVD) = 3,6734 €/m³ depuis le 27 avril 2026
  Coût-Vérité Assainissement (CVA) = 2,748 €/m³ depuis le 1er janvier 2026
  Fonds social de l'Eau = 0,0339 €/m³ depuis le 1er janvier 2026
  ```

- There is no fallback source.

## Region and communes

- Wallonia. The resolver's ZDE-derived table (`_PER_POSTCODE` in
  `providers/_postcodes.py`) maps 18 postcodes to `inasep`: 5070, 5542,
  5543, 5544, 5563, 5564, 5572, 5573, 5574, 5576, 5600, 5620, 5621, 5630,
  5650, 5651, 5660 and 5670.
- One card, no commune picker.

## How each figure is read

`parse_tariff` in the module, on the page's flat text with script and
style tags dropped.

- `cvd_eur_per_m3`: `_CVD_RE` anchors on the heading "Coût-Vérité
  Distribution (CVD) = N,NNNN €/m³", so the other euro amounts on the page
  (yearly impact figures, per-glass examples) cannot win. It tolerates the
  accent-stripped spellings bs4 sometimes produces ("Cout", "Verite"), a
  missing "=", the unit rendered as "€ €/m 3", and one to five decimals.
  The fixture reads 3,6734.
- `valid_from`: the same match captures the "depuis le 27 avril 2026" glued
  to the CVD (`_cvd_effective_date`, French month names with or without
  accents). It moves `valid_from` off 1 January only when that day is in
  the card's year; a date in an earlier year leaves 1 January. The CVA's
  own "depuis le 1er janvier" a few words later cannot answer for a CVD
  that has lost its date.
- Year: `detect_published_year` reads the "Tarifs 2026" heading. A
  "Tarifs" heading wins over the "1er janvier" dates on the SPGE lines
  below it, which can still carry last year's date under a new heading.
  With no year it can read, the clock's year is used.
- CVA and FSE: `check_spge_constants` reads them with `parse_cva`
  ("assainissement (CVA) = N €") and `parse_fse` ("fonds social de l'eau =
  N €") and hands them to `spge_components`.
- `yearly_fixed_fee`: `20 x CVD + 30 x CVA`, computed by `build_tariff`,
  which also holds the CVD to the 1.5 to 6.0 EUR/m³ window.
- VAT: ex-VAT figures; `vat_rate` is 6 %.

### CVA and FSE

`spge_components` in `providers/_walloon_simple.py`, against
`WALLONIA_SPGE_YEAR` in `const.py`:

- A card of `WALLONIA_SPGE_YEAR` or earlier is priced on
  `WALLONIA_CVA_EUR_PER_M3` (2,748) and `WALLONIA_FSE_EUR_PER_M3` (0,0339).
  The printed values are held to them within 0,005 and 0,001; a move
  raises `ExtractorError`, and so does a CVA the page no longer prints.
- A card of a later year is priced on the page's own CVA and FSE, and
  fails if the page does not print one of them.

### Dates

- The 2026 card runs from 27 April 2026 to 31 December 2026.
- In January the card is still dated by the page, so a page on last year's
  card stands until 31 March (`carry_prior_year_card`) and then goes stale.

## Failures

- Network errors, timeouts, HTTP 5xx and 429 raise `TransientFetchError`;
  other HTTP errors and a redirect off the site or off https raise
  `ExtractorError`.
- `ExtractorError`: no CVD heading, a CVD outside the window, a CVA or FSE
  off the constants for a card of their year, a CVA no longer printed, a
  later card missing either SPGE figure.

## Checks

- Live check: the default fetch, no CI skip, and part of the Walloon SPGE
  comparison.
- Drift check (weekly): "INASEP" against `inasep_2026.html`.

## Quirks

- INASEP revises its CVD in the middle of the year. The previous rate is
  not published, so the year-to-date cost still bills the whole year's
  volume at the current one; only `valid_from` says when it started.
- Every optional piece of whitespace in `_CVD_RE` is tied to its literal.
  An earlier `\s*(?:er)?\s+` shape backtracked quadratically on a page
  padded with spaces; `tests/test_inasep.py` pins the linear version with
  a 50 000-space run.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
