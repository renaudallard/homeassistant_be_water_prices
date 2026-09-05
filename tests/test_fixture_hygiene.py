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

"""Committed fixtures must not carry anyone's personal data.

A capture taken from a live site brings the site's own metadata with
it. PDFs are the worst offender: the XMP block keeps the designer's
name and address long after the visible page stops showing them, and
nothing in a normal diff review shows it.

The scan reads every fixture as bytes so it sees inside PDFs, images
and anything else that is not text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_DIRS = ("tests/fixtures", "research/fixtures")

# An address only counts when its final label looks like a real TLD --
# letters, 2 to 6 of them. That alone drops every npm specifier the
# vendored bundles are full of ("react@18.3.1", "core-js-bundle@3.2.1").
_EMAIL_RE = re.compile(rb"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})*\.[A-Za-z]{2,6}\b")
# What is left is either a utility's own published contact address, a
# placeholder the page prints, or a service identifier. None of those is
# personal data; anything else is somebody's name and has to go.
_ALLOWED = (
    b"@agsoknokke-heist.be",
    b"@aiem.be",
    b"@cile.be",
    b"@dewatergroep.be",
    b"@farys.be",
    b"@ieg.be",
    b"@inasep.be",
    b"@inbw.be",
    b"@pidpa.be",
    b"@swde.be",
    b"@vivaqua.be",
    b"@water-link.be",
    b"@aquaduin.be",
    b"@exemple.com",
    b"@example.com",
    b"@example.invalid",
    b"@sentry.wixpress.com",
    b"@sentry-next.wixpress.com",
)

# Credential shapes worth catching early: a Google API key ships in a
# maps embed, and a bearer token in a captured XHR response.
_SECRET_RES = (
    re.compile(rb"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(rb"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(rb"(?i)\bsk-[A-Za-z0-9]{20,}"),
)


def _fixture_files() -> list[Path]:
    out: list[Path] = []
    for rel in _FIXTURE_DIRS:
        directory = _ROOT / rel
        if directory.is_dir():
            out.extend(p for p in directory.rglob("*") if p.is_file())
    return sorted(out)


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: p.name)
def test_no_personal_email_in_fixture(path: Path) -> None:
    blob = path.read_bytes()
    found = {
        m.group(0)
        for m in _EMAIL_RE.finditer(blob)
        if not any(d in m.group(0).lower() for d in _ALLOWED)
    }
    assert not found, (
        f"{path.relative_to(_ROOT)} carries an address that is not a utility's own: "
        f"{sorted(x.decode('utf-8', 'replace') for x in found)}. Strip the file's "
        "metadata before committing it, or drop the fixture."
    )


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: p.name)
def test_no_credential_in_fixture(path: Path) -> None:
    blob = path.read_bytes()
    for pattern in _SECRET_RES:
        match = pattern.search(blob)
        assert match is None, (
            f"{path.relative_to(_ROOT)} looks like it carries a credential: "
            f"{match.group(0)[:24].decode('utf-8', 'replace')}..."
        )
