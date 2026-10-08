# The coordinator

`WaterCoordinator` (`coordinator.py`) is a `DataUpdateCoordinator` that
ticks once a day (`UPDATE_INTERVAL_HOURS`, 24). Water tariffs are annual,
so the tick is mostly a check that the utility's page still parses; the
meter-driven figures move between ticks on the meter's own state events.
This page follows one tick, then what the coordinator keeps between them.
See [glossary.md](glossary.md) for the terms and
[pricing-model.md](pricing-model.md) for the bill itself.

## Setup

`async_setup_entry` in `__init__.py` drops a saved commune the phantom
blocklists name, warms the extractor registry off the event loop
(`providers.async_load`), gives a commune-less entry the commune its
postcode is billed at (`commune_for_postcode`, see
[config-flow.md](config-flow.md)), then builds the coordinator with
`defer_meter_history=True` and restores its three Stores before the first
refresh: `async_load_ytd_state`, `async_load_metered_days` and
`async_load_last_good`. `async_unsub_live_tracking` is registered on unload
before that refresh, since the refresh already subscribes to the meter. A
first refresh that fails puts back the options the setup sweeps changed
and lets `ConfigEntryNotReady` through.

Once it succeeds the coordinator goes on `entry.runtime_data`, the
platforms are forwarded, `async_setup_live_tracking` subscribes to the
meter, `async_read_meter_history` starts as an entry background task when
the refresh left it pending, an update listener reloads the entry on any
change to it, and `statistics.async_maybe_backfill_once` writes the price
history (wrapped so a failure only logs).

## One tick

