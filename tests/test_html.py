"""Unit tests for the bs4-based HTML helpers."""

from __future__ import annotations

from bs4 import BeautifulSoup

from custom_components.be_water_prices.providers._html import (
    extract_amounts,
    find_table,
    parse_simple_table,
)


def test_extract_amounts_handles_belgian_decimals() -> None:
    assert extract_amounts("Cost is € 5,35 per m³ and € 40,23 per year") == [5.35, 40.23]


def test_extract_amounts_handles_dot_decimals() -> None:
    assert extract_amounts("€ 0.102 / kWh") == [0.102]


def test_extract_amounts_returns_empty_for_no_match() -> None:
    assert extract_amounts("no euros here, just pesos $5.00") == []


def test_find_table_returns_first_keyword_match() -> None:
    html = """
    <table><tr><td>old rates 2024</td></tr></table>
    <table><tr><td>Price from 2026 (VAT)</td><td>€ 5,35</td></tr></table>
    """
    soup = BeautifulSoup(html, "html.parser")
    found = find_table(soup, must_contain=("2026", "vat"))
    assert found is not None
    assert "5,35" in found.get_text()


def test_find_table_accent_folds() -> None:
    html = "<table><tr><td>Janvier 2026</td></tr></table>"
    soup = BeautifulSoup(html, "html.parser")
    found = find_table(soup, must_contain=("janvier 2026",))
    assert found is not None


def test_find_table_returns_none_when_no_match() -> None:
    soup = BeautifulSoup("<table><tr><td>nope</td></tr></table>", "html.parser")
    assert find_table(soup, must_contain=("2026",)) is None


def test_parse_simple_table_skips_empty_rows() -> None:
    soup = BeautifulSoup(
        """<table>
            <tr><th>label</th><th>value</th></tr>
            <tr><td></td><td></td></tr>
            <tr><td>Fee</td><td>€ 40,23</td></tr>
        </table>""",
        "html.parser",
    )
    rows = parse_simple_table(soup.find("table"))
    assert rows == [["label", "value"], ["Fee", "€ 40,23"]]
