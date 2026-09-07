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
    # Postcode is persisted into options so future resolver-coverage
    # migrations can auto-correct the utility (v2 schema).
    assert result["options"] == {CONF_CONSUMPTION_M3_PER_YEAR: 100, CONF_POSTCODE: "1000"}


@pytest.mark.asyncio
async def test_split_postcode_lands_on_choose_step(hass: HomeAssistant) -> None:
    """A street-level split (e.g. 8400 Oostende) cannot be resolved
    from postcode alone. The flow shows a small chooser with just the
    candidate operators rather than the full 16-utility dropdown.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTCODE: "8400"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "choose"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UTILITY: "de_watergroep"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "options"


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
async def test_options_flow_preserves_stored_postcode(hass: HomeAssistant) -> None:
    """An options save must not wipe the postcode persisted at setup.

    CONF_POSTCODE is not part of the options form, so it has to be
    reattached explicitly or it is lost on the first save.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80, CONF_POSTCODE: "1000"},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_CONSUMPTION_M3_PER_YEAR: 120},
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_CONSUMPTION_M3_PER_YEAR] == 120
    assert entry.options[CONF_POSTCODE] == "1000"


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
async def test_options_flow_preserves_commune_on_render_fail_then_submit_success(
    hass: HomeAssistant,
) -> None:
    """A commune list that fails at render but would succeed at submit must
    not let the absent field read as a deselect that wipes the commune.

    The flow reuses the render-time (empty) result for the whole flow, so
    ``_async_communes`` is consulted once and the second (success) result
    is never reached.
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
        side_effect=[(), fake_communes],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
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
async def test_reconfigure_drops_social_tariff_on_flemish_to_brussels_swap(
    hass: HomeAssistant,
) -> None:
    """social_tariff is a Flanders-only 80% reduction; the OptionsFlow
    hides the field for Brussels / Wallonia entries. A stale True
    carried over from a previous Flemish operator would be silently
    ignored by pricing.py while still appearing in entry.options --
    misleading users into believing the discount is applied. The
    reconfigure must strip it when the new utility isn't Flemish.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_PERSONS: 2,
            CONF_SOCIAL_TARIFF: True,
        },
        unique_id=f"{DOMAIN}_farys",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_postcode"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTCODE: "1000"}
    )  # Brussels -> VIVAQUA
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_UTILITY] == "vivaqua"
    # social_tariff is gone (silently ignored on Brussels anyway).
    assert CONF_SOCIAL_TARIFF not in entry.options
    assert entry.options[CONF_CONSUMPTION_M3_PER_YEAR] == 90


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
    assert result["type"] == data_entry_flow.FlowResultType.MENU
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_postcode"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reconfigure_postcode"

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
    # social_tariff is Flemish-only; pricing.py drops it on Brussels
    # entries, so the reconfigure scrubs it too rather than persist a
    # silently-ignored option.
    assert CONF_SOCIAL_TARIFF not in entry.options
    # Non-commune, non-Flemish-only options preserved verbatim; the
    # household size is Flemish-only and goes the way of the social tariff.
    assert entry.options[CONF_CONSUMPTION_M3_PER_YEAR] == 90
    assert CONF_PERSONS not in entry.options


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

    fake_communes = (
        CommuneOption(id="25071", label="9000 - Gent (Centrum)"),
        CommuneOption(id="44021", label="9000 - Gent (Mariakerke)"),
    )
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=fake_communes,
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_postcode"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_POSTCODE: "9000"},  # also Farys (per-commune)
        )
        # Per-commune resolution chains into the commune step.
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "reconfigure_commune"

        # The form is pre-filled with the saved commune, so submitting it
        # as-is sends that value back and it is preserved.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COMMUNE: "25071"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    assert entry.data[CONF_UTILITY] == "farys"
    assert entry.options[CONF_COMMUNE] == "25071"
    assert entry.options[CONF_COMMUNE_LABEL] == "9000 - Gent (Centrum)"


