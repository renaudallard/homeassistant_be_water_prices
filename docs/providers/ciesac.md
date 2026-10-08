# CIESAC

Module: `custom_components/be_water_prices/providers/ciesac.py`, a thin
wrapper over the shared prose parser in
`custom_components/be_water_prices/providers/_walloon_simple.py`.
Tests: `tests/test_walloon_small.py`. Fixture:
`tests/fixtures/ciesac_callmepower_2026.html`.

Compagnie Intercommunale des Eaux de la Source les Avins (groupe Clavier):
Clavier, Durbuy, Ouffet and Tinlot, four communes with a small population.

## Where the tariff is

- Callmepower's public aggregator page:
  https://callmepower.be/fr/eau/distributeurs/ciesac (`SOURCE_URL`).
- Not the operator: every path under `ciesac.be` has answered "Mise a jour
  du serveur en cours (46, ERR_UPDATING_SERVER)" for months, so there is no
  second source to hold the figure to.
- The page leads with a summary-card grid ("2,9 €/m³ CVD (distribution)
  2,748 €/m³ CVA (assainissement)") and then states the card in prose:
  "Coût vérité à la distribution (CVD) : 2,9 €/m³ ; Coût vérité à
  l'assainissement (CVA) : 2,748 €/m³ ; Contribution au Fonds social de
  l'eau : 0,0339 €/m³ ; Redevance annuelle : (20 x CVD) + (30 x CVA) =
  140,44 €".

## Region and communes

- Wallonia. In the resolver's ZDE-derived table (`_PER_POSTCODE` in
  `providers/_postcodes.py`) only 4560 maps to `ciesac`.
- One card, no commune picker.

## How each figure is read

Built by `build_extractor` and parsed with `_walloon_simple.parse_tariff`.

- `cvd_eur_per_m3` (`parse_cvd`): the summary cards print each value before
  its label, with the CVA card right after "CVD (distribution)", so a
  forward scan from that label reads the CVA. The parser anchors instead on
  the prose label `_LABELED_DIST_CVD_RE`, "distribution (CVD) : N €", where
  the value follows the label, and takes the largest match inside the 1.5
  to 6.0 EUR/m³ window. The fixture reads 2,9.
- CVA and FSE: `parse_cva` ("assainissement (CVA) : N €") and `parse_fse`
  ("fonds social de l'eau : N €"); both bind on the fixture.
- `yearly_fixed_fee`: `20 x CVD + 30 x CVA`, computed by `build_tariff`.
  The page's own 140,44 € is not read; it agrees (20 x 2,9 + 30 x 2,748).
- VAT: ex-VAT figures; `vat_rate` is 6 %.

### CVA and FSE

`spge_components` in `providers/_walloon_simple.py`, against
`WALLONIA_SPGE_YEAR` in `const.py`:

- A card of `WALLONIA_SPGE_YEAR` or earlier is priced on
  `WALLONIA_CVA_EUR_PER_M3` (2,748) and `WALLONIA_FSE_EUR_PER_M3` (0,0339).
  The printed CVA is required and held to the constant within 0,005; the
  printed FSE is held within 0,001 when it is there. A move raises
  `ExtractorError`.
- A card of a later year is priced on the page's own CVA and FSE, and
  fails if the page does not print one of them.

### Year and dates

- `detect_published_year` finds no "Tarifs YYYY" or "1er janvier YYYY"
  here and falls to the prose "en 2026" ("sur votre facture CIESAC en
  2026"). With no year it can read, the clock's year is used.
- `valid_from` is 1 January; `valid_until` 31 December, or 31 March of the
  current year for a card of an earlier year.
- The publication label is "CIESAC tarifs (via Callmepower) <year>", so the
  entry says where the figure came from.

## Failures

- Network errors, timeouts, HTTP 5xx and 429 raise `TransientFetchError`;
  other HTTP errors and a redirect off the site or off https raise
  `ExtractorError`.
- `ExtractorError`: no CVD, no CVD inside the window, a CVA no longer
  printed, a CVA or FSE off the constants for a card of their year, a
  later card missing either SPGE figure.

## Checks

- Live check: the default fetch, no CI skip, and part of the Walloon SPGE
  comparison.
- Drift check (weekly): "CIESAC" against `ciesac_callmepower_2026.html`.
  It compares the parser with Callmepower, so a wrong figure on Callmepower
  reads as no drift.

## Quirks

- **The rate is unverified.** Callmepower prints this CVD with one decimal
  (2,9) where every other Walloon operator publishes two or four, so it is
  rounded at best. On 2026-09-08 two of the three cards this aggregator
  supplied here were found wrong (AIEC by 53 EUR a year, IDEN by 18), which
  is why those two now read other sources. Treat this one as provisional
  until `ciesac.be` answers again.
- The operator's site runs the same WebDev CMS as IDEN's, so
  [`iden.py`](iden.md) is the place to start when it returns.
- One test in `tests/test_walloon_small.py`
  (`test_the_prose_pages_print_the_constants_the_engine_uses`) leaves
  CIESAC out of its FSE check, as a page that prints none; the committed
  fixture does print it, and `parse_fse` reads 0,0339 off it.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
