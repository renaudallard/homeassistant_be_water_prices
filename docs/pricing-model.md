# The pricing model

How a year of water is billed, as `pricing.py` implements it and
`coordinator.py` applies it. `pricing.py` is pure: no Home Assistant, no
network, so `tests/test_pricing.py` runs it from a plain venv. The worked
examples below are that file's, on the tariffs it builds by hand.

## The engine

`compute_annual_cost(tariff, consumption_m3, persons, *, social_tariff=False)`
is a year's bill at the full annual fee. `compute_ytd_cost(tariff,
consumption_m3_ytd, persons, year_elapsed_fraction, *, social_tariff=False)`
is the bill so far, the fee scaled by the fraction of the year elapsed
(clamped to 0 to 1). Both call `_compute_bill`, which picks the branch on
`tariff.region` and:

- clamps a negative consumption to 0;
- applies VAT once, at the end, since every `WaterTariff` figure is
  ex-VAT (see [provider-framework.md](provider-framework.md));
- rounds to the cent;
- returns None for a tariff it cannot price: a Brussels card without a
  linear rate, a Walloon card with no CVD, a Flemish card with no basis
  rate, or a region it does not know. The cost sensors are then unknown.

`persons` and `social_tariff` only change a Flemish bill.

## Flanders: the integrale waterprijs

A Flemish bill is three legs, each with a fixed and a volumetric part: the
drinkwater, the gemeentelijke saneringsbijdrage (municipal sewerage) and
the bovengemeentelijke saneringsbijdrage (supra-municipal treatment). The
card carries the drinkwater `basis_eur_per_m3` and `comfort_eur_per_m3`
and the two sanering rates.

```
basis_volume = 30 + 30 x persons
sanering = sanering_gemeentelijk + sanering_bovengemeentelijk
volumetric = min(m3, basis_volume) x (basis + sanering)
    + max(0, m3 - basis_volume) x (comfort + 2 x sanering)
