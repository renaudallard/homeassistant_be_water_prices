# Copyright (c) 2026, Renaud Allard <renaud@allard.it>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Postcode → utility resolver.

The map is intentionally coarse -- it picks the dominant operator per
regional postcode block, with a few hand-curated carve-outs for
small operators on well-known postcodes (AGSO Knokke-Heist's 8300/8301,
the Aquaduin Westkust postcodes, CILE's Liège core 4000-4099, INASEP's
Namur sud cluster). Per-commune precision for the rest of Wallonia
(splitting the long tail of CILE / Aquaduin / Water-link footprint
across the wider 4000-7999 range) waits for the Géoportail Wallonie
ZDE GeoPackage scrape.

Coverage today:

  * 1000-1299    Brussels-Capital                       → VIVAQUA
  * 1300-1499    Brabant Wallon                         → inBW
  * 1500-1999,
    3000-3999    Vlaams-Brabant + Halle-Vilvoorde
                 + Limburg                              → DE WATERGROEP
  * 2000-2070    Antwerp city core (Water-link)         → WATER-LINK
  * 2100-2999    Rest of Antwerp province               → PIDPA
                 (Water-link's ring communes -- Edegem,
                  Hove, Mortsel, Schoten, Beveren, etc.
                  -- need the manual picker; their
                  postcodes overlap with Pidpa's wider
                  service area so we default to Pidpa
                  outside the city core)
  * 4000-4099    Liège city core                        → CILE
  * 4100-4999    Liège region                           → SWDE
  * 5000-5099,
    5060-5101    Namur sud (INASEP service area)        → INASEP
  * 5100-5999,
    6000-7999    rest of Namur / Hainaut / Luxembourg    → SWDE
  * 8300-8301    Knokke-Heist                           → AGSO Knokke-Heist
  * 8430, 8450,
    8620, 8630,
    8660, 8670   Aquaduin Westkust communes             → AQUADUIN
  * everything else (most of West-/Oost-Vl, parts of
    Vlaams-Brabant served by Farys)                     → unresolved
"""

from __future__ import annotations

# Aquaduin's 6 Westkust communes (Koksijde, De Panne, Nieuwpoort, Veurne,
# Bredene, Middelkerke). Hand-curated because they're scattered inside the
# 8000-8999 West-Vlaanderen block where Farys would otherwise be the default.
_AQUADUIN_POSTCODES: frozenset[int] = frozenset(
    {
        8430,  # Middelkerke
        8450,  # Bredene
        8620,  # Nieuwpoort
        8630,  # Veurne
        8660,  # De Panne
        8670,  # Koksijde
    }
)

# INASEP's 10 Namur sud communes.
_INASEP_POSTCODES: frozenset[int] = frozenset(
    {
        5060,  # Sambreville
        5070,  # Fosses-la-Ville
        5081,  # Meux (La Bruyère)
        5310,  # Eghezée
        5340,  # Gesves
        5360,  # Hamois
        5370,  # Havelange
        5500,  # Dinant
        5530,  # Yvoir
        5640,  # Mettet
    }
)


def resolve(postcode: str) -> str | None:
    """Return the utility id serving ``postcode``, or ``None`` if unknown."""
    try:
        code = int(postcode)
    except ValueError:
        return None

    # Brussels.
    if 1000 <= code <= 1299:
        return "vivaqua"
    # Brabant Wallon.
    if 1300 <= code <= 1499:
        return "inbw"
    # Antwerp city core is Water-link; rest of the province is Pidpa. Ring
    # communes (Edegem, Hove, Mortsel, etc.) overlap with Pidpa's wider
    # service area so we default to Pidpa there and let the user override
    # via the manual picker.
    if 2000 <= code <= 2070:
        return "water_link"
    if 2000 <= code <= 2999:
        return "pidpa"
    # Vlaams-Brabant + Halle-Vilvoorde + Limburg.
    if 1500 <= code <= 1999 or 3000 <= code <= 3999:
        return "de_watergroep"
    # Wallonia carve-outs first, then SWDE default.
    if 4000 <= code <= 4099:
        return "cile"
    if code in _INASEP_POSTCODES:
        return "inasep"
    if 4000 <= code <= 7999:
        return "swde"
    # Flanders carve-outs in West-Vl.
    if code in (8300, 8301):
        return "agso_knokke"
    if code in _AQUADUIN_POSTCODES:
        return "aquaduin"
    return None
