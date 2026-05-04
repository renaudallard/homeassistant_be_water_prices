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

DOMAIN = "be_water_prices"

REGION_FLANDERS = "flanders"
REGION_WALLONIA = "wallonia"
REGION_BRUSSELS = "brussels"
REGIONS: tuple[str, ...] = (REGION_FLANDERS, REGION_WALLONIA, REGION_BRUSSELS)

# Daily refresh; tariffs change once a year so anything tighter is wasted work.
UPDATE_INTERVAL_HOURS = 24

# Treat the snapshot as stale once it has not been refreshed for this many days
# OR the parsed valid_until is in the past. Surfaced as a sensor attribute and
# logged; persistent failure is caught by the daily live_check workflow.
SNAPSHOT_STALE_AFTER_DAYS = 35

# Wallonia uses two flat-Wallonia volumetric components on top of each
# distributor's CVD. Both come from SPGE / CWaPE, not from the distributor's
# own publication, so they live here. Source: SPGE / CWaPE annual rate
# decision, mirrored on each Walloon distributor's tariff page.
WALLONIA_CVA_EUR_PER_M3 = 2.748
WALLONIA_FSE_EUR_PER_M3 = 0.0339

# Standard Belgian residential VAT rate on water.
DEFAULT_VAT_RATE = 0.06

# Standard VMM "integrale waterprijs" vastrecht structure for Flanders.
# Mandated by the Vlaamse regering since 2016 and uniform across operators
# (De Watergroep, Pidpa, Farys, Water-link, Aquaduin, AGSO Knokke). Each
# component is split into its own line on the bill but the operator-side
# numbers are identical, so we keep one source of truth here.
FLANDERS_VASTRECHT_DRINKWATER = 50.0
FLANDERS_VASTRECHT_GEMEENTELIJK = 30.0
FLANDERS_VASTRECHT_BOVENGEMEENTELIJK = 20.0
FLANDERS_VASTRECHT_TOTAL = (
    FLANDERS_VASTRECHT_DRINKWATER
    + FLANDERS_VASTRECHT_GEMEENTELIJK
    + FLANDERS_VASTRECHT_BOVENGEMEENTELIJK
)
FLANDERS_KORTING_DRINKWATER_PER_PERSON = 10.0
FLANDERS_KORTING_GEMEENTELIJK_PER_PERSON = 6.0
FLANDERS_KORTING_BOVENGEMEENTELIJK_PER_PERSON = 4.0
FLANDERS_KORTING_TOTAL_PER_PERSON = (
    FLANDERS_KORTING_DRINKWATER_PER_PERSON
    + FLANDERS_KORTING_GEMEENTELIJK_PER_PERSON
    + FLANDERS_KORTING_BOVENGEMEENTELIJK_PER_PERSON
)

# Config / option keys.
CONF_UTILITY = "utility"
CONF_POSTCODE = "postcode"
CONF_CONSUMPTION_M3_PER_YEAR = "consumption_m3_per_year"
CONF_PERSONS = "gedomicilieerd_persons"
CONF_SOCIAL_TARIFF = "social_tariff"
CONF_WATER_METER_SENSOR = "water_meter_sensor"
CONF_COMMUNE = "commune"

DEFAULT_CONSUMPTION_M3 = 80
DEFAULT_PERSONS = 1
MIN_PERSONS = 1
MAX_PERSONS = 5
