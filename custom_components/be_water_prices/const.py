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
# OR the parsed valid_until is in the past. Triggers a repair issue in HA.
SNAPSHOT_STALE_AFTER_DAYS = 35

# Wallonia uses two flat-Wallonia volumetric components on top of each
# distributor's CVD. Both come from SPGE / CWaPE, not from the distributor's
# own publication, so they live here. Source: SCOPE.md §1 Wallonia.
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

DEFAULT_CONSUMPTION_M3 = 80
DEFAULT_PERSONS = 1
MIN_PERSONS = 1
MAX_PERSONS = 5
