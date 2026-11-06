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

"""The stream guard: what a PDF's streams would inflate to, read off its bytes.

It runs inside the PDF reader's child process, before pdfplumber is
imported, under the child's memory and time limits. Its passes are regular
expressions, and a single scan of a crafted 16 MiB card held the
interpreter for seconds, which in Home Assistant's own process stalled the
event loop for as long, whatever thread it ran in. The child loads this
file on its own by path, so it imports nothing but the standard library.
"""

from __future__ import annotations

import re
import zlib


class Refused(Exception):
    """A PDF the guard will not hand to the reader, and why."""


# What the streams inside one PDF may inflate to, in total. The three
# tariff cards on file inflate to under a megabyte; a card that needs more
# than this is not a tariff card.
MAX_INFLATED_BYTES = 64 * 1024 * 1024
# pdfminer starts a stream's data after the line the keyword is on: it
# reads to the first CR or LF, whatever else the line holds, and a CR
# followed by LF counts once. "endstream" is not a stream.
_STREAM_RE = re.compile(rb"(?<!end)stream")
_EOL_RE = re.compile(rb"\r\n|\r|\n")
_OBJ_HEADER_RE = re.compile(rb"(?<![0-9])[0-9]+[ \t\r\n]+[0-9]+[ \t\r\n]+\Z")


# pdfminer drops a "#" in a name that no hex digit follows.
_LONE_HASH = rb"(?:#(?![0-9A-Fa-f]))*+"


def _name(word: bytes) -> bytes:
    """A pattern for ``word`` inside a PDF name, each byte either as itself
    or as the "#xx" escape pdfminer reads back to it, with any lone "#"
    around them that pdfminer drops."""
    escapes = (b"(?:%s|#(?i:%02x))" % (re.escape(bytes([c])), c) for c in word)
    return _LONE_HASH + _LONE_HASH.join(escapes) + _LONE_HASH


# A name ends at whitespace or a delimiter, so /FontFile and /First are not /F.
_NAME_END = rb"(?![^\s/\[\]()<>{}%])"
# pdfminer takes the abbreviated /F for /Filter, from the stream's own
# dictionary only, which :func:`_outer_spans` picks out.
_FILTER_KEY_RE = re.compile(
    b"/" + _name(b"F") + b"(?:" + _name(b"ilter") + b")?" + _NAME_END + rb"\s*"
)
# pdfminer's R makes a reference of the two tokens before it, whatever
# they are, so a value is taken as read only when the next token cannot be
# the second of them: the end of the dictionary, or a name that neither
# R nor a comment comes after. That is one token of lookahead and no more.
# An R that finds no integer drops both tokens it took, so a run of them
# further on can still reach back and take the value, and no pattern here
# follows that; the PDF reader's memory ceiling bounds what such a stream
# inflates to.
_KEPT = rb"(?=\s*(?:>>|/[^\s/\[\]()<>{}%]*+\s*+(?!%|R(?![^\s#/\[\]()<>{}%]))))"
# The only values let through: Flate or JPEG spelled plainly, and an
# integer alone, which is an annotation's flags rather than a filter.
_PLAIN_FILTERS = frozenset({b"/FlateDecode", b"/DCTDecode"})
_PLAIN_FILTER_RE = re.compile(
    b"(?:" + b"|".join(map(re.escape, sorted(_PLAIN_FILTERS))) + b")" + _NAME_END + _KEPT
)
_FLAGS_RE = re.compile(rb"[0-9]++" + _KEPT)
_FILTER_NAME_RE = re.compile(rb"/[^\s/\[\]()<>{}%]*")
_ENCRYPT_RE = re.compile(b"/" + _name(b"Encrypt") + _NAME_END)
_INFLATE_CHUNK = 65536
# The bytes that change how pdfminer reads what follows them in a
# dictionary: the brackets of dictionaries, arrays and procedures and the
# start of a literal string, a hex string or a comment. Inside a literal
# string only its parentheses count, and a backslash takes the byte after it
# out of the count. A hex string ends at the first byte that is neither a
# hex digit nor whitespace, which pdfminer then reads afresh, so "<41>>"
# closes a dictionary.
_DICT_SYNTAX_RE = re.compile(rb"<<|>>|[\[\]{}(<%]")
_CLOSER = {b"<<": b">>", b"[": b"]", b"{": b"}"}
_STRING_SYNTAX_RE = re.compile(rb"\\.|[()]", re.DOTALL)
_HEX_STRING_END_RE = re.compile(rb"[^\s0-9A-Fa-f]")
_COMMENT_END_RE = re.compile(rb"[\r\n]")
# How many brackets, strings and comments the walk below follows in one
# head before reading the whole head instead. The stream dictionaries on
# file hold at most a dozen; a head made of millions of stray brackets cost
# a minute and two gigabytes inside Home Assistant before the reader child
# ever started. Reading the whole head finds every key the walk would, and
# nested ones too, so past the cap a card can only be refused more often.
_MAX_DICT_SYNTAX = 4096
# How much work the guard may do on one card, in steps: a stream keyword
# tried, an "obj" looked at on the way back to a dictionary's start, a
# bracket, string or comment walked, a filter key read, and a call into
# the decompressor, which is held to a chunk in and a chunk out. Each
# costs at most a few tens of microseconds on a Raspberry Pi 4, so the
# limit holds the loops to about a second, where four megabytes of keywords
# took 23 seconds and passed. What it does not bound is a single regular
# expression scan over a long stretch of a crafted card, seconds on a 16
# MiB one; the pass runs in the reader's child for that reason, where the
# child's time limit holds it. Counted on 6 November 2026, the three water
# cards on file take at most 182 steps, 19 other PDFs of the water sector
# and elsewhere at most 3,310, and 441 electricity and gas cards at most
# 11,269.
MAX_GUARD_STEPS = 50_000


