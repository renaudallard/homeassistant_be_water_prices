# Entities, services, Repairs and diagnostics

What an entry exposes to Home Assistant. The figures behind each sensor are
in [pricing-model.md](pricing-model.md); when they are computed is in
[coordinator.md](coordinator.md).

## The device

Every entity of an entry sits on one device, identified by
`(be_water_prices, <entry id>)` and built by `utility_device_info` in
`coordinator.py`: named after the entry's title, the utility's label as
manufacturer, its region as model, and the tariff's source URL, with the
commune taken out, as the configuration link. Entity unique ids are
`<entry id>_<key>`, and every entity uses `has_entity_name`, so its id is
the device name plus the translated entity name.

## Sensors

`SENSORS` in `sensor.py`. Each reads one value off the coordinator's
`CoordinatorData` through its `value_fn`.

| Key | English name | Unit | Class | What it is |
| --- | --- | --- | --- | --- |
| `yearly_fee` | Yearly fixed fee | €/year | measurement | `yearly_fixed_fee` as the card prints it, ex-VAT |
| `basis_rate` | Basis rate | €/m³ | measurement | The basis rate (Flanders), the linear rate (Brussels) or the CVD (Wallonia), ex-VAT |
| `comfort_rate` | Comfort rate | €/m³ | measurement | The Flemish comfort rate; created for Flemish utilities only |
| `sanering_rate` | Sewerage rate | €/m³ | measurement | Supra-communal plus communal sanering plus CVA plus FSE, ex-VAT |
| `all_in_basis` | All-in basis rate | €/m³ | measurement | (`basis_rate` + `sanering_rate`) with VAT |
| `projected_annual_cost` | Projected annual cost | €/year | measurement | The configured consumption billed for a year, VAT in |
| `current_year_cost` | Current year cost | € | monetary, total | The running bill since 1 January, VAT in |
| `ytd_consumption` | Year-to-date consumption | m³ | water, total | The meter's water since 1 January |
| `rolling_year_consumption` | Rolling year consumption | m³ | measurement | The meter's last 365 days |
| `rolling_year_cost` | Rolling year cost | €/year | measurement | That volume billed as a year on today's card |
| `projected_year_consumption` | Projected year consumption | m³ | measurement | Where `ytd_consumption` stands on 31 December |
| `projected_year_end_cost` | Projected year-end cost | € | measurement | Where `current_year_cost` stands on 31 December |

`_is_applicable` leaves `comfort_rate` out outside Flanders. The
meter-driven sensors are always created and read `unknown` until a meter
is configured or found on the Energy dashboard, so a meter added there is
picked up on the next tick without a reload. After the backfill at setup,
`async_remove_inapplicable_entities` removes the registry entry of a
sensor the utility no longer produces (the comfort rate after a move out
of Flanders); it runs after the backfill because the statistics cleanup
finds its rows through that registry entry.

The two year-to-date sensors carry a `last_reset`: the later of 1 January
local time, the cycle's own start (`ytd_started_at`, set by a confirmed
meter swap, or by a meter the recorder holds no statistics for starting
its year at 0) and the last reset the entity
published, which it restores across restarts (`_ResetGuardState`), so a
reset never moves backwards and opens a new statistics cycle by mistake.

Every sensor exposes `utility`, `region`, `valid_from`, `valid_until`,
`publication_label`, `source_url`, `snapshot_age_hours`,
`snapshot_stale` and `last_error` as attributes. The label, the URL and
the error text are scrubbed of the commune first
(`_publication_label_without_commune`, `source_url_without_commune`,
`scrub_tokens` with `sensitive_tokens` from `_redact.py`).

Icons come from `icons.json`: one for each sensor without a device class,
while the two year-to-date sensors keep the icons of their device classes.

## The refresh button

`RefreshButton` in `button.py`, a diagnostic entity (`refresh`, "Refresh
tariff"). Pressing it runs `async_refresh` on the coordinator, which
fetches the tariff again at once. It stays available while the sensors
are not, since pressing it is how an entry whose tariff cannot be read is
retried.

## Services

Both are registered once, in `async_setup`, with the integration rather
than with an entry, so they exist while every entry is retrying setup.
Both target an `entry_id` or, without one, every loaded entry
(`loaded_entries` in `coordinator.py`); an `entry_id` that names no
loaded entry raises a translated `ServiceValidationError`
(`unknown_entry`).

- **`be_water_prices.refresh`** (`__init__.py`): refreshes the targeted
  coordinators side by side and returns once the fetches are done.
- **`be_water_prices.backfill_prices`** (`statistics.py`): writes hourly
  flat-line long-term statistics for the five card-driven sensors
  (`_BACKFILL_KEYS`: `yearly_fee`, `basis_rate`, `comfort_rate`,
  `sanering_rate`, `all_in_basis`) from `start_date` (default 1 January)
  up to the last hour the recorder has compiled, clamped to the card's
  `valid_from` and `valid_until`. `clear: true` deletes those sensors'
  statistics in full first and needs an `entry_id`
  (`clear_needs_entry`). The same writer runs by itself on the first
  setup of a year and whenever its gate moves: the gate
  (`backfill_year` in `entry.data`) carries the year, the utility, the
  card's year and the commune, and a stale snapshot defers it
  (`async_maybe_backfill_once`). On a change of utility the rows of keys
  the new utility does not produce are cleared, unless they hold history
  from before this year (`_async_clear_orphan_backfill_keys`).

The service fields and descriptions are in `services.yaml`, with
translations under `services` in `strings.json` and `translations/`.

## Repairs cards

Raised by the coordinator, one of each kind per entry, ids suffixed with
the entry id. All are warnings.

| Card | Raised when | Fix |
| --- | --- | --- |
| `snapshot_stale` | The snapshot is older than 35 days or past its `valid_until`, last year's card counting until 31 March | `SnapshotStaleRepairFlow`: refreshes now; a refresh that does not help leaves the card in place (`still_stale`) |
| `projection_outdated` | A whole calendar year the meter measured disagrees with the configured consumption | `ProjectionOutdatedRepairFlow`: writes the measured figure into the options, which reloads the entry; ignoring the card keeps the setting |
| `several_water_meters` | No meter is configured and the Energy dashboard lists more than one water meter | None: pick one in the options |
| `operator_moved` | The saved postcode now resolves to operators that leave the entry's out, other than the ones recorded when it was picked by hand (`postcode_resolved`) | None: reconfigure, or ignore |

`repairs.py` holds the two fix flows and `async_create_fix_flow`, which
maps an issue id to its flow. The cards are dropped when the entry
unloads or its setup fails (`_forget_coordinator` in `__init__.py`), and a
coordinator that no longer speaks for its entry raises none
(`_owns_the_entry`).

## Diagnostics

`diagnostics.py` dumps the entry's state, data and options and, when the
entry has a coordinator, the snapshot: the tariff, `fetched_at`, its age
and staleness, the projected and running costs, the year-to-date
consumption, the year figures and `last_error`. The postcode, what it
resolved to, the commune, its label and the meter entity id are redacted
by key (`_REDACT_KEYS`), and the commune is scrubbed out of every other
value too (the source URL, the publication label, the error text and the
backfill gate), so the file can be attached to an issue as it is. An
entry that never loaded dumps its config with an empty snapshot.