@pytest.mark.asyncio
async def test_reconfigure_flow_clearing_the_commune_drops_it(
    hass: HomeAssistant,
) -> None:
    """Clearing the pre-filled commune has to mean what the form says.

    The step offers leaving it empty to fall back to the default, and
    the old behaviour quietly kept the saved value instead -- so on a
    utility whose commune list has moved on, the entry went on feeding
    an id the operator no longer knows.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
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
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_postcode"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "9000"}
        )
        assert result["step_id"] == "reconfigure_commune"
        # Submitted with nothing selected.
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert CONF_COMMUNE not in entry.options
    assert CONF_COMMUNE_LABEL not in entry.options


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
        result["flow_id"], {"next_step_id": "reconfigure_postcode"}
    )
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
async def test_options_flow_drops_stale_commune_not_in_live_list(hass: HomeAssistant) -> None:
    """A saved commune no longer offered by the operator must not be
    prefilled as an unselectable value, and is dropped on save.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 80,
            CONF_PERSONS: 2,
            CONF_SOCIAL_TARIFF: False,
            CONF_COMMUNE: "99999",
            CONF_COMMUNE_LABEL: "Old Town (gone)",
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
        # The stale commune must NOT be suggested as the dropdown default.
        commune_keys = [k for k in result["data_schema"].schema if k == CONF_COMMUNE]
        assert commune_keys
        assert (commune_keys[0].description or {}).get("suggested_value") != "99999"
        # Submitting without re-selecting drops the stale commune.
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_CONSUMPTION_M3_PER_YEAR: 90, CONF_PERSONS: 2, CONF_SOCIAL_TARIFF: False},
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert CONF_COMMUNE not in entry.options
    assert CONF_COMMUNE_LABEL not in entry.options