class _Steps:
    """The steps the guard has taken on one card."""

    def __init__(self) -> None:
        self.taken = 0

    def take(self) -> None:
        """Count a step, refusing the card past :data:`MAX_GUARD_STEPS`."""
        self.taken += 1
        if self.taken > MAX_GUARD_STEPS:
            raise Refused(
                f"PDF takes more than {MAX_GUARD_STEPS} steps to check; refusing to read it"
            )


def _string_end(head: bytes, position: int, steps: _Steps) -> int:
    """Where the literal string opened just before ``position`` ends, else the end of ``head``."""
    depth = 1
    for syntax in _STRING_SYNTAX_RE.finditer(head, position):
        steps.take()
        if syntax[0] == b"(":
            depth += 1
        elif syntax[0] == b")":
            depth -= 1
            if not depth:
                return syntax.end()
    return len(head)


def _outer_spans(head: bytes, steps: _Steps) -> list[tuple[int, int]]:
    """The stretches of ``head`` that hold the stream dictionary's own keys.

    pdfminer reads /F and /Filter from the stream's dictionary alone, so a
    font named /F in a form's /Resources is no filter. Nested dictionaries,
    arrays, procedures, strings and comments are left out, and the bytes
    outside any of them are kept. pdfminer skips a closing bracket that is
    not the one the innermost open dictionary, array or procedure waits
    for, so ">>" inside an array closes nothing, and the walk skips it the
    same way. Its reading holds only once everything the head opens is
    closed: pdfminer takes the object that closed last before "stream",
    which is a nested one while the outer is still open, so a head left
    open is read whole, and so is one with more than
    :data:`_MAX_DICT_SYNTAX` brackets, strings and comments in it.
    """
    spans = []
    # The closing bracket each open dictionary, array or procedure waits for.
    waiting: list[bytes] = []
    start = position = 0
    for _ in range(_MAX_DICT_SYNTAX):
        steps.take()
        syntax = _DICT_SYNTAX_RE.search(head, position)
        if syntax is None:
            break
        if not waiting or waiting == [b">>"]:
            spans.append((start, syntax.start()))
        position = syntax.end()
        token = syntax[0]
        if token in _CLOSER:
            waiting.append(_CLOSER[token])
        elif token == b"(":
            position = _string_end(head, position, steps)
        elif token in (b"<", b"%"):
            end_re = _HEX_STRING_END_RE if token == b"<" else _COMMENT_END_RE
            end = end_re.search(head, position)
            position = len(head) if end is None else end.start()
        elif waiting and waiting[-1] == token:
            waiting.pop()
        start = position
    else:
        return [(0, len(head))]
    if waiting:
        return [(0, len(head))]
    spans.append((start, len(head)))
    return spans


