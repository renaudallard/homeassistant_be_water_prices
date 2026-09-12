# cile

One row per commune (`default` is the no-commune fetch), one column per month
the branch holds.
Each month links to what its tariff was parsed from and to what came out of it:
`pdf` is the card itself, in the cards repository's releases, `page` the text of
the page as it was read, and `json` the tariff as the integration parsed it, both
on this branch. A blank cell is a month the branch does not hold.

| commune | label | 2026-09 |
| --- | --- | --- |
| default |  | [page](https://github.com/renaudallard/homeassistant_be_water_prices/blob/archive/texts/02315f1c161547e5acd13cb79c263fcaae6b3ef3d87d214b80fba08d4e17b72c.txt) [json](https://github.com/renaudallard/homeassistant_be_water_prices/blob/archive/cile/default/2026-09.json) |
