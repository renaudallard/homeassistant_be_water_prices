"""Postcode → utility resolver.

The map is intentionally coarse for v0.2 / v0.3 -- it picks the
dominant operator per regional postcode block. Per-commune precision
(splitting Liège between SWDE and CILE, splitting Oost-Vl between
Farys and De Watergroep, etc.) waits for v0.4 once
``scripts/refresh_postcodes.py`` learns to ingest the Géoportail
Wallonie ZDE GeoPackage and the VMM Waterloket Flanders dump.

Today's coverage:

  * 1000-1299    Brussels-Capital                → VIVAQUA
  * 1300-1499    Brabant Wallon                  → unresolved (inBW, v0.4)
  * 1500-1999,
    3000-3499    Vlaams-Brabant + Halle-Vilvoorde → DE WATERGROEP
  * 2000-2999    Antwerp province               → PIDPA
  * 3500-3999    Limburg                        → DE WATERGROEP
  * 4000-7999    Liège / Namur / Lux. / Hainaut → SWDE
  * 8000-9999    West-Vl. + Oost-Vl.            → unresolved
                 (Farys serves the bulk; deferred until the
                  Farys extractor lands)
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
    if 2000 <= code <= 2999:
        return "pidpa"
    if 1500 <= code <= 1999 or 3000 <= code <= 3999:
        return "de_watergroep"
    if 4000 <= code <= 7999:
        return "swde"
    return None
