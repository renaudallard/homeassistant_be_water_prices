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

"""Reading a row of the card archive stays cheap whatever the row holds.

The coordinator reads up to twelve of them on a failing refresh, and a
row that is not one only sends it to the month before.
"""

from __future__ import annotations

import json
import threading
import types
from datetime import date
from typing import Any

import pytest

from custom_components.be_water_prices.providers import _archive

# Bound here at import, before conftest swaps the module's own name for the
# stub that keeps the suite off GitHub, so the tests below read the real one.
from custom_components.be_water_prices.providers._archive import archived_row
from tests import fixture_html

# The largest row on the branch on 2 November 2026, as it is stored.
_REAL_ROW = fixture_html("archive_row_water_link_2026-09.json")


def _serve(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    async def fetch(_session: Any, _url: str) -> str:
        return body

    monkeypatch.setattr(_archive, "fetch_text", fetch)


@pytest.mark.parametrize(
    "body",
    [
        "[" * 200_000 + "]" * 200_000,
        json.dumps({"utility": "swde", "pad": "x" * 100_000}),
        '{"utility": "swde", "x": ' + "[" * 100 + "]" * 100 + "}",
    ],
    ids=["deep", "large", "many brackets"],
)
async def test_a_row_far_past_any_real_one_is_no_row(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """The deep one raised RecursionError out of the read, and a large one
    was parsed on the event loop: 16 MB held it for two seconds a row."""
    _serve(monkeypatch, body)
    assert await archived_row(None, "swde", "default", date(2026, 11, 1)) is None  # type: ignore[arg-type]


async def test_a_real_row_is_read_whole_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    on_the_loop: list[bool] = []

    def loads(text: str) -> Any:
        on_the_loop.append(threading.current_thread() is threading.main_thread())
        return json.loads(text)

    monkeypatch.setattr(_archive, "json", types.SimpleNamespace(loads=loads))
    _serve(monkeypatch, _REAL_ROW)
    row = await archived_row(None, "water_link", "Beveren-Kruibeke-Zwijndrecht", date(2026, 9, 15))  # type: ignore[arg-type]
    assert row == json.loads(_REAL_ROW)
    assert on_the_loop == [False]
