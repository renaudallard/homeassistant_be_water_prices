# aiem

One row per commune (`default` is the no-commune fetch), one column per month
the branch holds.
Each month links to what its tariff was parsed from and to what came out of it:
`pdf` is the card itself, in the cards repository's releases, `page` the text of
the page as it was read, and `json` the tariff as the integration parsed it, both
on this branch. A blank cell is a month the branch does not hold.

| commune | label | 2026-09 |
| --- | --- | --- |
| default |  | [page](https://github.com/renaudallard/homeassistant_be_water_prices/blob/archive/texts/247a0d80cf8e024493d65aaf228ebcee047e8bdda4fa56698897b0af84c6f4f6.txt) [json](https://github.com/renaudallard/homeassistant_be_water_prices/blob/archive/aiem/default/2026-09.json) |
