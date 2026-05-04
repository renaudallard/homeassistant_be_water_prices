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
from .vivaqua import EXTRACTOR as _VIVAQUA

EXTRACTORS: dict[str, WaterExtractor] = {
    _VIVAQUA.id: _VIVAQUA,
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
