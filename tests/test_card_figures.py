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

"""The tariff a card's printed figures agree on, over Tesseract's readings
of AIEC's pictures since 2021."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from custom_components.be_water_prices.providers._card_figures import (
    CardFigures,
    derived,
    numbers,
    read_figures,
)
from custom_components.be_water_prices.providers.base import ExtractorError

FIXTURES = Path(__file__).parent / "fixtures"


def _ocr(edition: str) -> str:
    return (FIXTURES / f"aiec_ocr_{edition}.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("edition", "cvd", "cva", "fse"),
    [
        ("2023-01-01", "2.34", "2.365", "0.0321"),
        ("2026-01-01", "2.46", "2.748", "0.0339"),
        ("2026-04-01", "3.05", "2.748", "0.0339"),
    ],
)
def test_each_readable_edition_gives_the_figures_it_prints(
    edition: str, cvd: str, cva: str, fse: str
) -> None:
    got = read_figures(_ocr(edition), label="AIEC")
    assert (got.cvd, got.cva, got.fse) == (Decimal(cvd), Decimal(cva), Decimal(fse))


def test_the_2021_reading_lost_a_point_and_is_refused() -> None:
    """Tesseract read 1.2694 as 12694; the first total is then nowhere."""
    text = _ocr("2021-06-01")
    assert "12694" in text
    with pytest.raises(ExtractorError, match="misses first total TVAC"):
        read_figures(text, label="AIEC")


def test_the_january_card_is_found_without_its_header_cvd() -> None:
    """The red 2,460 on salmon was not read; the detail table's 2,4600 was."""
    text = _ocr("2026-01-01")
    assert "2,460\n" not in text and "2,4600" in text
    assert read_figures(text, label="AIEC").cvd == Decimal("2.46")


def _figure_tokens(text: str, figures: CardFigures) -> list[str]:
    """How each of the ten derived figures is spelt in ``text``."""
    out = []
    for value in derived(figures).values():
        q = value.quantize(Decimal("0.0001"), ROUND_HALF_UP)
        token = format(q, "f").replace(".", ",")
        assert token in text, token
        out.append(token)
    return out


@pytest.mark.parametrize("position", range(10))
def test_one_misread_digit_in_any_figure_never_gives_another_tariff(position: int) -> None:
    """Every occurrence of the figure, one digit off: refused or the same card."""
    text = _ocr("2026-04-01")
    right = read_figures(text, label="AIEC")
    token = _figure_tokens(text, right)[position]
    last = token[-1]
    for wrong in "0123456789":
        if wrong == last:
            continue
        try:
            got = read_figures(text.replace(token, token[:-1] + wrong), label="AIEC")
        except ExtractorError:
            continue
        assert got == right


def test_a_whole_number_does_not_stand_for_a_figure() -> None:
    """The first run matched a bare 2 from the inhabitant table to any figure
    rounded to a unit, and left several Fonds social standing."""
    text = _ocr("2026-04-01").replace("1,5250€", "2")
    with pytest.raises(ExtractorError, match="first tranche"):
        read_figures(text, label="AIEC")


def test_two_tariffs_that_both_explain_the_text_are_refused() -> None:
    text = _ocr("2026-04-01")
    other = CardFigures(Decimal("2.46"), Decimal("2.748"), Decimal("0.0339"))
    extra = " ".join(
        format(v.quantize(Decimal("0.0001"), ROUND_HALF_UP), "f").replace(".", ",")
        for v in derived(other).values()
    )
    with pytest.raises(ExtractorError, match="2 tariffs explain every figure"):
        read_figures(f"{text}\n2,4600 € {extra}\n", label="AIEC")


def test_a_text_with_no_inputs_is_refused() -> None:
    with pytest.raises(ExtractorError, match="no CVD, CVA and Fonds social"):
        read_figures("Structure tarifaire 2026", label="AIEC")


def test_numbers_read_a_lone_point_both_ways() -> None:
    assert numbers("1.098 €") == {(Decimal("1.098"), 3), (Decimal(1098), 0)}
    assert numbers("5.035,02") == {(Decimal("5035.02"), 2)}
    assert numbers("0,0339") == {(Decimal("0.0339"), 4)}
