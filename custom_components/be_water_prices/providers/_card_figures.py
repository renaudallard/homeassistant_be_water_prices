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

"""The one CWaPE tariff a card's printed figures agree on.

A Walloon card prints its CVD, CVA and Fonds social, and beside them the
figures CWaPE's structure derives from those three: the redevance
``20 x CVD + 30 x CVA``, the three tranches ``0,5 x CVD``, ``CVD + CVA`` and
``0,9 x CVD + CVA``, each before and after VAT, and the first two
tranches' totals with the Fonds social after VAT. AIEC has printed all ten
at four decimals on every edition since 2021, under different labels, in
different layouts and fonts.

So nothing here looks for a label or a position. Every number in the text
is collected, and the tariff is the one CVD, CVA and Fonds social, all
taken from those numbers, whose ten derived figures are all among them
too. A reading that misses a figure, misreads one, or leaves two tariffs
standing is refused.

The text is an OCR engine's reading of a picture. An engine misreads a
digit here and there, and each misread is its own, since it does not do
arithmetic: a wrong tariff would need ten unrelated misreads to agree with
it. What a misread does instead is make a figure go missing, and the card
is then refused.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ._walloon_simple import _MAX_PLAUSIBLE_CVD, _MIN_PLAUSIBLE_CVD, _SPGE_WINDOWS
from .base import ExtractorError

# The VAT the derived figures are printed with. Another rate makes every
# figure after VAT disagree, and the card is refused for a person to read.
VAT = Decimal("1.06")
# Every derived figure is printed with four decimals.
_PLACES = 4
_QUANTUM = Decimal(1).scaleb(-_PLACES)
# The three inputs are printed with three or four.
_INPUT_PLACES = 3
# A number as an OCR engine returns it: digits, with points and commas
# between them. Bounded, so a long run of digits costs what it is long.
_NUMBER_RE = re.compile(r"\d[\d.,]{0,30}\d|\d")


@dataclass(frozen=True)
class CardFigures:
    """What a card prints, in EUR/m3 before VAT."""

    cvd: Decimal
    cva: Decimal
    fse: Decimal


def numbers(text: str) -> set[tuple[Decimal, int]]:
    """Every number in ``text`` with the decimals it was printed with.

    A comma is the decimal mark and points group thousands (``1.098,50``).
    A number with a point and no comma is read with the point as the
    decimal mark, and when three digits follow it, as a thousands point
    too: ``1.098`` is both 1,098 and 1098, and only the figures decide.
    """
    out: set[tuple[Decimal, int]] = set()
    for token in _NUMBER_RE.findall(text):
        if "," in token:
            whole, _, frac = token.rpartition(",")
            whole = whole.replace(".", "")
            if whole.isdigit() and frac.isdigit():
                out.add((Decimal(f"{whole}.{frac}"), len(frac)))
        elif "." in token:
            parts = token.split(".")
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                out.add((Decimal(token), len(parts[1])))
                if len(parts[1]) == 3:
                    out.add((Decimal(parts[0] + parts[1]), 0))
        else:
            out.add((Decimal(token), 0))
    return out


def derived(figures: CardFigures) -> dict[str, Decimal]:
    """The ten figures CWaPE's structure derives from a card's three."""
    cvd, cva, fse = figures.cvd, figures.cva, figures.fse
    redevance = 20 * cvd + 30 * cva
    first, second, third = Decimal("0.5") * cvd, cvd + cva, Decimal("0.9") * cvd + cva
    return {
        "redevance": redevance,
        "redevance TVAC": redevance * VAT,
        "first tranche": first,
        "first tranche TVAC": first * VAT,
        "second tranche": second,
        "second tranche TVAC": second * VAT,
        "third tranche": third,
        "third tranche TVAC": third * VAT,
        "first total TVAC": (first + fse) * VAT,
        "second total TVAC": (second + fse) * VAT,
    }


def _missing(figures: CardFigures, printed: set[Decimal]) -> list[str]:
    """The derived figures not among the four-decimal numbers read."""
    return [
        name
        for name, value in derived(figures).items()
        if value.quantize(_QUANTUM, ROUND_HALF_UP) not in printed
    ]


def _within(value: Decimal, low: float, high: float) -> bool:
    return Decimal(str(low)) <= value <= Decimal(str(high))


def read_figures(text: str, *, label: str) -> CardFigures:
    """The one tariff whose derived figures are all in ``text``.

    Raises :class:`ExtractorError` naming the figures the best candidate
    misses, or the candidates, when none or more than one explains the
    card. ``label`` names the card in the message.
    """
    read = numbers(text)
    printed = {value for value, places in read if places == _PLACES}
    inputs = sorted({value for value, places in read if places >= _INPUT_PLACES})
    cvds = [v for v in inputs if _within(v, _MIN_PLAUSIBLE_CVD, _MAX_PLAUSIBLE_CVD)]
    cvas = [v for v in inputs if _within(v, *_SPGE_WINDOWS["CVA"])]
    fses = [v for v in inputs if _within(v, *_SPGE_WINDOWS["FSE"])]
    found: list[CardFigures] = []
    best: tuple[int, CardFigures, list[str]] | None = None
    for cvd, cva, fse in itertools.product(cvds, cvas, fses):
        figures = CardFigures(cvd, cva, fse)
        missing = _missing(figures, printed)
        if not missing:
            found.append(figures)
        elif best is None or len(missing) < best[0]:
            best = (len(missing), figures, missing)
    if len(found) == 1:
        return found[0]
    if found:
        raise ExtractorError(
            f"{label}: {len(found)} tariffs explain every figure: "
            + "; ".join(f"CVD {f.cvd}, CVA {f.cva}, FSE {f.fse}" for f in found)
        )
    if best is None:
        raise ExtractorError(f"{label}: no CVD, CVA and Fonds social among the numbers read")
    _, figures, missing = best
    raise ExtractorError(
        f"{label}: no tariff explains every figure; the closest, CVD {figures.cvd}, "
        f"CVA {figures.cva}, FSE {figures.fse}, misses {', '.join(missing)}"
    )
