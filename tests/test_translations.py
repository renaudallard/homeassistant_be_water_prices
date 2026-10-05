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

"""The four translations must tell the user the same things.

A sentence dropped from one language goes unnoticed: the form still
renders, it just says less than the English one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from custom_components.be_water_prices.const import DEFAULT_CONSUMPTION_M3

TRANSLATIONS = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "be_water_prices"
    / "translations"
)
LANGUAGES = ("en", "fr", "nl", "de")


def _load(lang: str) -> dict[str, Any]:
    with (TRANSLATIONS / f"{lang}.json").open(encoding="utf-8") as fp:
        data: dict[str, Any] = json.load(fp)
    return data


@pytest.mark.parametrize("lang", LANGUAGES)
def test_options_description_names_the_default_consumption(lang: str) -> None:
    """Every language says the form falls back to the default figure."""
    text = _load(lang)["config"]["step"]["options"]["description"]
    assert f"{DEFAULT_CONSUMPTION_M3} m³" in text


@pytest.mark.parametrize("lang", LANGUAGES)
def test_meter_repair_names_the_option_label(lang: str) -> None:
    """The two-meter card points at the label the options form shows.

    The card ends its settings path on the field name, so a label that
    drifts from it sends the user looking for a field that is not there.
    """
    data = _load(lang)
    card = data["issues"]["several_water_meters"]["description"]
    m = re.search(r"> ([^>()]+)\)", card)
    assert m is not None
    field = m.group(1)
    for flow in (data["config"]["step"]["options"], data["options"]["step"]["init"]):
        assert flow["data"]["water_meter_sensor"].startswith(field)


# How each language says that the saved commune goes with a new postcode.
_POSTCODE_CLEARS_COMMUNE = {
    "en": "postcode differs from the saved one",
    "fr": "code postal diffère de celui enregistré",
    "nl": "postcode verschilt van de opgeslagen",
    "de": "Postleitzahl von der gespeicherten abweicht",
}


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize("step", ["reconfigure_postcode", "reconfigure_choose"])
def test_reconfigure_says_a_new_postcode_clears_the_commune(lang: str, step: str) -> None:
    """Both steps after a postcode say a move drops the saved commune.

    The finish step drops it on a postcode other than the saved one
    unless a commune is picked, even under the same operator. Text that
    tied the drop to an operator change alone told a household moving
    within Pidpa that its commune would carry over.
    """
    text = _load(lang)["config"]["step"][step]["description"]
    assert _POSTCODE_CLEARS_COMMUNE[lang] in text


@pytest.mark.parametrize("lang", LANGUAGES)
def test_operator_repair_names_the_manual_pick(lang: str) -> None:
    """The operator_moved card says how to keep the operator in use.

    An operator picked by hand on an older release has no recorded
    resolver answer, so the card is raised for it too, and picking the
    same operator again on the manual step is what records the answer.
    The card names that step by the label the reconfigure menu shows.
    """
    data = _load(lang)
    card = data["issues"]["operator_moved"]["description"]
    label = data["config"]["step"]["reconfigure"]["menu_options"]["reconfigure_manual"]
    assert f"> {label} " in card
