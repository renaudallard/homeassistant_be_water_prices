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

"""A failed fetch's message reaches last_error, and the log, as one short line.

The message can quote what a card host chose, and it is copied onto every
sensor, the stale-snapshot Repair card, the log and diagnostics. A
provider that logs what a page said, a value or a message it then
recovers from, quotes it as short.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.be_water_prices import config_flow
from custom_components.be_water_prices.const import DOMAIN
from custom_components.be_water_prices.diagnostics import async_get_config_entry_diagnostics
from custom_components.be_water_prices.providers import (
    _pdf,
    aiec,
    aquaduin,
    farys,
    vivaqua,
    water_link,
)
from custom_components.be_water_prices.providers.base import (
    ExtractorError,
    WaterTariff,
    belgian_today,
)
from tests import fixture_html
from tests.test_ha_coordinator import _setup_entry, _this_years_card


def test_the_stream_guard_names_a_long_filter_in_a_few_characters() -> None:
    """A one-megabyte filter name gave a message of a million characters."""
    payload = b"%PDF-1.4\n1 0 obj<</Filter/" + b"A" * 1_000_000 + b">>stream\nx\nendstream\n"
    with pytest.raises(ExtractorError, match="filter") as err:
        _pdf.guard_pdf_streams(payload)
    assert len(str(err.value)) < 80


async def test_last_error_keeps_the_first_line_of_a_long_message(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A 16 MB card put its 16 MB on each of eleven sensors, in the Repair
    card, in diagnostics and in a warning on every refresh."""
    yesterday = belgian_today() - timedelta(days=1)
    answers: list[Any] = [_this_years_card(yesterday), ExtractorError("x" * 100_000 + "\nmore")]

    async def fetch(_session: Any) -> WaterTariff:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, WaterTariff)
        return answer

    entry = await _setup_entry(hass, fetch)
    coordinator = entry.runtime_data
    caplog.set_level(logging.WARNING)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    expected = "x" * 500
    assert coordinator.data is not None
    assert coordinator.data.last_error == expected
    published = {
        state.attributes["last_error"]
        for state in hass.states.async_all("sensor")
        if "last_error" in state.attributes
    }
    assert published == {expected}
    issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator.stale_issue_id)
    assert issue is not None and issue.translation_placeholders is not None
    assert issue.translation_placeholders["last_error"] == expected
    dump = await async_get_config_entry_diagnostics(hass, entry)
    assert dump["snapshot"]["last_error"] == expected
    assert max(len(record.getMessage()) for record in caplog.records) < 1000
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r", "\x85", "\u2028", "\u2029"])
def test_last_error_ends_where_python_ends_a_line(separator: str) -> None:
    assert _pdf.first_line(f"  card unreadable{separator}second line") == "card unreadable"


_MEGABYTE = "x" * 1_000_000
_OFF_SITE = "https://evil.example/" + _MEGABYTE + ".pdf"


def _farys_with_a_long_period() -> None:
    """The real Zaventem answer, its active period button given a megabyte
    of value: the card still parses, so nothing reaches last_error."""
    commands = json.loads(fixture_html("farys_zaventem_2026.json"))
    for command in commands:
        data = command.get("data")
        if isinstance(data, str) and "js-period-rates" in data:
            command["data"] = re.sub(
                r'(<li class="active">\s*<button[^>]*?value=")[^"]*(")',
                lambda m: m.group(1) + _MEGABYTE + m.group(2),
                data,
                count=1,
            )
    farys.parse_tariff(json.dumps(commands))


async def _off_site_link(*_args: object) -> str:
    return _OFF_SITE


async def _refused(*_args: object) -> Any:
    raise ExtractorError(_MEGABYTE)


async def _aquaduin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aquaduin, "fetch_and_parse", _off_site_link)
    with pytest.raises(ExtractorError, match="off-site"):
        await aquaduin.fetch(None)  # type: ignore[arg-type]


async def _water_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(water_link, "fetch_and_parse", _off_site_link)
    with pytest.raises(ExtractorError, match="off-site"):
        await water_link._fetch_pdf_text(None)  # type: ignore[arg-type]


async def _vivaqua(monkeypatch: pytest.MonkeyPatch) -> None:
    async def pages(_session: object, url: str, *_args: object) -> Any:
        if url == vivaqua.SOURCE_URL:
            return "the tariff"
        raise ExtractorError(_MEGABYTE)

    monkeypatch.setattr(vivaqua, "fetch_and_parse", pages)
    assert await vivaqua.fetch(None) == "the tariff"  # type: ignore[arg-type,comparison-overlap]


async def _aiec_page(monkeypatch: pytest.MonkeyPatch) -> None:
    async def aggregator(_session: object) -> str:
        return "the aggregator's card"

    monkeypatch.setattr(aiec, "fetch_html", _refused)
    monkeypatch.setattr(aiec, "_fetch_aggregator", aggregator)
    assert await aiec.fetch(None) == "the aggregator's card"  # type: ignore[arg-type,comparison-overlap]


async def _aiec_picture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aiec, "fetch_picture_text", _refused)
    assert await aiec._read_picture(None, date(2026, 4, 1), "https://x.test/p.jpg") is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "provider",
    [_aquaduin, _water_link, _vivaqua, _aiec_page, _aiec_picture],
    ids=["aquaduin", "water-link", "vivaqua", "aiec page", "aiec picture"],
)
async def test_a_provider_logs_what_a_page_said_in_a_few_hundred_characters(
    provider: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Aquaduin and Water-link quoted a whole off-site link at INFO, and
    VIVAQUA and AIEC a whole failure they recover from at WARNING: a
    megabyte on every fetch, which last_error's cut never reached."""
    caplog.set_level(logging.DEBUG)
    await provider(monkeypatch)
    assert caplog.records
    assert max(len(record.getMessage()) for record in caplog.records) < 400


def test_farys_names_a_long_period_in_a_few_hundred_characters(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The card parsed, so last_error was never set, and the WARNING that
    quoted the period was a megabyte long on every fetch at the default
    log level."""
    caplog.set_level(logging.WARNING)
    _farys_with_a_long_period()
    assert caplog.records
    assert max(len(record.getMessage()) for record in caplog.records) < 400


async def test_a_failed_commune_list_is_logged_in_a_few_hundred_characters(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Water-link's commune list quotes the card link its page gives, at any
    length, and the setup form logged the failure with its traceback: the
    message of this year's link and of last year's, megabytes at ERROR on
    every render of the form."""
    link = "https://elsewhere.example/" + "a" * 1_000_000 + ".pdf"

    async def failing(_session: Any) -> Any:
        try:
            raise ExtractorError(f"Water-link tariff PDF link points off-site: {link}")
        except ExtractorError:
            raise ExtractorError(f"Water-link tariff PDF link points off-site: {link}") from None

    extractor = SimpleNamespace(supports_communes=True, list_communes=failing)
    monkeypatch.setattr(config_flow, "get", lambda _utility_id: extractor)
    caplog.set_level(logging.INFO)
    assert await config_flow._async_communes(hass, "water_link") == ()
    assert caplog.records
    assert len(caplog.text) < 400
