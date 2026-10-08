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

The server's TLS chain is misconfigured: it does not send the GoDaddy
intermediate certificate, so the default Python trust path fails.

`fetch` verifies first. Only when the failure's cause is a TLS one
(`aiohttp.ClientConnectorCertificateError`, `aiohttp.ClientSSLError` or
`ssl.SSLError`) does it log a warning and retry once with
`verify_ssl=False`. A timeout, a DNS failure or an HTTP 5xx is raised as
it is: retrying those unverified would let an on-path attacker trigger the
downgrade by disturbing the first request. `ClientPayloadError` is kept
out of the TLS set for the same reason. The day inBW fixes its chain the
downgrade stops on its own.

The risk is bounded: no credentials are involved, and the worst case is a
man in the middle serving wrong tariff numbers (the rationale is in the
`fetch_text` docstring in `providers/_pdf.py`, which the module docstring
points to).

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
- VAT: the Prix unitaire column is ex-VAT; `vat_rate` is 6 %.

### CVA and FSE

`spge_components` in `providers/_walloon_simple.py`, with the label
"inBW", against `WALLONIA_SPGE_YEAR` in `const.py`:

- A card of `WALLONIA_SPGE_YEAR` or earlier is priced on
  `WALLONIA_CVA_EUR_PER_M3` (2,748) and `WALLONIA_FSE_EUR_PER_M3` (0,0339).
  The table's values are held to them within 0,005 and 0,001; a move
  raises `ExtractorError`, and so does a 30 x CVA row that is gone.
- A card of a later year is priced on the table's own CVA and FSE, and
  fails if either is missing.

### Dates

- `valid_from` 1 January, `valid_until` 31 December. A page still on last
  year's card in January is dated last year and stands until 31 March
  (`tests/test_inbw.py` pins it with the clock at 5 January 2027).

## Failures

- `TransientFetchError` for a network error, timeout, HTTP 5xx or 429 that
  is not a TLS failure; the unverified retry is not attempted for those.
- `ExtractorError`: no table, no full-CVD row, a first block that is not
  half the CVD, a redevance that is not 20 x CVD, a CVD outside the
  window, a CVA or FSE off the constants for a card of their year, a
  missing CVA row, a later card missing either SPGE figure.

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
