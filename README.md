# Tariff card archive

Written daily by `.github/workflows/archive_cards.yml` running
`scripts/archive_cards.py` from the main branch. Not edited by hand.

- `<utility>/<commune>/<YYYY-MM>.json`: the tariff as the integration
  parsed it in that month, for the utility's no-commune fetch (`default`)
  or for one commune of a per-commune utility, named by the commune id the
  integration uses (`_commune_label` in the file says which commune that
  is).
- `texts/<sha256>.txt`: every page and rendered PDF a parse read, stored
  once and shared between the rows that read it. Each row lists its own
  under `_sources`, and names a PDF it read by SHA-256.
- `pdfs.json`: where each PDF is kept, as `<release tag>/<sha256>.pdf` in
  the releases of the cards repository (`be_price_cards`, shared with
  be_electricity_prices; this integration's releases are `water-<YYYY-MM>`).
- `coverage.md`: which months the branch holds for each utility and
  commune, each linking to the PDF or the page it was parsed from.
- `pdfs.md`: every kept PDF, by release, with each row it was read for.

To get the original card of a utility, commune and month: open
`coverage.md`, find the row, click the month. Months older than three
years are removed.
