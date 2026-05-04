"""Postcode → utility resolver.

The map is intentionally coarse for v0.3 -- it picks the dominant
operator for each region's postcode block. Per-commune precision (e.g.
splitting Liège between SWDE and CILE) lands in v0.4 once
``scripts/refresh_postcodes.py`` learns to ingest the Géoportail
Wallonie ZDE GeoPackage and the VMM Waterloket Flanders dump.

Today's coverage:

  * 1000-1299    Brussels-Capital            → VIVAQUA
  * 1300-1499    Brabant Wallon              → unresolved (inBW, v0.4)
  * 1500-3999    Flanders                    → unresolved (v0.2)
  * 4000-7999    Liège / Namur / Luxembourg
                 / Hainaut                   → SWDE
                 (CILE / INASEP / régies →
                  user picks manually for now)
  * everything else                          → unresolved
"""

from __future__ import annotations


def resolve(postcode: str) -> str | None:
    """Return the utility id serving ``postcode``, or ``None`` if unknown."""
    try:
        code = int(postcode)
    except ValueError:
        return None
    if 1000 <= code <= 1299:
        return "vivaqua"
    if 4000 <= code <= 7999:
        return "swde"
    return None
