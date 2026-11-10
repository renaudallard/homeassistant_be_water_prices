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

"""inBW extractor against the captured 2026 fixture."""

from __future__ import annotations

import ssl
from datetime import UTC, datetime
from pathlib import Path

import pytest

from custom_components.be_water_prices.pricing import compute_annual_cost
from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.inbw import parse_tariff
from tests import SPGE_CVA_2026, SPGE_FSE_2026, fixture_html


def test_parses_2026_components() -> None:
    t = parse_tariff(fixture_html("inbw_2026.html"), year=2026)
    assert t.cvd_eur_per_m3 == 2.6
    assert t.cva_eur_per_m3 == SPGE_CVA_2026
    assert t.fse_eur_per_m3 == SPGE_FSE_2026
    # Redevance = 20·CVD + 30·CVA = 52 + 82.44 = 134.44
    assert round(t.yearly_fixed_fee, 2) == round(20 * 2.6 + 30 * SPGE_CVA_2026, 2)


def test_matches_published_facture_for_100_m3() -> None:
    # The inBW page itself shows 584.261 € TVAC for a 100 m³ sample bill.
    # Our cost engine has to reproduce that to the cent or we know either
    # the parser or the Wallonia tier math is off.
    t = parse_tariff(fixture_html("inbw_2026.html"), year=2026)
    assert compute_annual_cost(t, 100, 1) == 584.26


def test_raises_when_table_missing() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>no table here</body></html>")


def test_a_page_still_on_last_years_card_is_dated_last_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import date

    from custom_components.be_water_prices.providers import _walloon_simple, inbw

    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2027, 1, 5)

    monkeypatch.setattr(inbw, "belgian_today", _FakeDate.today)
    monkeypatch.setattr(_walloon_simple, "belgian_today", _FakeDate.today)
    t = parse_tariff(fixture_html("inbw_2026.html"))
    assert t.valid_from == date(2026, 1, 1)
    # Dated last year, so it stands until 31 March of this one.
    assert t.valid_until == date(2027, 3, 31)


def test_the_cva_is_read_off_the_30_x_cva_row() -> None:
    """The 30 x CVA row is the page's own statement of the CVA, rounded to
    the decimals it is published to."""
    page = fixture_html("inbw_2026.html")
    assert "82,440 €" in page
    assert parse_tariff(page, year=2026).cva_eur_per_m3 == 2.748
    assert parse_tariff(page.replace("82,440 €", "87,000 €"), year=2026).cva_eur_per_m3 == 2.9


async def test_a_tls_failure_is_raised_and_never_retried_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """inBW is fetched through a context that also trusts the intermediates
    it does not send. A chain that still fails is raised as it is, for the
    live check to file: retrying it without verification hands the page to
    whoever is on the path."""
    from unittest.mock import AsyncMock

    from custom_components.be_water_prices.providers import inbw
    from custom_components.be_water_prices.providers.base import TransientFetchError

    async def _refused(*_a: object, **_k: object) -> str:
        try:
            raise ssl.SSLCertVerificationError(1, "unable to get local issuer certificate")
        except ssl.SSLError as err:
            raise TransientFetchError("network error fetching inBW") from err

    refused = AsyncMock(side_effect=_refused)
    monkeypatch.setattr(inbw, "fetch_html", refused)
    with pytest.raises(TransientFetchError):
        await inbw.fetch(None)  # type: ignore[arg-type]
    assert refused.await_count == 1
    assert refused.await_args is not None
    assert refused.await_args.kwargs == {"ssl_context": inbw.ssl_context()}

    page = AsyncMock(return_value=fixture_html("inbw_2026.html"))
    monkeypatch.setattr(inbw, "fetch_html", page)
    assert (await inbw.fetch(None)).utility == "inbw"  # type: ignore[arg-type]


# certifi carries a root with a serial number of zero, which cryptography
# loads with a warning.
@pytest.mark.filterwarnings("ignore:Parsed a serial number which wasn't positive")
def test_the_shipped_intermediates_complete_the_chain_inbw_serves() -> None:
    """The certificate eau.inbw.be served on 10 November 2026, with no
    intermediate, verified through the shipped file against certifi's roots,
    the ones the fetch's context trusts. Without the file it does not."""
    import certifi
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

    from custom_components.be_water_prices.providers import inbw

    leaf = x509.load_pem_x509_certificate(
        (Path(__file__).parent / "fixtures" / "inbw_leaf_2025_11.pem").read_bytes()
    )
    shipped = x509.load_pem_x509_certificates(inbw._INTERMEDIATES.read_bytes())
    roots = x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes())
    verifier = (
        PolicyBuilder()
        .store(Store(roots))
        .time(datetime(2026, 11, 10, 12, tzinfo=UTC))
        .build_server_verifier(x509.DNSName("eau.inbw.be"))
    )
    names = [
        str(cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value)
        for cert in verifier.verify(leaf, shipped)
    ]
    assert names == [
        "*.inbw.be",
        "Go Daddy Secure Certificate Authority - G2",
        "Go Daddy Root Certificate Authority - G2",
    ]
    with pytest.raises(VerificationError):
        verifier.verify(leaf, [])


@pytest.mark.timeout(5)
def test_the_redevance_row_check_stays_linear_on_a_long_row() -> None:
    """The gap after the label read on to the end of the row from every
    "redevance" in it: the square of its length."""
    from custom_components.be_water_prices.providers.inbw import _REDEVANCE_ROW_RE

    assert _REDEVANCE_ROW_RE.search("redevance 20 " * 100_000) is None
    assert _REDEVANCE_ROW_RE.search("redevance annuelle (20 x cvd) 1 52,000 €") is not None
