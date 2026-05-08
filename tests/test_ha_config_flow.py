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

"""HA-driven config-flow and OptionsFlow tests.

Cover the postcode → utility resolve, the manual fallback, and the
OptionsFlow round-trip. The OptionsFlow tests pin the round-3
commune-preservation fix: if the live commune list cannot be fetched
the previously-saved commune option survives the save instead of
being silently wiped back to the operator-wide default.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.be_water_prices.const import (
    CONF_COMMUNE,
    CONF_COMMUNE_LABEL,
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_PERSONS,
    CONF_POSTCODE,
    CONF_SOCIAL_TARIFF,
    CONF_UTILITY,
    DOMAIN,
)
from custom_components.be_water_prices.providers.base import CommuneOption


@pytest.mark.asyncio
async def test_postcode_resolves_to_vivaqua_for_brussels(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTCODE: "1000"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "options"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONSUMPTION_M3_PER_YEAR: 100}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "VIVAQUA"
    assert result["data"] == {CONF_UTILITY: "vivaqua"}
    assert result["options"] == {CONF_CONSUMPTION_M3_PER_YEAR: 100}


@pytest.mark.asyncio
async def test_unknown_postcode_falls_through_to_manual(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTCODE: "99999"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "manual"


@pytest.mark.asyncio
async def test_initial_flow_persists_commune_label_for_per_commune_utility(
    hass: HomeAssistant,
) -> None:
    """Picking a commune during initial setup must store the human label
    alongside the opaque id so the very first publication_label and
    diagnostics dump read "Gent (Centrum)" rather than the bare numeric id.
    The OptionsFlow path is covered separately below.
    """
    fake_communes = (
        CommuneOption(id="25071", label="9000 - Gent (Centrum)"),
        CommuneOption(id="44021", label="9000 - Gent (Mariakerke)"),
    )
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=fake_communes,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "9000"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_CONSUMPTION_M3_PER_YEAR: 100,
                CONF_PERSONS: 2,
                CONF_SOCIAL_TARIFF: False,
                CONF_COMMUNE: "25071",
            },
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_UTILITY: "farys"}
    assert result["options"][CONF_COMMUNE] == "25071"
    assert result["options"][CONF_COMMUNE_LABEL] == "9000 - Gent (Centrum)"


@pytest.mark.asyncio
async def test_options_flow_persists_commune_label_alongside_id(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80, CONF_PERSONS: 2, CONF_SOCIAL_TARIFF: False},
        unique_id=f"{DOMAIN}_farys",
    )
    entry.add_to_hass(hass)

    fake_communes = (
        CommuneOption(id="25071", label="9000 - Gent (Centrum)"),
        CommuneOption(id="44021", label="9000 - Gent (Mariakerke)"),
    )
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=fake_communes,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == data_entry_flow.FlowResultType.FORM

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_CONSUMPTION_M3_PER_YEAR: 90,
                CONF_PERSONS: 2,
                CONF_SOCIAL_TARIFF: False,
                CONF_COMMUNE: "25071",
            },
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_COMMUNE] == "25071"
    assert entry.options[CONF_COMMUNE_LABEL] == "9000 - Gent (Centrum)"


@pytest.mark.asyncio
async def test_options_flow_preserves_commune_when_list_fetch_fails(hass: HomeAssistant) -> None:
    """Round-3 F4: a transient commune-list fetch failure must not silently
    wipe the user's previously-saved commune.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 80,
            CONF_PERSONS: 2,
            CONF_SOCIAL_TARIFF: False,
            CONF_COMMUNE: "25071",
            CONF_COMMUNE_LABEL: "9000 - Gent (Centrum)",
        },
        unique_id=f"{DOMAIN}_farys",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=(),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        # Commune dropdown is absent; user submits the rest of the form.
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_CONSUMPTION_M3_PER_YEAR: 90,
                CONF_PERSONS: 2,
                CONF_SOCIAL_TARIFF: False,
            },
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_COMMUNE] == "25071"
    assert entry.options[CONF_COMMUNE_LABEL] == "9000 - Gent (Centrum)"
    # And the consumption update DID land.
    assert entry.options[CONF_CONSUMPTION_M3_PER_YEAR] == 90