def _inflated_size(data: memoryview, budget: int, steps: _Steps) -> tuple[int, int]:
    """Inflate the deflate stream at the start of ``data``.

    Returns the bytes produced and the bytes consumed, stopping once the
    output passes ``budget``, the stream reaches its own end, or the
    bytes stop being deflate (pdfminer keeps what a corrupt stream
    yielded before the error, so that much counts too). The data is fed
    in chunks: handed the whole tail at once, the decompressor copies
    everything past the stream's end into its unconsumed tail, which is
    the rest of the file for every stream. Each call may make a chunk at
    most, and the call that fails is charged that much, since what it made
    is lost with it: allowed the whole budget, one call could inflate tens
    of megabytes and then fail, counted as nothing, once per keyword.
    """
    if len(data) < 2 or data[0] & 0x0F != 8 or (data[0] << 8 | data[1]) % 31:
        # Not a zlib header, so pdfminer would not inflate it either;
        # cheaper than raising through the decompressor.
        return 0, 0
    inflater = zlib.decompressobj()
    produced = consumed = 0
    for start in range(0, len(data), _INFLATE_CHUNK):
        chunk: bytes | memoryview = data[start : start + _INFLATE_CHUNK]
        while chunk:
            steps.take()
            room = min(budget - produced + 1, _INFLATE_CHUNK)
            try:
                out = inflater.decompress(chunk, room)
            except zlib.error:
                return produced + room, consumed
            produced += len(out)
            consumed += len(chunk) - len(inflater.unconsumed_tail) - len(inflater.unused_data)
            if produced > budget or inflater.eof:
                return produced, consumed
            chunk = inflater.unconsumed_tail
    return produced, consumed


def _object_start(payload: bytes, low: int, high: int, steps: _Steps) -> int | None:
    """Where the last "N G obj" header between ``low`` and ``high`` starts, if any."""
    position = payload.rfind(b"obj", low, high)
    while position >= 0:
        steps.take()
        header = _OBJ_HEADER_RE.search(payload, max(low, position - 40), position)
        if header is not None:
            return header.start()
        position = payload.rfind(b"obj", low, position)
    return None


