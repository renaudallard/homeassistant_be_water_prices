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

"""icons.json names only entities and services that exist."""

from __future__ import annotations

import json
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from custom_components.be_water_prices.sensor import SENSORS

PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "be_water_prices"


def _icons() -> dict[str, dict[str, dict[str, dict[str, str]]]]:
    with (PACKAGE / "icons.json").open(encoding="utf-8") as fp:
        data: dict[str, dict[str, dict[str, dict[str, str]]]] = json.load(fp)
    return data


def test_every_sensor_icon_names_a_sensor() -> None:
    keys = {desc.translation_key for desc in SENSORS}
    named = set(_icons()["entity"]["sensor"])
    assert named <= keys, named - keys
    # The two with a device class keep the icon Home Assistant gives it.
    assert keys - named == {"current_year_cost", "ytd_consumption"}


def test_every_service_has_an_icon() -> None:
    with (PACKAGE / "services.yaml").open(encoding="utf-8") as fp:
        services = set(yaml.safe_load(fp))
    assert set(_icons()["services"]) == services


def test_the_refresh_button_has_an_icon() -> None:
    assert _icons()["entity"]["button"] == {"refresh": {"default": "mdi:refresh"}}