`_async_update_data` fetches the card for the commune the entry is billed
at (`_fetched_commune`: the `commune` option when the extractor has a
`fetch_for_commune`, else the utility's default card through `fetch`), and
puts the commune's human label back into the publication label
(`relabel_with_human_commune`). The fetch runs under
`asyncio.timeout(FETCH_BUDGET_S)`, 180 s, parse included. Any exception is
a failed fetch, except `CancelledError`, `ConfigEntryAuthFailed` and
`ConfigEntryError`, which propagate. A failure is handed to
`_serve_cached` outside the `except` block, so nothing it raises carries
the raw fetch error, URL and commune included, as its context.

On success the tick:

1. computes the year to date (`_compute_ytd`, below);
2. builds `CoordinatorData`: the card, its fetch time, age 0,
   `snapshot_stale` from `_is_stale`, the projected annual cost on the
   typed consumption (`_project_cost`), the year to date, its start
   (`ytd_started_at`) and the year figures (`_year_figures`);
3. keeps the card as the last good one (`_hold`);
4. syncs the stale-snapshot and operator Repairs cards (the meter and
   projection cards are synced inside `_compute_ytd`);
5. when the entry is `LOADED` and this coordinator owns it, schedules
   `_async_rewrite_price_history` with `eager_start=False`.

## What a failed refresh serves

`_serve_cached` scrubs the failure (`scrub_tokens`, with
`sensitive_tokens` of the entry) into `last_error`; a budget overrun
becomes "fetch did not finish within 180 s". It then picks a card:

- When nothing is held, when what is held is stale, or when it is last
  year's card, it asks the card archive (`_from_card_archive`) and takes
  the archived card only if it was captured after the held one.
- Otherwise it serves the held card with its original fetch time, so
  `snapshot_age_hours` keeps counting.
- With neither it raises `UpdateFailed`, which on the first refresh is the
  entry's "not ready" reason.

The year to date is folded once on whichever card is served, and an
archived card that was taken becomes the held card through `_hold`.

`_hold` keeps `(card, fetched_at)` in memory and, while the coordinator
owns the entry, schedules a save of `card`, `fetched_at` and `commune` to
the card Store (`_card_store`, `async_delay_save` with no delay, not
awaited, so a meter event cannot slip in between). `async_load_last_good`
restores it at setup only when the card's utility and the stored commune
match the entry as it is now; a record that cannot be read
(`_held_from_record`, a naive timestamp included) is dropped.

`_from_card_archive` returns nothing when `CONF_CARD_ARCHIVE` is off. It
asks `_archived_row` for this month's row of the utility and commune
(`default` without one), then walks back a month at a time, up to
`_ARCHIVE_MONTHS_BACK` (12) months, stopping at the first row found. A
`TransientFetchError` (network, 5xx, 429) ends the walk with nothing, since
taking an older month in its place could mean last year's card. The row is
read with `tariff_from_dict`, relabelled with the commune's label, and
dated 06:00 UTC on its `_seen_on` day, never later than now, so a row
written this morning is not a snapshot from the future. Where the rows
come from is in [data-sources.md](data-sources.md).

## Staleness

`_is_stale` is true when the fetch time is in the future, when the card is
more than `SNAPSHOT_STALE_AFTER_DAYS` (35) days old, or when its
`valid_until` has passed. For the last test last year's card gets the
grace `providers.base.carry_prior_year_card` gives it, valid until
31 March, so a card held from December or a December archive row does not
go stale on 1 January. A card with no `valid_until` never goes stale by
date.

## The year to date

The running consumption and bill are one record, `_YtdCycle`: the meter it
belongs to, the `year` that dates the whole record, the published figure
`m3` (a high-water mark), the cost floor `cost`, the frame `offset_m3` (a
live reading `r` counts as `r - offset_m3` for the year), `basis` (what
the floor was priced for), `recorder_hwm` (the highest answer the
recorder gave this year), `seen_at` (when a reading was last placed) and
`started_at` (when a swap restarted the year, or when a meter the recorder
holds no statistics for started it empty; `None` means 1 January). A
record stamped with an earlier year keeps its fields but none of them
count.

`_fold` is the whole rule, a pure function with no clock and no I/O. Both
callers hand it the evidence they have: the daily tick a reading and maybe
a recorder answer, a meter event a reading only. In outline:

- A different meter discards the record.
- A reading below the frame (or below the year's figure when there is no
  frame) starts or extends a run of low readings; a reading more than
  `_IMPLAUSIBLE_JUMP_M3` (100 m³) from the run starts a new one. After
  `_SWAP_CONFIRM_READINGS` (3) that span at least `_SWAP_CONFIRM_SPAN_S`
  (600 s), either the frame is rebuilt under the reading (the meter still
  shows more water than the year used, so the frame was too high, or the
  year is served from the recorder with no frame) or the meter counts as
  replaced: the year restarts at 0 with `started_at` set.
- With no frame yet, a reading builds one from the highest figure the year
  has, or starts an empty year at 0 when the record already knew this
  meter and the recorder did not just fail.
- In the frame, a step larger than `_MAX_STEP_M3` (30 m³) plus 1 m³ for
  each day the meter was out of sight (`_AWAY_M3_PER_DAY`, measured from
  `seen_at`) is held until the next reading confirms it. A confirmed step
  that size is checked against the recorder when the round has one;
  without one nothing is published and the next tick asks.
- A recorder answer counts only for a meter it holds a statistic for and
  only while it is not below `recorder_hwm` (the water the reader took
  back out of misread days added back for that comparison). It then beats
  a reading more than `_RECORDER_LAG_M3` (2 m³) above it. Once the
  recorder has spoken for the year before, and the same round has a
  reading above 0 inside the frame, it also lowers a mark more than 2 m³
  above it to its figure, rebuilding the frame and dropping the floor.
- The published figure is the highest of the mark, the reading's figure
  and the recorder's. Its cost (`_ytd_cost_from_m3`, the fixed fee
  pro-rated to the day) is clamped to the floor while `basis` holds.

`_fold_cycle` is the only caller that keeps the answer. It measures the
gap since the last round, picks the card in force, computes the basis
(`_cost_basis`: release version, utility, commune, residents, social
tariff, card year), runs `_fold`, writes the record and the in-memory hold
state back, and sets `_ytd_arbitrate` when the round swapped the meter or
wants the recorder. The hold, the low-reading run and the highest reading
seen live in memory only.

`_card_in_force` keeps a card dated after the day being priced
(`_priced_on`) off the running bill when it holds an earlier card for the
same utility and commune: next year's card put up in December waits for
1 January. A fresh install in December holds none and prices on the card
it has. The card the year is priced on, with its commune, is
stored with the cycle (`card`) so a restart in December still knows it.
`_priced_on` also keeps a round that crosses midnight on 31 December
priced in the year it was folded for.