def guard_pdf_streams(payload: bytes) -> None:
    """Refuse a PDF whose streams would inflate past the budget.

    The byte cap bounds what comes off the wire, not what pdfminer
    inflates: a FlateDecode stream of whitespace compresses about a
    thousand to one, so a hundred kilobytes on the wire became a hundred
    megabytes in memory, and the cap left room for tens of gigabytes,
    which is an OOM kill of the whole process rather than an error. Each
    stream is inflated here with a bounded decompressor and the total held
    to :data:`MAX_INFLATED_BYTES`. The stream's own end marks where the
    count stops, never the "endstream" keyword: stored deflate blocks
    carry any bytes verbatim, so a stream can spell "endstream" long
    before it is done. A filter chain, a filter held in another object,
    a filter other than Flate and JPEG, or one written as anything but a
    plain name, is refused as well: a doubly deflated or ASCII85-wrapped
    stream is invisible to this pass, and no tariff card has used one. So is an encrypted document, since pdfminer
    opens one with the empty password and deciphers each stream before
    inflating it, out of this pass's sight.

    This is a text pass over bytes that pdfminer parses, so it refuses the
    shapes it can see and no more. A run of R tokens can take the filter
    it checked off pdfminer's stack and leave another in its place, and an
    "N G obj" spelled inside a stream's dictionary moves where the pass
    thinks that dictionary starts: a filter ahead of it is left out, and
    one after it can look nested. What it misses is bounded by the memory
    ceiling the reader runs under, and the pass runs under it too: the
    reader's child runs it before pdfplumber is imported, so it turns the
    shapes it sees into a clear message and keeps pdfplumber from ever
    opening them.

    Its cost is bounded as well as its memory. A keyword inside a stream's
    data is tried as a stream start, and the bytes fed to the decompressor
    by the streams that do not fail may not exceed the file by more than a
    chunk, which is what any set of streams that do not overlap consumes.
    Every loop of the pass counts its turns against
    :data:`MAX_GUARD_STEPS`. Its single scans over the whole card are not
    counted, and on a crafted 16 MiB card they hold the interpreter for
    seconds, which is why the pass runs in the child and not in Home
    Assistant, whose event loop would stall for as long.
    """
    if _ENCRYPT_RE.search(payload):
        raise Refused("PDF is encrypted; refusing to read it")
    view = memoryview(payload)
    steps = _Steps()
    inflated = consumed = previous_end = 0
    eol: re.Match[bytes] | None = None
    for match in _STREAM_RE.finditer(payload):
        steps.take()
        # The data starts after the keyword's line. The line end is looked
        # up once per line, not once per keyword, so a line full of
        # keywords costs one scan; a keyword with no line end after it
        # reads no data in pdfminer, nor does any keyword behind it.
        if eol is None or eol.start() < match.end():
            eol = _EOL_RE.search(payload, match.end())
            if eol is None:
                break
        # The stream's dictionary sits between its "N G obj" and "stream",
        # after the previous keyword since streams do not nest. It is looked
        # for all the way back to that keyword, however long the dictionary:
        # a fixed window used to cut a long one short and miss its filter.
        start = _object_start(payload, previous_end, match.start(), steps)
        head = payload[previous_end if start is None else start : match.start()]
        previous_end = match.end()
        # Without an "N G obj" ahead of it the head can begin inside one of
        # pdfminer's strings or comments, where its brackets mean nothing,
        # so all of it is read.
        spans = [(0, len(head))] if start is None else _outer_spans(head, steps)
        filters: list[int] = []
        for low, high in spans:
            for key in _FILTER_KEY_RE.finditer(head, low, high):
                steps.take()
                if not _FLAGS_RE.match(head, key.end()):
                    filters.append(key.end())
        if len(filters) > 1:
            # pdfminer reads /F before /Filter, so the one checked here
            # need not be the one it applies.
            raise Refused("PDF stream names more than one filter; refusing to read it")
        if filters and not _PLAIN_FILTER_RE.match(head, filters[0]):
            if head.startswith(b"[", filters[0]):
                raise Refused("PDF stream uses a filter chain, which cannot be bounded")
            name = _FILTER_NAME_RE.match(head, filters[0])
            if name is not None and name[0] not in _PLAIN_FILTERS:
                # Cut short: a card can make the name as long as itself, and
                # the message lands in last_error. The standard names run to
                # 16 characters.
                shown = name[0][:32].decode("ascii", "replace")
                raise Refused(f"PDF stream uses the {shown} filter")
            raise Refused(
                "PDF stream names its filter by reference or in a form that cannot be "
                "checked; refusing to read it"
            )
        produced, used = _inflated_size(view[eol.end() :], MAX_INFLATED_BYTES - inflated, steps)
        inflated += produced
        consumed += used
        if inflated > MAX_INFLATED_BYTES:
            raise Refused(
                f"PDF streams inflate past {MAX_INFLATED_BYTES} bytes; refusing to read it"
            )
        if consumed > len(payload) + _INFLATE_CHUNK:
            raise Refused("PDF streams overlap; refusing to read it")
