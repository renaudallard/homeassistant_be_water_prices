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

"""be_water_prices integration entry point.

Module-level imports stay free of ``homeassistant`` so the package is
importable from plain unit tests (the HA loader provides the modules
when the integration runs in HA proper).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from homeassistant.exceptions import ConfigEntryNotReady

    from .const import CONF_UTILITY
    from .coordinator import WaterCoordinator
    from .providers import async_load as async_load_providers
    from .providers import get as get_extractor
    from .statistics import (
        async_maybe_backfill_once,
        async_register_services,
    )

    # Drop any phantom commune id the runtime list_communes filter
    # blocks. Runs on every setup (not only v1->v2 migration) so a
    # future blocklist addition catches users who installed at v2
    # with what later became a phantom. Stash the pre-strip options so
    # a transient first-fetch failure (ConfigEntryNotReady) does not
    # leave the entry permanently stripped of a commune that could
    # come back into the blocklist's good graces later.
    original_options = dict(entry.options)
    _drop_phantom_commune_if_blocked(hass, entry)
    options_changed = entry.options != original_options

    # Build the extractor registry off the event loop: importing the
    # provider modules pulls in pdfplumber / BeautifulSoup / aiohttp and
    # reads manifest.json, blocking work HA forbids on the loop. The
    # synchronous get() inside WaterCoordinator then hits the cache.
    await async_load_providers(hass)
    coordinator = WaterCoordinator(hass, entry)
    # Restore the persisted YTD baseline before the first refresh so a
    # restart does not re-anchor the running cost down to the recorder's
    # trailing daily total.
    await coordinator.async_load_ytd_state()
    # Own the teardown before the first refresh, not after: that refresh
    # resolves the meter and subscribes to it, so a ConfigEntryNotReady
    # here would otherwise leave a live listener behind on every retry.
    # A no-op while nothing is subscribed.
    entry.async_on_unload(coordinator.async_unsub_live_tracking)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        if options_changed:
            hass.config_entries.async_update_entry(entry, options=original_options)
        raise
    if options_changed:
        # Once it sticks, and once: said before the refresh it was repeated
        # on every retry, since the rollback above put the commune back.
        # Silently rewriting the options moved the bill to the operator-wide
        # default with nothing to tell the user why the figure changed. The
        # commune stays out of the log, and so does the entry title, which
        # users rename after their town.
        _LOGGER.warning(
            "%s: the saved commune is one the operator no longer serves; dropping it and "
            "pricing on the operator-wide default until a commune is picked again",
            get_extractor(entry.data[CONF_UTILITY]).label,
        )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    # Home Assistant runs the on_unload callbacks when setup fails further
    # down as well, and never calls async_unload_entry for an entry that
    # did not load, so this is what keeps a failed setup from leaving the
    # coordinator in the bucket and the service registered for good.
    entry.async_on_unload(lambda: _forget_coordinator(hass, entry.entry_id))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Update the running-cost / YTD sensors live on each meter reading,
    # not just on the daily tick. Subscribes to the resolved meter entity
    # and re-points itself if a later tick resolves a different one; the
    # teardown for whichever subscription is current was registered above.
    coordinator.async_setup_live_tracking()
    # Register the OptionsFlow reload listener BEFORE the backfill so
    # any backfill failure (recorder not ready, parser exception,
    # future code addition that raises) does not leave the listener
    # unregistered -- if it did, the user would see a 'loaded' entry
    # whose OptionsFlow changes silently fail to trigger a reload,
    # with no recovery short of an HA restart. The cost is one extra
    # async_reload cycle when the backfill writes the gate -- a
    # one-shot annoyance on first install / operator switch.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async_register_services(hass)
    # Auto-once price-history backfill so the History/Energy graphs
    # are not empty before the first natural day of recording. Wrapped
    # in a broad except so a backfill failure logs loudly without
    # tearing down the rest of setup.
    try:
        await async_maybe_backfill_once(hass, entry)
    except Exception:
        _LOGGER.exception("price-history backfill failed; entry continues without it")
    # After the backfill, not before: the orphan-statistics cleanup finds
    # the rows it has to consider through the entity registry, so pulling
    # the registry entry first would strand them permanently.
    from .sensor import async_remove_inapplicable_entities

    async_remove_inapplicable_entities(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        # ``hass.data[DOMAIN]`` is populated only AFTER the coordinator
        # first refresh succeeds. If async_setup_entry raised before
        # that point (transient first-fetch failure, ImportError, etc.)
        # the bucket is missing and a naive lookup crashes the unload --
        # blocking the user from removing the entry without an HA
        # restart. Tolerate both the missing bucket and the missing
        # entry_id. The on_unload callbacks that run next drop the
        # coordinator, its Repair cards and the service.
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if coordinator is not None:
            # Stop listening to the meter before the flush, not after it in
            # the on_unload callbacks: the flush awaits the executor, and a
            # meter event handled in that window scheduled a debounced save
            # on this coordinator's Store, which wrote the file back thirty
            # seconds after the entry had been removed.
            coordinator.async_unsub_live_tracking()
            # Persist a pending YTD cycle change before the coordinator is
            # dropped so a reload / restart keeps the climbing high-water
            # mark instead of reverting to the last daily-tick value.
            await coordinator.async_save_ytd_state()
    return unloaded


def _forget_coordinator(hass: HomeAssistant, entry_id: str) -> None:
    """Drop the entry's coordinator and its Repair cards, and the service
    once no entry is left.

    Runs from the entry's on_unload callbacks, which Home Assistant fires
    after a clean unload and after a setup that failed past the first
    refresh. The cards that refresh raised would otherwise outlive the
    entry, with a Retry button that has nothing left to refresh.
    """
    from homeassistant.helpers import issue_registry as ir

    from .statistics import async_unregister_services

    domain_data = hass.data.get(DOMAIN, {})
    coordinator = domain_data.pop(entry_id, None)
    if coordinator is not None:
        ir.async_delete_issue(hass, DOMAIN, coordinator.stale_issue_id)
        ir.async_delete_issue(hass, DOMAIN, coordinator.projection_issue_id)
    if not domain_data:
        async_unregister_services(hass)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the entry's persisted YTD anchor when it is removed.

    Unload alone leaves the Store file behind, so removing and re-adding the
    same utility used to restore a baseline belonging to the deleted entry.
    """
    from .coordinator import async_remove_ytd_store

    await async_remove_ytd_store(hass, entry.entry_id)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Fully reload the entry when options change.

    A simple ``async_request_refresh`` would re-run the coordinator but
    leave the entity set untouched. The YTD entities are only created
    when a water meter is configured, so adding or removing the meter
    in OptionsFlow needs ``async_setup_entry`` to re-run -- which only
    a full reload triggers.
    """
    await hass.config_entries.async_reload(entry.entry_id)


def _drop_phantom_commune_if_blocked(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop a saved CONF_COMMUNE that matches a known phantom blocklist.

    Runs on every entry load (not only on v1 schema migration) so a
    future addition to the Farys / Pidpa phantom blocklists catches
    already-v2 entries that picked the commune before the operator's
    page was flagged. Cheap dict lookup; no-op for the vast majority
    of entries.
    """
    from ._phantom_blocklists import (
        FARYS_UNSERVABLE_IDS as _farys_phantom_ids,
    )
    from ._phantom_blocklists import (
        PIDPA_UNSERVABLE_SLUGS as _pidpa_phantom_slugs,
    )
    from .const import CONF_COMMUNE, CONF_COMMUNE_LABEL, CONF_UTILITY

    utility = entry.data.get(CONF_UTILITY, "")
    commune = entry.options.get(CONF_COMMUNE)
    if commune is None:
        return
    phantom_by_utility: dict[str, frozenset[str]] = {
        "farys": _farys_phantom_ids,
        "pidpa": _pidpa_phantom_slugs,
    }
    if commune not in phantom_by_utility.get(utility, frozenset()):
        return
    # The caller says so once the drop sticks; the log is the one surface
    # this integration keeps the commune out of.
    new_options = dict(entry.options)
    new_options.pop(CONF_COMMUNE, None)
    new_options.pop(CONF_COMMUNE_LABEL, None)
    hass.config_entries.async_update_entry(entry, options=new_options)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate v1 entries to v2.

    v2 entries store the user-entered postcode in ``CONF_POSTCODE`` so
    future resolver-coverage changes can be auto-applied. v1 entries
    don't carry one and stay as-is until the user reconfigures.

    Phantom-commune dropping is deliberately NOT gated on the v1
    schema check; it lives in :func:`_drop_phantom_commune_if_blocked`
    and runs on every entry load so a blocklist addition shipped in a
    future patch release catches already-v2 entries too.
    """
    if entry.version > 2:
        return False
    if entry.version == 1:
        hass.config_entries.async_update_entry(entry, version=2)
    return True
