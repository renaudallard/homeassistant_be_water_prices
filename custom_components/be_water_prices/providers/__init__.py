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

"""Water-utility extractor registry.

Each utility module exposes a top-level ``EXTRACTOR``. Adding a new
utility means writing a new module and registering it below.
"""

from __future__ import annotations

from .agso_knokke import EXTRACTOR as _AGSO_KNOKKE
from .aiec import EXTRACTOR as _AIEC
from .aiem import EXTRACTOR as _AIEM
from .aquaduin import EXTRACTOR as _AQUADUIN
from .base import (
    ExtractorError,
    WaterExtractor,
    WaterTariff,
)
from .ciesac import EXTRACTOR as _CIESAC
from .cile import EXTRACTOR as _CILE
from .de_watergroep import EXTRACTOR as _DE_WATERGROEP
from .iden import EXTRACTOR as _IDEN
from .ieg import EXTRACTOR as _IEG
from .inasep import EXTRACTOR as _INASEP
from .inbw import EXTRACTOR as _INBW
from .pidpa import EXTRACTOR as _PIDPA
from .swde import EXTRACTOR as _SWDE
from .vivaqua import EXTRACTOR as _VIVAQUA
from .water_link import EXTRACTOR as _WATER_LINK

EXTRACTORS: dict[str, WaterExtractor] = {
    _VIVAQUA.id: _VIVAQUA,
    _DE_WATERGROEP.id: _DE_WATERGROEP,
    _PIDPA.id: _PIDPA,
    _AQUADUIN.id: _AQUADUIN,
    _AGSO_KNOKKE.id: _AGSO_KNOKKE,
    _WATER_LINK.id: _WATER_LINK,
    _SWDE.id: _SWDE,
    _INBW.id: _INBW,
    _CILE.id: _CILE,
    _INASEP.id: _INASEP,
    _IEG.id: _IEG,
    _AIEM.id: _AIEM,
    _AIEC.id: _AIEC,
    _CIESAC.id: _CIESAC,
    _IDEN.id: _IDEN,
    # Still deferred:
    #   - Farys: watertarieven page is JS-rendered and carries no static numbers.
    #   - ~30 régies communales: no central publication channel found yet;
    #     deferred indefinitely on dev-hours / customer ratio.
}


def get(utility_id: str) -> WaterExtractor:
    try:
        return EXTRACTORS[utility_id]
    except KeyError as err:
        raise ExtractorError(f"no extractor registered for utility {utility_id!r}") from err


def all_extractors() -> tuple[WaterExtractor, ...]:
    return tuple(EXTRACTORS.values())


__all__ = [
    "EXTRACTORS",
    "ExtractorError",
    "WaterExtractor",
    "WaterTariff",
    "all_extractors",
    "get",
]
