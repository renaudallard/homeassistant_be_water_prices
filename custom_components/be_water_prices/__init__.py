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

from typing import TYPE_CHECKING

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .coordinator import WaterCoordinator
    from .statistics import (
        async_maybe_backfill_once,
        async_register_services,
    )

    coordinator = WaterCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async_register_services(hass)
    # Auto-once price-history backfill so the History/Energy graphs are
    # not empty before the first natural day of recording. Gated on
    # entry.data["backfill_year"] -- runs once per calendar year.
    await async_maybe_backfill_once(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        from homeassistant.helpers import issue_registry as ir

        from .statistics import async_unregister_services

        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        ir.async_delete_issue(hass, DOMAIN, coordinator.stale_issue_id)
        if not hass.data[DOMAIN]:
            async_unregister_services(hass)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Fully reload the entry when options change.

    A simple ``async_request_refresh`` would re-run the coordinator but
    leave the entity set untouched. The YTD entities are only created
    when a water meter is configured, so adding or removing the meter
    in OptionsFlow needs ``async_setup_entry`` to re-run -- which only
    a full reload triggers.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate v1 entries to v2.

    Two concerns motivate the v2 schema:

      1. Some pre-v2 entries carry a ``CONF_COMMUNE`` that the operator
         silently does not service (Farys phantom dropdown entries at
         split postcodes; Pidpa's ``antwerpen`` slug -- Water-link
         territory). The runtime ``list_communes`` now filters those
         out, but existing entries still hand the stale id to
         ``fetch_for_commune`` and crash on every coordinator tick.
         The migration drops ``CONF_COMMUNE`` / ``CONF_COMMUNE_LABEL``
         when the saved id matches a known phantom blocklist.

      2. v2 entries store the user-entered postcode in
         ``CONF_POSTCODE`` so future resolver-coverage changes can be
         auto-applied. v1 entries don't carry one and stay as-is until
         the user reconfigures.
    """
    from ._phantom_blocklists import (
        FARYS_UNSERVABLE_IDS as _farys_phantom_ids,
    )
    from ._phantom_blocklists import (
        PIDPA_UNSERVABLE_SLUGS as _pidpa_phantom_slugs,
    )
    from .const import CONF_COMMUNE, CONF_COMMUNE_LABEL, CONF_UTILITY

    if entry.version > 2:
        return False
    if entry.version == 1:
        utility = entry.data.get(CONF_UTILITY, "")
        commune = entry.options.get(CONF_COMMUNE)
        phantom_by_utility: dict[str, frozenset[str]] = {
            "farys": _farys_phantom_ids,
            "pidpa": _pidpa_phantom_slugs,
        }
        new_options = dict(entry.options)
        if commune is not None and commune in phantom_by_utility.get(utility, frozenset()):
            new_options.pop(CONF_COMMUNE, None)
            new_options.pop(CONF_COMMUNE_LABEL, None)
        hass.config_entries.async_update_entry(entry, options=new_options, version=2)
    return True
