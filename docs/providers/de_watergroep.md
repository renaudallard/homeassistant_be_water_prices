# De Watergroep

Module: `custom_components/be_water_prices/providers/de_watergroep.py`,
the card built by `build_flanders_tariff` in
`custom_components/be_water_prices/providers/_flanders.py`.
Tests: `tests/test_de_watergroep.py`, plus the De Watergroep cases in
`tests/test_per_commune.py`, `tests/test_parser_cross_checks.py`,
`tests/test_prior_year_card.py` and `tests/test_ajax_redirects.py`.
Fixtures: `tests/fixtures/dewatergroep_halle_2026.html` (the default),
`dewatergroep_sinaai_2026.html`, `dewatergroep_opglabbeek_2026.html` (a
commune that cannot show a leg) and `dewatergroep_tarieven_2026.html` (the
commune dropdown).

De Watergroep is the largest Flemish operator: 167 communes, about 3.3 M
customers, about 49.5 % of Flanders. The default card is the full
integrale waterprijs; picking a commune gives that commune's exact
figures.

## Where the tariff is

- One cookie-driven endpoint,
  `https://www.dewatergroep.be/Tarief/UpdateDetailTariefJaar/<year>`
  (`COMMUNE_DETAIL_URL_FMT`), asked with the commune's GUID in a
  `dwg_l=<GUID>` cookie and `X-Requested-With: XMLHttpRequest`. It returns
  the full per-commune bill: drinkwater plus the gemeentelijke and
  bovengemeentelijke saneringsbijdragen.
- Redirects are not followed (`allow_redirects=False`), so the commune
  cookie cannot travel to wherever one points. After each request the
  session's jar is cleared of `dwg_l`: aiohttp merges its jar into the
  Cookie header, and a cookie left from an earlier request could answer for
  a commune nobody asked about, with nothing in the body naming it. The
  memo key carries the commune for the same reason.
- There is no fallback source. The news article
  `over-de-watergroep/nieuws/tarieven-<year>` used to be one, and carries
  the drinkwater leg alone: it billed 355,32 EUR a year at 80 m³ where the
  Halle card bills 782,73, with nothing on the entry to say which was
  served.

## Region and communes

- Flanders. The resolver sends 1500 to 1999 and 3000 to 3999 here, minus
  17 Farys-served postcodes and a few secondary Farys ones (1733, 1931,
  1934, 1935), plus 122 De Watergroep postcodes scattered through the
  Farys block (`_DWG_POSTCODES_FLANDERS` in `providers/_postcodes.py`) and
  9451. Seven postcodes split at street level with Farys are put to the
  user as a choice.
- Commune list: `list_communes` scrapes the dropdown on
  https://www.dewatergroep.be/nl-be/drinkwater/tarieven
  (`COMMUNE_LIST_URL`), `<option>` entries whose value is a GUID; about 700
  entries (699 in October 2026). No phantom filter.
- Default commune: Halle, postcode 1500, GUID
  `{B16A143A-49E6-4CE5-A241-1AA09BFC406A}` (`_DEFAULT_COMMUNE_GUID`),
  labelled "Halle (DWG-served default)". Saneringsbijdragen vary by
  commune, so the default is an estimate for a household that never picks
  its own: across all 699 commune pages the mean error is 0,43 EUR a year
  and the worst real case 58,65 (Overijse, gemeentelijke 1,4039).
- No `commune_for_postcode`: no postcode is pre-selected.

## How each figure is read

`parse_commune_tariff`, on the answer's flat text.

- The block (`_basis_per_m3_block`): from "Basistarief per m³" up to the
  next "Basistarief per liter" or "Comforttarief", the first such block
  that holds "Waterverbruik drinkwater". The bound keeps the row patterns
  out of the comforttarief block and out of the per-litre table, whose
  afvoer row (0,0020) is a thousandth of the rate.
- `basis_eur_per_m3`: "Waterverbruik drinkwater € N,NNN" (2,9251 for
  Halle). Required.
- `comfort_eur_per_m3`: twice the basis (VMM 2x rule), not read.
- `sanering_gemeentelijk_eur_per_m3`: "Afvoer van afvalwater € N,NNN"
  (1,9572 for Halle, 1,9114 for Sinaai).
