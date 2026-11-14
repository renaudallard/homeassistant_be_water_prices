"""The card archive's texts as a render cache.

A card's bytes hash to a digest, and every row of the card archive names,
per reader variant, the text that digest rendered to. Installed as the
readers' render hook (``providers/_pdf.render_through``), a downloaded card
whose bytes are already known is served that text instead of being rendered
again. The download still happens, so whoever installs the cache keeps
measuring timing, bytes, status codes and freshness; only the render, which
is the cost, is skipped when nothing changed.

Shared by the card archiver, which also keeps bytes it has not seen, and the
live check, which reads the archive and renders only what is new. Imports
nothing from the integration so either script can use it as is.

A card published as a picture is read the same way, with Tesseract instead
of the PDF reader: the archiver installs :meth:`StoredTexts.read_picture`
as the readers' picture reader, and a picture whose bytes are known is
served its stored text without Tesseract running.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import importlib.metadata
import inspect
import io
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

# The PDF reader, which pins the pdfminer.six it runs on, and the code that
# drives it. A stored text is only what these made of a card, so each PDF
# source of a row names them, and a text is served again only to the same
# version and code: a reader upgrade or a render fix reads every card
# afresh, in the archive, the live check and the drift check alike.
_READER = "pdfplumber"
_PROVIDERS = (
    Path(__file__).resolve().parent.parent / "custom_components" / "be_water_prices" / "providers"
)
# The reader and the stream guard it runs before reading.
_RENDER_CODE = (_PROVIDERS / "_pdf.py", _PROVIDERS / "_pdf_guard.py")


def read_text(path: Path) -> str:
    """A stored text exactly as it was fetched: no newline translation, so
    a replay hands the parser the very bytes it read the first time."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def digest_of(pdf: str) -> str:
    """The digest a row names its PDF or its picture by."""
    return Path(pdf).stem


# What a picture is read with: French, one block of text, the picture
# grown to about 1800 px across (at least twice), which is where Tesseract
# read three of AIEC's four editions since 2021 and refused the fourth.
_OCR_ARGS = ("-l", "fra", "--psm", "6")
_OCR_WIDTH = 1800
_OCR_TIMEOUT_S = 300


def ocr_available() -> bool:
    """Whether Tesseract is installed here."""
    return shutil.which("tesseract") is not None


def _prepared(payload: bytes) -> bytes:
    """The picture as Tesseract is given it: grey, on white, grown."""
    from PIL import Image

    with Image.open(io.BytesIO(payload)) as picture:
        rgba = picture.convert("RGBA")
    flat = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba)
    grey = flat.convert("L")
    scale = max(2, round(_OCR_WIDTH / grey.width))
    grown = grey.resize((grey.width * scale, grey.height * scale), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    grown.save(out, format="PNG")
    return out.getvalue()


def ocr_picture(payload: bytes) -> str:
    """What Tesseract reads off a picture of a card."""
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", *_OCR_ARGS],
        input=_prepared(payload),
        capture_output=True,
        timeout=_OCR_TIMEOUT_S,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        check=True,
    )
    return result.stdout.decode("utf-8", errors="replace")


@functools.cache
def ocr_line() -> str:
    """The Tesseract version, its arguments and a digest of the preparation,
    as a picture source records them."""
    try:
        first = subprocess.run(
            ["tesseract", "--version"], capture_output=True, timeout=30, check=True
        ).stdout.decode("utf-8", errors="replace")
        version = first.split()[1] if len(first.split()) > 1 else "unknown"
    except (OSError, subprocess.SubprocessError):
        version = "absent"
    prep = hashlib.sha256(
        (inspect.getsource(_prepared) + repr((_OCR_ARGS, _OCR_WIDTH))).encode("utf-8")
    ).hexdigest()[:16]
    return f"tesseract=={version} {' '.join(_OCR_ARGS)} prep={prep}"


