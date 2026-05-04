"""HTML scraping helpers for water-utility tariff pages.

Most Belgian water utilities publish their numerical tariff card on a
plain HTML page, often in a ``<table>``. This module provides:

* :func:`fetch_html` -- async GET wrapper that mirrors :func:`_pdf.fetch_text`
  but returns the raw HTML body. (Re-exported from ``_pdf`` so a future
  caller does not need to import both modules.)
* :func:`parse_simple_table` -- extracts a ``<table>`` whose first
  column is a label and whose remaining columns are values, returning a
  ``list[list[str]]`` of rows. Skips empty cells and folds whitespace.
* :func:`find_table` -- locate the first ``<table>`` element whose
  rendered text contains every keyword in ``must_contain`` (case-
  insensitive, accent-folded). Useful when a page has more than one
  table and only one is the year you want.
* :func:`extract_amounts` -- regex helper that pulls every ``€ 12,34``
  amount from a string in document order.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from ._pdf import fetch_text, fold_accents, to_float

# Re-export so callers can import everything they need from this module.
fetch_html = fetch_text
__all__ = [
    "BeautifulSoup",
    "extract_amounts",
    "fetch_html",
    "find_table",
    "parse_simple_table",
    "to_float",
]


_AMOUNT_RE = re.compile(r"€\s*([0-9]+(?:[.,][0-9]+)?)")


def extract_amounts(text: str) -> list[float]:
    """Return every ``€ N[,N]`` amount found in ``text``, in order."""
    return [to_float(m.group(1)) for m in _AMOUNT_RE.finditer(text)]


def find_table(
    soup: BeautifulSoup,
    must_contain: tuple[str, ...],
) -> Tag | None:
    """Return the first ``<table>`` whose rendered text contains every
    keyword in ``must_contain``. Comparison is case-insensitive and
    accent-folded so "Janvier 2026" matches "janvier 2026".
    """
    needles = tuple(fold_accents(k) for k in must_contain)
    for table in soup.find_all("table"):
        haystack = fold_accents(table.get_text(" ", strip=True))
        if all(n in haystack for n in needles):
            return table
    return None


def parse_simple_table(table: Tag) -> list[list[str]]:
    """Extract a ``<table>`` as a list of rows of stripped cell text.

    Empty rows (all cells blank after stripping) are dropped. Every cell
    is whitespace-folded to a single space.
    """
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [
            re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])
        ]
        if any(c for c in cells):
            rows.append(cells)
    return rows
