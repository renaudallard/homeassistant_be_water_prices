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

"""IDEN -- Intercommunale de Distribution d'Eau de Nandrin, Tinlot et environs.

Three communes (Nandrin, Tinlot, Modave), tiny population. The
operator's own site (iden-eau.be) carries an educational page about
CVD/CVA but no structured tariff numbers; we pull from the Callmepower
public aggregator instead.

Source: https://callmepower.be/fr/eau/distributeurs/iden
"""

from __future__ import annotations

from ._walloon_simple import build_extractor
from ._walloon_simple import parse_tariff as _parse_tariff
from .base import WaterTariff

UTILITY_ID = "iden"
LABEL = "IDEN"
SOURCE_URL = "https://callmepower.be/fr/eau/distributeurs/iden"
_LABEL_PREFIX = "IDEN tarifs (via Callmepower)"


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
