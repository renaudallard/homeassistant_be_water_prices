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

"""Diagnostics redaction helpers."""

from __future__ import annotations

from custom_components.be_water_prices.const import (
    CONF_COMMUNE,
    CONF_COMMUNE_LABEL,
    CONF_POSTCODE,
)
from custom_components.be_water_prices.diagnostics import _scrub_tokens, _sensitive_tokens


class _Entry:
    def __init__(self, data: dict[str, str], options: dict[str, str]) -> None:
        self.data = data
        self.options = options


def test_sensitive_tokens_collects_commune_and_postcode() -> None:
    entry = _Entry(
        data={CONF_POSTCODE: "2440"},
        options={CONF_COMMUNE: "geel", CONF_COMMUNE_LABEL: "Geel"},
    )
    tokens = _sensitive_tokens(entry)  # type: ignore[arg-type]
    assert set(tokens) == {"2440", "geel", "Geel"}
    # Longest first so a label containing the slug is replaced whole.
    assert tokens == sorted(tokens, key=len, reverse=True)


def test_scrub_tokens_removes_commune_from_snapshot() -> None:
    snapshot = {
        "tariff": {
            "source_url": "https://www.pidpa.be/ons-aanbod/je-gemeente/geel",
            "publication_label": "Pidpa per-commune tarieven 2026 (geel)",
            "basis_eur_per_m3": 2.1888,
        },
        "last_error": "could not locate huishoudelijk 2026 table for commune 'geel'",
    }
    scrubbed = _scrub_tokens(snapshot, ["geel"])
    assert "geel" not in scrubbed["tariff"]["source_url"]
    assert "geel" not in scrubbed["tariff"]["publication_label"]
    assert "geel" not in scrubbed["last_error"]
    # Non-string fields are left untouched.
    assert scrubbed["tariff"]["basis_eur_per_m3"] == 2.1888


def test_scrub_tokens_passes_none_and_empty_through() -> None:
    assert _scrub_tokens(None, ["geel"]) is None
    assert _scrub_tokens("nothing sensitive", []) == "nothing sensitive"
