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

"""What an entry keeps across a restart: the year-to-date cycle, the meter's
days and the last good card, each in a Store of its own, and how a stored
record is read back.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .providers import WaterTariff
from .providers.base import tariff_from_dict
from .ytd_fold import _YtdCycle

# Bumped only if the persisted YTD cycle dict changes shape incompatibly.
# The minor version carries shape changes so a rollback degrades to a
# re-bootstrap instead of a failed setup; see _YtdStore.
_YTD_STORE_VERSION = 1


_YTD_STORE_MINOR_VERSION = 2


# The meter's days behind the year figures, in a Store of their own: they
# are read once a day and have nothing to do with the YTD cycle's shape.
_METERED_STORE_VERSION = 1


# The last good card and when it was fetched, in a Store of its own too, so
# a restart while the utility is down still serves it.
_CARD_STORE_VERSION = 1


class _YtdStore(Store[dict[str, Any]]):
    """Store for the YTD cycle anchor, with a place to migrate its shape.

    The shape is versioned through ``minor_version`` rather than
    ``version`` on purpose: Home Assistant calls the migration for either,
    but only re-raises the base NotImplementedError on a *major* mismatch.
    Since the load runs during entry setup with nothing catching it, a
    major bump would break setup for anyone rolling the integration back,
    while an older build meeting a newer minor simply finds no keys it
    knows and re-bootstraps from the recorder.
    """

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        if old_minor_version < 2:
            return _migrate_cycle_to_v2(old_data)
        return old_data


def _migrate_cycle_to_v2(old: dict[str, Any]) -> dict[str, Any]:
    """Fold the eight-key cycle written before v2 into the single record.

    The old shape carried two year-to-date figures that never met: the live
    one, ``live_hwm_m3 - baseline_m3``, and the recorder-served one,
    ``served_hwm_m3``, each with its own year stamp. The record holds one, so
    the migration picks the newer year and the larger figure in it, which is
    what every reader of the old shape would have published anyway.

    A mark that cannot be dated is dropped rather than carried: it could
    never be released, and it would pin the bill at an old peak in this year
    and every year after it. The default on ``cost_year`` only applies when
    the key is absent, which is a release predating it, whose mark belongs
    to the anchor's own year. Present and ``None`` is a release that knew
    about the key and had nothing to date, and keeps its ``None``.
    """
    if "m3" in old or "offset_m3" in old:
        # Already the new shape. Home Assistant re-stamps a store with its own
        # minor version after any migration, so a release rolled back over this
        # one loaded the record, failed to recognise a single key, and wrote it
        # straight back under minor 1. The label says which release touched the
        # file last, not what shape it holds, so decide by the keys that are
        # actually there. Folding a v2 payload as if it were v1 finds none of
        # the keys it looks for and empties the record.
        kept: dict[str, Any] = {
            key: old.get(key) for key in ("meter", "year", "m3", "cost", "offset_m3")
        }
        for key in ("basis", "recorder_hwm", "seen_at", "started_at", "card"):
            # Carried when it is there, and not invented when it is not.
            # Rebuilding the dict without a key dropped it on every
            # rollback-and-upgrade: the floor lost its own provenance, and
            # the year lost what the recorder had reported for it. Adding
            # one as None to a record that predates it would make this
            # migration rewrite a record it is meant to hand back
            # untouched. Anything added to the record belongs on this list.
            if key in old:
                kept[key] = old[key]
        return kept
    anchor_year = old.get("year")
    offset = old.get("baseline_m3")
    hwm = old.get("live_hwm_m3")
    live_m3 = max(0.0, hwm - offset) if offset is not None and hwm is not None else None
    mark_year = old.get("cost_year", anchor_year)
    served = old.get("served_hwm_m3")
    cost = old.get("cost_hwm")

    figures: list[tuple[int, float]] = []
    if anchor_year and live_m3 is not None:
        figures.append((anchor_year, live_m3))
    if mark_year and served is not None:
        figures.append((mark_year, served))
    cost_year = mark_year if cost is not None else None

    years = [year for year, _ in figures]
    if cost_year:
        years.append(cost_year)
    if not years:
        return {
            "meter": old.get("meter"),
            "year": None,
            "m3": None,
            "cost": None,
            "offset_m3": None,
        }
    year = max(years)
    current = [figure for stamp, figure in figures if stamp == year]
    return {
        "meter": old.get("meter"),
        "year": year,
        "m3": max(current) if current else None,
        "cost": cost if cost_year == year else None,
        "offset_m3": offset if anchor_year == year and live_m3 is not None else None,
    }


def _ytd_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """The per-entry Store holding that entry's YTD cycle anchor.

    Defined once so entry removal deletes exactly the file the coordinator
    writes rather than a second guess at the same key.
    """
    return _YtdStore(
        hass,
        _YTD_STORE_VERSION,
        f"{DOMAIN}.{entry_id}.ytd",
        minor_version=_YTD_STORE_MINOR_VERSION,
    )


async def async_remove_stores(hass: HomeAssistant, entry_id: str) -> None:
    """Delete an entry's persisted YTD cycle anchor, its meter's days and
    its last good card."""
    await _ytd_store(hass, entry_id).async_remove()
    await _metered_store(hass, entry_id).async_remove()
    await _card_store(hass, entry_id).async_remove()


def _metered_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """The per-entry Store holding the last read of the meter's days."""
    return Store(hass, _METERED_STORE_VERSION, f"{DOMAIN}.{entry_id}.metered")