@pytest.mark.asyncio
async def test_options_flow_clears_commune_when_user_explicitly_deselects(
    hass: HomeAssistant,
) -> None:
    """Distinguishes the preservation case (#R3-F4) from an explicit clear:
    when the dropdown is shown but the user submits without picking, the
    commune option is intentionally dropped.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 80,
            CONF_PERSONS: 2,
            CONF_SOCIAL_TARIFF: False,
            CONF_COMMUNE: "25071",
            CONF_COMMUNE_LABEL: "9000 - Gent (Centrum)",
        },
        unique_id=f"{DOMAIN}_farys",
    )
    entry.add_to_hass(hass)

    fake_communes = (CommuneOption(id="25071", label="9000 - Gent (Centrum)"),)
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=fake_communes,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_CONSUMPTION_M3_PER_YEAR: 80,
                CONF_PERSONS: 2,
                CONF_SOCIAL_TARIFF: False,
                # No CONF_COMMUNE -> explicit clear (vol.Optional omits the key).
            },
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert CONF_COMMUNE not in entry.options
    assert CONF_COMMUNE_LABEL not in entry.options


@pytest.mark.asyncio
async def test_reconfigure_flow_swaps_utility_and_clears_commune(
    hass: HomeAssistant,
) -> None:
    """Reconfigure with a postcode that resolves to a different utility:
    entry.data[CONF_UTILITY] flips, options carry over, but the
    operator-specific commune fields are dropped because Farys's numeric
    IDs mean nothing to VIVAQUA.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_PERSONS: 2,
            CONF_SOCIAL_TARIFF: False,
            CONF_COMMUNE: "25071",
            CONF_COMMUNE_LABEL: "9000 - Gent (Centrum)",
        },
        unique_id=f"{DOMAIN}_farys",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_POSTCODE: "1000"},  # Brussels → VIVAQUA
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    assert entry.data[CONF_UTILITY] == "vivaqua"
    assert entry.unique_id == f"{DOMAIN}_vivaqua"
    assert entry.title == "VIVAQUA"
    assert CONF_COMMUNE not in entry.options
    assert CONF_COMMUNE_LABEL not in entry.options
    # Non-commune options preserved verbatim.
    assert entry.options[CONF_CONSUMPTION_M3_PER_YEAR] == 90
    assert entry.options[CONF_PERSONS] == 2
    assert entry.options[CONF_SOCIAL_TARIFF] is False


@pytest.mark.asyncio
async def test_reconfigure_flow_keeps_commune_when_utility_unchanged(
    hass: HomeAssistant,
) -> None:
    """Reconfigure with a postcode that resolves to the same utility:
    options (including the commune) carry over untouched. The reload
    still happens so the user gets a fresh fetch.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_PERSONS: 2,
            CONF_SOCIAL_TARIFF: False,
            CONF_COMMUNE: "25071",
            CONF_COMMUNE_LABEL: "9000 - Gent (Centrum)",
        },
        unique_id=f"{DOMAIN}_farys",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_POSTCODE: "9000"},  # also Farys
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    assert entry.data[CONF_UTILITY] == "farys"
    assert entry.options[CONF_COMMUNE] == "25071"
    assert entry.options[CONF_COMMUNE_LABEL] == "9000 - Gent (Centrum)"


@pytest.mark.asyncio
async def test_reconfigure_flow_falls_through_to_manual_picker(
    hass: HomeAssistant,
) -> None:
    """An unresolved postcode goes to the reconfigure-manual step; the
    user picks a utility manually and the entry is rewritten.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTCODE: "99999"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reconfigure_manual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UTILITY: "swde"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_UTILITY] == "swde"


@pytest.mark.asyncio
async def test_reconfigure_flow_aborts_when_target_utility_already_configured(
    hass: HomeAssistant,
) -> None:
    """If the user has separate entries for two utilities and tries to
    reconfigure one toward the other's utility, abort rather than
    end up with two entries sharing the same unique_id.
    """
    vivaqua = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    vivaqua.add_to_hass(hass)
    farys = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_farys",
    )
    farys.add_to_hass(hass)

    result = await farys.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_POSTCODE: "1000"},  # already-configured VIVAQUA
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # Original Farys entry untouched.
    assert farys.data[CONF_UTILITY] == "farys"
