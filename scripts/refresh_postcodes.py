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

Two outputs, both printed to stdout one after the other (paste each
block into ``custom_components/be_water_prices/providers/_postcodes.py``):

  1. The Walloon ``_PER_POSTCODE`` dict (4000-7999):
     - Downloads the Opendatasoft Belgian postcode polygon set
       (georef-belgium-postal-codes; ~1231 polygons, one per former
       commune carrying a (postcode, centroid) pair).
     - For every Walloon polygon, point-in-polygon-queries the
       Géoportail Wallonie ZDE ArcGIS layer with the centroid to
       learn which DISTRIBUTEUR serves it.
     - Maps DISTRIBUTEUR strings (SWDE, INASEP, INBW, …) to our own
       utility ids; drops postcodes whose distributor is the régies
       long tail (BOUILLON, VRESSE, …) since we don't ship an
       extractor for them.

  2. The ``_DWG_POSTCODES_FLANDERS`` frozenset (DWG-served pockets
     scattered inside the otherwise-Farys 8000-9999 block):
     - Scrapes DWG's commune dropdown at /nl-be/drinkwater/tarieven
       and Farys's commune dropdown at /tarieven/woonklant.
     - Keeps postcodes that DWG lists but Farys does not (so the
       resolver flips them from "farys" to "de_watergroep");
     - Drops postcodes that both operators list (street-level split;
       resolver keeps "farys" as the dominant default, user can
       manual-override on reconfigure).

Re-run the script annually -- the Walloon ZDE is updated by the
distributors via ZDEOnMap, and DWG's commune coverage shifts when
intercommunales merge.

The remaining Flemish range rules (Brussels, Brabant Wallon, Antwerp
core, AGSO Knokke-Heist, Aquaduin Westkust) are hand-curated and
stable enough not to need scraping.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Pull the runtime Farys phantom blocklist so the carve-out we compute
# here matches what the integration's list_communes() actually surfaces
# at runtime. Without this the script silently regresses DWG carve-out
# postcodes (8432 Leffinge, 9571 Hemelveerdegem, ...) on every refresh
# because Farys's raw dropdown still carries the phantom entry while
# the runtime drops it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.be_water_prices._phantom_blocklists import (
    FARYS_UNSERVABLE_LABELS as _FARYS_PHANTOM_LABELS,
)
from custom_components.be_water_prices.providers._postcodes import (
    _AQUADUIN_POSTCODES,
    _SPLIT_POSTCODES,
)

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


class ZdeQueryError(Exception):
    """The ZDE endpoint could not be queried (network / HTTP failure).

    Distinct from a successful query that returns no polygon hit, so the
    caller never mistakes a transient outage for genuine no-coverage and
    silently commits an incomplete map.
    """


def query_zde_for_centroid(lon: float, lat: float) -> str | None:
    """Return the DISTRIBUTEUR name covering ``(lon, lat)``, or None.

    ``None`` means the point falls outside every ZDE polygon (genuine
    no-coverage). A network / HTTP failure raises :class:`ZdeQueryError`
    instead, so it is never conflated with no-coverage.
    """
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
        raise ZdeQueryError(f"ZDE query failed: {err}") from err
    features = data.get("features", [])
    if not features:
        return None
    return str(features[0]["attributes"].get("DISTRIBUTEUR") or "").strip() or None


def build_wallonia_map(features: list[dict[str, object]]) -> dict[str, str]:
    """Walloon postcode → utility_id mapping derived from the ZDE."""
    out: dict[str, str] = {}
    skipped_distributors: dict[str, int] = {}
    no_zde_hit = 0
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
        # Throttle and report progress once per network call, before the
        # early continues -- otherwise no-hit / unsupported centroids (the
        # long tail of Walloon regies) issue back-to-back unspaced requests.
        time.sleep(0.05)  # politely throttle the ArcGIS endpoint
        if (i + 1) % 50 == 0:
            print(f"  …{i + 1}/{len(walloon)} processed", file=sys.stderr)
        if distributor is None:
            no_zde_hit += 1
            continue
        utility = _DISTRIBUTEUR_TO_UTILITY.get(distributor)
        if utility is None:
            skipped_distributors[distributor] = skipped_distributors.get(distributor, 0) + 1
            continue
        out[postcode] = utility
    print(f"resolved {len(out)} postcodes", file=sys.stderr)
    if no_zde_hit:
        print(f"skipped {no_zde_hit} postcodes with no ZDE hit", file=sys.stderr)
    if skipped_distributors:
        print(f"skipped (unsupported distributor): {skipped_distributors}", file=sys.stderr)
    return out


