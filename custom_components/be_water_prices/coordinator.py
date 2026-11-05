# Copyright (c) 2026, Renaud Allard <renaud@allard.it>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Daily-refresh coordinator for be_water_prices."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ._redact import scrub_tokens, sensitive_tokens, source_url_without_commune
from .const import (
    CONF_CARD_ARCHIVE,
    CONF_COMMUNE,
    CONF_COMMUNE_LABEL,
    CONF_POSTCODE,
    CONF_POSTCODE_RESOLVED,
    CONF_UTILITY,
    DEFAULT_CARD_ARCHIVE,
    DOMAIN,
    FETCH_BUDGET_S,
    SNAPSHOT_STALE_AFTER_DAYS,
    UPDATE_INTERVAL_HOURS,
)
from .coordinator_meter import YearFigures, _MeterMixin
from .coordinator_store import (
    _card_store,
    _held_from_record,
    _metered_store,
    _MeteredDays,
    _ytd_store,
)
from .coordinator_ytd import _YtdMixin
from .providers import ExtractorError, TransientFetchError, WaterTariff, get
from .providers._archive import ARCHIVE_MONTHS_BACK as _ARCHIVE_MONTHS_BACK
from .providers._archive import archive_reads
from .providers._archive import archived_row as _archived_row
from .providers._postcodes import resolve_candidates
from .providers.base import (
    WaterExtractor,
    carry_prior_year_card,
    relabel_with_human_commune,
    tariff_from_dict,
    tariff_to_dict,
)
from .ytd_fold import _YtdCycle

_LOGGER = logging.getLogger(__name__)


def utility_device_info(coordinator: WaterCoordinator) -> DeviceInfo:
    """Build the HA DeviceInfo block shared by every entity on this entry.

    Anchors every sensor onto one per-entry device identified by
    ``(DOMAIN, entry.entry_id)`` so the integration's *Devices* tab
    shows a single card per configured utility instead of orphan
    entities. ``manufacturer`` carries the utility label, ``model``
    carries the region, and ``configuration_url`` deep-links to the
    utility's tariff publication for one-click verification.
    """
    utility_id = str(coordinator.entry.data.get(CONF_UTILITY, ""))
    try:
        extractor = get(utility_id)
        manufacturer = extractor.label
        model = extractor.region.title()
    except ExtractorError:
        manufacturer = utility_id or "Belgian Water"
        model = ""
    source_url: str | None = None
    if coordinator.data is not None:
        # The device card is as public as the sensor attribute -- it shows
        # in screenshots and exports the same way -- so the commune slug
        # comes out of the deep link too.
        source_url = source_url_without_commune(
            coordinator.data.tariff.source_url,
            coordinator.entry.options.get(CONF_COMMUNE),
        )
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.entry.entry_id)},
        name=coordinator.entry.title,
        manufacturer=manufacturer,
        model=model or None,
        configuration_url=source_url,
    )


@dataclass
class CoordinatorData:
    tariff: WaterTariff
    fetched_at: datetime
    snapshot_age_hours: float
    snapshot_stale: bool
    last_error: str = ""
    projected_annual_cost_eur: float | None = None
    current_year_cost_eur: float | None = None
    ytd_consumption_m3: float | None = None
    # When the year-to-date figures above started counting, or None for
    # 1 January: what the YTD sensors report as their last reset.
    ytd_started_at: datetime | None = None
    year_figures: YearFigures = field(default_factory=YearFigures)


