# AIEC

Module: `custom_components/be_water_prices/providers/aiec.py`, using
`build_extractor`, `build_tariff`, `spge_components` and `parse_tariff`
from `custom_components/be_water_prices/providers/_walloon_simple.py`,
`read_figures` from `providers/_card_figures.py` and
`archived_picture_text` from `providers/_archive.py`.
Tests: `tests/test_aiec_picture.py`, `tests/test_card_figures.py` and
`tests/test_walloon_small.py`. Fixtures: `tests/fixtures/aiec_operator_2026.html`
(the operator's page), `tests/fixtures/aiec_callmepower_2026.html` (the
aggregator), `tests/fixtures/aiec_tarif_2026-01-01.jpg` and
`aiec_tarif_2026-04-01.jpg` (the last two pictures), and
`tests/fixtures/aiec_ocr_<date>.txt`, what Tesseract 5.5 read off the
editions of 1 June 2021, 1 January 2023, 1 January 2026 and 1 April 2026.

Association Intercommunale des Eaux du Condroz, a small operator covering
parts of the Condroz.

## Where the tariff is

- The operator's page, https://www.eauxducondroz.be/Prix.htm
  (`OPERATOR_URL`, read over https). AIEC publishes its card only as a
  picture: the page embeds a JPEG whose name carries the day the card took
  effect, `Tarif-2026-04-1-WEaf268c1c2a.jpg` on the fixture (the tail is
  the CMS dedupe hash).
- The picture is read by the card archiver with Tesseract, in CI, and the
  text it read is kept on the archive branch beside the row it made (see
  [data-sources.md](../data-sources.md#the-card-archive)). Nothing in Home
  Assistant decodes a picture: an installation reads that text and finds
  the tariff in it itself.
- `_TRANSCRIBED` in the module holds the cards a person has read, keyed by
  the picture's date. It holds one: 1 April 2026, CVD 3,050, CVA 2,748 and
  Fonds social 0,0339 EUR/m³ ex-VAT, read off "STRUCTURE TARIFAIRE DE
  L'EAU AU 01/04/2026".
- Fallback: Callmepower's aggregator page,
  https://callmepower.be/fr/eau/distributeurs/aiec (`SOURCE_URL`), parsed
  by the shared prose parser.

AIEC has drawn every edition differently: the 2021 and 2023 cards are one
layout in one font at 848 px, the January 2026 card another layout in a
serif font at 1229 px, and the April 2026 card that layout again in
Calibri at 585 px, its digits 9 px tall. So the reading looks for figures,
never for a label or a position.

## How the card is found

`fetch` loads the operator page:

1. A `TransientFetchError` (network error, timeout, HTTP 5xx or 429) is
   raised as it is: the aggregator is not consulted, so a blip on that
   one-page site cannot quietly swap the operator's rate for the
   aggregator's. Any other `ExtractorError` (404, a redirect off the site,
   and so on) means the page has moved: the aggregator is served, undated,
   with a warning.
2. `published_card` (run in a worker thread) reads every
   `Tarif-YYYY-M-D...jpg` name on the page (`_CARD_IMAGE_RE`, the gap
   before the extension bounded to 200 characters) and keeps the newest
   valid date, with the picture's URL: the attribute value the name sits
   in, joined to the page's URL, kept only when it stays on
   `eauxducondroz.be` over https.
3. A date in `_TRANSCRIBED` is served from there.
4. Otherwise the picture's text is asked for (`fetch_picture_text` in
   `providers/_pdf.py`). In Home Assistant that is the archive's reading:
   `archived_picture_text` takes the newest AIEC row on the branch, this
   month's first and then back a month at a time, and the text of its
   source of variant `ocr` whose URL is the picture's. A row that read
   another picture, or none, gives nothing. The text is fetched only from
   `texts/<sha256>.txt` and only kept when its SHA-256 is its name.
5. The text is checked (below). A card that checks out is served, dated
   from the picture and labelled "AIEC tarifs au 01/04/2026 (lu sur
   l'image)".
6. Anything else falls back to the aggregator with a warning, re-dated to
   the picture by `date_against_operator` when the picture's date is later
   in the same year (the aggregator prints none, so its card is always
   1 January). A failure to reach the archive is raised, as on the
   operator page.

The `card_archive` option turns step 4 off with the rest of the archive's
reads.

## The check

`read_figures` in `providers/_card_figures.py` collects every number in
the text with the decimals it was printed with (a comma is the decimal
mark, points group thousands; a lone point followed by three digits is read
both ways). The candidates are the numbers with three or more decimals in
the windows the integration already holds: CVD 1,5 to 6,0, CVA 1,0 to
10,0, Fonds social 0,01 to 0,2 EUR/m³. The tariff is the one CVD, CVA and
Fonds social whose ten derived figures are all among the numbers read at
four decimals, rounded half-up:

| Figure | Value, with v = 1,06 |
| --- | --- |
| Redevance, and × v | 20·CVD + 30·CVA |
| First tranche, and × v | 0,5·CVD |
| Second tranche, and × v | CVD + CVA |
| Third tranche, and × v | 0,9·CVD + CVA |
| First and second totals × v | (0,5·CVD + FSE)·v and (CVD + CVA + FSE)·v |