@pytest.mark.asyncio
async def test_options_flow_caches_commune_list_across_render_and_submit(
    hass: HomeAssistant,
) -> None:
    """The OptionsFlow has its own commune-list cache (parallel to the
    ConfigFlow cache). Pin call_count == 1 across render+submit so a
    future refactor that drops the cache (or moves the dict scope)
    surfaces in CI rather than silently re-introducing the transient-
    failure silent-drop on options edits.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80, CONF_PERSONS: 2, CONF_SOCIAL_TARIFF: False},
        unique_id=f"{DOMAIN}_farys",
    )
    entry.add_to_hass(hass)

    fake_communes = (CommuneOption(id="25071", label="9000 - Gent (Centrum)"),)
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=fake_communes,
    ) as mock:
        result = await hass.config_entries.options.async_init(entry.entry_id)
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
    assert mock.call_count == 1, f"expected 1 _async_communes call, got {mock.call_count}"


@pytest.mark.asyncio
async def test_initial_flow_caches_commune_list_across_render_and_submit(
    hass: HomeAssistant,
) -> None:
    """The commune list is fetched once per flow instance, not on every
    step entry. Without the cache, _async_communes is called both on
    form-render and form-submit; a transient failure on the second call
    silently drops the user's commune pick.
    """
    fake_communes = (CommuneOption(id="25071", label="9000 - Gent (Centrum)"),)
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=fake_communes,
    ) as mock:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "9000"}
        )
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
    assert mock.call_count == 1, f"expected 1 _async_communes call, got {mock.call_count}"


@pytest.mark.asyncio
async def test_migrate_v1_entry_drops_phantom_farys_commune(hass: HomeAssistant) -> None:
    """A v1 entry that picked one of the 23 phantom Farys commune ids
    (Farys lists them but doesn't actually serve them; coordinator
    crashed every refresh) must be migrated to v2 with the bad
    commune dropped, so the integration loads on Farys's
    operator-wide default until the user reconfigures.
    """
    from custom_components.be_water_prices import (
        _drop_phantom_commune_if_blocked,
        async_migrate_entry,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_COMMUNE: "25126",  # 1500 - Halle (Halle) -- phantom
            CONF_COMMUNE_LABEL: "1500 - Halle (Halle)",
        },
        unique_id=f"{DOMAIN}_farys",
        version=1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    _drop_phantom_commune_if_blocked(hass, entry)
    assert entry.version == 2
    assert CONF_COMMUNE not in entry.options
    assert CONF_COMMUNE_LABEL not in entry.options
    # Non-commune options carry over.
    assert entry.options[CONF_CONSUMPTION_M3_PER_YEAR] == 90


@pytest.mark.asyncio
async def test_phantom_strip_is_rolled_back_when_the_first_fetch_fails(
    hass: HomeAssistant,
) -> None:
    """A failed first fetch must not leave the commune permanently gone.

    The strip happens before the first refresh, so a transient outage on
    setup would otherwise persist the stripped options and lose a commune
    that a later blocklist revision could have allowed again. Setup rolls
    the options back on ConfigEntryNotReady, and only that rollback keeps
    the strip retryable.
    """
    from unittest.mock import patch

    from homeassistant.config_entries import ConfigEntryState

    from custom_components.be_water_prices.providers.base import (
        ExtractorError,
        WaterExtractor,
    )

    async def _fetch_fails(_session: object) -> object:
        raise ExtractorError("HTTP 503 from upstream")

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_COMMUNE: "25126",  # 1500 - Halle (Halle) -- phantom
            CONF_COMMUNE_LABEL: "1500 - Halle (Halle)",
        },
        unique_id=f"{DOMAIN}_farys",
        version=2,
    )
    entry.add_to_hass(hass)

    fake = WaterExtractor(
        id="farys",
        label="Farys",
        region="flanders",
        fetch=_fetch_fails,  # type: ignore[arg-type]
    )
    with patch("custom_components.be_water_prices.coordinator.get", return_value=fake):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    # The strip was undone, so the next attempt starts from the same place.
    assert entry.options[CONF_COMMUNE] == "25126"
    assert entry.options[CONF_COMMUNE_LABEL] == "1500 - Halle (Halle)"


@pytest.mark.asyncio
async def test_the_phantom_warning_is_said_once_when_the_drop_sticks(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Retries put the commune back and said it again each time, naming the entry."""
    import logging
    from unittest.mock import patch

    from homeassistant.config_entries import ConfigEntryState

    from custom_components.be_water_prices.providers.base import (
        ExtractorError,
        WaterExtractor,
        WaterTariff,
    )
    from tests.test_ha_coordinator import _fresh_tariff

    outage = True

    async def _fetch(_session: object) -> WaterTariff:
        if outage:
            raise ExtractorError("HTTP 503 from upstream")
        return _fresh_tariff()

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Water at the Halle house",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_COMMUNE: "25126",  # 1500 - Halle (Halle) -- phantom
            CONF_COMMUNE_LABEL: "1500 - Halle (Halle)",
        },
        unique_id=f"{DOMAIN}_farys",
        version=2,
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(id="farys", label="Farys", region="flanders", fetch=_fetch)
    caplog.set_level(logging.WARNING)
    with patch("custom_components.be_water_prices.coordinator.get", return_value=fake):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()
        # Two retries, as Home Assistant's backoff runs them, then the outage ends.
        for _ in range(2):
            entry.async_cancel_retry_setup()
            async with entry.setup_lock:
                await entry.async_setup(hass)
            await hass.async_block_till_done()
        outage = False
        entry.async_cancel_retry_setup()
        async with entry.setup_lock:
            await entry.async_setup(hass)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert CONF_COMMUNE not in entry.options
    lines = [r.getMessage() for r in caplog.records if "no longer serves" in r.getMessage()]
    assert len(lines) == 1
    assert lines[0].startswith("Farys:")
    assert "Halle" not in lines[0]


@pytest.mark.asyncio
async def test_reconfigure_commune_drops_stale_saved_when_no_longer_in_list(
    hass: HomeAssistant,
) -> None:
    """A v2 entry whose saved commune has been filtered out of the
    live list (phantom blocklist addition, operator renumber) must
    drop the stale id on the next reconfigure -- even when the user
    submits the form without picking a replacement. Otherwise the
    suggested-value preservation path keeps writing the bad commune
    forward and the coordinator keeps crashing.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_COMMUNE: "999999",  # not in the fresh fake_communes
            CONF_COMMUNE_LABEL: "9999 - Stale (Old)",
        },
        unique_id=f"{DOMAIN}_farys",
    )
    entry.add_to_hass(hass)

    # Live list does NOT contain "999999" -- simulates phantom blocklist
    # addition after the entry was created.
    fake_communes = (CommuneOption(id="25071", label="9000 - Gent (Centrum)"),)
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=fake_communes,
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_postcode"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_POSTCODE: "9000"},  # resolves to Farys
        )
        assert result["step_id"] == "reconfigure_commune"
        # User submits unchanged.
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    # Stale commune is dropped, not preserved.
    assert CONF_COMMUNE not in entry.options
    assert CONF_COMMUNE_LABEL not in entry.options


@pytest.mark.asyncio
async def test_phantom_sweep_runs_on_v2_entries_too(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A v2 entry that picked a commune before it was flagged as a
    phantom (later blocklist addition) must still get cleaned on
    subsequent loads -- the sweep is not gated on the schema version.
    """
    import logging

    from custom_components.be_water_prices import _drop_phantom_commune_if_blocked

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_COMMUNE: "25126",  # phantom (1500 - Halle)
            CONF_COMMUNE_LABEL: "1500 - Halle (Halle)",
        },
        unique_id=f"{DOMAIN}_farys",
        version=2,
    )
    entry.add_to_hass(hass)

    with caplog.at_level(logging.WARNING):
        _drop_phantom_commune_if_blocked(hass, entry)
    assert entry.version == 2  # untouched
    assert CONF_COMMUNE not in entry.options
    assert CONF_COMMUNE_LABEL not in entry.options
    # The sweep says nothing itself: setup speaks once the drop sticks, and
    # a setup retry would put the commune back first.
    assert "no longer serves" not in caplog.text