- `sanering_bovengemeentelijk_eur_per_m3`: "Zuivering van afvalwater €
  N,NNN" (1,7019).
- `yearly_fixed_fee` 100 and per-resident discount 20: the VMM constants
  in `const.py`.
- VAT: ex-VAT figures; `vat_rate` is 6 %. `build_flanders_tariff` refuses
  a rate of zero or less, a rate outside 0.5 to 20 EUR/m³, and a negative
  sanering.
- Label: "De Watergroep tarieven `<year>` (`<commune>`)". The per-commune
  fetch puts the GUID there and the coordinator swaps it for the saved
  commune label (`relabel_with_human_commune` in `providers/base.py`).

### A leg that is missing

A leg is never billed at zero. All 699 commune pages carry both legs, 698
as an amount, so an absent leg is either De Watergroep saying it cannot
show it or a parser problem, never a commune that levies nothing.

- When the page prints "De kostprijs kan momenteel niet getoond worden" in
  place of a saneringsbijdrage (matched word by word, since the page wraps
  the sentence), the parse raises `UnshowableLeg`, a subclass of
  `ExtractorError`. Read as zero it billed 3660 Opglabbeek 575,26 EUR a
  year against 782,73 on 2026-09-08.
- A card that omits the row raises a plain `ExtractorError`
  ("printed no gemeentelijke saneringsbijdrage"). Read as zero that cost
  207,47 EUR a year on an 80 m³ bill.

### Year

- The URL asks for the Belgian clock's year. `_served_year` then reads the
  year off the answer's active tab, since an endpoint asked for a year it
  has not published can answer with the newest card it has:

  ```
  UpdateDetailTariefJaar/<year>/hh-tarieven" class="active" title="Huishoudelijke tarieven <year>"
  ```

  Both years must agree. Without a readable tab the asked year stands.
- `_newest_commune_card`: while the new year's endpoint has nothing (it
  fails, or answers with last year's tab), last year's card is fetched from
  its own endpoint and stands until 31 March (`carry_prior_year_card`).
  `tests/test_de_watergroep.py` pins both shapes with the clock at
  5 January 2027.

### A commune whose page will not show a leg

`fetch_for_commune` catches `UnshowableLeg` for a picked commune and
serves the current default (Halle) card instead, logged as a warning and
labelled "Halle (DWG-served default)" so the swap shows on the entity and
in diagnostics. It does not fall back to that commune's own last-year
card, which would stand only until 31 March and then sit there stale.
3660 Opglabbeek has printed the sentence for months and could not be set
up at all before this; for Opglabbeek the default is the figure the bill
was calculated at.

Only that failure is caught. An omitted row on the current card goes the
ordinary way through `_newest_commune_card`, last year's card for the same
commune, and fails when that one does not parse either. For the default
commune itself an unshowable leg also goes to last year's card, as there
is no default to swap it for.

## Failures

- `TransientFetchError`: network error, timeout, HTTP 5xx or 429. Never
  answered with last year's card.
- `ExtractorError`: a 3xx or 4xx answer, an empty body or one without
  "Basistarief" ("probably an invalid GUID"), no "Basistarief per m³"
  block, no drinkwater row, a missing leg, an unshowable leg on the
  default card when last year's will not serve either, a dropdown with no
  options.

## Checks

- Live check: the default fetch, no CI skip. The commune list floor
  (`MIN_COMMUNES`) is 600.
- Drift check (weekly), two rows: "De Watergroep (default commune)"
  against `dewatergroep_halle_2026.html` through `fetch_for_commune` with
  the Halle GUID (the endpoint is all there is), and "De Watergroep
  (Sinaai)" against `dewatergroep_sinaai_2026.html`, a commune whose
  gemeentelijke leg differs from the default so a move in either shows.
  The Sinaai fixture once held a blank afvoer row the live page did not
  have, and nothing reported it because it was not on the list.
- The card archive walks all the communes on Sundays. Its replay session
  carries a stand-in jar (`_NoJar` in `scripts/archive_cards.py`) for the
  cookie clear the extractor does after every request.

## Quirks

- The `parse_commune_tariff` docstring still says a bare label with no
  amount counts as a commune that levies nothing; the code refuses it, and
  `tests/test_per_commune.py` and `tests/test_de_watergroep.py` pin the
  refusal.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