def render_dict(mapping: dict[str, str]) -> str:
    """Render a Python dict literal sorted by postcode for pretty diffs."""
    lines = ["_PER_POSTCODE: dict[str, str] = {"]
    for postcode in sorted(mapping):
        lines.append(f'    "{postcode}": "{mapping[postcode]}",')
    lines.append("}")
    return "\n".join(lines)


DWG_DROPDOWN_URL = "https://www.dewatergroep.be/nl-be/drinkwater/tarieven"
FARYS_DROPDOWN_URL = "https://www.farys.be/nl/watertarieven"
# Captures "<postcode> - <commune> (<gemeente>)" from a <option> label
# in either operator's dropdown.
_POSTCODE_FROM_LABEL_RE = re.compile(r"^\s*(\d{4})\b")
# Matches "<option ... value=...>label</option>". Used for both DWG
# (GUID values) and Farys (numeric values); we only care about the label.
_OPTION_LABEL_RE = re.compile(
    r"<option[^>]*>\s*([^<]+?)\s*</option>",
    re.IGNORECASE | re.DOTALL,
)


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "be_water_prices refresh_postcodes"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _scrape_postcodes(html: str) -> set[str]:
    out: set[str] = set()
    for match in _OPTION_LABEL_RE.finditer(html):
        m = _POSTCODE_FROM_LABEL_RE.match(match.group(1))
        if m:
            out.add(m.group(1))
    return out


def _scrape_farys_postcodes_filtered(html: str) -> set[str]:
    """Farys postcodes excluding the phantom-label entries.

    A Farys dropdown option is a phantom when the AJAX endpoint returns
    no insert command for its commune id; the runtime list_communes
    drops those via _UNSERVABLE_COMMUNE_LABELS. The script applies the
    same filter so a postcode whose only Farys entries are phantoms
    (e.g. 8432 Leffinge, 9571 Hemelveerdegem) is treated as DWG-only
    here too.
    """
    out: set[str] = set()
    for match in _OPTION_LABEL_RE.finditer(html):
        label = match.group(1).strip()
        if label in _FARYS_PHANTOM_LABELS:
            continue
        m = _POSTCODE_FROM_LABEL_RE.match(label)
        if m:
            out.add(m.group(1))
    return out


def build_dwg_flanders_carveout() -> list[str]:
    """DWG-served postcodes in 8000-9999 that Farys does not serve.

    These are the postcodes the range-based Flemish resolver would
    otherwise mis-route to Farys. Postcodes both operators list (a
    street-level split) are intentionally dropped: the resolver keeps
    Farys as the dominant default and users in the DWG half can manual-
    override on reconfigure.

    Farys's raw dropdown carries 23 phantom entries the runtime filters
    out; we apply the same filter so postcodes whose only Farys entries
    are phantoms (Leffinge, Hemelveerdegem, ...) are correctly treated
    as DWG-only.
    """
    print("scraping DWG commune dropdown …", file=sys.stderr)
    dwg_pc = _scrape_postcodes(_fetch(DWG_DROPDOWN_URL))
    print(f"  {len(dwg_pc)} DWG postcodes", file=sys.stderr)
    print("scraping Farys commune dropdown (filtered) …", file=sys.stderr)
    farys_pc = _scrape_farys_postcodes_filtered(_fetch(FARYS_DROPDOWN_URL))
    print(f"  {len(farys_pc)} Farys postcodes (post-phantom-filter)", file=sys.stderr)
    aquaduin_pc = {str(pc) for pc in _AQUADUIN_POSTCODES}
    split_pc = set(_SPLIT_POSTCODES.keys())
    carve = sorted(
        pc
        for pc in dwg_pc
        if 8000 <= int(pc) <= 9999
        and pc not in farys_pc
        and pc not in aquaduin_pc
        and pc not in split_pc
    )
    print(f"unambiguous DWG carve-outs in 8000-9999: {len(carve)}", file=sys.stderr)
    return carve


def render_dwg_frozenset(postcodes: list[str]) -> str:
    lines = ["_DWG_POSTCODES_FLANDERS: frozenset[int] = frozenset("]
    lines.append("    {")
    for pc in postcodes:
        lines.append(f"        {pc},")
    lines.append("    }")
    lines.append(")")
    return "\n".join(lines)


def main() -> int:
    features = fetch_postcodes()
    try:
        mapping = build_wallonia_map(features)
    except ZdeQueryError as err:
        # Abort rather than print (and let the maintainer commit) a map left
        # incomplete by a transient ZDE outage. Rerun once the endpoint is back.
        print(f"aborting: {err}", file=sys.stderr)
        return 1
    carve = build_dwg_flanders_carveout()
    print("# === Walloon _PER_POSTCODE ===")
    print(render_dict(mapping))
    print()
    print("# === Flemish _DWG_POSTCODES_FLANDERS ===")
    print(render_dwg_frozenset(carve))
    return 0


if __name__ == "__main__":
    sys.exit(main())