@pytest.mark.asyncio
async def test_migrate_v1_entry_leaves_valid_commune_alone(hass: HomeAssistant) -> None:
    """A v1 entry whose saved commune is real (not on the phantom
    blocklist) must be migrated to v2 with no option changes.
    """
    from custom_components.be_water_prices import (
        async_migrate_entry,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_COMMUNE: "25071",  # 9000 - Gent (Centrum) -- valid
            CONF_COMMUNE_LABEL: "9000 - Gent (Centrum)",
        },
        unique_id=f"{DOMAIN}_farys",
        version=1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 2
    assert entry.options[CONF_COMMUNE] == "25071"
    assert entry.options[CONF_COMMUNE_LABEL] == "9000 - Gent (Centrum)"


@pytest.mark.asyncio
async def test_migrate_v1_entry_with_de_watergroep_commune_carries_over(
    hass: HomeAssistant,
) -> None:
    """A v1 DWG entry with a saved commune migrates to v2 untouched:
    the phantom blocklist only covers Farys and Pidpa, so DWG entries
    fall through phantom_by_utility.get(...) to the default empty
    frozenset. Pin this so a future tightening of the migrator (e.g.
    adding a DWG-specific check) is an explicit, tested change rather
    than a silent regression for every existing DWG user.
    """
    from custom_components.be_water_prices import (
        async_migrate_entry,
    )

    dwg_commune_guid = "{B16A143A-49E6-4CE5-A241-1AA09BFC406A}"  # Halle, default
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="De Watergroep",
        data={CONF_UTILITY: "de_watergroep"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_COMMUNE: dwg_commune_guid,
            CONF_COMMUNE_LABEL: "Halle (DWG-served default)",
        },
        unique_id=f"{DOMAIN}_de_watergroep",
        version=1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 2
    assert entry.options[CONF_COMMUNE] == dwg_commune_guid
    assert entry.options[CONF_COMMUNE_LABEL] == "Halle (DWG-served default)"


