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

"""Constants for be_water_prices."""

from __future__ import annotations

import json
from pathlib import Path

DOMAIN = "be_water_prices"


def _read_version() -> str:
    """This release's version, straight from the manifest.

    Read here rather than in each place that wants it: the User-Agent
    every fetch sends and the key the running bill's floor is measured
    under are both this number, and two readers would drift.
    """
    manifest = Path(__file__).resolve().parent / "manifest.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version", "0"))
    except (OSError, ValueError):
        return "0"


INTEGRATION_VERSION = _read_version()

REGION_FLANDERS = "flanders"
REGION_WALLONIA = "wallonia"
REGION_BRUSSELS = "brussels"
REGIONS: tuple[str, ...] = (REGION_FLANDERS, REGION_WALLONIA, REGION_BRUSSELS)

# Daily refresh; tariffs change once a year so anything tighter is wasted work.
UPDATE_INTERVAL_HOURS = 24

# How long one tariff fetch may take, parse included. The per-request
# timeouts bound the network, not the parse: a page-long content stream
# kept pdfplumber busy for ten minutes past the inflate guard, and the
# refresh it wedged never re-armed the daily tick. The live check and the
# drift check hold their fetches to the same figure, so a card that times
# out for users fails there too instead of being graded fine.
FETCH_BUDGET_S = 180

# The project's own daily capture of every utility's card, one JSON per
# utility, commune and month on the archive branch (scripts/archive_cards.py
# writes it). Read when a refresh fails with nothing to serve, a restart
# while the utility is down, and again once what is held has gone stale or
# is last year's card, so the entry loads on the last card the archive saw
# instead of retrying setup until the utility is back, and keeps up with the
# archive while it stays away.
CARD_ARCHIVE_URL = (
    "https://raw.githubusercontent.com/renaudallard/homeassistant_be_water_prices/archive"
)

# Treat the snapshot as stale once it has not been refreshed for this many days
# OR the parsed valid_until is in the past, last year's card counting as valid
# until 31 March. Surfaced as a sensor attribute and logged; persistent
# failure is caught by the daily live_check workflow.
SNAPSHOT_STALE_AFTER_DAYS = 35

# The Belgian residential VAT rate on water, for a card that neither states
# its rate nor prints VAT-inclusive figures to read it off: IDEN's page
# and the Pidpa Tariefplan projection. Every other card's rate is read off
# the card.
DEFAULT_VAT_RATE = 0.06

# The Flemish korting on the vastrecht counts at most this many registered
# residents per wooneenheid: "De korting geldt voor maximaal 5
# gedomicilieerde personen per wooneenheid", as the De Watergroep, Pidpa,
# AGSO and Aquaduin cards print it. A rule of the decree like the 30 m3 per
# resident of the basisvolume, not a rate. The vastrecht and the korting
# themselves are read off each card.
FLANDERS_KORTING_MAX_RESIDENTS = 5

# Config / option keys.
CONF_UTILITY = "utility"
CONF_POSTCODE = "postcode"
# What the resolver answered for that postcode when the operator was
# picked by hand against it. The postcode check stays quiet while the
# resolver still gives that answer, and speaks once it changes.
CONF_POSTCODE_RESOLVED = "postcode_resolved"
CONF_CONSUMPTION_M3_PER_YEAR = "consumption_m3_per_year"
CONF_PERSONS = "gedomicilieerd_persons"
CONF_SOCIAL_TARIFF = "social_tariff"
CONF_WATER_METER_SENSOR = "water_meter_sensor"
CONF_COMMUNE = "commune"
CONF_COMMUNE_LABEL = "commune_label"
# Whether that archive may be asked at all. The request names the utility
# and the commune id, so it is a switch, on by default.
CONF_CARD_ARCHIVE = "card_archive"
DEFAULT_CARD_ARCHIVE = True

DEFAULT_CONSUMPTION_M3 = 80
MIN_CONSUMPTION_M3 = 1
MAX_CONSUMPTION_M3 = 2000
DEFAULT_PERSONS = 1
# Registered residents drive two separate things, and only one of them
# is capped. The basisvolume is 30 m3 per wooneenheid plus 30 m3 per
# gedomicilieerde with no maximum, so a bound of 5 understated the free
# block for a larger household and overstated its bill. The korting on
# the vastrecht IS capped, at FLANDERS_KORTING_MAX_RESIDENTS, and pricing.py
# applies the cap.
#
# So these two only bound what a person can sensibly type. Zero is a
# real answer: a second home, or a rental between tenants.
MIN_PERSONS = 0
MAX_PERSONS = 20

# How far the configured yearly consumption has to sit from a whole year
# the meter actually measured before we offer to replace it. A household
# varies from year to year without the projection being wrong, so the
# prompt is for a figure that was never right rather than for weather.
PROJECTION_DRIFT_RATIO = 0.10