class WaterCoordinator(_MeterMixin, _YtdMixin, DataUpdateCoordinator[CoordinatorData]):
    """Fetches the configured utility's tariff once a day."""

    def __init__(
        self, hass: HomeAssistant, entry: WaterConfigEntry, *, defer_meter_history: bool = False
    ) -> None:
        self.entry = entry
        utility_id = entry.data[CONF_UTILITY]
        self._extractor: WaterExtractor = get(utility_id)
        # The last good card and when it was fetched or captured, restored
        # from _card_store at setup: what a failed refresh serves.
        self._last_good: tuple[WaterTariff, datetime] | None = None
        # Resolved meter entity and the meter's reading back at Jan 1,
        # captured on each daily tick so meter state-change events can
        # recompute YTD live without re-querying the recorder.
        self._meter_entity_id: str | None = None
        # The calendar year the year-to-date statistics were settled for,
        # and when that was last asked (statistics.async_maybe_backfill_year).
        self.year_backfilled: int | None = None
        self.year_backfill_tried: datetime | None = None
        # The meter last seen going back to zero on a schedule, see
        # :meth:`_sees_a_register`.
        self._resetting_meter: str | None = None
        # Unsub for the live meter subscription. Kept so a later tick that
        # resolves a different meter can re-point it; the entry unload calls
        # async_unsub_live_tracking to tear the current one down.
        self._meter_unsub: CALLBACK_TYPE | None = None
        # Unsub for the hourly read of a meter with no register to follow,
        # torn down with the live subscription.
        self._hourly_unsub: CALLBACK_TYPE | None = None
        # Held while the year is computed. The hourly read and a refresh
        # running at once would interleave their recorder queries and folds.
        self._ytd_lock = asyncio.Lock()
        # The year-to-date cycle, persisted across restarts via _store. It
        # holds the year's consumption, its cost floor and the meter offset
        # that produces them; see :class:`_YtdCycle`. Restoring it means an
        # HA restart or reload no longer re-derives the year from the
        # recorder's trailing daily total, which used to snap the published
        # figure downward.
        self._ytd = _YtdCycle()
        # The card the year to date is priced on and the commune it was
        # fetched for, persisted with the cycle. An operator can put next
        # year's card up in December, and after a restart nothing else
        # remembers the one still in force; see :meth:`_card_in_force`.
        self._ytd_card: tuple[WaterTariff, str | None] | None = None
        # A reading held pending confirmation by the next one, and the run
        # length of consecutive readings below the cycle's bar. Both are
        # in-memory only: a restart simply re-runs the hold, and a real
        # meter swap re-accumulates the run.
        self._ytd_hold_m3: float | None = None
        self._retired = False
        self._ytd_hold_run = 0
        self._ytd_hold_span_s = 0.0
        self._ytd_run_m3: float | None = None
        self._ytd_last_fold_at: float | None = None
        # Highest reading the meter has shown this cycle. In memory only: it
        # decides whether a reading is the meter climbing or a dip, and after
        # a restart the very next reading re-establishes it.
        self._ytd_high_m3: float | None = None
        # Set when a round has treated the meter as replaced, cleared by
        # the next tick that asks the recorder about it. In memory only, so
        # a restart in between loses the arbitration: the record such a
        # round persists is a complete, self-consistent frame, so every
        # clause of the gate reads False and nothing asks again. A swap is
        # the exception, since its record still shows it, and loading the
        # record sets the flag again for one (see async_load_ytd_state).
        # Any other round that wants the recorder still needs the intent
        # persisted rather than held.
        self._ytd_arbitrate: bool = False
        # Whether the last recorder query succeeded, None before anything has
        # asked. Transient by design: it says what the database did a moment
        # ago, which is exactly as long as the answer is worth trusting.
        self._recorder_ok: bool | None = None
        # Set when the cycle changes. The daily tick and a clean unload flush
        # it to the Store authoritatively; the live meter-event path
        # additionally schedules a debounced save so a hard crash between
        # ticks keeps the climbing figure.
        self._cycle_dirty = False
        # (meter, year, configured m3, read as a register) the projection
        # prompt was last decided for. The answer only changes when one of
        # those does, so remembering them spares the thirteen-month
        # statistics query on every tick but the first. The meter belongs
        # in the key: an options change reloads the entry and rebuilds this,
        # but Energy-dashboard auto-discovery can resolve a different meter
        # with no reload at all, and that is a different year's worth of
        # water. So does the way it was read, for the reason _MeteredDays
        # keeps it.
        self._projection_checked: tuple[str, int, float, bool] | None = None
        # The meter's closed days back to a month before the rolling year,
        # read once a day: the rolling year and the rest of the calendar
        # year, taken from last year's same days, both come off it.
        self._metered: _MeteredDays | None = None
        # Whether the next refresh leaves those two reads of the meter's year
        # out: asked for by setup only, whose first refresh Home Assistant
        # waits on inside a startup stage every integration shares. Set
        # pending by the refresh that left them out, for setup to start
        # async_read_meter_history once it no longer holds startup up.
        self._meter_history_deferred = defer_meter_history
        self.meter_history_pending = False
        self._store: Store[dict[str, Any]] = _ytd_store(hass, entry.entry_id)
        self._metered_store = _metered_store(hass, entry.entry_id)
        self._card_store = _card_store(hass, entry.entry_id)
        super().__init__(
            hass,
            _LOGGER,
            # Explicit rather than the setup-context fallback Home Assistant
            # flags: the entry is what registers the shutdown and makes the
            # daily tick an entry task.
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
        )

    def _fetched_commune(self) -> str | None:
        """The commune the utility's card is fetched for, None for its
        default card."""
        commune = self.entry.options.get(CONF_COMMUNE)
        if commune and self._extractor.fetch_for_commune is not None:
            return str(commune)
        return None

    async def _async_update_data(self) -> CoordinatorData:
        session = async_get_clientsession(self.hass)
        commune = self._fetched_commune()
        budget = asyncio.timeout(FETCH_BUDGET_S)
        failure: Exception | None = None
        # An extractor whose card is a picture reads the archive's reading
        # of it, which the card_archive option switches off with the rest.
        archive = self.entry.options.get(CONF_CARD_ARCHIVE, DEFAULT_CARD_ARCHIVE)
        try:
            async with budget:
                with archive_reads(archive):
                    if commune is not None and self._extractor.fetch_for_commune is not None:
                        tariff = await self._extractor.fetch_for_commune(session, commune)
                        tariff = relabel_with_human_commune(
                            tariff,
                            commune_id=commune,
                            commune_label=self.entry.options.get(CONF_COMMUNE_LABEL),
                        )
                    else:
                        tariff = await self._extractor.fetch(session)
        except Exception as err:
            # Catch broader than ExtractorError so a future extractor
            # that forgets to wrap (asyncio.TimeoutError, ssl.SSLError,
            # KeyError on a malformed response) still falls through to
            # the cached-snapshot path. Two exception families must
            # still propagate:
            #   - asyncio.CancelledError: HA cancels coordinator tasks
            #     on shutdown / reload, that signal has to reach the
            #     event loop unchanged.
            #   - ConfigEntryAuthFailed / ConfigEntryError: HA's
            #     DataUpdateCoordinator runs the reauth flow on these,
            #     swallowing them here would silently break future
            #     credentialed-tariff extractors.
            from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError

            if isinstance(err, asyncio.CancelledError | ConfigEntryAuthFailed | ConfigEntryError):
                raise
            failure = err
        if failure is not None:
            # Handled outside the except block on purpose: whatever the
            # cached path below raises would otherwise carry the raw
            # fetch error as its context, and a traceback logged by the
            # coordinator would print the URL, town name and all, that
            # every other surface scrubs.
            return await self._serve_cached(failure, budget.expired())

        now = datetime.now(UTC)
        ytd_m3, ytd_cost = await self._compute_ytd(tariff)
        data = CoordinatorData(
            tariff=tariff,
            fetched_at=now,
            snapshot_age_hours=0.0,
            snapshot_stale=self._is_stale(tariff, now),
            projected_annual_cost_eur=self._project_cost(tariff),
            current_year_cost_eur=ytd_cost,
            ytd_consumption_m3=ytd_m3,
            ytd_started_at=self._ytd.started,
            year_figures=self._year_figures(tariff, ytd_m3, ytd_cost),
        )
        self._hold(tariff, now)
        self._sync_repair_issue(data)
        self._sync_operator_issue()
        if self.entry.state is ConfigEntryState.LOADED and self._owns_the_entry():
            # Only once the entry is up. The first refresh runs inside
            # setup, which calls the backfill itself a few lines later,
            # and doing it here as well would stamp the gate mid-setup and
            # reload the entry out from under it.
            #
            # Scheduled rather than awaited: an await between the fold
            # above and this return is the window a live meter event uses
            # to publish a higher figure that the locals here would then
            # overwrite with a lower one.
            #
            # Not started eagerly: the gate and the writer read
            # coordinator.data, which Home Assistant only replaces with
            # what this returns once it has returned. An eager start ran
            # them against the previous snapshot, so the tick that first
            # fetched a new card compared the old card year, found the
            # gate matching and left the price line alone for a day.
            self.hass.async_create_task(self._async_rewrite_price_history(), eager_start=False)
        return data

    async def _async_rewrite_price_history(self) -> None:
        """Re-run the auto-once price backfill when its gate has moved.

        The gate carries the year of the card the rates came off, so it
        trips when a publisher that ran late finally puts the new one up
        and January's flat line can be rewritten at the rate that really
        applied. Only entry setup consulted it, so an install that has not
        restarted between January and the card landing kept the old line.

        Cheap on every other day: the gate matches and the call returns
        having touched nothing.
        """
        from .statistics import async_maybe_backfill_once

        try:
            await async_maybe_backfill_once(self.hass, self.entry)
        except Exception:
            _LOGGER.exception("could not rewrite the price history for %s", self.entry.entry_id)

    @staticmethod
    def _age_hours(fetched_at: datetime) -> float:
        # Use abs() so a future-dated fetched_at (clock skew, container
        # restored from a future-dated snapshot) surfaces a positive
        # age on the sensor attribute -- masking it with a 0 clamp
        # would hide the symptom while _is_stale fired the Repair.
        return abs((datetime.now(UTC) - fetched_at).total_seconds()) / 3600.0

    @staticmethod
    def _is_stale(tariff: WaterTariff, fetched_at: datetime) -> bool:
        delta = datetime.now(UTC) - fetched_at
        # Future-dated cache is always stale: a snapshot 'from the
        # future' is suspect by definition (clock skew, NTP jump,
        # container restored). Surfacing it as stale lets the
        # snapshot_stale Repair fire on day one instead of waiting
        # for real time to catch up over weeks / months.
        if delta.total_seconds() < 0:
            return True
        age_days = delta.days
        if age_days > SNAPSHOT_STALE_AFTER_DAYS:
            return True
        # Last year's card stands until 31 March, as it does when an
        # extractor serves it. A card held from a December fetch, or a
        # December row of the archive, still says 31 December, and the
        # first failed refresh in January raised the Repair on it. The
        # grace only ever adds time: a card dated later stands to its date.
        today = dt_util.now().date()
        carried = carry_prior_year_card(tariff, today.year).valid_until
        return all(d is not None and d < today for d in (tariff.valid_until, carried))

    @property
    def stale_issue_id(self) -> str:
        """Stable Repairs issue id for this entry's stale-snapshot warning."""
        return f"snapshot_stale_{self.entry.entry_id}"

    async def _serve_cached(self, failure: Exception, out_of_time: bool) -> CoordinatorData:
        """What a refresh publishes when its fetch failed with ``failure``.

        The last good snapshot, kept across restarts, with the failure
        scrubbed into last_error and the stale check re-run, so a dashboard
        sees the age and the reason rather than every sensor going blank. With no snapshot to
        fall back on, or with one that has gone stale or is last year's
        card and a newer card in the project's archive, the archived card
        is served instead. With neither the refresh fails, which on a first
        refresh is the entry's "not ready" reason.
        """
        if out_of_time:
            # A bare TimeoutError, whose str() is empty. A PDF parse it gave
            # up on keeps its thread until the reader's own time limit kills
            # the child, at most that long after the parse began.
            failure = ExtractorError(f"fetch did not finish within {FETCH_BUDGET_S} s")
        # The message quotes the URL it failed on, and a per-commune URL
        # carries the town name. The sensor attribute, diagnostics and the
        # Repair card all scrub that; the log was the one surface left
        # publishing it.
        scrubbed = scrub_tokens(
            str(failure), sensitive_tokens(self.entry), placeholder="**redacted**"
        )
        held = self._last_good
        held_at = held[1] if held is not None else None
        # Nothing to serve, or what is held has gone stale: ask the
        # archive. Asking again on a stale snapshot is what carries an
        # outage that outlives the staleness window: the archive files a
        # row a month, and an entry that adopted one in January would
        # otherwise serve that same January card until Home Assistant
        # restarts, Repair card and all. Last year's card is asked about
        # too, although it is not stale until 31 March: the archive may
        # already hold the new one. Only a capture newer than what is held
        # replaces it.
        card: tuple[WaterTariff, datetime] | None = None
        if held is None or self._is_stale(*held) or held[0].valid_from.year < dt_util.now().year:
            archived = await self._from_card_archive()
            if archived is not None and (held_at is None or archived[1] > held_at):
                card = archived
        if card is not None:
            tariff, fetched_at = card
            _LOGGER.warning(
                "water tariff fetch failed (%s), serving the card archived on %s: %s",
                type(failure).__name__,
                fetched_at.date(),
                scrubbed,
            )
        elif held is not None:
            tariff, fetched_at = held
            _LOGGER.warning(
                "water tariff fetch failed (%s), serving cached: %s",
                type(failure).__name__,
                scrubbed,
            )
        else:
            raise UpdateFailed(scrubbed) from failure
        # One fold a round, whichever card is being served: the cycle
        # counts the rounds it has seen, and two in the same refresh would
        # have a reading confirm itself.
        ytd_m3, ytd_cost = await self._compute_ytd(tariff)
        data = CoordinatorData(
            tariff=tariff,
            fetched_at=fetched_at,
            snapshot_age_hours=self._age_hours(fetched_at),
            snapshot_stale=self._is_stale(tariff, fetched_at),
            last_error=scrubbed,
            projected_annual_cost_eur=self._project_cost(tariff),
            current_year_cost_eur=ytd_cost,
            ytd_consumption_m3=ytd_m3,
            ytd_started_at=self._ytd.started,
            year_figures=self._year_figures(tariff, ytd_m3, ytd_cost),
        )
        if card is not None:
            # From here on the archived card is the last good snapshot,
            # ageing like one.
            self._hold(tariff, fetched_at)
        self._sync_repair_issue(data)
        return data

    def _hold(self, tariff: WaterTariff, fetched_at: datetime) -> None:
        """Keep ``tariff`` as the card a failed refresh serves, here and in
        the Store, so a restart while the utility is down serves it too.

        Saved after the refresh returns rather than awaited: an await here
        is the window a live meter event uses to publish a figure the
        refresh would then overwrite.

        The delay is not zero: a save timed for the loop's present never
        fires under a frozen test clock, and the loop then spins on it,
        starving the recorder's thread.
        """
        self._last_good = (tariff, fetched_at)
        if not self._owns_the_entry():
            return
        record = {
            "card": tariff_to_dict(tariff),
            "fetched_at": fetched_at.isoformat(),
            "commune": self._fetched_commune(),
        }
        self._card_store.async_delay_save(lambda: record, 1)

    async def async_load_last_good(self) -> None:
        """Restore the last good card before the first refresh.

        Only one fetched for the utility and commune the entry has now: a
        reconfigured entry would otherwise serve the old operator's card.
        A record that cannot be read is dropped, as the entry then starts
        with nothing held, as it did before the card was stored.
        """
        try:
            data = await self._card_store.async_load()
        except Exception:
            _LOGGER.exception("could not load the last good card; starting without one")
            return
        held = _held_from_record(data)
        if held is None:
            return
        tariff, fetched_at, commune = held
        if tariff.utility != self._extractor.id or commune != self._fetched_commune():
            return
        self._last_good = (tariff, fetched_at)

    async def _from_card_archive(self) -> tuple[WaterTariff, datetime] | None:
        """The last card the project's archive holds for this entry and the
        moment it was captured; None when the archive is switched off,
        unreachable, or has nothing for this utility and commune.

        Asked when a refresh failed with nothing to serve, which is a
        fresh install while the utility is down, or a restart with no card
        stored for the entry's utility and commune, and again
        once what is held has gone stale or is last year's card. The entry
        then loads on that card, stale after the usual 35 days, instead of
        retrying setup until the utility is back. This month's row first,
        since the archive writes one per month, then back a month at a
        time: last month's covers the first days of a month, and the ones
        before it a utility the daily run cannot reach at all.
        """
        if not self.entry.options.get(CONF_CARD_ARCHIVE, DEFAULT_CARD_ARCHIVE):
            return None
        key = self._fetched_commune() or "default"
        session = async_get_clientsession(self.hass)
        month = dt_util.now().date()
        row: dict[str, Any] | None = None
        for _ in range(_ARCHIVE_MONTHS_BACK):
            try:
                row = await _archived_row(session, self._extractor.id, key, month)
            except TransientFetchError as err:
                # This month may well be held; the archive just did not say.
                # Walking on would take an older month in its place, which
                # in January is last year's card, so stop and let the next
                # refresh ask again. The URL in the message names the
                # commune, so only the kind of failure is logged.
                _LOGGER.debug(
                    "card archive unreachable for %s (%s)", self._extractor.id, type(err).__name__
                )
                return None
            if row is not None:
                break
            # The first of the month, a day back: the last day of the one
            # before, whatever its length.
            month = month.replace(day=1) - timedelta(days=1)
        if row is None:
            return None
        try:
            tariff = tariff_from_dict(row)
            seen_on = date.fromisoformat(str(row["_seen_on"]))
        except (KeyError, TypeError, ValueError):
            return None
        if key != "default":
            tariff = relabel_with_human_commune(
                tariff, commune_id=key, commune_label=self.entry.options.get(CONF_COMMUNE_LABEL)
            )
        # The archive walks at 05:23 UTC; the hour only feeds the age. A
        # row written today can be read before that hour, and a manual run
        # writes at any hour, so never date the capture after now: a
        # snapshot from the future counts as stale, and a card captured
        # this morning raised the stale-snapshot Repair for a whole day.
        captured = datetime(seen_on.year, seen_on.month, seen_on.day, 6, tzinfo=UTC)
        return tariff, min(captured, dt_util.utcnow())

    @callback
    def async_retire(self) -> None:
        """Mark this coordinator as no longer speaking for its entry.

        Called when the entry forgets it, on unload and on a failed
        setup. Read from the entry's state alone, the old coordinator of
        a reload still looked like the owner while the new setup was in
        progress and the entry not yet handed the new one, and took the
        owner's save path.
        """
        self._retired = True
        self.async_unsub_live_tracking()

    def _owns_the_entry(self) -> bool:
        """Whether this coordinator still speaks for its entry.

        A refresh started from the Repair card runs in the flow's own
        task, which an unload neither cancels nor waits for. When its
        fetch lands after the entry is unloaded, removed, or set up again
        with a new coordinator, nothing it learned may reach the Store,
        the Repairs list or the meter subscription: the file a removal
        deleted came back and the cards were raised for an entry that no
        longer exists.
        """
        return not self._retired and self.entry.state in (
            ConfigEntryState.SETUP_IN_PROGRESS,
            ConfigEntryState.LOADED,
        )

    def _sync_repair_issue(self, data: CoordinatorData) -> None:
        """Create or clear the stale-snapshot Repair issue for this entry.

        Surfaces in Settings -> Repairs as a warning card when the
        snapshot has not refreshed for SNAPSHOT_STALE_AFTER_DAYS days
        or the parsed valid_until is in the past, last year's card
        counting as valid until 31 March. Auto-clears on the next
        successful, fresh fetch.
        """
        if not self._owns_the_entry():
            return
        if data.snapshot_stale:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.stale_issue_id,
                is_fixable=True,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="snapshot_stale",
                translation_placeholders={
                    "utility": self._extractor.label,
                    "age_days": f"{int(data.snapshot_age_hours // 24)}",
                    "valid_until": (
                        data.tariff.valid_until.isoformat()
                        if data.tariff.valid_until is not None
                        else "unknown"
                    ),
                    # Already scrubbed on the way into CoordinatorData,
                    # but a caller could build one by hand.
                    "last_error": scrub_tokens(
                        data.last_error, sensitive_tokens(self.entry), placeholder="**redacted**"
                    )
                    or "(none)",
                },
                # Carry the entry id so the Repairs UI flow handler in
                # repairs.py knows which coordinator to refresh when the
                # user clicks the fix button.
                data={"entry_id": self.entry.entry_id},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self.stale_issue_id)

    @property
    def operator_issue_id(self) -> str:
        """Stable Repairs issue id for this entry's re-resolved postcode."""
        return f"operator_moved_{self.entry.entry_id}"

    @callback
    def _sync_operator_issue(self) -> None:
        """Say so when the stored postcode no longer resolves here.

        v2 entries keep the postcode expressly so a later correction to
        the resolver can reach them, and nothing read it: ``resolve_candidates``
        is called from the config flow alone, so a household that installed
        before the correction stays on the operator that was wrong when it
        installed. Twelve postcodes moved from Pidpa to Water-link in
        0.7.10 and every entry already on one of them kept paying 121.09
        EUR a year too much, with nothing anywhere to say the release had
        changed anything.

        Saying it rather than acting on it. Changing operator rewrites the
        entry's title, its unique id and its commune, and a move into
        Flanders has to ask for the household first; the reconfigure flow
        knows all of that and this does not need to learn it twice. What
        was missing was the signal, not the machinery.
        """
        if not self._owns_the_entry():
            # Same card id as the successor's after a reload: deleting it
            # here would hide a notice the live coordinator just raised.
            return
        postcode = self.entry.options.get(CONF_POSTCODE) or self.entry.data.get(CONF_POSTCODE)
        utility = self.entry.data.get(CONF_UTILITY, "")
        candidates = resolve_candidates(str(postcode)) if postcode else ()
        overridden = tuple(self.entry.options.get(CONF_POSTCODE_RESOLVED) or ())
        if not candidates or utility in candidates or candidates == overridden:
            # No postcode, an unresolvable one, or one that still answers
            # with the operator in use. A postcode split between operators
            # counts as answering: the household picked one of them. So
            # does an answer the household already overrode by picking the
            # operator by hand; a later change to it is still news.
            ir.async_delete_issue(self.hass, DOMAIN, self.operator_issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.operator_issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="operator_moved",
            translation_placeholders={
                "utility": self._extractor.label,
                "resolved": get(candidates[0]).label,
            },
        )