`_compute_ytd` resolves the meter, moves the live subscription if the
meter changed, and returns nothing without one. It asks the recorder
(`_recorder_ytd_m3`, 1 January to today) only when the record cannot
answer on its own: another meter or year, no frame, no reading, a frame
that has fallen behind the published figure, or `_ytd_arbitrate`. After
that query it reads the meter again, folds, and saves the record when it
changed. If a meter event moved the cycle during the save, it folds once
more with no evidence and publishes the cycle as it stands.

The YTD Store is `_YtdStore`. Its shape moves on the minor version
(`_YTD_STORE_MINOR_VERSION`), so an older release meets a newer record
without a failed setup.
`_migrate_cycle_to_v2` folds the eight-key record of minor 1 (separate live
and recorder-served marks with their own year stamps) into one record,
picking the newer year and the larger figure in it. A record that already
has the new keys (`m3` or `offset_m3`) is passed through, whatever minor
version a rolled-back release stamped on it. `async_load_ytd_state`
drops a record `_cycle_from_record` cannot read rather than fail setup,
and sets `_ytd_arbitrate` again for a swap no tick has dated yet. The tick
saves on change, a meter event schedules a save `_YTD_SAVE_DELAY_S` (30 s)
later, and `async_unload_entry` flushes through `async_save_ytd_state`.

### Live tracking

`async_setup_live_tracking` subscribes to the resolved meter with
`async_track_state_change_event`, after unsubscribing the previous meter,
and only while the coordinator owns the entry.
`_recompute_live_ytd` converts the new state to m³ (`_state_volume_m3`),
folds it with no recorder answer and, when the figure or the cost moved,
replaces `self.data` and calls `async_update_listeners`. It does not use
`async_set_updated_data`, which would re-arm the daily timer on every
draw, and leaves `snapshot_age_hours` alone.

### The recorder reads

`_recorder_daily_rows` asks `statistics_during_period` for daily buckets
of `change`, `sum` and `state` in m³, through the recorder's executor. It
returns `None` when there is no recorder, no running instance, or no
statistic for the meter, and raises `RecorderUnavailable` when a running
recorder failed to answer. `_admitted_changes` then decides which buckets
are water: it drops a first bucket whose change is the meter's whole
running total, nets a negative day against the next one, keeps only the
climb of a day whose change is at least its own register, caps a bucket
that the next register shows was a misread (`_took_back_a_spike`), and
refuses a day claiming more than `_IMPLAUSIBLE_JUMP_M3` (100 m³) plus 1 m³
per day without a bucket (`_exceeds_a_day`).

`_recorder_ytd_m3` sums what was admitted and returns it with the amount
taken back; a year whose buckets were all refused, more than
`_REFUSALS_BEFORE_UNREADABLE` of them, is unreadable rather than empty.
`_recorder_full_year_m3` reads a closed calendar year for the projection
Repair: it needs a bucket before 1 January, one in December and buckets on
two days in three (`_enough_days`), and offers nothing for a year with a
negative day or a day either guard refuses.

### Deferred meter history at setup

Setup's own refresh runs inside a startup stage Home Assistant times for
every integration together. So with `defer_meter_history` the first
`_compute_ytd` still folds the year to date but leaves out the two
year-long reads (`_read_meter_history`: the projection check and the metered
days) and sets `meter_history_pending`. `async_read_meter_history` then
makes them in the background and republishes only the year figures,
computed from the year to date already published, so nothing is folded
twice.

## The year figures

`_read_metered_days` reads the meter's closed days back `_METERED_DAYS`
(365 + 31) days, once a day per meter, through `_admitted_changes` at debug
level, and saves `meter`, `read_on` and `days` to the metered Store
(`be_water_prices.<entry_id>.metered`). A failed read keeps the last one
for up to `_METERED_HOLD` (7 days), and `async_load_metered_days` adopts a
stored read only within that window.

`_year_figures` builds `YearFigures` from it on the card in force:
`_rolling_year_m3` is the 365 closed days before the read, priced as a
year; the projection is the year to date plus last year's same remaining
days (`_rest_of_year_m3`, which handles 29 February), and its cost is the
running bill plus what those days add. `_metered_m3` applies the coverage
rule to each window: a bucket in the month before it, one in its last
month, and two days in three.