None, or more than one, is a refusal naming the figures the closest
candidate misses. Every edition since 2021 prints all ten at four
decimals. An OCR engine does not compute, so its misreads are independent
of each other: a wrong tariff would need ten of them to agree with it, and
what a misread does instead is make a figure go missing. A whole number
never stands for a figure; the first trial run matched a bare `2` from the
per-inhabitant table to anything rounded to a unit.

`fetch` then requires the picture's own date, `dd/mm/yyyy`, in the text:
every edition heads its card "au 01/04/2026", and a text read off another
card agrees with itself just as well.

On the four editions, as Tesseract 5.5 read them: January 2023, January
2026 and April 2026 give the figures they print (January 2026 without its
red header CVD, which was not read, from the detail table's `2,4600`), and
June 2021 is refused, its `1.2694` read as `12694`.

## Read in CI

The card archiver installs a picture reader (`StoredTexts.read_picture` in
`scripts/card_texts.py`) when Tesseract is installed. The picture is
downloaded under the usual guards (16 MiB cap, no redirect off the site, a
JPEG or PNG signature), grown to about 1800 px across (at least twice) in
grey on white, and read with `tesseract stdin stdout -l fra --psm 6` on one
thread. The row's source records the picture's digest and the engine
(`ocr`: version, arguments, a digest of the preparation); the picture goes
to the cards releases as `<sha256>.jpg`. A picture whose bytes are known is
served its stored text, and a Tesseract upgrade reads the kept pictures
again.

In CI the transcribed card is read too: a reading that disagrees with
`_TRANSCRIBED`, or any refused reading, is written to the file
`--ocr-failures` names and filed as an issue under the `archive-cards-ocr`
label.

## Region and communes

- Wallonia. The resolver's ZDE-derived table (`_PER_POSTCODE` in
  `providers/_postcodes.py`) maps 5360, 5361, 5362, 5363, 5370, 5372, 5374,
  5376, 5377, 5590 and 6990 to `aiec`.
- One card, no commune picker.

## The cards

- `cvd_eur_per_m3`, CVA and FSE: transcribed, or read and checked, or the
  aggregator's (below), each CVA and FSE through `spge_components`.
- `valid_from`: the picture's date. `source_url` is `OPERATOR_URL`.
- `yearly_fixed_fee`: `20 x CVD + 30 x CVA`, 143,44 EUR on the April 2026
  card.
- `vat_rate`: 0,06, the "TVAC 6%" every edition prints; another rate
  makes the check refuse.
- `valid_until`: 31 December of the picture's year. In the following year
  `build_tariff` gives it the usual grace to 31 March
  (`carry_prior_year_card`).
- The April 2026 picture also prints a per-inhabitant table of yearly
  bills (233, 449, 665, 882, 1.098, 1.314 and 1.531 EUR). With 3,050 the
  cost engine reproduces it to within 0,50 EUR for 35 to 245 m³, and no
  other CVD does.

### The aggregator card

- Parsed by `_walloon_simple.parse_tariff` with the label "AIEC tarifs (via
  Callmepower)".
- `cvd_eur_per_m3`: Callmepower's summary cards print the value before its
  label, and the CVA card (2,748) follows the "CVD (distribution)" label, so
  a forward scan reads the CVA, which for AIEC (2,46 under 2,748) inflated
  the rate. The parser anchors on the prose "Coût vérité distribution (CVD)
  : 2,460 €/m³" (`_LABELED_DIST_CVD_RE`). If that anchor stops matching,
  the generic scan still skips any value equal to the CVA the page prints.
- CVA and FSE: read off the page with `parse_cva` and `parse_fse` and
  checked by `spge_components`, as on every prose page.
- Year: "(tarifs 2026)" through `detect_published_year`; `valid_from` 1
  January unless re-dated from the picture.

## Failures

- Operator page or archive transient failure: `TransientFetchError`, never
  the aggregator.
- A reading refused by the check or by the date: the aggregator, a warning
  in the log, and in CI an issue.
- Aggregator failures propagate as they are: `TransientFetchError` for a
  blip, `ExtractorError` for a parse failure, a CVA or FSE not printed or
  outside its window, or a CVD outside the 1.5 to 6.0 EUR/m³ window.
- Refusing an unread card outright was tried first and took every AIEC
  entry dark while stopping new ones from finishing setup, so a card
  nobody has read falls back to the aggregator instead.

## Checks

- Live check: the default fetch, no CI skip, and part of the Walloon SPGE
  comparison. It installs no picture reader, so it takes the archive's
  reading as an installation does.
- Drift check (weekly): "AIEC" compares `_aiec_card` on
  `aiec_operator_2026.html` (the transcribed card, refusing a picture
  nobody has transcribed) with the live fetch.
- `tests/test_aiec_picture.py` runs the installed Tesseract on the two 2026
  pictures when it is there, which `test.yml` makes sure of.

## Adding a card

A new picture needs nothing: the next archive run reads it, and
installations serve it once the reading checks out. When the run files an
`archive-cards-ocr` issue instead, read the picture and add a line to
`_TRANSCRIBED` keyed by the date in the file name, with the CVD, CVA and
Fonds social it prints ex-VAT, and check it against the per-inhabitant
table the picture prints.

## Quirks

- A card still served in its grace period next year is priced on the
  CVA and FSE its picture prints, until AIEC publishes the new card.
- The 2021 and 2023 pictures were `Files/Image/prix.png`, overwritten in
  place; the January 2026 one is `Files/Image/Prix20260101.jpg`, whose
  name `published_card` does not read. Only the dated names since April
  2026 are found.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