@pytest.mark.asyncio
async def test_migrate_v1_entry_without_commune_succeeds(hass: HomeAssistant) -> None:
    """A v1 entry that never picked a commune must migrate to v2
    cleanly: the `commune is not None` guard inside the migrator only
    has the truthy branch exercised today; this pins the no-commune
    case so a refactor of the guard expression can't silently change
    behaviour for entries without a commune.
    """
    from custom_components.be_water_prices import (
        async_migrate_entry,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
        version=1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 2
    assert CONF_COMMUNE not in entry.options
    assert entry.options[CONF_CONSUMPTION_M3_PER_YEAR] == 80


@pytest.mark.asyncio
async def test_migrate_v1_entry_drops_phantom_pidpa_antwerpen(hass: HomeAssistant) -> None:
    """Pidpa's sitemap listed 'antwerpen' as a slug but it has no
    tariff page (Water-link territory). v1 entries on that slug get
    cleaned up on migration.
    """
    from custom_components.be_water_prices import (
        _drop_phantom_commune_if_blocked,
        async_migrate_entry,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pidpa",
        data={CONF_UTILITY: "pidpa"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 90, CONF_COMMUNE: "antwerpen"},
        unique_id=f"{DOMAIN}_pidpa",
        version=1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    _drop_phantom_commune_if_blocked(hass, entry)
    assert entry.version == 2
    assert CONF_COMMUNE not in entry.options


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
        result["flow_id"], {"next_step_id": "reconfigure_postcode"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_POSTCODE: "1000"},  # already-configured VIVAQUA
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # Original Farys entry untouched.
    assert farys.data[CONF_UTILITY] == "farys"


@pytest.mark.asyncio
async def test_reconfigure_flow_postcode_swap_to_per_commune_chains_into_commune_step(
    hass: HomeAssistant,
) -> None:
    """Postcode resolves to a different per-commune utility -> commune
    step shows. Commune-tied options are cleared before the new commune
    is layered on, so the entry lands on a fresh commune-precise tariff.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 90},
        unique_id=f"{DOMAIN}_vivaqua",
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
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_postcode"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_POSTCODE: "9000"},  # Farys (per-commune)
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "reconfigure_commune"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COMMUNE: "44021"}
        )
        # A move into Flanders asks for the household next.
        assert result["step_id"] == "reconfigure_household"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PERSONS: 1, CONF_SOCIAL_TARIFF: False}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_UTILITY] == "farys"
    assert entry.options[CONF_COMMUNE] == "44021"
    assert entry.options[CONF_COMMUNE_LABEL] == "9000 - Gent (Mariakerke)"
    assert entry.options[CONF_CONSUMPTION_M3_PER_YEAR] == 90


@pytest.mark.asyncio
async def test_reconfigure_flow_manual_picker_per_commune_chains_into_commune_step(
    hass: HomeAssistant,
) -> None:
    """Per-commune manual pick (Water-link / DWG / Farys / Pidpa) chains
    into a commune dropdown so the user lands on a commune-precise
    tariff without needing a follow-up OptionsFlow.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pidpa",
        data={CONF_UTILITY: "pidpa"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_COMMUNE: "boechout",
            CONF_COMMUNE_LABEL: "Boechout",
        },
        unique_id=f"{DOMAIN}_pidpa",
    )
    entry.add_to_hass(hass)

    fake_communes = (
        CommuneOption(id="antwerpen", label="Antwerpen"),
        CommuneOption(id="mortsel", label="Mortsel"),
    )
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=fake_communes,
    ):
        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] == data_entry_flow.FlowResultType.MENU
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_manual"}
        )
        assert result["step_id"] == "reconfigure_manual"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_UTILITY: "water_link"}
        )
        # Per-commune utility -> commune step, not abort.
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "reconfigure_commune"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COMMUNE: "mortsel"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    assert entry.data[CONF_UTILITY] == "water_link"
    assert entry.unique_id == f"{DOMAIN}_water_link"
    assert entry.options[CONF_COMMUNE] == "mortsel"
    assert entry.options[CONF_COMMUNE_LABEL] == "Mortsel"
    assert entry.options[CONF_CONSUMPTION_M3_PER_YEAR] == 90


@pytest.mark.asyncio
async def test_reconfigure_flow_manual_picker_non_per_commune_skips_commune_step(
    hass: HomeAssistant,
) -> None:
    """Picking a non-per-commune operator (VIVAQUA / SWDE / Aquaduin /
    AGSO / Walloons) finishes immediately -- no commune step.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pidpa",
        data={CONF_UTILITY: "pidpa"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 90},
        unique_id=f"{DOMAIN}_pidpa",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_UTILITY: "vivaqua"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_UTILITY] == "vivaqua"


@pytest.mark.asyncio
async def test_reconfigure_flow_manual_picker_skips_commune_step_when_list_unavailable(
    hass: HomeAssistant,
) -> None:
    """When the live commune list cannot be fetched (transient network /
    parser failure), the commune step is silently skipped and the entry
    loads on the operator-wide default. User can pick a commune later
    via OptionsFlow.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pidpa",
        data={CONF_UTILITY: "pidpa"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 90},
        unique_id=f"{DOMAIN}_pidpa",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=(),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_manual"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_UTILITY: "water_link"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_UTILITY] == "water_link"
    assert CONF_COMMUNE not in entry.options


