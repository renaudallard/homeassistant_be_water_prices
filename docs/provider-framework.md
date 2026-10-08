# The provider framework

How a utility module plugs in, and the shared pieces it builds on. Read
[architecture.md](architecture.md) first. The reference implementations
are `providers/swde.py` for a single HTML page and `providers/aquaduin.py`
for a PDF card, with `tests/test_swde.py` and `tests/test_aquaduin.py`.

## The protocol

A utility module exposes `EXTRACTOR: WaterExtractor` (`providers/base.py`,
which also declares the shape as `WaterExtractorProtocol`):

| Field | What it is |
| --- | --- |
| `id`, `label` | The registry id (also the `utility` key stored in the entry) and the name the setup flow shows. |
| `region` | `flanders`, `wallonia` or `brussels` (`const.REGIONS`). It decides which Flemish options the flow asks for and whether the comfort rate sensor is created. |
| `fetch(session)` | The utility's current card as a `WaterTariff`, for its default commune where it has communes. Raises `ExtractorError` on anything it cannot read; never defaults a missing figure to zero. |
| `fetch_for_commune(session, commune)` | Optional. The card of one commune, by the opaque id a `CommuneOption` carries. |
| `list_communes(session)` | Optional. The communes the user can pick, as a tuple of `CommuneOption(id, label)`, the label typically `"<postcode> - <commune>"`. |
| `commune_for_postcode(postcode)` | Optional. The commune id a postcode is billed under where that is not the default commune, or None. The config flow pre-selects it (Water-link's 2070, 2540, 2640 and 2650, Farys's Drogenbos and Zaventem postcodes, Pidpa's Nijlen, Wommelgem and Kasterlee). |
| `supports_communes` | Property: true when both `fetch_for_commune` and `list_communes` are set. The flow only offers a commune selector, and the live check only checks a commune list, for such a utility. |

The utilities with communes are De Watergroep, Farys, Pidpa and Water-link:
their saneringsbijdragen differ from one commune to the next. Every other
utility sets `fetch` alone. The coordinator calls `fetch_for_commune` when
the entry's options carry a commune and the utility has one, and `fetch`
otherwise.

Each module also has a pure parser that the tests call on a fixture's
text, `year` standing in for the clock: `parse_tariff(text, year=None)` in
most, with a commune argument where the card is per commune, and
`parse_commune_tariff` for De Watergroep's and Pidpa's commune pages. The
fetch downloads, then hands the text to it.

Errors:

- `ExtractorError`: the page or card cannot be fetched or parsed. The
  coordinator serves its held card (or the card archive's) and raises the
  stale-snapshot Repair once that card goes stale; see
  [coordinator.md](coordinator.md).
- `TransientFetchError`, a subclass: a network error, a timeout, HTTP 5xx
  or 429. The live check reports these as `TRANSIENT` rather than opening
  an issue. A fetch that falls back to last year's card on an
  `ExtractorError` must re-raise a `TransientFetchError` instead
  (`aquaduin.fetch` shows the pattern), or a blip would quietly swap this
  year's card for last year's.

The coordinator catches any exception from a fetch, not only
`ExtractorError`, so a parser bug fails like a refused card and the held
card keeps pricing. Cancellation and Home Assistant's own
`ConfigEntryAuthFailed` / `ConfigEntryError` still propagate.

## The tariff

`WaterTariff` (frozen, keyword only) is one year's card for one utility.
Every EUR figure is ex-VAT; `vat_rate` carries the rate and the cost engine
applies it once at the end. A utility that publishes VAT-inclusive figures
divides by `1 + vat_rate` when parsing (VIVAQUA does).

| Field | Meaning |
| --- | --- |
| `utility`, `region` | The extractor id and its region. The pricing branch is chosen on `region`. |
| `valid_from`, `valid_until` | The card's window. 1 January to 31 December by default; a rate that took effect later in the year moves `valid_from` (INASEP, AIEM). |
| `publication_label`, `source_url` | What the card calls itself and where it was read. A per-commune label ends in `(<commune id>)`; see the commune labels below. |
| `yearly_fixed_fee` | EUR/year: the Flemish vastrecht, the Walloon redevance or VIVAQUA's fixed charge. |
| `yearly_fixed_fee_per_resident_discount` | EUR/year per registered resident, the Flemish korting. 0 elsewhere. |
| `basis_eur_per_m3`, `comfort_eur_per_m3` | Flanders: the drinkwater basistarief and comforttarief. None elsewhere. |
| `linear_eur_per_m3` | Brussels: VIVAQUA's supply rate. None elsewhere. |
| `sanering_gemeentelijk_eur_per_m3`, `sanering_bovengemeentelijk_eur_per_m3` | Flanders: the municipal and supra-municipal saneringsbijdrage. VIVAQUA's sanitation rate is carried in the first. 0 where not used. |
| `cvd_eur_per_m3`, `cva_eur_per_m3`, `fse_eur_per_m3` | Wallonia: the distributor's CVD and the flat CVA and FSE. 0 elsewhere. |
| `vat_rate` | 0.06 by default (`const.DEFAULT_VAT_RATE`). |

How these become a bill is in [pricing-model.md](pricing-model.md).

Helpers in `base.py`:

- `belgian_today()`: today in `Europe/Brussels`, whatever zone the process
  clock runs in. Every extractor dates its target year with it; a container
  started without `TZ` runs on UTC and would otherwise ask for last year's
  card for the first hour of 1 January.
- `carry_prior_year_card(tariff, target_year)`: a card of an earlier year
  stands until 31 March of `target_year` (its `valid_until` moved there),
  so a utility that publishes late in January does not raise the
  stale-snapshot Repair on day one. A card of `target_year` or later is
  returned as it is.
- `tariff_to_dict(tariff)` / `tariff_from_dict(data)`: the card as JSON
  values with ISO dates, and back. One codec serves the coordinator's
  stored last good card, the card the running bill is held on, and the
  card archive (`scripts/archive_cards.py` writes rows with it and the
  coordinator reads them back). `tariff_from_dict` ignores keys the
  dataclass has no field for, so a row can carry its own metadata, such
  as the archive's `_seen_on`.
- `relabel_with_human_commune(tariff, commune_id=..., commune_label=...)`:
  swaps the `(<commune id>)` in `publication_label` for the label the user
  picked (`CONF_COMMUNE_LABEL`), since Farys's ids are numbers and De
  Watergroep's are GUIDs. The coordinator applies it to every fetched
  per-commune card and to a per-commune card read from the archive.

## The registry

`providers/__init__.py` lists the modules in `_MODULE_NAMES`. Its order is
the order of the manual picker in the setup flow: VIVAQUA, then the six
Flemish operators, then the nine Walloon ones. The registry is built on
the first call to `get(utility_id)` or `all_extractors()` by importing each
module and keying its `EXTRACTOR` on its `id`; importing the package alone
pulls in no extractor, so a stdlib-only script such as
`scripts/refresh_postcodes.py` can import `_postcodes` without aiohttp,
BeautifulSoup or pdfplumber. `get` raises `ExtractorError` for an unknown
id. Importing the modules is blocking work (it also reads `manifest.json`
for the User-Agent), so async callers in Home Assistant run
`async_load(hass)` first, which builds the registry in the executor. The
old `EXTRACTORS` dict is still served lazily for callers that import it.

`scripts/live_check.py` and `scripts/archive_cards.py` walk
`all_extractors()`, so a registered utility is live-checked and archived
without being named in either.

## Shared readers

### `_pdf.py`: HTTP and PDF

The module is a vendored subset of the electricity integration's reader.

- `fetch_text(session, url, *, timeout=20, verify_ssl=True)` returns a body
  as text, sending `USER_AGENT` (`Home Assistant be_water_prices/<version>`).
  `verify_ssl=False` is for a server with a broken chain and nothing else;
  inBW is the one user, and only after a TLS failure.
- Status mapping (`_http_error`): 5xx and 429 are `TransientFetchError`,
  any other non-2xx (a 3xx that reaches the caller included) is
  `ExtractorError`. A network error or timeout is `TransientFetchError`.
- Size cap: `MAX_RESPONSE_BYTES` (16 MiB). A declared length over it is
  refused, and the body is streamed and refused once it passes the cap,
  since a chunked answer declares none. Text is decoded with the declared
  charset, falling back to UTF-8 with replacement for an unknown or
  unusable one.
- Redirect guard: a redirected answer is refused when it left https or
  left the requested site (the last two labels of the host), and the
  target is logged at debug level only, since it can be an address only
  the Home Assistant host can see. The two AJAX fetchers that send a
  commune (De Watergroep's cookie, Farys's form field) do their own request
  with `allow_redirects=False`, so the commune never travels to wherever a
  redirect points, and a redirect fails them (`tests/test_ajax_redirects.py`).
- `fetch_pdf_text_layout(session, url)` downloads a card (30 s), checks
  the `%PDF` signature after stripping a BOM or leading blank lines, and
  renders it with `extract_pdf_text_layout`. An answer that is no PDF is
  refused naming its content type, never quoting its bytes.
- Inflate guard: `guard_pdf_streams(payload)` inflates every stream with a
  bounded decompressor and refuses a card whose streams would inflate past
  `MAX_INFLATED_BYTES` (64 MiB) in total, an encrypted card, a stream that
  names more than one filter or a filter chain, a filter other than Flate
  or JPEG (`DCTDecode`), and a filter given by reference. It is a text
  pass over the bytes and only refuses the shapes it can see.
- The reader runs in a child process (`extract_pdf_text_layout`): the
  current interpreter started with `-P`, the PDF on stdin and its text on
  stdout, pdfplumber's `extract_text` on each page after `dedupe_chars`.
  It is held to `PDF_READER_MEMORY_BYTES` (256 MiB, as `RLIMIT_DATA`)
  and `PDF_READER_TIMEOUT_S` (`FETCH_BUDGET_S - 60`, so 120 s), with
  `PYTHONMALLOC=malloc` because the mimalloc arena Home Assistant OS
  exports would count against the limit before the reader starts. The
  child's stderr goes to the null device except for one line naming the
  exception, so no page text and no pdfminer warnings reach Home
  Assistant. A timeout, a signal, a non-zero exit or an empty text layer
  is an `ExtractorError`.
- `memoise_text_fetches(store)`: inside the block, a page read twice is
  served from `store`, keyed by URL, and a rendered PDF by
  `"layout\0<url>"`. A provider that asks one endpoint for several answers
  puts what it asked for in the key through `memoised_text` (De Watergroep
  adds `#dwg_l=<commune>`, Farys `#municipality=<id>`). The card archiver
  and the live check install it; Home Assistant never does.
- `render_through(hook)`: inside the block, every PDF render is handed to
  `hook(variant, url, payload, render)`, which the archiver and both checks
  use to skip rendering a card whose bytes they already hold.
  `render_pdf(variant, url, payload, render)` is the entry point; a
  provider that obtains PDF bytes some other way must call it, or the hook
  never sees the card.
- Parsing helpers: `to_float` (comma or dot decimals, the space family as
  thousands separators, `ExtractorError` on a non-finite number),
  `fold_accents`, `stated_card_year` (the year a Dutch card says it
  applies from, "Geldig vanaf 1 januari" or "tarieven per 1 januari", used
  by Water-link and Aquaduin to refuse a link pointing at another year's
  card), and `error_text`, an exception message that is never empty.

### `_html.py`: HTML

- `fetch_html` is `fetch_text` under another name.
- `fetch_and_parse(session, url, parser, *args, verify_ssl=True,
  **kwargs)` fetches a page and runs `parser(html, *args, **kwargs)` in a
  worker thread, so BeautifulSoup does not block the event loop. Every
  HTML extractor goes through it.
- `extract_amounts(text)` returns every euro amount in document order,
  "€ 12,34" and "12,34 €" alike, a space-grouped thousand as one amount, a
  minus against the sign or the number as a negative, and a figure that
  both patterns find once. Dot-grouped thousands are left alone because
  SWDE prints "€ 2.748" with a dot decimal.

### `_flanders.py`: the Flemish builder

`build_flanders_tariff(utility_id=, year=, publication_label=, source_url=,
basis=, comfort=, sanering_gemeentelijk=0.0, sanering_bovengemeentelijk=0.0)`
builds every Flemish card. It sets the decreed vastrecht and korting
(`FLANDERS_VASTRECHT_TOTAL`, 100 EUR, and
`FLANDERS_KORTING_TOTAL_PER_PERSON`, 20 EUR, from `const.py`), dates the
card 1 January to 31 December of `year`, and refuses a basis or comfort
rate of zero or less or outside 0.5 to 20 EUR/m³, and a negative sanering.
A sanering of zero is allowed: Aquaduin folds its sanering into the basis,
and a commune can levy none. The check that the comforttarief is twice the
basistarief is each parser's own, since the builder cannot tell a right
pair from a pair that is a decimal out together; De Watergroep derives
its comfort rate as twice the basis.

### `_walloon_simple.py`: the Walloon helpers

Shared by every Walloon extractor. The small intercommunales (IEG, AIEM,
CIESAC, and AIEC's fallback) are built whole with
`build_extractor(utility_id=, label=, source_url=,
publication_label_prefix=)`, which wires `fetch_tariff` and
`parse_tariff`. The others parse their own page and use the pieces:

- `parse_cvd(html)`: the CVD off a prose page. It tries "actuelle du CVD"
  and then "distribution (CVD)", taking the largest value inside the
  plausibility window (1.5 to 6.0 EUR/m³), then the largest "CVD ... N €"
  in the window that is not the CVA constant. Largest, because a CVD only
  indexes up and pages quote an older one beside the current one. Nothing
  plausible is an `ExtractorError`.
- `detect_published_year(text, today=None)`: the tariff year a page
  states, "Tarifs YYYY" first, then "1er janvier YYYY", then "en YYYY",
  only years within one of today counting, and the current year winning
  over a neighbour the page also names. None when the page states none.
- `spge_components(cva=, fse=, year=, label=, logger=, cva_required=True)`:
  the CVA and FSE a card is priced on. A card of `WALLONIA_SPGE_YEAR`
  (`const.py`, 2026) or earlier is priced on `WALLONIA_CVA_EUR_PER_M3` and
  `WALLONIA_FSE_EUR_PER_M3`, and what its page prints is held to them. A
  later card is priced on its page's own figures and fails when the page
  prints either one not at all.
- `warn_constant_drift(...)`: raises `ExtractorError` (and logs a warning)
  when a printed figure is more than the threshold from the constant,
  0.005 EUR/m³ for the CVA and 0.001 for the FSE. A figure not printed is
  not checked. Raising rather than logging puts a moved SPGE figure on the
  watched paths: the stale-snapshot Repair and the live check.
- `hold_to_constant(...)`: the same, with a figure not printed counting as
  a failure. `spge_components` uses it for the CVA when `cva_required`;
  IDEN and AIEC pass `cva_required=False`, and the FSE is never required.
- `check_spge_constants(text, year=, utility_id=, logger=)`: reads the CVA
  and FSE off a prose page (`parse_cva`, `parse_fse`) and hands them to
  `spge_components`.
- `build_tariff(utility_id=, cvd=, source_url=, publication_label=, year=,
  cva=, fse=, valid_from=None)`: refuses a CVD outside the window, sets the
  redevance to `20 x CVD + 30 x CVA`, dates the card from `valid_from` or
  1 January to 31 December, and passes it through `carry_prior_year_card`.
- `effective_date(text, year)`: the "à partir du DD/MM/YYYY" AIEM prints
  beside its CVD, when that day is in `year`.

### `_postcodes.py`: the postcode resolver

Pure, with no imports beyond `re`. `resolve_candidates(postcode)` returns
every utility that may serve a four-digit postcode: one for most, two for
the postcodes split between De Watergroep and Farys at street level
(`_SPLIT_POSTCODES`), none for one it cannot place. `resolve` returns the
first candidate. Brussels and Flanders are range rules with carve-outs
(Water-link's districts and ring communes, AGSO Knokke-Heist, Aquaduin,
De Watergroep's postcodes inside the Farys block and Farys's inside De
Watergroep's, and `_SECONDARY_POSTCODES`). Wallonia is the `_PER_POSTCODE`
table, generated by `scripts/refresh_postcodes.py` from the ZDE map; a
postcode a régie communale serves is absent, so the user picks by hand
instead of being sent to SWDE. Details in [data-sources.md](data-sources.md)
and [config-flow.md](config-flow.md).

### Commune labels and redaction

A per-commune card names its commune in `publication_label`, and Pidpa's
`source_url` ends in the commune's slug. What leaves the integration is
scrubbed in `_redact.py`:

- `sensitive_tokens(entry)`: the commune id and label from the entry's data
  and options, longest first. The postcode is left out on purpose: four
  digits would match years and dates.
- `scrub_tokens(value, tokens, placeholder)`: replaces them throughout a
  string, dict or list. The coordinator scrubs a fetch error with it before
  logging it or storing it as `last_error`, and again for the Repair card;
  the sensors scrub `last_error`, and diagnostics scrubs the entry and the
  snapshot.
- `source_url_without_commune(source_url, commune)`: redacts the commune
  from the URL, for the `source_url` attribute and the device's
  configuration URL.

The sensors also drop a trailing parenthesis from `publication_label` when
it names the commune, so a provider should put the commune there, as
`(<commune id>)`, and nowhere else in the label.

## Conventions

- Read what is really there. A figure the page does not print is an
  `ExtractorError`, never a zero and never a guess; a row a commune page
  always carries and this one lacks is a parser problem.
- Figures as the page prints them, converted to EUR ex-VAT.
- Date the card from what the page states where it states a year
  (`detect_published_year`, `stated_card_year`), and say so in the label
  where it does not (SWDE's "(page states no year)").
- Serve last year's card through `carry_prior_year_card` when this year's
  is not up yet, and only on a hard failure; re-raise a transient one.
- Anchor on labels rather than positions, bound every regex gap, and check
  what can be checked on the card itself: the VMM comfort rule, VIVAQUA's
  supply plus sanitation against its total, the CVA and FSE against the
  constants.
- A URL read off a page decides what is fetched next, so pin it to the
  utility's site over https before following it (Aquaduin's and
  Water-link's PDF links).

## Adding a utility

The README's "Adding another utility" paragraph gives the outline: a new
module under `providers/`, registered in `providers/__init__.py`, the
postcode resolver extended, and a fixture-based unit test. In full:

1. Write `providers/<id>.py` with a pure parser and an `EXTRACTOR`.
   Build the card with `build_flanders_tariff` or the `_walloon_simple`
   helpers for those regions. SWDE is the reference for a single HTML page
   (`fetch_and_parse`, a heading walk, `spge_components`, `build_tariff`;
   see [providers/swde.md](providers/swde.md)). Aquaduin is the reference
   for a PDF (the link discovered on a page and pinned to the site,
   `fetch_pdf_text_layout`, `stated_card_year`, the prior-year fallback;
   see [providers/aquaduin.md](providers/aquaduin.md)).
2. Append the module name to `_MODULE_NAMES` in its region's group, since
   that order is the picker's, and update the counts in the comment above
   it. Add the id, label and region to `_EXPECTED` in
   `tests/test_extractor_registry.py`, which fails on a registry that
   differs from it.
3. Map its postcodes. A Flemish or Brussels utility gets a rule or a
   carve-out in `_postcodes.py`. A Walloon one gets its ZDE distributor
   name in `_DISTRIBUTEUR_TO_UTILITY` in `scripts/refresh_postcodes.py`,
   and the script is re-run to regenerate `_PER_POSTCODE`.
4. Commit a real publication under `tests/fixtures/`, named after the
   utility and the year (`swde_2026.html`, `aquaduin_2026.pdf`), with a
   qualifier before the year where one utility needs several
   (`farys_zaventem_2026.json`). `tests/test_fixture_hygiene.py` scans
   every fixture for personal data and credentials. Write
   `tests/test_<id>.py` against it with `fixture_html` or `fixture_bytes`
   from `tests/__init__.py`, pinning every figure read and the refusals.
5. Add a `FixtureCheck` to `CHECKS` in `scripts/fixture_drift.py`, pairing
   the fixture's parse with the live fetch, so the weekly drift check
   compares the two.
6. For a utility with communes, set `fetch_for_commune`, `list_communes`
   and, where postcodes are billed off the default commune,
   `commune_for_postcode`, and give it a floor in `MIN_COMMUNES` in
   `scripts/live_check.py`; `tests/test_live_check.py` requires a floor
   for every lister and for nothing else. A commune list that carries
   communes the operator does not serve gets a blocklist in
   `_phantom_blocklists.py`, as Farys's and Pidpa's do.
7. If GitHub's runners cannot reach the utility, add it to `CI_BLOCKED` in
   both scripts: keyed by extractor id in `live_check.py` and by check
   label in `fixture_drift.py`. Tests require each key to name a real
   extractor or check. The skip only applies where `GITHUB_ACTIONS` is
   set.
8. Add the utility to the README's "Supported utilities" table, to the
   postcode table where it changes, and to the utility dropdown of
   `.github/ISSUE_TEMPLATE/bug_report.yml`, and write its page under
   `docs/providers/`.

The tests and both scripts are described in
[ci-and-testing.md](ci-and-testing.md).
