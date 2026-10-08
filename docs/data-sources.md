# Data sources

Where every input the integration prices on comes from: the utilities'
own publications, the project's card archive, Home Assistant's recorder
and Energy dashboard, the postcode tables, the phantom commune lists and
the regulated figures kept in `const.py`.

## The utilities' pages and PDFs

Each utility has one extractor under `providers/`, which fetches the
utility's own tariff page or PDF through Home Assistant's shared HTTP
session and parses it into a `WaterTariff`. Every request carries the same
User-Agent (`USER_AGENT` in `providers/_pdf.py`, the integration's name
and version). A network failure, an HTTP 5xx or a 429 is a
`TransientFetchError`; any other HTTP error, or a page that no longer
parses, is an `ExtractorError`. What each utility publishes, where, and
how its parser reads it is on that utility's page, `providers/<utility>.md`
(for example [providers/swde.md](providers/swde.md)); the shared fetch,
PDF and HTML machinery is in [provider-framework.md](provider-framework.md).

## The card archive

The `archive` branch of this repository holds every utility's parsed card,
one JSON file per utility, commune and month:

```
<utility>/<commune>/<YYYY-MM>.json
```

`<utility>` is the extractor id and `<commune>` the id the integration
passes to `fetch_for_commune` (Pidpa's slug, Farys's numeric id, De
Watergroep's GUID), or `default` for the no-commune fetch. A row is
`tariff_to_dict` of the card plus `_seen_on` (the capture day),
`_commune` and `_commune_label` on a commune row, and `_sources`, the pages
and PDFs the parse read, each pointing at its text under
`texts/<sha256>.txt` and, for a PDF, naming its digest. `pdfs.json` maps a
digest to `<release tag>/<sha256>.pdf`; `coverage.md` and
`coverage/<utility>.md` list the months held.

`scripts/archive_cards.py` writes it. It walks every registered utility,
fetching the default card and, unless `--defaults-only`, every commune the
utility lists, and rewrites a month's row only when the card changed
(`_same_card` ignores `_seen_on` and the text paths). `_seen_on` is
therefore the first day that month's row saw its card. A month whose card
matches the month before points at that month's texts rather than storing
them again, and months more than twelve before the running one
(`_KEEP_MONTHS`) are pruned, with the texts nothing refers to any more.
When the parser sources (`providers/*.py`, `const.py`) or the PDF reader
change, every stored row is replayed offline from its texts with the clock
pinned to its `_seen_on`, and rewritten where the parse differs
(`--reparse` forces the replay, `--rerender` a fresh render of every
PDF). The workflow `.github/workflows/archive_cards.yml` runs it daily
at 05:23 UTC, walking the communes on Sundays only; how it pushes and
reports is in [ci-and-testing.md](ci-and-testing.md). Water-link's CDN
refuses GitHub's runners, so its rows are stored by running the script
from a residential address.

With `--pdfs DIR` each PDF whose bytes the branch has not recorded is
written to `DIR/water-<YYYY-MM>/<sha256>.pdf` and the workflow uploads it
to the release of that name in
[be_price_cards](https://github.com/renaudallard/be_price_cards), the
repository the electricity and gas integrations share. The integration
never reads those releases; only the archiver's replay fetches a kept PDF
back.

The integration reads the branch through `CARD_ARCHIVE_URL`
(`raw.githubusercontent.com`, the `archive` branch) in one place:
`_archived_row` in `coordinator.py` asks for
`<utility>/<commune>/<YYYY-MM>.json`, the commune percent-encoded, with the
usual User-Agent and nothing else. A transient failure is raised; any
other HTTP error (a missing month is a 404), a body that is not JSON or a
JSON value that is not an object reads as no row. When it is asked, and
how far back it walks, is in [coordinator.md](coordinator.md). The
`card_archive` option (`CONF_CARD_ARCHIVE`, on by default) turns it off.

## The recorder and the Energy dashboard

With no `water_meter_sensor` set, the meter is the first source of type
`water` in the Energy dashboard's preferences:
`_discover_energy_water_meter` takes its `stat_energy_from` from the
manager's `energy_sources` (through
`homeassistant.components.energy.async_get_manager`, waiting at most
10 s) and counts the water sources for the several-meters card. That is
all the dashboard is read for.

The meter itself is read two ways. The live path takes the entity's
state (from its state events, and from `hass.states` on a tick) and
converts it to m³ with Home Assistant's `VolumeConverter`. The recorder
path reads the meter's long-term statistics, never its state history:
`get_metadata` first, to refuse a unit the recorder could not convert,
then `statistics_during_period` with the `day` period, the volume unit
class set to m³, and the `change`, `sum` and `state` columns. Days are
local days. Three windows are asked for:

- 1 January to today, for the year to date (`_recorder_ytd_m3`);
- the 396 closed days before today (`_METERED_DAYS`), for the rolling year
  and the year-end projection (`_read_metered_days`);
- 1 December of the year before last to 31 December of last year, for the
  projection Repair (`_recorder_full_year_m3`).

`change` is what is summed; `sum` only shows whether the first bucket's
change is the meter's whole running total, and `state` is the register a
day's change is checked against. How the buckets are filtered is in
[coordinator.md](coordinator.md). `statistics.py` also reads the recorder
for its own writes (the newest compiled statistics run, and whether a
price sensor has rows before a date); see [entities.md](entities.md).

## Postcodes

`providers/_postcodes.py` maps a postcode to the utilities serving it.
`resolve_candidates` accepts exactly four ASCII digits and returns an
empty tuple for an unknown postcode, one utility for most, and two for the
seven postcodes split between operators at street level
(`_SPLIT_POSTCODES`: 1770, 8020, 8400, 8490, 9080, 9550, 9570), where the
config flow asks the household to pick. `resolve` returns the first
candidate. The rules, in order:

1. `_SECONDARY_POSTCODES`: five secondary postcodes of a commune neither
   commune dropdown names under that postcode.
2. 1000 to 1299: VIVAQUA.
3. 2000 to 2070 and `_WATER_LINK_POSTCODES` (the city's other districts,
   Hove, Mortsel, Edegem): Water-link; the rest of 2000 to 2999: Pidpa.
4. `_FARYS_POSTCODES_VLAAMS_BRABANT` (17): Farys; the rest of 1500 to 1999
   and 3000 to 3999: De Watergroep.
5. 1300 to 1499 and 4000 to 7999: `_PER_POSTCODE` (540 postcodes), with
   no fallback, so a postcode served by a régie communale without an
   extractor is unresolved and goes to the manual picker.
6. 8300 and 8301: AGSO Knokke-Heist; `_AQUADUIN_POSTCODES` (6): Aquaduin;
   `_DWG_POSTCODES_FLANDERS` (122): De Watergroep; the rest of 8000 to
   9999: Farys.

The config flow calls it at setup and on reconfigure, and the coordinator
calls it on every successful tick to raise the `operator_moved` Repairs
card when a stored postcode has moved to another operator.

`scripts/refresh_postcodes.py` regenerates the three generated tables and
prints them to stdout for pasting:

- `_PER_POSTCODE`: for each Walloon polygon of Opendatasoft's
  `georef-belgium-postal-codes` set, the centroid is queried against the
  Géoportail Wallonie ZDE layer (`ZDE_QUERY_URL`, published under CC BY
  4.0), and its `DISTRIBUTEUR` mapped to a utility id
  (`_DISTRIBUTEUR_TO_UTILITY`). Distributors without an extractor are
  left out.
- `_DWG_POSTCODES_FLANDERS` and `_FARYS_POSTCODES_VLAAMS_BRABANT`: the
  postcodes in De Watergroep's tariff page dropdown and not in Farys's
  (8000 to 9999), and the reverse (1500 to 1999), with Farys's phantom
  labels filtered first and the Aquaduin and split postcodes left out.

It aborts rather than print a short result: a ZDE query failing three
times, fewer than 400 Walloon postcodes, or fewer than 100 postcodes in
either dropdown. Run it once a year, since distributors update the ZDE
and intercommunales merge. The other tables are kept by hand.

## Phantom commune blocklists

`_phantom_blocklists.py` lists commune options a utility's own list offers
but has no tariff behind. It imports nothing outside the standard library,
so `scripts/refresh_postcodes.py` can load it without the providers
package.

- `FARYS_UNSERVABLE_LABELS`: 23 Farys dropdown labels whose commune the
  tariff endpoint returns no data for (street-level splits where De
  Watergroep is the operator). `farys.py` drops them from
  `list_communes`, and `refresh_postcodes.py` drops them before computing
  the carve-outs.
- `FARYS_UNSERVABLE_IDS`: the same 23 by Farys's option id.
- `PIDPA_UNSERVABLE_SLUGS`: `antwerpen`, a sitemap page with no household
  tariff table; `pidpa.py` drops it from `list_communes`.

On every setup `_drop_phantom_commune_if_blocked` in `__init__.py` removes
a saved Farys id or Pidpa slug found in these lists from the entry's
options, so an addition reaches entries that picked the commune earlier.

## Regulated constants

`const.py` holds the figures the regulator sets rather than the utility:

- **Flemish vastrecht and korting.** `FLANDERS_VASTRECHT_DRINKWATER`,
  `FLANDERS_VASTRECHT_GEMEENTELIJK` and
  `FLANDERS_VASTRECHT_BOVENGEMEENTELIJK` (50, 30, 20 EUR a year, total
  `FLANDERS_VASTRECHT_TOTAL`), and the per-resident korting on each
  (`FLANDERS_KORTING_*_PER_PERSON`: 10, 6, 4, total
  `FLANDERS_KORTING_TOTAL_PER_PERSON`). `build_flanders_tariff` in
  `providers/_flanders.py` puts the two totals on every Flemish card as
  `yearly_fixed_fee` and `yearly_fixed_fee_per_resident_discount`.
- **Walloon CVA and FSE.** `WALLONIA_CVA_EUR_PER_M3` (2.748) and
  `WALLONIA_FSE_EUR_PER_M3` (0.0339), the figures in force in
  `WALLONIA_SPGE_YEAR` (2026). Every Walloon extractor gets them through
  `spge_components` in `providers/_walloon_simple.py`, directly or through
  `check_spge_constants` for pages printing them in prose. A card of that
  year or earlier is priced on the constants, and a figure its page prints
  that differs from them (by more than 0.005 for the CVA, 0.001 for the
  FSE) fails the fetch, as does a CVA the page stopped printing unless the
  extractor passes `cva_required=False`. A card of a later year is priced
  on its page's own figures and fails if either is missing.
  `scripts/live_check.py` fails while any card is past
  `WALLONIA_SPGE_YEAR`, which is the prompt to move the constants and the
  year together, and when the cards of one year disagree on either figure
  (`_check_spge`).
- **VAT.** `DEFAULT_VAT_RATE` (6 %) is the `vat_rate` the Flemish, Walloon
  and VIVAQUA builders set; Farys also checks its printed VAT-inclusive
  rates against it.

How they enter the bill is in [pricing-model.md](pricing-model.md).
