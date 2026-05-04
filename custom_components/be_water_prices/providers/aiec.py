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

"""AIEC -- Association Intercommunale des Eaux du Condroz.

Tiny operator covering parts of the Condroz region (cheapest water in
Wallonia historically). The Aquawal directory page for AIEC carries
only contact info, not the rate; the operator's own site doesn't
publish a structured tariff page either. We pull from the Callmepower
public aggregator -- a third-party but actively maintained source that
mirrors the official rate annually -- as the rate publication.

Source: https://callmepower.be/fr/eau/distributeurs/aiec
"""

from __future__ import annotations

from ._walloon_simple import build_extractor
from ._walloon_simple import parse_tariff as _parse_tariff
from .base import WaterTariff

UTILITY_ID = "aiec"
LABEL = "AIEC"
SOURCE_URL = "https://callmepower.be/fr/eau/distributeurs/aiec"
_LABEL_PREFIX = "AIEC tarifs (via Callmepower)"


def parse_tariff(html: str, year: int | None = None) -> WaterTariff:
    return _parse_tariff(
        html,
        utility_id=UTILITY_ID,
        source_url=SOURCE_URL,
        label_prefix=_LABEL_PREFIX,
        year=year,
    )


EXTRACTOR = build_extractor(
    utility_id=UTILITY_ID,
    label=LABEL,
    source_url=SOURCE_URL,
    publication_label_prefix=_LABEL_PREFIX,
)
