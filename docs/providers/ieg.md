# IEG

Module: `custom_components/be_water_prices/providers/ieg.py`, a thin
wrapper over the shared prose parser in
`custom_components/be_water_prices/providers/_walloon_simple.py`.
Tests: `tests/test_walloon_small.py`. Fixture:
`tests/fixtures/ieg_2026.html`.

Intercommunale d'Electricité et de Gaz, Mouscron (about 50 k people).
Despite the name it also runs the water network for Mouscron and a few
neighbouring communes.

## Where the tariff is

- The operator's own page:
  https://ieg.be/eau/espace-client/facturation/structure-du-prix-de-leau/
  (`SOURCE_URL`).
- The page states the year and lists the components in prose: "A partir
  du 1er janvier 2026, le prix du m³ d’eau distribuée par IEG est établi
  comme suit : CVD : Coût Vérité de Distribution : 2,3800€/m³ CVA : Coût
  Vérité d’Assainissement : 2,7480€/m³", and further down "Une
  contribution au fonds social de l’eau de 0,0339€ est perçue sur chaque
  m³ consommé".
- There is no fallback source.

## Region and communes

- Wallonia. The resolver's ZDE-derived table (`_PER_POSTCODE` in
  `providers/_postcodes.py`) maps 7700 and 7712 to `ieg`.
- One card for the whole area, no commune picker.

## How each figure is read

Built by `build_extractor` and parsed with `_walloon_simple.parse_tariff`,
on the page's flat text with script and style tags dropped.

- `cvd_eur_per_m3` (`parse_cvd`): IEG's phrasing matches neither of the
  two anchors ("actuelle du CVD", "distribution (CVD) :"), so the value
  comes from the generic scan: every "CVD ... N,NNN €" on the page, the
  largest inside the 1.5 to 6.0 EUR/m³ window, a value equal to the CVA
  constant skipped. The fixture reads 2,38.
- CVA: `parse_cva`, the IEG pattern "CVA : Coût Vérité d'Assainissement :
  N €".
- FSE: `parse_fse`, the IEG pattern "fonds social de l'eau de N €".
- `yearly_fixed_fee`: `20 x CVD + 30 x CVA`, computed by `build_tariff`. The
  page gives the formula "(20 x CVD) + (30 x CVA)" but no amount.
- VAT: ex-VAT figures; `vat_rate` is 6 %.

### CVA and FSE

Both are the SPGE flat-Wallonia figures, handled by `spge_components` in
`providers/_walloon_simple.py` against `WALLONIA_SPGE_YEAR` in `const.py`:

- A card of `WALLONIA_SPGE_YEAR` or earlier is priced on
  `WALLONIA_CVA_EUR_PER_M3` (2,748) and `WALLONIA_FSE_EUR_PER_M3` (0,0339).
  The printed figures are held to them (0,005 for the CVA, 0,001 for the
  FSE) and a move raises `ExtractorError`. A CVA the page no longer prints
  raises too: `tests/test_walloon_small.py` rewrites "CVA" to "C.V.A." on
  this fixture and expects "cannot be checked against it".
- A card of a later year is priced on the page's own CVA and FSE, and
  fails if the page does not print one of them.

### Year and dates

- `detect_published_year` reads "1er janvier 2026" off the page. With no
  year it can read, the clock's year is used.
- `valid_from` is 1 January of that year (the "à partir du" date reader,
  `effective_date`, only binds to AIEM's "actuelle du CVD" phrasing).
- `valid_until` is 31 December, or 31 March of the current year for a card
  of an earlier year (`carry_prior_year_card`): a page still on last year's
  card in January stands until then and goes stale after.

## Failures

- Network errors, timeouts, HTTP 5xx and 429 raise `TransientFetchError`;
  other HTTP errors and a redirect off the site or off https raise
  `ExtractorError` (`fetch_text` in `providers/_pdf.py`).
- `ExtractorError`: no CVD, no CVD inside the window, a CVA or FSE off the
  constants for a card of their year, a CVA no longer printed, a later card
  missing either SPGE figure.

## Checks

- Live check: the default fetch, no CI skip, and part of the Walloon SPGE
  comparison (fails while any Walloon card is past `WALLONIA_SPGE_YEAR`, and
  when the cards of one year disagree on the CVA or FSE).
- Drift check (weekly): "IEG" against `ieg_2026.html`.

## Quirks

- IEG uses the shared CWaPE residential structure through
  `_walloon_simple.py`, which builds the card; the tier math is applied in
  `pricing.py` (see [the pricing model](../pricing-model.md)).
- The page also states the 90 % CVD tranche ("de 5.001 à 50.000m³ : 90%
  CVD + CVA par m³"), which `pricing.py` applies to every Walloon card
  above 5 000 m³.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
