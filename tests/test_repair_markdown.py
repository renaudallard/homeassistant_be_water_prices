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

"""A card cannot write markdown into the stale-snapshot Repair dialog.

The fix flow's description renders as markdown, and its last_error can
quote what a card host chose.
"""

from __future__ import annotations

import re
import string
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.be_water_prices.const import DOMAIN
from custom_components.be_water_prices.providers.base import (
    ExtractorError,
    WaterTariff,
    belgian_today,
)
from custom_components.be_water_prices.repairs import async_create_fix_flow
from tests.test_ha_coordinator import _setup_entry, _this_years_card

_MARKDOWN = "[Fix now](https://evil.example/ha) ![](https://evil.example/p.png)"


async def test_last_error_reaches_the_repair_dialog_as_plain_words(hass: HomeAssistant) -> None:
    """Written as it came, a link showed in the admin's dialog and a picture
    loaded from the card host's server when the dialog opened."""
    yesterday = belgian_today() - timedelta(days=1)
    answers: list[Any] = [_this_years_card(yesterday), ExtractorError(_MARKDOWN)]

    async def fetch(_session: Any) -> WaterTariff:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, WaterTariff)
        return answer

    entry = await _setup_entry(hass, fetch)
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator.stale_issue_id)
    assert issue is not None
    flow = await async_create_fix_flow(hass, coordinator.stale_issue_id, issue.data)
    flow.hass = hass
    flow.handler = DOMAIN
    flow.issue_id = coordinator.stale_issue_id
    result = await flow.async_step_init()

    shown = result["description_placeholders"]["last_error"]
    punctuation = re.escape(string.punctuation)
    # Every punctuation character carries the backslash that makes it a
    # literal, so nothing in the text is markup, and it reads as it came.
    assert re.fullmatch(rf"(?:\\[{punctuation}]|[^{punctuation}])*", shown)
    assert re.sub(r"\\(.)", r"\1", shown) == _MARKDOWN
    assert result["description_placeholders"]["utility"] == "VIVAQUA"
    assert await hass.config_entries.async_unload(entry.entry_id)
