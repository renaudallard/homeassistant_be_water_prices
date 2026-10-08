# AIEC

Module: `custom_components/be_water_prices/providers/aiec.py`, using
`build_extractor`, `build_tariff`, `spge_components` and `parse_tariff`
from `custom_components/be_water_prices/providers/_walloon_simple.py`.
Tests: `tests/test_walloon_small.py`. Fixtures:
`tests/fixtures/aiec_operator_2026.html` (the operator's page) and
`tests/fixtures/aiec_callmepower_2026.html` (the aggregator).

Association Intercommunale des Eaux du Condroz, a small operator covering
parts of the Condroz.

## Where the tariff is

- The operator's page, https://www.eauxducondroz.be/Prix.htm
  (`OPERATOR_URL`, read over https). AIEC publishes its card only as a
  picture: the page embeds a JPEG whose name carries the day the card took
  effect, `Tarif-2026-04-1-WEaf268c1c2a.jpg` on the fixture (the tail is the
  CMS dedupe hash). The rate inside cannot be read without OCR.
- The rate is therefore transcribed by hand in the module, in
  `_TRANSCRIBED_CVD`, keyed by the picture's date. Today it holds one card:
  1 April 2026, CVD 3,050 EUR/m³, read off "STRUCTURE TARIFAIRE DE L'EAU AU
  01/04/2026".
- Fallback: Callmepower's aggregator page,
  https://callmepower.be/fr/eau/distributeurs/aiec (`SOURCE_URL`), parsed by
  the shared prose parser.

This is the one per-distributor rate in the integration that is not
fetched. It earns the exception: the aggregator sat on 2,460 for the five
months after AIEC moved to 3,050 on 1 April 2026, under-billing an 80 m³
household by 53,16 EUR a year with nothing anywhere to show it.

## Region and communes

- Wallonia. The resolver's ZDE-derived table (`_PER_POSTCODE` in
  `providers/_postcodes.py`) maps 5360, 5361, 5362, 5363, 5370, 5372, 5374,
  5376, 5377, 5590 and 6990 to `aiec`.
- One card, no commune picker.

## How each figure is read

`fetch` decides between the transcribed card and the aggregator:

1. GET the operator page. A `TransientFetchError` (network error, timeout,
   HTTP 5xx or 429) is raised as it is: the aggregator is not consulted, so
   a blip on that one-page site cannot quietly swap the operator's rate for
   the aggregator's. An entry that already has a card keeps serving it
   behind the coordinator's stale-snapshot handling.
2. Any other `ExtractorError` on that page (404, a redirect off the site,
   and so on) means the page has moved: there is no way to tell which card
   is on screen, so the aggregator is served, undated, with a warning.
3. `published_card_date` (run in a worker thread) reads every
   `Tarif-YYYY-M-D...jpg` name on the page (`_CARD_IMAGE_RE`, the gap
   before the extension bounded to 200 characters) and keeps the newest
   valid date.
4. `card_from_operator_page`: if that date is in `_TRANSCRIBED_CVD`, the
   transcribed card is served and the aggregator is not asked.
5. Otherwise the aggregator is fetched. When the page did show a dated
   picture, a warning says the release does not carry that card's rate.
   `date_against_operator` then re-dates the aggregator card to the
   picture's date when that date is later in the same year (the aggregator
   prints none, so its card is always 1 January).

### The transcribed card

- `cvd_eur_per_m3`: the value in `_TRANSCRIBED_CVD` for the picture's date
  (3,050).
- `valid_from`: the picture's date. `source_url` is `OPERATOR_URL`, and the
  label reads "AIEC tarifs au 01/04/2026".
- CVA and FSE: the picture page prints neither, so `spge_components` is
  called with both absent and `cva_required=False`. For a card whose year
  is `WALLONIA_SPGE_YEAR` (`const.py`) or earlier this gives the constants
  `WALLONIA_CVA_EUR_PER_M3` (2,748) and `WALLONIA_FSE_EUR_PER_M3` (0,0339).
  For a later card it raises ("CVA is not printed on its `<year>` card"): a
  card transcribed for a year past the constants cannot be built until a
  release moves them.
- `yearly_fixed_fee`: `20 x CVD + 30 x CVA`, 143,44 EUR on the 2026 card.
- `valid_until`: 31 December of the picture's year. In the following year
  `build_tariff` gives it the usual grace to 31 March
  (`carry_prior_year_card`), and because its year is still the constants'
  year it is still priced on them. `tests/test_walloon_small.py` pins both
  halves (`test_aiecs_transcribed_card_depends_on_the_constants_year`).
- The picture also prints a per-inhabitant table of yearly bills (233,
  449, 665, 882, 1.098, 1.314 and 1.531 EUR). With 3,050 the cost engine
  reproduces it to within 0,50 EUR for 35 to 245 m³, and no other CVD
  does.

### The aggregator card

- Parsed by `_walloon_simple.parse_tariff` with the label "AIEC tarifs (via
  Callmepower)".
- `cvd_eur_per_m3`: Callmepower's summary cards print the value before its
  label, and the CVA card (2,748) follows the "CVD (distribution)" label, so
  a forward scan reads the CVA, which for AIEC (2,46 under 2,748) inflated
  the rate. The parser anchors on the prose "Coût vérité distribution (CVD)
  : 2,460 €/m³" (`_LABELED_DIST_CVD_RE`). If that anchor stops matching,
  the generic scan still skips any value equal to the CVA constant.
- CVA and FSE: read off the page with `parse_cva` and `parse_fse` and held
  to the constants by `spge_components`, as on every prose page.
- Year: "(tarifs 2026)" through `detect_published_year`; `valid_from` 1
  January unless re-dated from the picture.

## Failures

- Operator page transient failure: `TransientFetchError`, never the
  aggregator.
- Aggregator failures propagate as they are: `TransientFetchError` for a
  blip, `ExtractorError` for a parse failure, a moved CVA or FSE, or a CVD
  outside the 1.5 to 6.0 EUR/m³ window.
- A transcribed card for a year past `WALLONIA_SPGE_YEAR` raises
  `ExtractorError`.
- Refusing an unread card outright was tried first and took every AIEC
  entry dark while stopping new ones from finishing setup, so a card
  nobody has read falls back to the aggregator instead.

## Checks

- Live check: the default fetch, no CI skip, and part of the Walloon SPGE
  comparison.
- Drift check (weekly): "AIEC" compares `_aiec_card` on
  `aiec_operator_2026.html` (the transcribed card, refusing a picture
  nobody has transcribed) with the live fetch. A new picture sends the live
  fetch to Callmepower, whose CVD then shows as drift against 3,050 unless
  Callmepower has the same figure; `valid_from` is not compared.

## Adding a card

When AIEC publishes a new picture, read it and add a line to
`_TRANSCRIBED_CVD` keyed by the date in the file name. The existing entry
was checked against the per-inhabitant table printed on the same picture;
a new one should be too.

## Quirks

- The constants apply to any card of `WALLONIA_SPGE_YEAR` or earlier, and
  this page prints no CVA or FSE to compare against. Once a release moves
  the constants to a new year, a 2026 card still served in its grace
  period is priced on the new figures, the CVA and FSE in force while it
  is served.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
