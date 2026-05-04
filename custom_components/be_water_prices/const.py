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
