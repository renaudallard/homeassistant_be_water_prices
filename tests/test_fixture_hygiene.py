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

The scan reads every fixture as bytes, so it sees images and anything
else that is not text. A PDF keeps its page text, and often its XMP,
in deflated streams that a scan of the raw bytes cannot see, so those
are inflated and scanned as well.
"""

from __future__ import annotations

import re
import zlib
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
    b"@eauxducondroz.be",
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


# No fixture stream comes close: the largest, a font, inflates to
# about 400 KB.
_INFLATE_CAP = 16 * 1024 * 1024
_INFLATE_CHUNK = 65536
_STREAM_RE = re.compile(rb"(?<!end)stream\r?\n")


def _inflated_streams(blob: bytes) -> list[bytes]:
    """Every deflated stream in ``blob`` that inflates to text.

    A stream that inflates to binary, which a NUL byte gives away, is a
    font, an image or a cross-reference table. Nobody types an address
    into those, and a font table throws up shapes like "5@K.BkM" that
    the address pattern takes for one.
    """
    view = memoryview(blob)
    out: list[bytes] = []
    for match in _STREAM_RE.finditer(blob):
        inflater = zlib.decompressobj()
        parts: list[bytes] = []
        size = 0
        try:
            # Fed in chunks so a stream near the start does not copy the
            # rest of the file into the decompressor's unconsumed tail.
            for start in range(match.end(), len(blob), _INFLATE_CHUNK):
                parts.append(
                    inflater.decompress(view[start : start + _INFLATE_CHUNK], _INFLATE_CAP - size)
                )
                size += len(parts[-1])
                if inflater.eof or size >= _INFLATE_CAP:
                    break
        except zlib.error:
            pass
        data = b"".join(parts)
        if data and b"\0" not in data:
            out.append(data)
    return out


def _scanned(path: Path) -> list[bytes]:
    blob = path.read_bytes()
    if path.suffix.lower() != ".pdf":
        return [blob]
    return [blob, *_inflated_streams(blob)]


def _foreign_addresses(path: Path) -> set[bytes]:
    return {
        m.group(0)
        for blob in _scanned(path)
        for m in _EMAIL_RE.finditer(blob)
        if not any(d in m.group(0).lower() for d in _ALLOWED)
    }


def _credential(path: Path) -> bytes | None:
    for blob in _scanned(path):
        for pattern in _SECRET_RES:
            match = pattern.search(blob)
            if match is not None:
                return match.group(0)
    return None


def _fixture_files() -> list[Path]:
    out: list[Path] = []
    for rel in _FIXTURE_DIRS:
        directory = _ROOT / rel
        if directory.is_dir():
            out.extend(p for p in directory.rglob("*") if p.is_file())
    return sorted(out)


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: p.name)
def test_no_personal_email_in_fixture(path: Path) -> None:
    found = _foreign_addresses(path)
    assert not found, (
        f"{path.relative_to(_ROOT)} carries an address that is not a utility's own: "
        f"{sorted(x.decode('utf-8', 'replace') for x in found)}. Strip the file's "
        "metadata before committing it, or drop the fixture."
    )


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: p.name)
def test_no_credential_in_fixture(path: Path) -> None:
    found = _credential(path)
    assert found is None, (
        f"{path.relative_to(_ROOT)} looks like it carries a credential: "
        f"{found[:24].decode('utf-8', 'replace')}..."
    )


def _deflated_pdf(tmp_path: Path, content: bytes) -> Path:
    body = zlib.compress(content)
    path = tmp_path / "capture.pdf"
    path.write_bytes(
        b"%%PDF-1.7\n1 0 obj\n<</Type/Metadata/Subtype/XML/Filter/FlateDecode/Length %d>>"
        b"stream\n%s\nendstream\nendobj\n%%EOF\n" % (len(body), body)
    )
    return path


def test_address_in_deflated_stream_is_found(tmp_path: Path) -> None:
    address = b"jan.peeters@gmail.com"
    path = _deflated_pdf(tmp_path, b"<dc:creator>" + address + b"</dc:creator>")
    assert address not in path.read_bytes()
    assert _foreign_addresses(path) == {address}


def test_key_in_deflated_stream_is_found(tmp_path: Path) -> None:
    # Built at run time so the source itself carries no key shape.
    key = b"AIza" + b"x" * 35
    path = _deflated_pdf(tmp_path, b"BT (" + key + b") Tj ET")
    assert key not in path.read_bytes()
    assert _credential(path) == key