## Meter resolution

`async_resolve_meter_entity` returns the `water_meter_sensor` option when
set, else the first `water` source of the Energy dashboard
(`_discover_energy_water_meter`, which waits at most
`_ENERGY_MANAGER_TIMEOUT_S`, 10 s, for the energy manager and also returns
how many water sources it lists). With several, the first is billed, a
warning is logged and the several-meters card is raised. A unit Home
Assistant cannot convert to m³ is refused on both sides: the live reading
reads as `None`, and `_refuse_an_unconvertible_unit` raises
`RecorderUnavailable` before the recorder query, since the recorder would
return the figures unconverted. No unit, ASCII `m3` and `m³`
(`_ALREADY_CUBIC_METRES`) need no conversion on either side.

## Repairs cards

All four are keyed on the entry id and deleted by `_forget_coordinator` in
`__init__.py` when the entry unloads or a setup fails past the first
refresh.

- `snapshot_stale_<entry_id>` (`_sync_repair_issue`), fixable: the fix
  flow in `repairs.py` runs `async_refresh` and aborts with `still_stale`
  when the snapshot did not recover, so the card stays.
- `projection_outdated_<entry_id>` (`_sync_projection_issue`), fixable:
  last calendar year's metered figure, rounded, when it lies within the
  consumption bounds and is at least `PROJECTION_DRIFT_RATIO` (10 %) off
  the typed figure. The fix writes it into the options, which reloads the
  entry. The answer is kept in memory per meter, year and typed figure, so
  later ticks skip the query until a restart.
- `several_water_meters_<entry_id>` (`_sync_several_meters_issue`): the
  Energy dashboard lists more than one water source and no meter is set.
- `operator_moved_<entry_id>` (`_sync_operator_issue`): the stored
  postcode no longer resolves to the entry's utility. Quiet without a
  postcode, for one that does not resolve, when the utility is among the
  candidates or the candidates equal `postcode_resolved`, the answer the
  household already overrode.

## Ownership

`_owns_the_entry` is true while the coordinator is not retired and the
entry is `SETUP_IN_PROGRESS` or `LOADED`. A refresh started from a Repairs
fix flow runs in the flow's task, which an unload does not wait for, so
every write (the Stores, the Repairs cards, the meter subscription) checks
it first. `async_retire`, called from `_forget_coordinator`, marks the
coordinator retired and drops its subscription. `entry_coordinator`
returns `entry.runtime_data` only while it still owns the entry, and is
what the Repairs flows and the price history use.

## The price history

`_async_rewrite_price_history` calls `statistics.async_maybe_backfill_once`
after a tick. It is scheduled, not awaited, and not started eagerly: the
gate reads `coordinator.data`, which Home Assistant replaces only once the
tick has returned. The gate (`backfill_year` in the entry's data) is the
year, the utility, the card's year and the commune, so a card published
late redraws January's line on the tick that brings it. A stale snapshot
leaves the gate unstamped. Writing the gate updates the entry, and the
update listener reloads it. The rest is in
[entities.md](entities.md).

## The Stores and their removal

| Store key | Version | Holds |
| --- | --- | --- |
| `be_water_prices.<entry_id>.ytd` | 1, minor 2 | the `_YtdCycle` and the card in force |
| `be_water_prices.<entry_id>.metered` | 1 | the last read of the meter's days |
| `be_water_prices.<entry_id>.card` | 1 | the last good card, its fetch time and commune |

`async_remove_entry` calls `async_remove_stores`, which deletes all three,
so removing and re-adding a utility starts clean.

## The refresh service and the button

`be_water_prices.refresh`, registered in `async_setup`, runs
`async_refresh` on every entry `loaded_entries` returns (the one named by
`entry_id`, else all loaded ones) side by side and returns when they are
done. A named entry that is not loaded is refused with `unknown_entry`.
The *Refresh tariff* button (`button.py`, `RefreshButton`) awaits the same
`async_refresh` and stays available while the sensors are not, since it is
how an entry whose card cannot be read is retried. Every tick fetches, so
neither needs a force flag. `backfill_prices` is described in
[entities.md](entities.md).
