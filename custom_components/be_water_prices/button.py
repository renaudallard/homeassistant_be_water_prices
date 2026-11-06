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

"""A button that fetches the tariff again, whatever its age."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import (
    MIN_FETCH_INTERVAL,
    WaterConfigEntry,
    WaterCoordinator,
    utility_device_info,
)

_REFRESH = ButtonEntityDescription(
    key="refresh",
    translation_key="refresh",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WaterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([RefreshButton(entry.runtime_data)])


class RefreshButton(CoordinatorEntity[WaterCoordinator], ButtonEntity):
    """Fetch the utility's tariff again now rather than at the next daily tick."""

    _attr_has_entity_name = True
    entity_description = _REFRESH

    def __init__(self, coordinator: WaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_refresh"
        self._attr_device_info = utility_device_info(coordinator)

    @property
    def available(self) -> bool:
        # Pressing it is how an entry whose tariff cannot be read is retried.
        return True

    async def async_press(self) -> None:
        # Any user allowed to press a button can press this one, and the
        # coordinator fetches at most once in MIN_FETCH_INTERVAL. A press
        # it would fetch nothing for is refused, saying when the button
        # can fetch again, rather than looking as if it had fetched.
        if held := self.coordinator.fetch_held_for():
            ready = dt_util.utcnow() + held
            # Up to the minute, so a press at the time it gives fetches.
            shown = ready.replace(second=0, microsecond=0)
            if shown < ready:
                shown += timedelta(minutes=1)
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="refresh_pressed_recently",
                translation_placeholders={
                    "last": dt_util.as_local(ready - MIN_FETCH_INTERVAL).strftime("%H:%M"),
                    "next": dt_util.as_local(shown).strftime("%H:%M"),
                },
            )
        await self.coordinator.async_refresh()
