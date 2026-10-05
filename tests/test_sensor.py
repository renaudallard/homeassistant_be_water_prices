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

"""WaterSensor last_reset handling for the TOTAL year-to-date sensors."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.be_water_prices.const import CONF_UTILITY
from custom_components.be_water_prices.coordinator import CoordinatorData
from custom_components.be_water_prices.providers.base import WaterTariff
from custom_components.be_water_prices.sensor import (
    SENSORS,
    WaterSensor,
    _source_url_without_commune,
)

_BRUSSELS = dt_util.get_time_zone("Europe/Brussels")
# Written out rather than asked of the code under test: a reset that moved
# on the 1st of every month would otherwise agree with itself.
_JAN_1 = datetime(2026, 1, 1, tzinfo=_BRUSSELS)


@pytest.fixture
def _mid_july_in_brussels(freezer: Any) -> Iterator[None]:
    """Pin the clock and the zone the year-to-date sensors reset in."""
    zone = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(_BRUSSELS)
    freezer.move_to(datetime(2026, 7, 15, 12, tzinfo=_BRUSSELS))
    yield
    dt_util.set_default_time_zone(zone)


class _StubEntry:
    def __init__(self) -> None:
        self.entry_id = "e1"
        self.title = "VIVAQUA"
        self.data: dict[str, str] = {CONF_UTILITY: "vivaqua"}
        self.options: dict[str, str] = {}


class _StubCoordinator:
    def __init__(self) -> None:
        self.entry = _StubEntry()
        self.data = None


def _sensor(key: str) -> WaterSensor:
    return _sensor_with(key, _StubCoordinator())


def _sensor_with(key: str, coordinator: _StubCoordinator) -> WaterSensor:
    desc = next(d for d in SENSORS if d.key == key)
    return WaterSensor(coordinator, desc)  # type: ignore[arg-type]


def _publish(sensor: WaterSensor, m3: float | None, started: datetime | None = None) -> None:
    """Hand the sensor a coordinator round, as the coordinator does."""
    sensor.coordinator.data = SimpleNamespace(  # type: ignore[assignment]
        ytd_consumption_m3=m3, current_year_cost_eur=m3, ytd_started_at=started
    )
    with patch.object(WaterSensor, "async_write_ha_state"):
        sensor._handle_coordinator_update()


@pytest.mark.usefixtures("_mid_july_in_brussels")
def test_a_correction_in_the_same_year_keeps_the_reset() -> None:
    """A lower figure that still covers the year is not a new cycle.

    A recorder taking back a spike, or the social tariff rebuilding the
    bill, lowers the figure for the year since 1 January. Moving the
    reset there made Home Assistant add the whole corrected year on top
    of the old one: 437.56 + 87.51 in the long-term sum for a bill of
    87.51.
    """
    for key in ("ytd_consumption", "current_year_cost"):
        sensor = _sensor(key)
        for value in (437.56, 87.51, 88.0):
            _publish(sensor, value)
            assert sensor.last_reset == _JAN_1


@pytest.mark.usefixtures("_mid_july_in_brussels")
def test_a_restarted_year_moves_the_reset_to_its_start() -> None:
    """A confirmed swap or a different meter restarts the figure, and the
    reset follows the start the coordinator publishes for it."""
    sensor = _sensor("ytd_consumption")
    _publish(sensor, 50.0)
    assert sensor.last_reset == _JAN_1
    swap = datetime(2026, 7, 10, 8, tzinfo=_BRUSSELS)
    _publish(sensor, 0.0, started=swap)
    assert sensor.last_reset == swap
    # A tick with no figure in between leaves the start where it was.
    _publish(sensor, None, started=swap)
    _publish(sensor, 3.0, started=swap)
    assert sensor.last_reset == swap


@pytest.mark.usefixtures("_mid_july_in_brussels")
def test_the_reset_never_moves_back() -> None:
    """Home Assistant opens a new cycle when last_reset moves back too.

    A start the coordinator could not restore must not take the reset
    back to 1 January, which would count the year once more.
    """
    sensor = _sensor("ytd_consumption")
    swap = datetime(2026, 7, 10, 8, tzinfo=_BRUSSELS)
    _publish(sensor, 0.0, started=swap)
    _publish(sensor, 1.0)
    assert sensor.last_reset == swap


@pytest.mark.usefixtures("_mid_july_in_brussels")
def test_last_years_start_gives_way_to_the_new_year() -> None:
    sensor = _sensor("ytd_consumption")
    _publish(sensor, 0.0, started=datetime(2025, 12, 2, tzinfo=_BRUSSELS))
    assert sensor.last_reset == _JAN_1


@pytest.mark.usefixtures("_mid_july_in_brussels")
async def test_a_restored_reset_is_kept_as_a_floor() -> None:
    """The reset this entity last went out under survives a restart.

    That includes one an earlier release moved on any fall: dropping it
    on the upgrade would take last_reset back to 1 January, and the
    statistics would count the year a second time.
    """
    sensor = _sensor("ytd_consumption")
    earlier = dt_util.now() - timedelta(days=3)

    class _Stored:
        def as_dict(self) -> dict[str, Any]:
            # last_native is what an earlier release stored next to it.
            return {"reset_at": earlier.isoformat(), "last_native": 42.0}

    with (
        patch.object(WaterSensor, "async_get_last_extra_data", AsyncMock(return_value=_Stored())),
        patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            AsyncMock(),
        ),
    ):
        await sensor.async_added_to_hass()

    assert sensor._reset_at == earlier
    _publish(sensor, 1.0)
    assert sensor.last_reset == earlier


@pytest.mark.usefixtures("_mid_july_in_brussels")
def test_the_reset_is_handed_out_for_storage() -> None:
    sensor = _sensor("ytd_consumption")
    swap = datetime(2026, 7, 10, 8, tzinfo=_BRUSSELS)
    _publish(sensor, 10.0, started=swap)
    stored = sensor.extra_restore_state_data
    assert stored is not None
    assert stored.as_dict() == {"reset_at": swap.isoformat()}
    # A sensor with no reset has nothing to store.
    assert _sensor("basis_rate").extra_restore_state_data is None


def test_last_reset_none_for_non_total_sensor() -> None:
    sensor = _sensor("basis_rate")
    assert sensor.last_reset is None


def test_source_url_redacts_commune_slug() -> None:
    url = "https://www.pidpa.be/ons-aanbod/je-gemeente/geel"
    # Per-commune Pidpa URL embeds the town slug -> redacted.
    assert "geel" not in _source_url_without_commune(url, "geel")
    # No commune configured, or a commune id not present in the URL
    # (e.g. Farys numeric id), leaves the URL untouched.
    assert _source_url_without_commune(url, None) == url
    assert _source_url_without_commune(url, "25071") == url


def test_the_device_link_redacts_the_commune_too() -> None:
    """The device card is as public as the sensor attribute.

    configuration_url deep-links to the tariff publication, and for the
    per-commune utilities that URL carries the town name. It shows in
    screenshots and exports exactly like the attribute that was already
    being redacted.
    """
    from datetime import UTC

    from custom_components.be_water_prices.const import CONF_COMMUNE
    from custom_components.be_water_prices.coordinator import (
        CoordinatorData,
        utility_device_info,
    )
    from custom_components.be_water_prices.providers.base import WaterTariff

    coordinator = _StubCoordinator()
    coordinator.entry.data = {CONF_UTILITY: "pidpa"}
    coordinator.entry.options = {CONF_COMMUNE: "geel"}
    coordinator.data = CoordinatorData(  # type: ignore[assignment]
        tariff=WaterTariff(
            utility="pidpa",
            region="flanders",
            valid_from=date(2026, 1, 1),
            valid_until=date(2026, 12, 31),
            publication_label="Pidpa tarieven 2026 (Geel)",
            source_url="https://www.pidpa.be/ons-aanbod/je-gemeente/geel",
            yearly_fixed_fee=50.0,
        ),
        fetched_at=datetime(2026, 1, 2, tzinfo=UTC),
        snapshot_age_hours=1.0,
        snapshot_stale=False,
    )

    info = utility_device_info(coordinator)  # type: ignore[arg-type]
    assert "geel" not in (info["configuration_url"] or "")
    assert "**redacted**" in (info["configuration_url"] or "")


def test_label_suffix_survives_when_it_is_not_the_commune() -> None:
    """Only a suffix naming the commune is redaction; the rest is content.

    VIVAQUA puts the VAT basis in that parenthetical and De Watergroep's
    fallback puts its drinkwater-only marker there. Dropping them told
    the user less about their tariff and hid nothing.
    """
    from custom_components.be_water_prices.sensor import (
        _publication_label_without_commune as strip,
    )

    assert strip("Price from January 1st 2026 (VAT included 6 %)", ["Geel"]) == (
        "Price from January 1st 2026 (VAT included 6 %)"
    )
    # An entry with no commune configured has nothing to redact.
    assert strip("Pidpa tarieven 2026 (Geel)", []) == "Pidpa tarieven 2026 (Geel)"
    # The commune itself still goes, nested marker and all.
    assert strip("Pidpa tarieven 2026 (Geel)", ["Geel"]) == "Pidpa tarieven 2026"
    assert (
        strip("De Watergroep tarieven 2026 (Halle (DWG-served default))", ["Halle"])
        == "De Watergroep tarieven 2026"
    )


def test_last_error_is_scrubbed_of_the_commune() -> None:
    """A fetch error quotes the URL it failed on, commune slug and all.

    The neighbouring source_url and publication_label attributes are both
    redacted, so publishing last_error raw put the household's location
    back into the recorder through the side door.
    """
    from datetime import UTC

    from custom_components.be_water_prices.const import CONF_COMMUNE, CONF_COMMUNE_LABEL
    from custom_components.be_water_prices.coordinator import CoordinatorData
    from custom_components.be_water_prices.providers.base import WaterTariff

    coordinator = _StubCoordinator()
    coordinator.entry.data = {CONF_UTILITY: "pidpa", CONF_COMMUNE: "geel"}
    coordinator.entry.options = {CONF_COMMUNE_LABEL: "Geel"}
    coordinator.data = CoordinatorData(  # type: ignore[assignment]
        tariff=WaterTariff(
            utility="pidpa",
            region="flanders",
            valid_from=date(2026, 1, 1),
            valid_until=date(2026, 12, 31),
            publication_label="Pidpa tarieven 2026 (Geel)",
            source_url="https://www.pidpa.be/tarieven/geel",
            yearly_fixed_fee=50.0,
            basis_eur_per_m3=2.0,
            comfort_eur_per_m3=4.0,
        ),
        fetched_at=datetime(2026, 1, 2, tzinfo=UTC),
        snapshot_age_hours=1.0,
        snapshot_stale=False,
        last_error="HTTP 404 fetching https://www.pidpa.be/tarieven/geel",
    )

    attrs = _sensor_with("basis_rate", coordinator).extra_state_attributes
    assert "geel" not in attrs["last_error"].lower()
    assert "404" in attrs["last_error"]


def _card(region: str, **rates: float) -> WaterTariff:
    return WaterTariff(
        utility="x",
        region=region,
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 12, 31),
        publication_label="x",
        source_url="https://example.invalid/",
        **rates,
    )


# Expected figures are worked out by hand, not with the sensor's own
# formula, so a slip in the formula cannot agree with itself. The same
# values go to the price history backfill.
@pytest.mark.parametrize(
    ("card", "expected"),
    [
        pytest.param(
            _card(
                "flanders",
                yearly_fixed_fee=100.0,
                basis_eur_per_m3=2.0848,
                comfort_eur_per_m3=4.1696,
                sanering_gemeentelijk_eur_per_m3=1.6533,
                sanering_bovengemeentelijk_eur_per_m3=1.1809,
            ),
            # sanering 1.6533 + 1.1809, all in (2.0848 + 2.8342) * 1.06
            {
                "yearly_fee": 100.0,
                "basis_rate": 2.0848,
                "comfort_rate": 4.1696,
                "sanering_rate": 2.8342,
                "all_in_basis": 5.2141,
            },
            id="flanders",
        ),
        pytest.param(
            _card(
                "brussels",
                yearly_fixed_fee=37.735849,
                linear_eur_per_m3=2.3585,
                sanering_gemeentelijk_eur_per_m3=1.2264,
            ),
            # all in (2.3585 + 1.2264) * 1.06 = 3.799994
            {
                "yearly_fee": 37.74,
                "basis_rate": 2.3585,
                "comfort_rate": None,
                "sanering_rate": 1.2264,
                "all_in_basis": 3.8,
            },
            id="brussels",
        ),
        pytest.param(
            _card(
                "wallonia",
                yearly_fixed_fee=147.24,
                cvd_eur_per_m3=3.24,
                cva_eur_per_m3=2.748,
                fse_eur_per_m3=0.0339,
            ),
            # sanering is CVA + FSE, all in (3.24 + 2.7819) * 1.06 = 6.383214
            {
                "yearly_fee": 147.24,
                "basis_rate": 3.24,
                "comfort_rate": None,
                "sanering_rate": 2.7819,
                "all_in_basis": 6.3832,
            },
            id="wallonia",
        ),
    ],
)
def test_rate_sensor_values(card: WaterTariff, expected: dict[str, float | None]) -> None:
    data = CoordinatorData(
        tariff=card,
        fetched_at=datetime(2026, 1, 2, tzinfo=UTC),
        snapshot_age_hours=1.0,
        snapshot_stale=False,
    )
    got = {d.key: d.value_fn(data) for d in SENSORS if d.key in expected}
    assert got == pytest.approx(expected)


async def test_comfort_rate_is_removed_when_the_operator_loses_it(hass) -> None:  # type: ignore[no-untyped-def]
    """Reconfiguring out of Flanders must not leave a restored entity behind.

    The comfort rate stops being created, but its registry entry
    survives and Home Assistant shows it with the "no longer being
    provided" banner until somebody clicks delete.
    """
    from homeassistant.helpers import entity_registry as er

    from custom_components.be_water_prices.const import CONF_UTILITY, DOMAIN
    from custom_components.be_water_prices.sensor import async_remove_inapplicable_entities

    entry = _StubEntry()
    entry.data = {CONF_UTILITY: "swde"}  # Wallonia: no comfort rate
    ent_reg = er.async_get(hass)
    for key in ("basis_rate", "comfort_rate"):
        ent_reg.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_{key}", suggested_object_id=f"x_{key}"
        )

    async_remove_inapplicable_entities(hass, entry)  # type: ignore[arg-type]

    assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_comfort_rate") is None
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_basis_rate") is not None


def test_readme_lists_the_english_entity_ids() -> None:
    """The suffixes the README documents are those of an English install.

    Home Assistant names the entity id after the sensor name in its own
    language, so a Dutch, French or German install gets other ids. The
    tables have to say which install they describe and list exactly what
    English produces, or users copy ids that do not exist.
    """
    import json
    import re
    from pathlib import Path

    from homeassistant.util import slugify

    root = Path(__file__).resolve().parent.parent
    en = json.loads(
        (root / "custom_components/be_water_prices/translations/en.json").read_text(
            encoding="utf-8"
        )
    )["entity"]["sensor"]
    expected = {slugify(en[d.translation_key]["name"]) for d in SENSORS}

    documented: set[str] = set()
    in_table = False
    for line in (root / "README.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("| Entity id suffix (English install) |"):
            in_table = True
        elif not line.startswith("|"):
            in_table = False
        elif in_table and (m := re.match(r"\| `([a-z0-9_]+)` \|", line)):
            documented.add(m.group(1))

    assert documented == expected
