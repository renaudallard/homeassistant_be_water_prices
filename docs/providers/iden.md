# IDEN

Module: `custom_components/be_water_prices/providers/iden.py`, using
`build_tariff`, `detect_published_year` and `spge_components` from
`custom_components/be_water_prices/providers/_walloon_simple.py`.
Tests: `tests/test_walloon_small.py`. Fixture:
`tests/fixtures/iden_2026.html`.

Intercommunale de Distribution d'Eau de Nandrin, Tinlot et environs: three
communes (Nandrin, Tinlot, Modave), small population.

## Where the tariff is

- The operator's own card:
  https://www.iden-eau.be/iden_web/fr/Tarification.awp (`SOURCE_URL`), a
  WebDev page.
- Three read-only form fields, each headed "Depuis le 1er janvier
  `<year>` :", labelled Distribution, Assainissement and Fonds Social de
  l'Eau. The rates are the `value` attributes of the `<input>` fields.
- Below the card an explanatory section ("Qu'entend-on par CVD et CVA ?")
  repeats every label with no value.
- Read from the operator rather than an aggregator: Callmepower carried
  3,555 where IDEN's page says 3,3552, a transposed digit that overstated
  an 80 m³ bill by 18 EUR a year. A drift check compares the parser with
  its own source, so a source that is itself wrong is invisible to it.
- There is no fallback source.

## Region and communes

- Wallonia. The resolver's ZDE-derived table (`_PER_POSTCODE` in
  `providers/_postcodes.py`) maps only 4550 to `iden`.
- One card, no commune picker.

## How each figure is read

`parse_tariff` in the module itself:

- `_text_with_field_values` inlines each `<input>` value where the field
  stands, wrapped in `‹` and `›`, then flattens the page with
  BeautifulSoup. Without it `get_text()` drops the rates.
- The page wraps each initial in `<strong>`, so the text reads "C oût- V
  érité à la D istribution". The patterns anchor on the tail of the word
  that survives the split and require the value inside the next field,
  within 40 characters, so the explanatory section cannot supply it:
  - `cvd_eur_per_m3`: `_CVD_RE`, "istribution ... ‹N,NNN" (3,3552 on the
    fixture).
  - CVA: `_CVA_RE`, "ssainissement ... ‹N,NNN" (2,7480).
  - FSE: `_FSE_RE`, "ocial de l'Eau ... ‹N,NNN" (0,0339).

  Each takes three to five decimals.
- `yearly_fixed_fee`: `20 x CVD + 30 x CVA`, computed by `build_tariff`,
  which also holds the CVD to the 1.5 to 6.0 EUR/m³ window.
- VAT: ex-VAT figures; `vat_rate` is 6 %.

### CVA and FSE

All three fields are required by the parser itself: a missing one raises
"could not locate IDEN's ..." before the SPGE rule runs. The two SPGE
figures then go to `spge_components` in `providers/_walloon_simple.py`
(called with `cva_required=False`, which changes nothing here since the
CVA is always present by then), against `WALLONIA_SPGE_YEAR` in
`const.py`:

- A card of `WALLONIA_SPGE_YEAR` or earlier is priced on
  `WALLONIA_CVA_EUR_PER_M3` (2,748) and `WALLONIA_FSE_EUR_PER_M3` (0,0339),
  the printed values held to them within 0,005 and 0,001. A move raises
  `ExtractorError`.
- A card of a later year is priced on the two figures the page prints.

### Year and dates

- `detect_published_year` reads "1er janvier 2026" off the field headings.
  Unlike the other Walloon parsers there is no clock fallback: a page that
  states no year it can read raises "IDEN states no tariff year".
- `valid_from` is 1 January; `valid_until` 31 December, or 31 March of the
  current year for a card of an earlier year.

## Failures

- Network errors, timeouts, HTTP 5xx and 429 raise `TransientFetchError`;
  other HTTP errors and a redirect off the site or off https raise
  `ExtractorError`.
- `ExtractorError`: a missing CVD, CVA or FSE field, no year, a CVD outside
  the window, a CVA or FSE off the constants for a card of their year.

## Checks

- Live check: the default fetch, no CI skip, and part of the Walloon SPGE
  comparison.
- Drift check (weekly): "IDEN" against `iden_2026.html`.

## Quirks

- `tests/test_walloon_small.py` pins that the rate comes from the field:
  blanking the `VALUE="3,3552` attribute raises rather than letting the
  explanatory section answer.
- CIESAC's site runs the same WebDev CMS, so this parser is the starting
  point for [CIESAC](ciesac.md) when its site returns.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