@pytest.mark.asyncio
async def test_reconfigure_flow_postcode_split_lands_on_choose_step(
    hass: HomeAssistant,
) -> None:
    """The reconfigure flow's postcode path lands on the chooser when
    the new postcode is a split. Pick one of the candidates and the
    entry is rewritten in place.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 90},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_postcode"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_POSTCODE: "8400"},  # split between DWG and Farys
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reconfigure_choose"

    # Picking a non-per-commune option short-circuits the commune step.
    # Use Farys (per-commune) to confirm the chain still routes correctly:
    # split chooser -> commune picker -> finish.
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=(CommuneOption(id="x", label="8400 - Mariakerke (Oostende)"),),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_UTILITY: "farys"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "reconfigure_commune"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COMMUNE: "x"}
        )
        assert result["step_id"] == "reconfigure_household"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PERSONS: 1, CONF_SOCIAL_TARIFF: False}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_UTILITY] == "farys"


@pytest.mark.asyncio
async def test_reconfigure_flow_manual_picker_per_commune_blank_commune(
    hass: HomeAssistant,
) -> None:
    """User submits the commune step without picking -- entry loads on
    the operator-wide default rather than the old utility's commune.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pidpa",
        data={CONF_UTILITY: "pidpa"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_COMMUNE: "boechout",
            CONF_COMMUNE_LABEL: "Boechout",
        },
        unique_id=f"{DOMAIN}_pidpa",
    )
    entry.add_to_hass(hass)

    fake_communes = (CommuneOption(id="antwerpen", label="Antwerpen"),)
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=fake_communes,
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_manual"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_UTILITY: "water_link"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {},  # no CONF_COMMUNE -> use default
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert entry.data[CONF_UTILITY] == "water_link"
    assert CONF_COMMUNE not in entry.options
    assert CONF_COMMUNE_LABEL not in entry.options


@pytest.mark.asyncio
async def test_failed_first_refresh_leaves_no_meter_listener(hass: HomeAssistant) -> None:
    """A setup that fails after the first refresh must not leak a listener.

    The first refresh resolves the meter and subscribes to it, so any
    failure between that point and the teardown registration leaves a live
    state_changed listener behind on every retry.
    """
    from datetime import date
    from unittest.mock import AsyncMock, patch

    from homeassistant.config_entries import ConfigEntryState

    from custom_components.be_water_prices.const import CONF_WATER_METER_SENSOR
    from custom_components.be_water_prices.providers.base import WaterExtractor, WaterTariff

    def _vivaqua_tariff() -> WaterTariff:
        return WaterTariff(
            utility="vivaqua",
            region="brussels",
            valid_from=date(2026, 1, 1),
            valid_until=date(2030, 12, 31),
            publication_label="VIVAQUA test",
            source_url="https://example.invalid/",
            yearly_fixed_fee=40.0,
            linear_eur_per_m3=2.5,
            sanering_gemeentelijk_eur_per_m3=2.5,
        )

    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch_ok(_session: object) -> object:
        return _vivaqua_tariff()

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 80,
            CONF_WATER_METER_SENSOR: "sensor.water_meter",
        },
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(
        id="vivaqua",
        label="VIVAQUA",
        region="brussels",
        fetch=_fetch_ok,  # type: ignore[arg-type]
    )
    before = hass.bus.async_listeners().get("state_changed", 0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
        # The refresh succeeds and subscribes; platform setup then fails.
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=RuntimeError("platform setup blew up"),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_ERROR

    assert hass.bus.async_listeners().get("state_changed", 0) == before


@pytest.mark.asyncio
async def test_a_failed_setup_leaves_no_coordinator_and_no_service(hass: HomeAssistant) -> None:
    """Home Assistant skips async_unload_entry for an entry that never loaded."""
    from datetime import date
    from typing import Any
    from unittest.mock import AsyncMock, patch

    from homeassistant.config_entries import ConfigEntryState

    from custom_components.be_water_prices.providers.base import WaterExtractor, WaterTariff
    from custom_components.be_water_prices.statistics import SERVICE_BACKFILL_PRICES

    async def _fetch(_session: Any) -> WaterTariff:
        return WaterTariff(
            utility="vivaqua",
            region="brussels",
            valid_from=date(2026, 1, 1),
            valid_until=date(2026, 12, 31),
            publication_label="VIVAQUA 2026",
            source_url="https://example.invalid/",
            yearly_fixed_fee=40.0,
            linear_eur_per_m3=2.0,
        )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=RuntimeError("boom")
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert not hass.services.has_service(DOMAIN, SERVICE_BACKFILL_PRICES)


@pytest.mark.asyncio
async def test_reconfiguring_an_entry_whose_setup_failed_reloads_it(hass: HomeAssistant) -> None:
    """No update listener exists for an entry that never loaded, so nothing reloaded it."""
    from unittest.mock import AsyncMock, patch

    from homeassistant.config_entries import ConfigEntryState

    from custom_components.be_water_prices.providers.base import WaterExtractor, WaterTariff
    from tests.test_ha_coordinator import _fresh_tariff

    async def _fetch(_session: object) -> WaterTariff:
        return _fresh_tariff()

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_farys",
        version=2,
    )
    entry.add_to_hass(hass)
    patches = (
        patch(
            "custom_components.be_water_prices.coordinator.get",
            return_value=WaterExtractor(id="x", label="X", region="brussels", fetch=_fetch),
        ),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=None),
        ),
    )
    with (
        patches[0],
        patches[1],
        patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=RuntimeError("boom")
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR

    with patches[0], patches[1]:
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_postcode"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "1000"}
        )
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert entry.data[CONF_UTILITY] == "vivaqua"
        assert entry.state is ConfigEntryState.LOADED
        await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_a_reconfigure_dialog_outliving_the_entry_aborts_cleanly(hass: HomeAssistant) -> None:
    """The next submit after a removal raised UnknownEntry out of the flow."""
    from unittest.mock import patch

    from custom_components.be_water_prices.providers.base import CommuneOption

    for postcode in ("1000", "9000"):
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Farys",
            data={CONF_UTILITY: "farys"},
            options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
            unique_id=f"{DOMAIN}_farys",
            version=2,
        )
        entry.add_to_hass(hass)
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_postcode"}
        )
        flow_id = result["flow_id"]
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        # A postcode that finishes at once, and one that renders the commune step.
        with patch(
            "custom_components.be_water_prices.config_flow._async_communes",
            return_value=(CommuneOption(id="25071", label="9000 - Gent"),),
        ):
            result = await hass.config_entries.flow.async_configure(
                flow_id, {CONF_POSTCODE: postcode}
            )
        assert result["type"] == data_entry_flow.FlowResultType.ABORT
        assert result["reason"] == "entry_removed"