type WaterConfigEntry = ConfigEntry[WaterCoordinator]


def entry_coordinator(entry: WaterConfigEntry) -> WaterCoordinator | None:
    """The coordinator the entry is set up with, None while it has none.

    Setup hands it over once the first refresh has succeeded. Home
    Assistant takes it back when the entry unloads, but a setup that fails
    after the handover leaves it on the entry, retired by the teardown,
    until the next attempt replaces it.
    """
    coordinator: WaterCoordinator | None = getattr(entry, "runtime_data", None)
    if coordinator is None or not coordinator._owns_the_entry():
        return None
    return coordinator


def loaded_entries(hass: HomeAssistant, entry_id: str | None) -> list[WaterConfigEntry]:
    """The loaded entries a service call targets: the one named, else all.

    A named entry that is not loaded is refused, so the caller is told
    rather than shown a call that did nothing.
    """
    entries: list[WaterConfigEntry] = [
        entry
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if entry_id is None or entry.entry_id == entry_id
    ]
    if entry_id is not None and not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_entry",
            translation_placeholders={"entry_id": entry_id},
        )
    return entries


def jan_1_local() -> datetime:
    """Local midnight on 1 January of the current year: the reset the
    year-to-date sensors report, and where the backfills start."""
    return dt_util.now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
