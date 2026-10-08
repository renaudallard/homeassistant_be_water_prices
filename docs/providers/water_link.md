# Water-link

Module: `custom_components/be_water_prices/providers/water_link.py`, the
card built by `build_flanders_tariff` in
`custom_components/be_water_prices/providers/_flanders.py`.
Tests: `tests/test_water_link.py`, plus the Water-link cases in
`tests/test_parser_cross_checks.py` and `tests/test_per_commune.py`.
Fixture: `tests/fixtures/water_link_2026.pdf`.

Water-link serves all of Antwerp city plus Hove, Mortsel, Edegem and
Beveren-Kruibeke-Zwijndrecht, about 200 k customers.

## Where the tariff is

- One household PDF per year,
  `https://water-link.be/sites/default/files/<YYYY>-<MM>/<YYYY>%20HH.pdf`,
  read with pdfplumber (`fetch_pdf_text_layout`).
- The upload directory carries the month the card was published, so the
  link is discovered from the Antwerpen tariff page,
  https://water-link.be/informatie/tarieven-en-kortingen/tarieven/antwerpen
  (`TARIFF_PAGE_URL`), rather than templated.
- `_PDF_HREF_RE_FMT` binds the year to the file name, not the directory: a
  card for next year uploaded in December sits under this year's
  directory (`2026-12/2027 HH.pdf`) and must not be read as this year's.
  The required `<year> HH.pdf` shape also excludes the "andere" and "NHH"
  (non-household) cards the page links. Drupal's `_0`, `_1`, ... suffix on
  a re-uploaded file is accepted.
- The link must be https on `water-link.be` or a subdomain; an off-site
  link raises.
- When the page carries no link for the year, the templated January path
  `SOURCE_URL_FMT` (`.../<year>-01/<year>%20HH.pdf`) is used, which is
  where every card so far has landed.
- The card's `source_url` is the PDF actually read.
- The watertarieven HTML page carries 22 unlabelled rate tables; the rates
  are read from the PDF instead.

## Region and communes

- Flanders. The resolver sends 2000 to 2070 here, plus the city's other
  district postcodes and the three ring communes (`_WATER_LINK_POSTCODES`
  in `providers/_postcodes.py`: 2099, 2100, 2140, 2150, 2170, 2180, 2540,
  2600, 2610, 2640, 2650, 2660). Ranst, Hemiksem and Schoten stay with
  Pidpa: Water-link lists them but its card bills none of them.
- Per-commune: the PDF carries one BASISTARIEF row per commune, five in
  2026 (Antwerpen, Beveren-Kruibeke-Zwijndrecht, Edegem, Hove, Mortsel).
  Drinkwater and zuivering are uniform across the area; only the
  gemeentelijke afvoer differs, Antwerpen at 1,3345 EUR/m³ and the ring
  communes at 1,9572.
- Default commune: Antwerpen (`_DEFAULT_COMMUNE`), the city Water-link is
  named after and the bulk of its customers. The commune is picked in the
  OptionsFlow.
- `list_communes` (`_parse_commune_lines`) reads the commune names off the
  BASISTARIEF block of the same PDF; each name is both the id and the
  label.
- Pre-selected postcodes (`commune_for_postcode`): 2070 (Zwijndrecht and
  Burcht) to Beveren-Kruibeke-Zwijndrecht, 2540 to Hove, 2640 to Mortsel,
  2650 to Edegem. Each sits in the ring group on Water-link's own card, not
  in Antwerpen. Left on the default, an 80 m³ household in 2070 was billed
  66 EUR a year too little. The city's own districts need no entry, since
  the card bills all of them on the Antwerpen row. An entry with no commune
  gets its postcode's commune on load too (`_adopt_commune_for_postcode` in
  `__init__.py`).

## How each figure is read

