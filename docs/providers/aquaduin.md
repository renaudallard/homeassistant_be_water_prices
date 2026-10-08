# Aquaduin

Module: `custom_components/be_water_prices/providers/aquaduin.py`, the
card built by `build_flanders_tariff` in
`custom_components/be_water_prices/providers/_flanders.py`.
Tests: `tests/test_aquaduin.py`, plus the Aquaduin case in
`tests/test_parser_cross_checks.py`. Fixture:
`tests/fixtures/aquaduin_2026.pdf`.

Aquaduin (formerly IWVA) serves six Westkust communes, about 80 k
year-round residents. Its card is the gold-standard numeric PDF among the
Belgian water utilities, and the README's reference for a PDF-based
extractor.

## Where the tariff is

- The year page,
  https://www.aquaduin.be/nl/zelf-regelen/tarieven/tarieven-<year>
  (`SOURCE_URL_FMT`), which is also the `source_url` on the card.
- The PDF link is scraped from that page (`_find_pdf_href`). Since its 2026
  rebuild the CMS serves the file under an opaque `/volumes/...` path with
  a `?v=` cache-buster that changes on every upload, so only the page URL
  and the `overzicht-tarieven-<year>.pdf` file name are stable. A
  re-uploaded file's `_0`, `_1`, ... suffix is accepted.
- The link must resolve to https on `aquaduin.be` or a subdomain of it; an
  off-site link raises (`_discover_pdf_url`).
- The PDF is read with pdfplumber (`fetch_pdf_text_layout`, in a child
  process).
- Fallback: last year's page and PDF, see below.

## Region and communes

- Flanders. The resolver sends postcodes 8620, 8630, 8660, 8670, 8690 and
  8691 here (`_AQUADUIN_POSTCODES` in `providers/_postcodes.py`), ahead of
  the Farys default for the 8000-9999 block.
- One card, no commune picker.

## How each figure is read

`parse_tariff` in the module, on the PDF text:

```
Overzicht tarieven per 1 januari 2026
...
Basistarief 30 m³ + 30 m³ per gedomicilieerde persoon 5,9908 euro/m³ 6,35
Comforttarief > Basisverbruik (pro rata verrekend) 11,9816 euro/m³ 12
```

- Year: the card states the year it applies from, "Overzicht tarieven per
  1 januari `<year>`" (`stated_card_year` in `providers/_pdf.py`), and the
  parser holds the page URL's year to it. A card stating another year is
  refused, so a link left pointing at an older card is not served as this
  year's.
- `basis_eur_per_m3`: `_BASIS_RE`, "Basistarief 30 m³ ... N,NNNN euro/m³"
  (5,9908). The gap is `.*?` because "+ 30 m³ per gedomicilieerde persoon"
  carries digits. Three to five decimals.
- `comfort_eur_per_m3`: `_COMFORT_RE`, anchored on "Comforttarief >
  Basisverbruik" so an explainer above the row cannot supply it (11,9816).
  It must be twice the basis within 0,01 (VMM 2x rule). If the row is not
  found the comfort rate is taken as twice the basis.
- Sanering: Aquaduin publishes one integrated basistarief, drinkwater and
  saneringsbijdragen together, not the usual split. It is stored as
  `basis_eur_per_m3` with both sanering fields at 0, which keeps the bill
  right (basis plus sanering is still 5,9908); the `basis_rate` sensor
  shows the integrated rate as a result.
- `yearly_fixed_fee` 100 and per-resident discount 20: the VMM constants
  in `const.py` (`FLANDERS_VASTRECHT_TOTAL`,
  `FLANDERS_KORTING_TOTAL_PER_PERSON`). The PDF's own "20 %" line is the
  korting as a percentage of the vastrecht and is not read.
- VAT: ex-VAT figures; `vat_rate` is 6 %. `build_flanders_tariff` refuses
  a rate of zero or less, a rate outside 0.5 to 20 EUR/m³, and a negative
  sanering.
- `valid_from` 1 January, `valid_until` 31 December.

### Last year's card

`fetch` asks for the Belgian clock's year. Any `ExtractorError` that is not
transient (no PDF link on the page, an off-site link, a 404, an answer that
is not a PDF, a card that will not parse or states another year) sends it
to last year's page and PDF, parsed for last year and served until 31 March
(`carry_prior_year_card`).

This only helps while last year's page still links its card, and Aquaduin
strips that link once a year is over: on 2026-09-07 the 2025 page carried
none (prior-year pages keep the numbers as an inline HTML table). When the
link is gone the fallback raises, and the coordinator keeps serving the
cached card with the stale-snapshot Repair until the new card is
published.

## Failures

- `TransientFetchError` (network error, timeout, HTTP 5xx or 429) on this
  year's page or PDF is raised as it is, not masked by last year's card.
- `ExtractorError` when last year's card cannot be read either.
- PDF reading is bounded: a response over 16 MiB, streams inflating past
  64 MiB, or a parse past `PDF_READER_TIMEOUT_S` raise `ExtractorError`
  (`providers/_pdf.py`).

## Checks

- Live check: the default fetch, no CI skip.
- Drift check (weekly): "Aquaduin" against `aquaduin_2026.pdf`.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
