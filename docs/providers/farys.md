# Farys

Module: `custom_components/be_water_prices/providers/farys.py`, the card
built by `build_flanders_tariff` in
`custom_components/be_water_prices/providers/_flanders.py`.
Tests: `tests/test_farys.py`, plus the Farys cases in
`tests/test_parser_cross_checks.py`, `tests/test_per_commune.py`,
`tests/test_prior_year_card.py` and `tests/test_ajax_redirects.py`.
Fixtures: `tests/fixtures/farys_gent_2026.json` (Gent-centrum) and
`tests/fixtures/farys_zaventem_2026.json` (Zaventem).

Farys (TMVW) covers Oost-Vlaanderen and parts of West-Vlaanderen and
Vlaams-Brabant: 85 communes, about 1.5 M people (about 22 % of Flanders).

## Where the tariff is

- https://www.farys.be/nl/watertarieven (`PAGE_URL`) is rendered by
  JavaScript: the static HTML carries only the commune dropdown. Picking a
  commune fires a Drupal AJAX form POST.
- The extractor POSTs to that endpoint itself,
  https://www.farys.be/nl/watertarieven?ajax_form=1 (`ENDPOINT_URL`), with
  the commune id in the form (`switcher=WaterRateInformation`,
  `municipality=<id>`, `current_page_nid=20471`,
  `form_id=farys_municipalities_switcher_form`,
  `_triggering_element_name=municipality`). No `form_build_id` is needed.
- The answer is a JSON list of Drupal commands; the `insert` command whose
  `data` holds "Basistarief" carries the per-commune integrale waterprijs
  as HTML (`_extract_html_payload`).
- Redirects are not followed (`allow_redirects=False`): a 3xx is a moved
  endpoint and raises. The memo key carries the commune, since every
  commune is asked at the same URL.
- The card's `source_url` is `PAGE_URL`. There is no fallback source.

## Region and communes

- Flanders. The resolver sends most of 8000 to 9999 here, after the AGSO,
  Aquaduin and De Watergroep carve-outs, plus 17 Farys-served postcodes
  inside the 1500 to 1999 De Watergroep block and a few secondary
  postcodes (`providers/_postcodes.py`). Seven postcodes split at street
  level between Farys and De Watergroep (1770, 8020, 8400, 8490, 9080,
  9550, 9570) are put to the user as a choice.
- Default commune: Gent-centrum, id 25071 (`DEFAULT_MUNICIPALITY_ID`), the
  namesake city and the largest commune in its Oost-Vlaanderen heartland.
  The commune is picked in the OptionsFlow.
- Commune list: `list_communes` scrapes the `<option value="<id>">` entries
  of the dropdown on `PAGE_URL` (about 290). Labels read "`<postcode>` -
  `<commune>` (`<gemeente>`)".
- Phantom filter: 23 entries Farys lists but serves no tariff for (split
  postcodes where De Watergroep is the actual operator; the endpoint
  answers without an `insert` command) are dropped by label,
  `FARYS_UNSERVABLE_LABELS` in
  `custom_components/be_water_prices/_phantom_blocklists.py`, so they
  cannot be picked. An entry that saved one of them, by id
  (`FARYS_UNSERVABLE_IDS`), has it removed on load
  (`_drop_phantom_commune_if_blocked` in `__init__.py`). About 265
  communes remain; the lister returned 266 in October 2026.
- Pre-selected postcodes (`commune_for_postcode`): five of the 266 commune
  cards are not the default's, checked against all of them on 2026-09-09.

  | Postcode | Commune id | Why |
  | --- | --- | --- |
  | 1620 | 25906 | Drogenbos levies a gemeentelijke saneringsbijdrage of 1,4903 against 1,9572 elsewhere |
  | 1930 | 25926 | Zaventem (and Nossegem, billed the same), gemeentelijke tussenkomst |
  | 1932 | 25931 | Sint-Stevens-Woluwe, gemeentelijke tussenkomst |
  | 1933 | 25936 | Sterrebeek, gemeentelijke tussenkomst |
  | 1935 | 25926 | Zaventem, which Farys lists under 1930 |

  Left on the default, Drogenbos was over-charged 49,49 EUR a year and the
  Zaventem communes 8,55. An entry with no commune gets its postcode's
  commune on load too (`_adopt_commune_for_postcode` in `__init__.py`).