vastrecht = max(0, yearly_fixed_fee - persons x korting)
bill = (volumetric + fee_factor x vastrecht) x (1 + VAT)
bill = bill x 0.20 (with the social tariff)
```

- The basisvolume is 30 m³ for the dwelling plus 30 m³ per registered
  resident, with no cap: `MAX_PERSONS` (20) and `MIN_PERSONS` (0) in
  `const.py` only bound what the form accepts. Zero residents still gets
  the 30 m³ of the dwelling (a second home).
- Above the basisvolume every leg is billed at twice its basis rate. For
  the sanering the engine doubles the rates itself; for the drinkwater it
  uses the card's comforttarief, which every Flemish parser holds to
  twice the basistarief (De Watergroep derives it that way). A card with
  no comfort rate falls back to twice the basis.
- The vastrecht and the korting are the decreed figures carried in
  `const.py`, not read off the cards: `FLANDERS_VASTRECHT_TOTAL` is
  50 + 30 + 20 = 100 EUR a year (drinkwater, gemeentelijk,
  bovengemeentelijk) and `FLANDERS_KORTING_TOTAL_PER_PERSON` is
  10 + 6 + 4 = 20 EUR per resident. `build_flanders_tariff` puts them in
  `yearly_fixed_fee` and `yearly_fixed_fee_per_resident_discount`. The
  floor at 0 is what caps the korting at five residents: five times 20
  cancels the 100.
- The social tariff takes 80 % off the whole bill, VAT and vastrecht
  included: the engine multiplies the VAT-inclusive total by 0.20.
- Aquaduin prints one integrated basistarief, drinkwater and sanering
  together, so its card carries that rate as the basis with both sanering
  rates at 0. The bill comes out the same; only the `basis_rate` sensor
  shows the integrated rate.

Worked examples from `tests/test_pricing.py` (a Pidpa card built in the
test: basis 2,0848, comfort 4,1696, gemeentelijk 1,6533, bovengemeentelijk
1,1809 EUR/m³, vastrecht 100, korting 20):

| Case | Result |
| --- | --- |
| 80 m³, 1 resident | 60 x 4,919 + 20 x 9,838 + 80 = 571,90 ex-VAT, 606,21 EUR |
| 60 m³, 1 resident (at the basisvolume) | 60 x 4,919 + 80 = 375,14 ex-VAT, 397,65 EUR |
| 100 m³, 4 residents (all in the basisvolume) | 491,90 + 20 = 511,90 ex-VAT, 542,61 EUR |
| 0 m³, 1 resident, social tariff | 80 x 1,06 x 0,20 = 16,96 EUR |
| 0 m³, 10 residents | 0,00 EUR (vastrecht floored at 0) |

## Wallonia: the CWaPE tiers

A Walloon card carries the distributor's CVD and the flat CVA and FSE.

```
first = min(m3, 30) x (0.5 x CVD + FSE)
middle = max(0, min(m3, 5000) - 30) x (CVD + CVA + FSE)
top = max(0, m3 - 5000) x (0.9 x CVD + CVA + FSE)
bill = (first + middle + top + fee_factor x redevance) x (1 + VAT)
```

- The first 30 m³ pay half the CVD and no CVA: a residential household is
  exempt from the CVA on that block.
- Above 5 000 m³ the CVD falls to 90 %. No household reaches it, but the
  year to date is driven by the meter, and a building-wide meter can.
  The further tranches SWDE and CILE publish far above that are not
  modelled (the README's "Known limitations").
- The tier allowances are annual, so the year to date fills them the same
  way the projection does.
- The redevance is `20 x CVD + 30 x CVA` a year, which `build_tariff` in
  `providers/_walloon_simple.py` stores as `yearly_fixed_fee`.

The CVA and FSE are the SPGE figures, flat across Wallonia. `const.py`
carries the ones in force in `WALLONIA_SPGE_YEAR` (2026):
`WALLONIA_CVA_EUR_PER_M3` 2,748 and `WALLONIA_FSE_EUR_PER_M3` 0,0339
EUR/m³. A card of that year or an earlier one is priced on them, its page
held to them; a card of a later year is priced on the figures its own page
prints. `spge_components` decides which, see
[provider-framework.md](provider-framework.md).

Worked examples from `tests/test_pricing.py` (SWDE's 2026 CVD of 3,24
EUR/m³, from the SWDE fixture, and the constants; redevance
20 x 3,24 + 30 x 2,748 = 147,24 EUR ex-VAT):

| Case | Result |
| --- | --- |
| 0 m³ | 147,24 ex-VAT, 156,07 EUR |
| 30 m³ | 30 x 1,6539 + 147,24 = 196,857 ex-VAT, 208,67 EUR |
| 80 m³ | 49,617 + 50 x 6,0219 + 147,24 = 497,952 ex-VAT, 527,83 EUR |

## Brussels: VIVAQUA's linear tariff

```
bill = (m3 x (linear + sanering) + fee_factor x yearly_fixed_fee) x (1 + VAT)
```

One rate for every cubic metre and a fixed charge. VIVAQUA prints its
figures VAT inclusive and split into supply and sanitation (the
sanitation billed on behalf of Hydria); the parser divides each by 1,06
and stores supply as `linear_eur_per_m3` and sanitation as
`sanering_gemeentelijk_eur_per_m3`. The engine adds both sanering fields
to the linear rate.

Worked example from `tests/test_pricing.py`, on VIVAQUA's 2026 figures
(fixed charge 40,23 EUR, supply 2,62 and sanitation 2,73 EUR/m³, all VAT
inclusive): 80 m³ cost 80 x 5,35 + 40,23 = 468,23 EUR, and 0 m³ cost the
fixed charge alone, 40,23 EUR.

## VAT

Every builder sets `vat_rate` to `DEFAULT_VAT_RATE` (0,06), the
residential rate on water, and the engine applies the card's `vat_rate`
once to the ex-VAT total. In Flanders the social reduction is applied
after VAT.

## The year to date

`WaterCoordinator._ytd_cost_from_m3(tariff, ytd_m3, year)` prices the
water the meter recorded since 1 January:

```
fraction = ((priced_on - 1 January).days + 1) / days_in_year
cost = compute_ytd_cost(tariff, ytd_m3, persons, fraction, social)
```

Today is counted as elapsed, so on 1 January the fee is already one day's
share. The volumetric part is the annual one: tiers and blocks fill from
1 January. Only the fixed fee (vastrecht, redevance, VIVAQUA's fixed
charge) is pro-rated. `_priced_on(year)` is the day priced: today, or the
last or first day of `year` when the clock has moved past it during the
round, so a tick that crosses midnight on 31 December does not price the
closing year at one day elapsed.

Worked examples from `tests/test_pricing.py` at half a year
(`year_elapsed_fraction` 0,5): Brussels 40 m³ 234,12 EUR, SWDE 80 m³
449,79 EUR, Pidpa 80 m³ with 1 resident 563,81 EUR. At a fraction of 1
the year to date equals the annual projection.

### Which card prices the year

`_card_in_force(tariff, year)` picks the card the running bill is priced
on. It is the fetched card unless that card's `valid_from` is after the
day priced, which is a utility putting next year's card up in December
(Farys, or a Walloon page headed with next year only). Then the card held
for the running bill is used, as long as it belongs to the same utility
and commune and is not dated after `year`; a fresh install holding none
prices on the card it has. The card held is kept on disk with the year's
record. Last year's card served in January (`carry_prior_year_card`) is
in force, so the year runs at last year's rates until the new card lands,
and is then billed again at the new card from 1 January.

### The cost floor

The running bill never publishes a decrease for a transient reason: a
cheaper card fetched for a day or a backward clock step. Each round's cost
is held at or above the highest cost published this year, but only while
the household is billed the same way. `_cost_basis` is that key: the
integration version, the utility, the commune, the residents, the social
tariff and the year of the card in force. When any of them changes the
floor is rebuilt, so
enabling the social tariff or registering a resident lowers the bill at
once, a release that corrects a rate reaches it the day it ships, and the
new year's card replaces January's stand-in even when it is cheaper. A
cheaper card of the same year does not lower it; a mid-year cut waits for
the year to turn. The rule itself, and the consumption clamp under it, are
in [coordinator.md](coordinator.md).

## The projection, the rolling year and the year end

- `_project_cost(tariff)` is `compute_annual_cost` on the consumption
  typed in the options (`consumption_m3_per_year`, 80 m³ by default),
  through `_annual_cost`, which adds the residents and social tariff from
  the options. It prices the fetched card, so next year's card put up in
  December shows here at once.
- The rolling year is what the meter recorded over the 365 closed days
  before the last read, priced by `_annual_cost` as a year (full fee) on
  the card in force.
- The projected year consumption is the year to date plus what the meter
  recorded last year from tomorrow's date to 31 December (a 29 February
  last year is left out when this year has none).
- The projected year end cost is the running bill plus what those days
  add on the card in force:

  ```
  end_cost = current_year_cost
           + annual(projected_m3) - ytd(ytd_m3, at today's fraction)
  ```

  The difference is the remaining days' water through what is left of the
  blocks and tiers, plus the rest of the year's fee. Building on the
  published running bill keeps a figure the floor holds above today's
  card.

A window counts only when the meter covered it: a statistics bucket in
the 31 days before it, one in its last 31 days, and buckets on at least
two days in three in between (`_metered_m3`). Until then those figures are
unknown. Which days count is described in [coordinator.md](coordinator.md).

## What the sensors expose

The suffixes are an English install's; see [entities.md](entities.md).

| Sensor | Value | Source |
| --- | --- | --- |
| `yearly_fixed_fee` | `yearly_fixed_fee`, ex-VAT, before the korting | the card |
| `basis_rate` | the basis rate, or the linear rate, or the CVD, ex-VAT | the card |
| `comfort_rate` | the comfort rate, ex-VAT (Flemish entries only) | the card |
| `sewerage_rate` | both sanering rates plus the CVA and FSE, ex-VAT | the card |
| `all_in_basis_rate` | `(basis_rate + sewerage_rate) x (1 + VAT)` | the card |
| `projected_annual_cost` | the year at the typed consumption | `_project_cost` |
| `current_year_cost` | the running bill since 1 January | `_ytd_cost_from_m3`, floored |
| `year_to_date_consumption` | m³ since 1 January | the meter |
| `rolling_year_consumption` | m³ over the last 365 days | the meter's statistics |
| `rolling_year_cost` | that volume billed as a year | `_annual_cost` |
| `projected_year_consumption` | the year to date plus last year's rest | the meter's statistics |
| `projected_year_end_cost` | the running bill on 31 December | `_year_figures` |

The five rate sensors are what the card publishes: none applies the
korting or the social tariff. In Wallonia `basis_rate` is the CVD and
`all_in_basis_rate` is `(CVD + CVA + FSE) x 1,06`, the rate of the tranche
from 31 to 5 000 m³; the first 30 m³ cost less. The four cost sensors are
what the household is billed, korting, social tariff, tiers and VAT
included. The price backfill writes only the five rate sensors' history
(`_BACKFILL_KEYS` in `statistics.py`).
