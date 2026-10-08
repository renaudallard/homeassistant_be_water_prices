# The setup wizard and the options

Everything here is in `config_flow.py` (`BeWaterPricesConfigFlow`, version
2, and `BeWaterPricesOptionsFlow`), with the setup-time sweeps in
`__init__.py`. The user-facing walk-through is in the project
[README](../README.md#configuration).

## What an entry holds

- `entry.data`: `utility` (`CONF_UTILITY`), and `backfill_year`, the gate
  `statistics.py` stamps once the price history is written (see
  [entities.md](entities.md)).
- `entry.options`: the annual consumption (`consumption_m3_per_year`, 1 to
  2000 m³, default 80), for a Flemish utility the registered residents
  (`gedomicilieerd_persons`, 0 to 20, default 1) and the social tariff
  (`social_tariff`), for a per-commune utility the commune id and its
  label (`commune`, `commune_label`), the water meter (`water_meter_sensor`,
  optional), the card archive switch (`card_archive`, default on), the
  postcode typed at setup (`postcode`) and, after an operator picked by
  hand, what that postcode resolved to (`postcode_resolved`).

The unique id is `be_water_prices_<utility>`, so there is one entry per
utility. The title is the utility's label.

## First setup

1. **`user`**: a postcode. The registry is built off the event loop first
   (`providers.async_load`), then `providers._postcodes.resolve_candidates`
   maps the postcode to its operators (see
   [data-sources.md](data-sources.md)). One candidate goes straight to the
   options; several, a postcode split between operators at street level,
   go to `choose`; none, a postcode no supported operator serves, go to
   `manual`.
2. **`choose`**: a dropdown limited to the candidates.
3. **`manual`**: a dropdown of every registered utility, in registry order.
4. **`options`**: the household form (`_options_schema`). The residents and
   the social tariff only appear for a Flemish utility (`_is_flanders`).
   A per-commune utility (one whose extractor `supports_communes`) gets a
   commune dropdown filled from its live list (`_async_communes`); a list
   that cannot be fetched leaves the field out rather than blocking the
   flow. Where the operator bills the postcode on a commune of its own,
   that commune is suggested (`_commune_for_postcode`, the extractor's
   `commune_for_postcode` hook). The meter selector only lists sensors
   with the water device class; left blank, the coordinator reads the
   Energy dashboard's water meter (see [coordinator.md](coordinator.md)).
   On submit the commune's label is stored beside its id, so the first
   publication label and diagnostics show the name rather than the id.

The commune list is cached per flow instance (`_async_communes_cached`),
non-empty results only, so a render and its submit do not fetch it twice
and a failed fetch can be retried.

## The options

`BeWaterPricesOptionsFlow.async_step_init` shows the same household form
for the entry's utility. A commune the operator's live list no longer
carries is dropped from the defaults so the user picks again, and said in
the log without naming it (`_warn_stale_commune`). When the list cannot
be fetched, the saved commune is kept. The postcode and what it resolved
to carry over. Saving reloads the entry through the update listener
(`_async_update_listener` in `__init__.py`), since adding or removing the
meter changes which entities exist.

## Reconfigure

The entry's three-dot menu opens `reconfigure`, a menu of two paths:

- **`reconfigure_postcode`** asks for a postcode again and re-runs the
  resolver, going on to `reconfigure_choose` for a split postcode and to
  `reconfigure_manual` for one that resolves to nothing.
- **`reconfigure_manual`** picks the utility directly.

A per-commune utility then goes through `reconfigure_commune`. Its
dropdown is pre-filled with the saved commune when the utility and the
postcode are unchanged; after a new postcode, with the commune the
operator bills that postcode on, if any (`_moved_from` decides what
counts as a move: an entry's first postcode is not one). A list that
cannot be fetched skips the step. A move into Flanders from Brussels or
Wallonia, with no residents saved, asks for the household in
`reconfigure_household`.

`_async_finish_reconfigure` writes the result. A utility already
configured in another entry aborts (`already_configured`). The commune is
dropped on a change of utility, on a new postcode and when it left the
operator's list; the residents and social tariff are dropped on a move
out of Flanders. A postcode typed here replaces the saved one and clears
`postcode_resolved`; the manual path keeps the postcode and records what
it resolved to when the chosen utility is not among the candidates, so
the `operator_moved` Repairs card stays quiet unless a later release
changes the answer. An entry whose setup failed is updated and reloaded;
a loaded one is updated and its listener reloads it. A dialog left open
on an entry that was removed meanwhile ends with `entry_removed`.

## At every setup

`async_setup_entry` runs two sweeps on the options before the first
refresh:

- `_drop_phantom_commune_if_blocked` drops a saved Farys or Pidpa commune
  that `_phantom_blocklists.py` lists (one the operator lists but does not
  serve), so a later addition to the list reaches existing entries.
- `_adopt_commune_for_postcode` gives an entry with no commune the one its
  postcode is billed on, when the operator's hook names one. A commune the
  user picked is never overwritten.

Both are rolled back when the first refresh is not ready
(`ConfigEntryNotReady`), so a setup retry does not leave the entry
stripped of its commune.

## Migration

`async_migrate_entry` moves a version 1 entry to version 2 without
changing its data (a v1 entry stores no postcode and keeps none until it
is reconfigured). An entry written by a newer release is refused with a
log line naming both versions.