```
Geldig vanaf 1 januari 2026
1 Vastrecht per wooneenheid 50,0000 30,0000 20,0000 100,0000 106,0000
Korting per gedomicilieerde inwoner -10,0000 -6,0000 -4,0000 -20,0000 -21,2000
...
Integrale waterprijs: prijs per verbruikte m³ BASISTARIEF (*)
Antwerpen 1,6692 1,3345 1,7019 4,7056 4,9879
Beveren-Kruibeke-Zwijndrecht 1,6692 1,9572 1,7019 5,3283 5,6480
Edegem 1,6692 1,9572 1,7019 5,3283 5,6480
Hove 1,6692 1,9572 1,7019 5,3283 5,6480
Mortsel 1,6692 1,9572 1,7019 5,3283 5,6480
Integrale waterprijs: prijs per verbruikte m³ COMFORTTARIEF (*)
Antwerpen 3,3384 2,6690 3,4038 9,4112 9,9759
...
```

That is the extracted text of the 2026 fixture. The columns are water,
afvoer, zuivering, total ex-VAT and total with VAT.

- Year: the card states "Geldig vanaf 1 januari `<year>`"
  (`stated_card_year`), and the parser holds the link's year to it; a card
  stating another year is refused.
- Rows (`_parse_commune_row`): after the "BASISTARIEF" marker (or
  "COMFORTTARIEF"), the first line starting with the commune's name and
  followed by four amounts: water, afvoer, zuivering, total ex-VAT. The
  three legs must make the printed total within 0,00005, so a row that
  lost or gained a column is refused.
- `basis_eur_per_m3`: the basis row's water column (1,6692).
- `comfort_eur_per_m3`: the comfort row's water column (3,3384), held to
  twice the basis within 0,01 (VMM 2x rule).
- `sanering_gemeentelijk_eur_per_m3`: the basis row's afvoer column
  (1,3345 for Antwerpen, 1,9572 for the ring).
- `sanering_bovengemeentelijk_eur_per_m3`: the basis row's zuivering
  column (1,7019).
- `yearly_fixed_fee` 100 and per-resident discount 20: the VMM constants
  in `const.py`; the PDF's vastrecht and korting lines are not read.
- VAT: ex-VAT figures; `vat_rate` is 6 %. `build_flanders_tariff` refuses
  a rate of zero or less, a rate outside 0.5 to 20 EUR/m³, and a negative
  sanering.
- Label: "Water-link tarieven huishoudelijk `<year>` (`<commune>`)".
  `valid_from` 1 January, `valid_until` 31 December.

### Last year's card

`_fetch_pdf_text` asks for the Belgian clock's year. A non-transient
`ExtractorError` on this year's card (the PDF missing, a link off-site, an
answer that is not a PDF) sends it to last year's link, read the same way
and served until 31 March (`carry_prior_year_card`). The parse runs after
that choice, so a card that downloads but will not parse, or states
another year than its link, fails rather than falling back.

## Failures

- `TransientFetchError` (network error, timeout, HTTP 5xx or 429) on the
  tariff page or on this year's PDF is raised as it is, never masked by
  last year's card.
- `ExtractorError`: a commune with no basis or comfort row, legs that do
  not make the total, a comfort rate that is not twice the basis, a card
  stating another year, no BASISTARIEF block or no commune rows (for the
  commune list), last year's card unreadable too.

## Checks

- Live check: skipped on GitHub Actions (`CI_BLOCKED` in
  `scripts/live_check.py`): Water-link's CDN answers HTTP 403 to datacenter
  IP ranges, while residential IPs work. Rerun locally to check it. The
  commune list floor (`MIN_COMMUNES`) is 4; the lister returned 5 in
  October 2026.
- Drift check (weekly): "Water-link (Antwerpen default)" against
  `water_link_2026.pdf`, skipped on Actions for the same reason
  (`CI_BLOCKED` in `scripts/fixture_drift.py`).
- The card archive (`scripts/archive_cards.py`) uses the live check's
  `CI_BLOCKED` and skips Water-link on a runner as well.

## Quirks

- The commune scan pattern joins name words with single spaces; an older
  shape backtracked quadratically on a padded line, and the scan ran on
  the event loop from the config flow. It now runs in a thread.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