def _card_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """The per-entry Store holding the last good card and its fetch time."""
    return Store(hass, _CARD_STORE_VERSION, f"{DOMAIN}.{entry_id}.card")


@dataclass(frozen=True)
class _MeteredDays:
    """Each closed day's water as the recorder held it for ``meter`` on ``read_on``."""

    meter: str
    read_on: date
    days: dict[date, float]
    # Whether the days were read as a register's, see _sees_a_register. A
    # meter whose integration comes up after this one has no state yet to
    # say it resets, so it is read as a register until it does, and a read
    # made that way is made again once it has.
    register: bool = True


def _figure(value: object) -> float | None:
    """``value`` as a finite float, or None when it is not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _cycle_from_record(data: object) -> _YtdCycle | None:
    """The persisted cycle, or None when the record does not describe one.

    The file sits in .storage where anyone can edit it. A field of the
    wrong type raised out of the fold on every setup attempt, which left
    the entry retrying for good; a record that was not a mapping failed
    the setup outright. Either is worth one fresh cycle, not the entry.
    """
    if not isinstance(data, dict):
        return None
    meter = data.get("meter")
    year = data.get("year")
    basis = data.get("basis")
    if meter is not None and not isinstance(meter, str):
        return None
    if basis is not None and not isinstance(basis, str):
        return None
    if year is not None and (isinstance(year, bool) or not isinstance(year, int)):
        return None
    figures = {
        key: data.get(key)
        for key in ("m3", "cost", "offset_m3", "recorder_hwm", "seen_at", "started_at")
    }
    if any(value is not None and _figure(value) is None for value in figures.values()):
        return None
    return _YtdCycle(
        meter=meter,
        year=year,
        m3=_figure(figures["m3"]),
        cost=_figure(figures["cost"]),
        offset_m3=_figure(figures["offset_m3"]),
        basis=basis,
        recorder_hwm=_figure(figures["recorder_hwm"]),
        # No stamp means nothing has been seen on this meter since the
        # record was written, and the honest answer to "how long ago" is
        # that we do not know. Read as no gap, so the meter earns its
        # allowance back from the first reading rather than being handed
        # one on the strength of an unknown.
        seen_at=_figure(figures["seen_at"]),
        started_at=_figure(figures["started_at"]),
    )


def _card_from_record(data: object) -> tuple[WaterTariff, str | None] | None:
    """The persisted card in force and its commune, or None when the
    record holds none it can read. A bad one costs the held card, which
    only matters while next year's card is up early."""
    if not isinstance(data, dict):
        return None
    commune = data.get("commune")
    if commune is not None and not isinstance(commune, str):
        return None
    try:
        return tariff_from_dict(data), commune
    except (KeyError, TypeError, ValueError):
        return None


def _held_from_record(data: object) -> tuple[WaterTariff, datetime, str | None] | None:
    """The persisted last good card, its fetch time and the commune it was
    fetched for, or None when the record holds none it can read."""
    if not isinstance(data, dict):
        return None
    commune = data.get("commune")
    if commune is not None and not isinstance(commune, str):
        return None
    try:
        tariff = tariff_from_dict(data["card"])
        fetched_at = datetime.fromisoformat(str(data["fetched_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    if fetched_at.tzinfo is None:
        return None
    return tariff, fetched_at, commune


def _metered_from_record(data: object) -> _MeteredDays | None:
    """The persisted read of the meter's days, or None when the record
    does not describe one. The file is in .storage where anyone can edit
    it, and a bad one costs a read, not the entry. A record written before
    the way of reading was kept is taken as a register's, so a meter that
    resets reads its days again once."""
    if not isinstance(data, dict):
        return None
    meter = data.get("meter")
    stored = data.get("days")
    register = data.get("register", True)
    if not isinstance(meter, str) or not isinstance(stored, dict):
        return None
    if not isinstance(register, bool):
        return None
    try:
        read_on = date.fromisoformat(str(data.get("read_on")))
        days = {date.fromisoformat(str(day)): _figure(m3) for day, m3 in stored.items()}
    except ValueError:
        return None
    if any(m3 is None for m3 in days.values()):
        return None
    return _MeteredDays(
        meter=meter,
        read_on=read_on,
        days={day: m3 for day, m3 in days.items() if m3 is not None},
        register=register,
    )
