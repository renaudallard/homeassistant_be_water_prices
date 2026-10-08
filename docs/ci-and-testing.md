# CI and testing

The checks a change has to pass, the scheduled workflows that watch the
utilities, and the card archive. The commands are in the project
[README](../README.md#development).

## Running the checks

```bash
pip install -r requirements-dev.txt
ruff check .
ruff format --check .
mypy --strict custom_components/be_water_prices
mypy --strict scripts
pytest tests/
python scripts/live_check.py    # hits real utility endpoints
```

`requirements-dev.txt` pins every tool and the Home Assistant release
`hacs.json` declares as the floor (`homeassistant==2026.2.3` with
`pytest-homeassistant-custom-component==0.13.316`); the workflows that need
only a few of these install them by name with `pip -c requirements-dev.txt`,
so each version is written once.

`scripts/gate.sh` runs the same checks against a snapshot of HEAD: it adds
a throwaway git worktree under `tmp/gate/`, so editing the real tree while
it runs cannot reach it and what is checked is exactly what a push would
publish. The quick checks (ruff, ruff format, both mypy runs, and
actionlint over `.github/workflows/*.yml` when a binary sits at
`tmp/actionlint`) start beside pytest, one core each. pytest itself runs
on one core, as `test.yml` runs it: spread over a Raspberry Pi's cores,
the Pidpa card renders ran past the 30 second test timeout. The two mypy
runs keep their caches under `tmp/mypy_strict` and `tmp/mypy_scripts`.
`GATE_PYTHON` names the interpreter, else `.venv/bin/python`; arguments
are passed to pytest (`scripts/gate.sh tests/test_pidpa.py`).

## The test suite

`pyproject.toml` runs pytest on `tests/` only (a repo copy kept under
`tmp/` carries its own conftest), with `asyncio_mode = "auto"` and a 30
second per-test timeout, so a parser loop that stops advancing fails its
test instead of hanging the job. mypy is strict on the integration and on
`scripts/`; `tests.*` is left out on purpose, since the suite drives error
paths with partial stubs and wrong inputs.

`tests/conftest.py` sets up three autouse fixtures:

- `_card_archive_holds_nothing` replaces `coordinator._archived_row`, so
  a failing refresh never asks GitHub for the card archive; only the tests
  of that path answer it.
- `auto_enable_custom_integrations` loads the custom integration for every
  test. It pulls `hass` in for the pure parser tests too, which is what
  sets up their event loop.
- `_force_brussels_timezone` sets Home Assistant's zone to
  Europe/Brussels for every test. The harness starts in US/Pacific, which
  hides year and month boundary bugs and flips date comparisons in the
  hours where the UTC date is a day ahead.

`tests/recorder/` holds the tests that drive Home Assistant's own
statistics compiler on a real recorder database. `recorder_mock` has to be
set up before `hass`, so `tests/recorder/conftest.py` overrides the two
fixtures that request `hass` with no-ops, and each test there sets the
zone itself.

The parsers are tested against real 2026 publications under
`tests/fixtures/` (HTML pages, the Farys AJAX answers as JSON, and the
Aquaduin, Pidpa and Water-link PDFs), named after the utility, the commune
where the page is a commune's, and the year. `tests/__init__.py` provides
`fixture_html` and `fixture_bytes`. Refresh a fixture with the utility's
current page or PDF to run the parser against new data; the weekly drift
check below says when one is due.

## The workflows

Each workflow grants its job token only the permissions it uses.

### test.yml

On every push to main, on pull requests, by hand, and called by
`autorelease.yml`. Two jobs:

- `test` runs ruff, both mypy runs and pytest on Python 3.13 and 3.14.
  3.13 is the oldest Python the floor release of Home Assistant accepts;
  3.14 is what a current Debian or Raspberry Pi OS gives a local checkout.
- `test-current-ha` runs the type check and the suite against a recent
  Home Assistant, pinned as a matching pair (`HA_VERSION` 2026.9.4 with
  `HARNESS_VERSION` 0.13.367) since the harness pins one exact release.

Each job stops after 15 minutes (a run takes about three). A newer commit
cancels the run it replaces; the group carries the calling workflow's
name, so a plain push never cancels the suite a release is waiting on.

### validate.yml

HACS validation and hassfest on every push to main, on pull requests, by
hand and daily at 06:37 UTC, off the hour: the start of the hour is where
GitHub sheds scheduled runs first, which took the electricity
integration's 06:00 runs on 2026-08-27 with no run created at all.

### autorelease.yml

A version bump in `manifest.json` releases itself (it can also be started
by hand). The release job waits on three gates: the test suite through
`test.yml`, HACS and hassfest. It then tags the version and publishes the
release with `be_water_prices.zip`, the file the README's manual install
downloads.

A release counts only once it is published with the zip: `gh release
create` makes a draft first and publishes it after the upload, so a draft
a dead run left would otherwise pass for the release. A stray draft is
dropped before each of five attempts (10, 30, 60 and 120 seconds apart),
keeping the tag, and an attempt that died after publishing counts as
success. The notes start at the highest older tag by version sort, since
GitHub guessed the previous release wrong for v0.7.3 and v0.7.4. Runs are
serialised per ref without cancelling one in progress, so two bumps in a
row are released one after the other.

### live_check.yml

Daily at 06:11 UTC, after the card archive whose texts it reads, and by
hand. It runs `scripts/live_check.py`, which fetches every registered
extractor's real publication and checks the card it parses (region,
a fee between 5 and 500 EUR a year, at least one volumetric rate between
0.5 and 20 EUR/m³, a `valid_from` year next to today's). The commune
lists of De Watergroep, Farys, Pidpa and Water-link are a row each: an
error, or fewer communes than the floor in `MIN_COMMUNES`, fails it,
since the config flow drops the commune selector when that list fails
and a new entry is then billed on the operator's default commune.

A last row sets the Walloon cards side by side (`_check_spge`): it fails
while any card is past `WALLONIA_SPGE_YEAR`, the year of the CVA and FSE
constants in `const.py`, so the release that moves them is prompted, and
when the cards of one year disagree on the CVA (by more than 0.005) or the
FSE (0.001), which is what catches a page that misread or lags on its
figure. See [pricing-model.md](pricing-model.md) for why a card past that
year is priced on its page's own figures.

The exit code is a bitmask: 1 for a real failure (a parse or shape error,
an HTTP 4xx, a redirect off the site, a fetch past `FETCH_BUDGET_S`), 2
for a transient one (a timeout, a reset connection, HTTP 5xx or 429). A
fetch that takes longer than the 180 second budget, parse included, is a
real failure rather than a transient one, since that is exactly the
failure users see: the integration gives up on the same budget and keeps
its held card. The workflow tries up to five times (10, 30, 60 and 120
seconds apart), latches the worst result across attempts, and is cleared
only by one fully green attempt. Only a real failure opens or updates the
issue labelled `live-check` (`[live-check] water extractor broken`); a
report that never got written (the script crashed) becomes an issue that
says so, with the traceback left in the run log.

The checkout keeps no credentials (`persist-credentials: false`): the job
can write issues and runs the extractors' parsers, and neither needs the
token on disk. The concurrency group lets a newer run replace one still
going, so the same issue is not filed twice.

### fixture_drift.yml

Sundays at 06:30 UTC, and by hand. `scripts/fixture_drift.py` parses each
utility's live publication and diffs the result against the parser's
output on the committed fixture. A tariff field that drifted by more than
the threshold (rates above 0.001 EUR/m³, fees above 0.01 EUR a year) opens
or updates the issue labelled `fixture-drift` (`[fixture-drift] water
fixtures need refresh`), the drifted fields being the fingerprint. This
catches silent rate drift that the live check misses. It only sees the
fields read off a page, so it cannot notice a move in a decreed constant;
the live check carries that. A run where a utility was unreachable on a
blip exits 2: it neither files an issue nor comments "drift cleared" on
one it did not recheck, and says so in the step summary. A clean run
comments "drift cleared" on an open issue but never closes it. A live
fetch past the 180 second budget is an error, as in the live check.

### archive_cards.yml

Daily at 05:23 UTC; see [The card archive](#the-card-archive) below.

### Issues filed by CI

Every workflow files through `scripts/file_ci_issue.sh`: one open issue
per problem, found by its label (never by a title substring), with a
fingerprint of what failed in a marker comment. When the open issue's
latest post carries the same fingerprint and is younger than the cooldown
(seven days), the run posts nothing, so a utility that stays broken gets
one comment a week and a failure that changes shape is posted at once.

### Runner limits

Water-link's CDN answers HTTP 403 to GitHub's runner addresses. The live
check, the drift check and the archive skip it on a runner (`CI_BLOCKED`
in `scripts/live_check.py`, keyed on `GITHUB_ACTIONS`), so a local run
from a residential address does check it.

Both checks read the archive branch's texts when given a checkout
(`--texts tmp/archive`, which the workflows pass after fetching the
branch): a PDF whose bytes the archive already holds is downloaded and
parsed as before, but its text is taken from the branch instead of being
rendered again, and the report ends with how many were served that way.

The readers in `providers/_pdf.py` carry two seams for scripts that walk
every utility in one go, both off in Home Assistant itself: a text memo
(`memoise_text_fetches`) that serves a page or a rendered PDF read twice
in one walk from memory, and a render hook (`render_through`) that is
handed the bytes of every downloaded PDF before it is rendered, so a
caller can skip the render of a card it has already seen or keep the
bytes. A provider that obtains a PDF some other way than through
`fetch_pdf_text_layout` renders it with `render_pdf`, so the hook still
sees it.

## The card archive

`scripts/archive_cards.py` walks every registered utility, fetches the
tariff the integration would price on right now, once for the utility's
default and once for each commune a per-commune utility lists, and
writes what it parsed into a checkout of the `archive` branch:

```bash
python scripts/archive_cards.py --out tmp/archive --pdfs tmp/pdfs [--only pidpa]
```

What the branch holds, how a month's row is written and replayed through
the current parser, and how the integration reads it back are in
[data-sources.md](data-sources.md#the-card-archive). Each PDF names the
pdfplumber release and render code that read it, and its text is served
again only to the same, so after a reader upgrade or a render fix every
kept PDF is rendered afresh, in the walk, the replay, the live check and
the drift check (`--rerender` forces it, `--reparse` forces the replay).
`--index-only` rewrites `coverage.md` and the per-utility sheets under
`coverage/` without fetching anything.

`archive_cards.yml` runs the archiver every morning at 05:23 UTC against
the `archive` branch. It walks the communes of the per-commune utilities
on Sundays and the sixteen default rows only on the other days (the
tariffs are annual, and De Watergroep alone lists about seven hundred
communes); a manual run walks them unless its `communes` input is
unticked, and `--defaults-only` is the local equivalent. It uploads the
PDFs of the day to the releases of the shared cards repository
[`be_price_cards`](https://github.com/renaudallard/be_price_cards)
(`water-<YYYY-MM>`, one per month the cards were seen in, deleted once it
is more than twelve months old and the manifest no longer points into it;
the electricity and gas integrations' releases live beside them as
`electricity-<YYYY-MM>` and `gas-<YYYY-MM>`). It then rewrites the index
and the per-utility sheets so each month links to a file that exists,
publishes them under `water/` in that repository (rebasing and trying
again when the electricity or gas archive pushed there first), and
commits the branch when anything changed. A manual run can ask for
`--reparse` or `--rerender`. Water-link is skipped on a runner; an archive
run from a residential address stores it.

The job's own token, which writes the branch, is not kept in the checkout
while the walk runs third-party code; only the push is given it, as an
authorization header on that call. The listings push gets the cards token
the same way, and the listings clone needs none, the cards repository
being public, so neither token lands in a checkout's config. The upload
needs a fine-grained token with contents read and write on the cards
repository in the `BE_WATER_CARDS` secret; without it the branch still
gets the parsed cards and their texts and the step says so. An upload or
a listings push that fails, an expired token among them, does not hold
the branch back either: the PDFs it missed are offered again by the next
run.

A run that stores nothing, a refused push or an expired token files an
issue labelled `archive-cards`, one per problem with a comment per
further failing run. The token's expiry is announced two weeks ahead the
same way, under a label of its own, `archive-cards-token`, so the warning
never lands in an open failure thread.

### Finding a stored card by hand

Everything on the
[`archive`](https://github.com/renaudallard/homeassistant_be_water_prices/tree/archive)
branch is addressed by the two ids the integration uses, which are the
directory names: the utility (`pidpa`, `farys`, `inbw`, ...) and the
commune, as the id the integration passes to the extractor (`geel` for
Pidpa, the numeric id for Farys, the GUID for De Watergroep, the name for
Water-link), or `default` for the no-commune fetch. Browse the branch to
see them; the commune's label is inside each file.

1. **The parsed card** is one JSON per month at
   `<utility>/<commune>/<YYYY-MM>.json`, for example
   [`pidpa/geel/2026-09.json`](https://github.com/renaudallard/homeassistant_be_water_prices/blob/archive/pidpa/geel/2026-09.json).
   It holds the tariff exactly as the integration parsed it, plus
   `_seen_on` (the day it was captured), `_commune` and `_commune_label`
   for a commune row, and `_sources`: every page or document the parse
   read, each with its text file under `texts/` and, for a PDF, the digest
   of the file.
2. **The page or the PDF the parser read** is easiest through
   [`coverage.md`](https://github.com/renaudallard/homeassistant_be_water_prices/blob/archive/coverage.md)
   at the branch root, which names one sheet per utility under
   `coverage/`: a row per commune, a column per month. Each month cell
   carries two links: `page` opens the text of the page as it was read, on
   the branch (`pdf` downloads the card from the cards repository's
   releases instead, for a card parsed from a PDF), and `json` opens the
   parsed row above. The same sheets are published under
   [`water/`](https://github.com/renaudallard/be_price_cards/tree/main/water)
   in the cards repository itself, and each release's notes point there,
   so a file seen on the releases page can be named too: search that
   repository for the file's name. Behind them is `pdfs.json`, which maps a
   digest to `water-<YYYY-MM>/<digest>.pdf` in those releases; the digest
   in a JSON's `_sources` is the same key.
3. **The text the parser read** is under `texts/`, named by the digest of
   the text itself and listed in the JSON's `_sources`, for checking a
   figure against the page without fetching it again. A month whose card
   is the same as the previous month's names that month's text, so the
   same page is not stored twelve times a year.

How the integration itself reads the branch is in
[data-sources.md](data-sources.md).
