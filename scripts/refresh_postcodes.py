#!/usr/bin/env python3
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

"""One-shot postcode → utility map refresher.

For Wallonia (postcodes 4000-7999) the script:

  1. Downloads the Opendatasoft Belgian postcode polygon set
     (georef-belgium-postal-codes; ~1231 polygons, one per former
     commune carrying a (postcode, centroid) pair).
  2. For every Walloon polygon, point-in-polygon-queries the
     Géoportail Wallonie ZDE ArcGIS layer with the centroid to
     learn which DISTRIBUTEUR serves it.
  3. Maps DISTRIBUTEUR strings (SWDE, INASEP, INBW, …) to our own
     utility ids; drops postcodes whose distributor is the régies
     long tail (BOUILLON, VRESSE, …) since we don't ship an
     extractor for them.

The result is a single static dict literal printed to stdout (write
it into ``custom_components/be_water_prices/providers/_postcodes.py``
inside the ``_PER_POSTCODE`` mapping). Re-run annually -- the
underlying data is updated continuously by the distributors via
ZDEOnMap.

Flanders is intentionally not refreshed here. The VMM Waterloket
form-based lookup would require browser-style POST simulation per
postcode for ~2000 entries; the existing range-based rules in
``_postcodes.py`` cover Flanders correctly today and the carve-outs
for Aquaduin / AGSO Knokke / Water-link / Farys are hand-curated.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request

# Single-source-of-truth mapping from the geoportail's DISTRIBUTEUR
# strings to our extractor utility ids. Operators not in this map are
# dropped from the output (unsupported régies / industrial-only IDEA).
_DISTRIBUTEUR_TO_UTILITY: dict[str, str] = {
    "SWDE": "swde",
    "INBW": "inbw",
    "INASEP": "inasep",
    "CILE": "cile",
    "AIEC": "aiec",
    "AIEM": "aiem",
    "CIESAC": "ciesac",
    "IDEN": "iden",
    "IEG": "ieg",
}

POSTCODE_DATASET_URL = (
    "https://public.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
    "georef-belgium-postal-codes/exports/geojson?limit=-1"
)
ZDE_QUERY_URL = (
    "https://geoservices.wallonie.be/arcgis/rest/services/INDUSTRIES_SERVICES/ZDE/MapServer/1/query"
)


def fetch_postcodes() -> list[dict[str, object]]:
    print("downloading Belgian postcode polygons …", file=sys.stderr)
    req = urllib.request.Request(
        POSTCODE_DATASET_URL,
        headers={"User-Agent": "be_water_prices refresh_postcodes"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    return list(data["features"])


def query_zde_for_centroid(lon: float, lat: float) -> str | None:
    """Return the DISTRIBUTEUR name covering ``(lon, lat)`` or None."""
    params = {
        "geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "DISTRIBUTEUR",
        "returnGeometry": "false",
        "f": "json",
    }
    url = ZDE_QUERY_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "be_water_prices"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except Exception as err:
        print(f"  ZDE query failed: {err}", file=sys.stderr)
        return None
    features = data.get("features", [])
    if not features:
        return None
    return str(features[0]["attributes"].get("DISTRIBUTEUR") or "").strip() or None


def build_wallonia_map(features: list[dict[str, object]]) -> dict[str, str]:
    """Walloon postcode → utility_id mapping derived from the ZDE."""
    out: dict[str, str] = {}
    skipped: dict[str, int] = {}
    seen_postcodes: set[str] = set()
    walloon = [
        f
        for f in features
        if 4000 <= int(str(f["properties"]["postcode"])) <= 7999  # type: ignore[index]
    ]
    print(f"querying ZDE for {len(walloon)} Walloon postcode polygons …", file=sys.stderr)
    for i, feature in enumerate(walloon):
        props = feature["properties"]
        postcode = str(props["postcode"])  # type: ignore[index]
        if postcode in seen_postcodes:
            continue  # multiple sub-municipal polygons share a postcode
        seen_postcodes.add(postcode)
        centroid = props["geo_point_2d"]  # type: ignore[index]
        lon = float(centroid["lon"])  # type: ignore[index]
        lat = float(centroid["lat"])  # type: ignore[index]
        distributor = query_zde_for_centroid(lon, lat)
        if distributor is None:
            skipped[postcode] = 1
            continue
        utility = _DISTRIBUTEUR_TO_UTILITY.get(distributor)
        if utility is None:
            skipped[distributor] = skipped.get(distributor, 0) + 1
            continue
        out[postcode] = utility
        if (i + 1) % 50 == 0:
            print(f"  …{i + 1}/{len(walloon)} processed", file=sys.stderr)
        time.sleep(0.05)  # politely throttle the ArcGIS endpoint
    print(f"resolved {len(out)} postcodes", file=sys.stderr)
    if skipped:
        print(f"skipped (unsupported distributor / no ZDE hit): {skipped}", file=sys.stderr)
    return out


def render_dict(mapping: dict[str, str]) -> str:
    """Render a Python dict literal sorted by postcode for pretty diffs."""
    lines = ["_PER_POSTCODE: dict[str, str] = {"]
    for postcode in sorted(mapping):
        lines.append(f'    "{postcode}": "{mapping[postcode]}",')
    lines.append("}")
    return "\n".join(lines)


def main() -> int:
    features = fetch_postcodes()
    mapping = build_wallonia_map(features)
    print(render_dict(mapping))
    return 0


if __name__ == "__main__":
    sys.exit(main())