## How each figure is read

`parse_tariff`, on the text of the `insert` HTML.

- Every rate row prints the rate twice, ex-VAT then with 6 % VAT, and both
  are read (`_PAIR`): rate x 1,06 must match the VAT figure within 0,001.
  A row that lost its ex-VAT cell would otherwise hand over the VAT figure,
  six percent high and shaped like a price.
- `basis_eur_per_m3`: "Basistarief drinkwater (per m³)" (3,0058 for
  Gent-centrum).
- `comfort_eur_per_m3`: "Comforttarief drinkwater (per m³)" (6,0116).
- Gemeentelijke tussenkomst: a commune that pays part of the drinkwater
  leg for its residents has it printed as a negative row under the tarief
  it applies to, "Gemeentelijke tussenkomst op basistarief drinkwater (per
  m³)" and the comforttarief twin. Both are optional and read signed; the
  value is added to the leg before any check. Zaventem prints 3,0058 and a
  tussenkomst of -0,0807, so its basis is 2,9251 and its comfort 5,8502.
- Then the comfort rate must be twice the basis within 0,01 (VMM 2x rule).
- `sanering_gemeentelijk_eur_per_m3`: "Basistarief gemeentelijke bijdrage
  (per m³)" (1,9572); `sanering_bovengemeentelijk_eur_per_m3`:
  "Basistarief bovengemeentelijke bijdrage (per m³)" (1,7019). The patterns
  spell the label out to its closing "(per m³)", so an empty cell cannot
  let the match run into the comforttarief on the next line.
- Sum check: "Integrale waterprijs basistarief (per m³)", printed two rows
  below the legs, must equal the netted basis plus both saneringen within
  0,0001 (6,5842 for Zaventem, not the 6,6649 the gross rows add up to).
- `yearly_fixed_fee` 100 and per-resident discount 20: the VMM constants
  in `const.py`.
- VAT: `vat_rate` is 6 %. `build_flanders_tariff` refuses a rate of zero or
  less, a rate outside 0.5 to 20 EUR/m³, and a negative sanering.
- Label: "Farys watertarieven `<year>` (`<commune>`)". The per-commune fetch
  puts the numeric id there and the coordinator swaps it for the saved
  commune label (`relabel_with_human_commune` in `providers/base.py`).

### Year

- `_active_period_year` reads the period switcher, `ul.js-period-rates
  li.active button[value]`: the bare year on most cards, "Jan. 2026" on the
  Zaventem ones. A switcher that names no year is logged and the clock
  takes over; no switcher, the clock.
- A card served ahead of the calendar is priced as published, with a
  warning. The rate sensors show it at once; the running bill stays on the
  card in force until 1 January (`tests/test_early_card.py`).
- `valid_from` 1 January of that year, `valid_until` 31 December, and a
  page still on last year's period stands until 31 March
  (`carry_prior_year_card`).

## Failures

- `TransientFetchError`: network error, timeout, HTTP 5xx or 429 on the
  POST.
- `ExtractorError`: a 3xx or 4xx answer, a body that is not JSON or not a
  command list, no `insert` command with tariff data (what a phantom
  commune answers), a missing row, an ex-VAT and VAT figure that disagree,
  a comfort rate that is not twice the basis, legs that do not make the
  printed integrale waterprijs, a dropdown with no options.

## Checks

- Live check: the default fetch, no CI skip. The commune list floor
  (`MIN_COMMUNES`) is 225.
- Drift check (weekly), two rows: "Farys (Gent-centrum default)" against
  `farys_gent_2026.json`, and "Farys (Zaventem)" against
  `farys_zaventem_2026.json` through `fetch_for_commune(s, "25926")`,
  because the default card prints no tussenkomst and cannot show that row
  moving or disappearing.

## Quirks

- Farys serves 85 communes, but its dropdown has one entry per postcode
  and deelgemeente ("8020 - Hertsberge (Oostkamp)"), which is why the list
  runs to about 265.

## See also

- [The provider framework](../provider-framework.md): the extractor
  protocol, the shared fetch helpers and their error classes.
- [The coordinator](../coordinator.md): what a failed fetch leaves on the
  entry (the last good snapshot, the stale-snapshot Repair, the card
  archive).
- [The pricing model](../pricing-model.md): how the card becomes a bill.
