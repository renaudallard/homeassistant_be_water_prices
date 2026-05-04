"""Water-utility extractor registry.

Each utility module exposes a top-level ``EXTRACTOR``. Adding a new
utility means writing a new module and registering it below.
"""

from __future__ import annotations

from .base import (
    ExtractorError,
    WaterExtractor,
    WaterTariff,
)
from .de_watergroep import EXTRACTOR as _DE_WATERGROEP
from .inbw import EXTRACTOR as _INBW
from .pidpa import EXTRACTOR as _PIDPA
from .swde import EXTRACTOR as _SWDE
from .vivaqua import EXTRACTOR as _VIVAQUA

EXTRACTORS: dict[str, WaterExtractor] = {
    _VIVAQUA.id: _VIVAQUA,
    _DE_WATERGROEP.id: _DE_WATERGROEP,
    _PIDPA.id: _PIDPA,
    _SWDE.id: _SWDE,
    _INBW.id: _INBW,
    # Farys is intentionally absent: the watertarieven page is JS-rendered
    # and the static HTML carries no per-m³ numbers (only a commune list).
    # Will land in a follow-up once we discover the Drupal endpoint or use
    # a per-commune fallback URL.
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