@pytest.mark.asyncio
async def test_a_move_into_flanders_asks_for_the_household(hass: HomeAssistant) -> None:
    """The Flemish tariff prices on residents and social tariff; a Brussels entry has neither."""
    from unittest.mock import patch

    from custom_components.be_water_prices.const import CONF_PERSONS, CONF_SOCIAL_TARIFF
    from custom_components.be_water_prices.providers.base import CommuneOption

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 120},
        unique_id=f"{DOMAIN}_vivaqua",
        version=2,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=(CommuneOption(id="25071", label="9000 - Gent"),),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_postcode"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "9000"}
        )
        assert result["step_id"] == "reconfigure_commune"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COMMUNE: "25071"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "reconfigure_household"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PERSONS: 3, CONF_SOCIAL_TARIFF: True}
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_UTILITY] == "farys"
    assert entry.options[CONF_PERSONS] == 3
    assert entry.options[CONF_SOCIAL_TARIFF] is True
    assert entry.options[CONF_COMMUNE] == "25071"


@pytest.mark.asyncio
async def test_the_flow_side_stale_commune_drops_are_said(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Both drops moved the bill to the operator-wide default in silence."""
    import logging
    from unittest.mock import patch

    from custom_components.be_water_prices.const import CONF_PERSONS, CONF_SOCIAL_TARIFF
    from custom_components.be_water_prices.providers.base import CommuneOption

    caplog.set_level(logging.WARNING)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Water at Old Town",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 80,
            CONF_PERSONS: 2,
            CONF_SOCIAL_TARIFF: False,
            CONF_COMMUNE: "99999",
            CONF_COMMUNE_LABEL: "Old Town (gone)",
        },
        unique_id=f"{DOMAIN}_farys",
        version=2,
    )
    entry.add_to_hass(hass)
    live = (CommuneOption(id="25071", label="9000 - Gent (Centrum)"),)
    communes = "custom_components.be_water_prices.config_flow._async_communes"

    def _said() -> list[str]:
        return [
            r.getMessage() for r in caplog.records if "no longer in the operator" in r.getMessage()
        ]

    with patch(communes, return_value=live):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_CONSUMPTION_M3_PER_YEAR: 80, CONF_PERSONS: 2, CONF_SOCIAL_TARIFF: False},
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert CONF_COMMUNE not in entry.options
    assert len(_said()) == 1

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_COMMUNE: "99999", CONF_COMMUNE_LABEL: "Old Town"}
    )
    with patch(communes, return_value=live):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_postcode"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "9000"}
        )
        assert result["step_id"] == "reconfigure_commune"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["reason"] == "reconfigure_successful"
    assert CONF_COMMUNE not in entry.options
    lines = _said()
    assert len(lines) == 2
    assert all(line.startswith("Farys:") for line in lines)
    assert "Old Town" not in caplog.text and "99999" not in caplog.text


@pytest.mark.asyncio
async def test_a_step_reached_out_of_order_aborts(hass: HomeAssistant) -> None:
    """None of the five invalid_flow_state aborts was exercised; the choose-step one survived deletion."""
    from custom_components.be_water_prices.config_flow import BeWaterPricesConfigFlow

    flow = BeWaterPricesConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.context = {}
    steps = (
        flow.async_step_choose({CONF_UTILITY: "farys"}),
        flow.async_step_options({CONF_CONSUMPTION_M3_PER_YEAR: 80}),
        flow.async_step_reconfigure_choose({CONF_UTILITY: "farys"}),
        flow.async_step_reconfigure_commune({CONF_COMMUNE: "x"}),
        flow._async_finish_reconfigure(),
    )
    for step in steps:
        result = await step
        assert result["type"] == data_entry_flow.FlowResultType.ABORT
        assert result["reason"] == "invalid_flow_state"


@pytest.mark.asyncio
async def test_reconfiguring_an_entry_that_never_loaded_schedules_no_reload(
    hass: HomeAssistant,
) -> None:
    """Reloading every non-loaded entry set the real extractor running in these tests."""
    from unittest.mock import patch

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_farys",
        version=2,
    )
    entry.add_to_hass(hass)
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_postcode"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "1000"}
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_UTILITY] == "vivaqua"
    reload.assert_not_called()


@pytest.mark.asyncio
async def test_a_move_onto_a_utility_already_configured_aborts_before_the_household_form(
    hass: HomeAssistant,
) -> None:
    from unittest.mock import patch

    from custom_components.be_water_prices.providers.base import CommuneOption

    MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_farys",
        version=2,
    ).add_to_hass(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 120},
        unique_id=f"{DOMAIN}_vivaqua",
        version=2,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        return_value=(CommuneOption(id="25071", label="9000 - Gent"),),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_postcode"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "9000"}
        )
        assert result["step_id"] == "reconfigure_commune"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COMMUNE: "25071"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_opening_and_cancelling_the_options_dialog_drops_nothing_and_says_nothing(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The warning fired when the form rendered, three times for three cancelled dialogs."""
    import logging
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers.base import CommuneOption

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Farys",
        data={CONF_UTILITY: "farys"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 90,
            CONF_COMMUNE: "99999",
            CONF_COMMUNE_LABEL: "Gone",
        },
        unique_id=f"{DOMAIN}_farys",
        version=2,
    )
    entry.add_to_hass(hass)
    caplog.set_level(logging.WARNING)
    live = (CommuneOption(id="1", label="Gent"), CommuneOption(id="2", label="Halle"))
    with patch(
        "custom_components.be_water_prices.config_flow._async_communes",
        new=AsyncMock(return_value=live),
    ):
        for _ in range(3):
            result = await hass.config_entries.options.async_init(entry.entry_id)
            assert result["type"] == data_entry_flow.FlowResultType.FORM
            hass.config_entries.options.async_abort(result["flow_id"])
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_postcode"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "9000"}
        )
        assert result["step_id"] == "reconfigure_commune"
        hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done()
    assert entry.options[CONF_COMMUNE] == "99999"
    assert "no longer in the operator" not in caplog.text
