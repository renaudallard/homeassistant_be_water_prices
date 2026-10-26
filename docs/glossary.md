# Glossary

## Belgian water terms

| Term | Meaning |
| --- | --- |
| Aquafin | The Flemish company that treats wastewater above the municipal level; the bovengemeentelijke saneringsbijdrage pays for it (`sanering_bovengemeentelijk_eur_per_m3`). |
| BIM (bénéficiaire de l'intervention majorée) | The status a mutual insurer grants a household on a low income (verhoogde tegemoetkoming in Dutch). Held on 1 January, it gives a Brussels household the intervention sociale. |
| Basisvolume (basisverbruik) | The Flemish first block: 30 m³ a year for the dwelling plus 30 m³ per registered resident, uncapped, billed at the basistarief (`basis_volume` in `pricing.py`). |
| Basistarief | The Flemish rate per m³ inside the basisvolume (`basis_eur_per_m3` for the drinkwater leg). |
| Brugel | The Brussels regulator, which approves VIVAQUA's multi-year tariff. |
| Callmepower | The aggregator site callmepower.be, which lists Walloon distributors' prices. CIESAC is read from it, and AIEC falls back to it when its own card has been neither transcribed nor read. |
| Card, tariff card | One utility's published tariff for a year: a page, a PDF or, for AIEC, a picture. Parsed into a `WaterTariff`. |
| Comforttarief | The Flemish rate above the basisvolume, twice the basistarief on every leg (`comfort_eur_per_m3` for the drinkwater). |
| Comfortvolume (comfortverbruik) | What a household draws above its basisvolume, billed at the comforttarief (`consumed_comfort` in `pricing.py`). |
| CVA (coût-vérité à l'assainissement) | The Walloon true cost of sanitation, per m³, flat across Wallonia and coming from SPGE / CWaPE: 2,748 EUR/m³ in 2026, read off each page (`cva_eur_per_m3`). Not charged on a household's first 30 m³. |
| CVD (coût-vérité à la distribution) | The Walloon true cost of distribution, per m³, each distributor's own (`cvd_eur_per_m3`). The one figure every Walloon parser reads. |
| CWaPE | The Walloon regulator. The residential tier structure (half the CVD and no CVA on the first 30 m³, the full rate to 5 000 m³, 90 % of the CVD above) is called the CWaPE structure in the code. |
| Drinkwater | The drinking-water leg of the Flemish bill, the operator's own: basistarief, comforttarief and 50 EUR of the vastrecht. |
| FSE (Fonds social de l'eau) | The Walloon social water fund contribution per m³, flat across Wallonia: 0,0339 EUR/m³ in 2026, read off each page (`fse_eur_per_m3`). |
| Gedomicilieerd | Officially registered as living at the address. The number of such residents sets the Flemish basisvolume and korting (`gedomicilieerd_persons` in the options, `CONF_PERSONS`). |
| Hydria | The Brussels sanitation body on whose behalf VIVAQUA bills the sanitation part of its rate. |
| Integrale waterprijs | The Flemish bill structure: drinkwater, gemeentelijke and bovengemeentelijke saneringsbijdrage, each with a vastrecht and a per-m³ rate in basis and comfort blocks. |
| Intercommunale | A company owned by several communes. Most Walloon distributors are one: CILE, INASEP, IEG, AIEM, AIEC, CIESAC and IDEN carry the word in their names, and `_walloon_simple.py` is the builder for the small ones. |
| Intervention sociale (sociale tussenkomst) | The Brussels Region's yearly payment for water to a household with BIM status, which VIVAQUA pays into its bank account: a figure for the first person and one for each further person, read off VIVAQUA's page. Taken off the cost sensors when the social tariff is ticked on a Brussels entry. |
| Korting | The Flemish reduction of the vastrecht per registered resident, read off the card: 10 + 6 + 4 = 20 EUR in 2026, counting at most five residents (`FLANDERS_KORTING_MAX_RESIDENTS`) and never taking the vastrecht below zero. |
| Redevance | The Walloon yearly fixed fee, `20 x CVD + 30 x CVA` (`yearly_fixed_fee`, built by `build_tariff`). |
| Régie communale | A commune that runs its own water distribution. About thirty in Wallonia; none is supported, and their postcodes fall through to the manual picker. |
| Saneringsbijdrage (gemeentelijk, bovengemeentelijk) | The Flemish sewerage contributions: the gemeentelijke one for the commune's sewers, which differs per commune, and the bovengemeentelijke one for treatment above the municipal level. |
| Social tariff (sociaal tarief) | The Flemish reduction for eligible households: 80 % off the whole bill. The same option (`social_tariff`) stands for BIM status on a Brussels entry, which prices the intervention sociale. |
| SPGE (Société publique de gestion de l'eau) | The Walloon public water management company. With CWaPE it is the source of the flat CVA and FSE, which every Walloon page prints. |
| Vastrecht | The Flemish yearly fixed charge, read off the card: 50 + 30 + 20 = 100 EUR for the three legs in 2026, before the korting. |
| VMM (Vlaamse Milieumaatschappij) | The Flanders environment agency. The code cites it for the integrale waterprijs rules: the comfort rate at twice the basis and the 80 % social reduction. |
| ZDE (zones de distribution d'eau) | The Géoportail Wallonie map of which distributor serves where. `scripts/refresh_postcodes.py` builds the Walloon postcode table from it. |

## Home Assistant terms

| Term | Meaning |
| --- | --- |
| Config entry | One configured instance of the integration: one household on one utility, and one commune where the utility has communes. Typed `WaterConfigEntry` (`ConfigEntry[WaterCoordinator]`); its data holds the utility, its options the postcode, the commune and the household settings. |
| Coordinator | `WaterCoordinator`, a `DataUpdateCoordinator` that fetches the card once a day (`UPDATE_INTERVAL_HOURS`), prices it and hands `CoordinatorData` to the entities. |
| Energy dashboard | Home Assistant's energy configuration. The integration takes its water meter from the dashboard's water sources unless the options name one. |
| Long-term statistics | The hourly and daily aggregates the recorder keeps. The meter's daily statistics anchor the year to date and give the rolling year and the year-end projection, and the price backfill imports hourly rows for the rate sensors (`statistics.py`). |
| Recorder | Home Assistant's history database, queried for the meter's statistics. When it cannot answer, the coordinator's readers raise `RecorderUnavailable` and the round goes on without a recorder figure. |
| Repairs | Issues shown under Settings > Repairs. This integration raises them for a stale card, an outdated typed consumption, several water meters and a postcode that now resolves to another operator. |
| runtime_data | The attribute of a config entry that holds its live object, here the `WaterCoordinator`, set at setup and read by the platforms. |
