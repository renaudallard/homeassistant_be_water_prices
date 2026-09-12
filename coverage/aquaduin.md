# aquaduin

One row per commune (`default` is the no-commune fetch), one column per month
the branch holds.
Each month links to what its tariff was parsed from and to what came out of it:
`pdf` is the card itself, in the cards repository's releases, `page` the text of
the page as it was read, and `json` the tariff as the integration parsed it, both
on this branch. A blank cell is a month the branch does not hold.

| commune | label | 2026-09 |
| --- | --- | --- |
| default |  | [pdf](https://github.com/renaudallard/be_price_cards/releases/download/water-2026-09/999f1ac0e9e24f681e1b64011faa8824dd22290709fdb1d5e969bac0b30e26fc.pdf) [json](https://github.com/renaudallard/homeassistant_be_water_prices/blob/archive/aquaduin/default/2026-09.json) |