def picture_suffix(payload: bytes) -> str:
    """The file extension a picture's bytes call for."""
    return "png" if payload.startswith(b"\x89PNG") else "jpg"


def readers_line() -> str:
    """The reader version and a digest of the render code, as a PDF source
    records them."""
    try:
        version = importlib.metadata.version(_READER)
    except importlib.metadata.PackageNotFoundError:
        version = "absent"
    code = hashlib.sha256()
    for path in _RENDER_CODE:
        code.update(path.read_bytes())
    render = code.hexdigest()[:16]
    return f"{_READER}=={version} render={render}"


class StoredTexts:
    """What the archive already knows about card bytes."""

    def __init__(self, archive: Path, *, serve: bool = True) -> None:
        self.archive = archive
        # False when every card must be rendered afresh (--rerender).
        self.serve = serve
        # (variant, digest) -> text path in the archive, from every stored row
        # whose text the installed reader and render code made.
        self.texts: dict[tuple[str, str], str] = {}
        readers = readers_line()
        ocr = ocr_line()
        # The rows sit under cards/, as scripts/archive_cards.py writes them.
        for row in archive.glob("cards/*/*/????-??.json"):
            try:
                sources = json.loads(row.read_text(encoding="utf-8")).get("_sources", [])
            except ValueError:
                continue
            for source in sources:
                if "pdf" in source and source.get("readers") == readers:
                    self.texts[(source["variant"], digest_of(source["pdf"]))] = source["text"]
                elif "picture" in source and source.get("ocr") == ocr:
                    self.texts[("ocr", digest_of(source["picture"]))] = source["text"]
        # What this run rendered, so a second card on the same bytes is
        # served too.
        self.fresh: dict[tuple[str, str], str] = {}
        # url -> digest, for whoever needs to name the PDF behind a URL.
        self.digests: dict[str, str] = {}
        # Every card this run was handed, in order: a provider that gets a
        # card some other way than through a reader leaves nothing in the
        # text memo, and this is how the archiver still learns what it
        # read. Cleared per fetch by the caller.
        self.calls: list[tuple[str, str, str, str]] = []
        self.rendered = 0
        self.unrendered = 0

    def digest_for(self, url: str) -> str | None:
        """The digest of the PDF behind ``url``, once its bytes were seen."""
        return self.digests.get(url)

    def keep(self, digest: str, payload: bytes, suffix: str = "pdf") -> None:
        """Called once per downloaded card; the archiver stores unseen bytes."""

    async def render(
        self, variant: str, url: str, payload: bytes, renderer: Callable[[bytes], str]
    ) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        self.digests[url] = digest
        self.keep(digest, payload)
        key = (variant, digest)
        text = self.fresh.get(key)
        if text is None and self.serve:
            stored = self.texts.get(key)
            if stored is not None and (self.archive / stored).exists():
                text = read_text(self.archive / stored)
        if text is not None:
            self.unrendered += 1
            self.calls.append((variant, url, digest, text))
            return text
        text = await asyncio.to_thread(renderer, payload)
        self.rendered += 1
        self.fresh[key] = text
        self.calls.append((variant, url, digest, text))
        return text

    async def read_picture(self, url: str, payload: bytes) -> str:
        """The text of a card picture, read with Tesseract only when its bytes
        are new, installed as the readers' picture reader."""
        digest = hashlib.sha256(payload).hexdigest()
        self.digests[url] = digest
        self.keep(digest, payload, picture_suffix(payload))
        key = ("ocr", digest)
        text = self.fresh.get(key)
        if text is None and self.serve:
            stored = self.texts.get(key)
            if stored is not None and (self.archive / stored).exists():
                text = read_text(self.archive / stored)
        if text is not None:
            self.unrendered += 1
        else:
            text = await asyncio.to_thread(ocr_picture, payload)
            self.rendered += 1
            self.fresh[key] = text
        self.calls.append(("ocr", url, digest, text))
        return text
