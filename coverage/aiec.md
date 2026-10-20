# aiec

One row per commune (`default` is the no-commune fetch), one column per month
the branch holds.
Each month links to what its tariff was parsed from and to what came out of it:
`pdf` or `picture` is the card itself, in the cards repository's releases, `page`
the text of the page as it was read, and `json` the tariff as the integration
parsed it, both on this branch. A blank cell is a month the branch does not hold.

| commune | label | 2026-09 | 2026-10 |
| --- | --- | --- | --- |
| default |  | [page](https://github.com/renaudallard/homeassistant_be_water_prices/blob/archive/texts/fe22be9aa648dea40bf6a805903be078f0f61f786520a1781c795d69ff2e649e.txt) [json](https://github.com/renaudallard/homeassistant_be_water_prices/blob/archive/aiec/default/2026-09.json) | [picture](https://github.com/renaudallard/be_price_cards/releases/download/water-2026-10/647cb69dcd012d1538a99e16e576baaab7070ce26881a4f1a05db80983c4be71.jpg) [json](https://github.com/renaudallard/homeassistant_be_water_prices/blob/archive/aiec/default/2026-10.json) |
