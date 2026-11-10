# inBW

Module: `custom_components/be_water_prices/providers/inbw.py`, using
`build_tariff`, `detect_published_year` and `spge_components` from
`custom_components/be_water_prices/providers/_walloon_simple.py`.
Tests: `tests/test_inbw.py`, plus the inBW cases in
`tests/test_parser_cross_checks.py` and `tests/test_walloon_small.py`.
Fixture: `tests/fixtures/inbw_2026.html`.

inBW (formerly IECBW) is the water utility for Brabant wallon, 27
communes.

## Where the tariff is

- One HTML page: https://eau.inbw.be/prix-de-leau (`SOURCE_URL`).
- The page carries one `<table class="table">` with the full per-tier
  breakdown of an example 100 m³ residential bill, columns Rubrique,
  Quantité, Prix unitaire, Hors TVA, TVAC. On the fixture:

| Rubrique de la facture | Quantité | Prix unitaire | Hors TVA | TVAC |
| --- | --- | --- | --- | --- |
| Redevance annuelle (20 x CVD) | 1 | 52,000 € | 52,000 € | 55,120 € |
| Consommation entre 0 et 30 m³ | 30 m³ | 1,300 € | 39,000 € | 41,340 € |
| Consommation entre 30 et 5000 m³ | 70 m³ | 2,600 € | 182,00 € | 192,920 € |
| Redevance annuelle (30 x CVA) | 1 | 82,440 € | 82,440 € | 87,386 € |
| Contribution Fonds Social de l'Eau | | | | |
| | 100 m³ | 0,0339 € | 3,390 € | 3,593 € |
| Total facture TVAC | | | | 584,261 € |

- There is no fallback source.

## TLS

The server does not send its GoDaddy intermediate certificate, so the
default trust path fails. GoDaddy's public intermediates ship in
`certs/godaddy_intermediates.pem`, and `fetch` reads the page through a
context of its own (`inbw.ssl_context`): certifi's roots, the ones Home
Assistant's client context trusts, plus that file. Verification is never
turned off.

- "Go Daddy Secure Certificate Authority - G2" issued the certificate
  served on 10 November 2026, valid until 16 December 2026.
- "GoDaddy TLS Intermediate CA DV - R1v1", with its "GoDaddy TLS Root CA -
  R1" cross-signed by "Go Daddy Root Certificate Authority - G2", is what
  GoDaddy issues from since; no CA bundle carries the R1 root yet. It is
  there for the renewal, as api.atrias.be moved to it on 10 November 2026.
- The file is the gas integration's, which reads Atrias the same way. Both
  come from GoDaddy's `gd_bundle_dv-r1-g2.crt.pem` plus the G2
  intermediate.

A chain the file does not verify raises a `TransientFetchError` from
aiohttp's certificate error, which the live check reports as a failure and
files. Until 10 November 2026 a TLS failure was retried with verification
off, which let anyone on the path answer for inBW.

## Region and communes

- Wallonia. The resolver's ZDE-derived table (`_PER_POSTCODE` in
  `providers/_postcodes.py`) maps 20 postcodes to `inbw`, from 1300 to
  1495.
- One card, no commune picker.

## How each figure is read

`parse_tariff` in the module walks the first `<table>`.
`_row_amount_after_label` finds the row whose first cell holds every
keyword and returns the first amount in its Prix unitaire cell; a section
heading row with no amount falls through to the unlabelled row below it.

- `cvd_eur_per_m3`: the "Consommation entre 30 et 5000 m³" row, which is
  the full CVD (2,600 on the fixture).
- Cross-check: the "Consommation entre 0 et 30 m³" row must be half the
  CVD within 0,005 (the CWaPE residential rule), when present.
- Cross-check: the Hors TVA cell of the "Redevance annuelle (20 x CVD)"
  row must equal 20 x CVD within 0,05, when present.
- CVA: the Prix unitaire of "Redevance annuelle (30 x CVA)" divided by 30
  (82,440 / 30 = 2,748). The redevance cell shows 30 x CVA, not the CVA.
- FSE: the "Fonds Social" heading row falls through to the row below it
  (0,0339).
- Year: `detect_published_year` over the page text ("Tarifs 2026"). With
  no year it can read, the clock's year is used.
- `yearly_fixed_fee`: `20 x CVD + 30 x CVA`, computed by `build_tariff`
  (134,44 EUR on the fixture), which also holds the CVD to the 1.5 to 6.0
  EUR/m³ window.
- VAT: the Prix unitaire column is ex-VAT; `vat_rate` is the rate the page
  states ("TVA : 6 %", read by `stated_vat_rate`).

### CVA and FSE

The CVA is the 30 x CVA row's Prix unitaire divided by 30, rounded to 4
decimals, and the FSE the Fonds Social row's. Both go to `spge_components`
in `providers/_walloon_simple.py`, with the label "inBW": the card is
priced on them as printed, and a row that is gone, or a figure outside
its window (1 to 10 EUR/m³ for the CVA, 0,01 to 0,2 for the FSE), fails
the fetch.

### Dates

- `valid_from` 1 January, `valid_until` 31 December. A page still on last
  year's card in January is dated last year and stands until 31 March
  (`tests/test_inbw.py` pins it with the clock at 5 January 2027).

## Failures

- `TransientFetchError` for a network error, timeout, HTTP 5xx or 429, and
  for a chain the shipped intermediates do not verify, which the live check
  files.
- `ExtractorError`: no table, no full-CVD row, a first block that is not
  half the CVD, a redevance that is not 20 x CVD, a CVD outside the
  window, a missing CVA or Fonds Social row, a CVA or FSE outside its
  window.

## Checks

- Live check: the default fetch, no CI skip, and part of the Walloon SPGE
  comparison.
- Drift check (weekly): "inBW" against `inbw_2026.html`.
- `tests/test_inbw.py` runs the parsed card through the cost engine for
  100 m³ and gets 584,26 EUR, the page's own 584,261 € TVAC, which checks
  the parser and the Walloon tier math together (see
  [the pricing model](../pricing-model.md)).

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
