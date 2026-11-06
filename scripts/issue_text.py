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

"""Text a check quotes from what a utility serves, written so that the issue
it is posted in renders none of it.

Shared by the live check and the fixture drift check, whose reports become
issue bodies. Imports nothing, so either script can use it as is.
"""

from __future__ import annotations


def code_spans(text: str) -> str:
    """``text`` as code: each of its lines a code span, the spans apart on a
    ``<br>``, and nothing for a blank text.

    A detail or an error can quote what a utility serves (a cell off its
    card, an address off its pages, a server's reason phrase, an error about
    either), and inside a code span GitHub renders no mention, link, image,
    issue reference or HTML. A backtick would close the span, so none is
    kept. A CR or LF would end the table row or the list item, and the
    runner splits the printed report on either, so each line, of a
    traceback for one, gets a span of its own and the ``<br>`` between them
    is the only markup. In a table cell a pipe still ends the cell inside a
    span, so a table escapes the pipes of what this returns.
    """
    lines = text.replace("`", "").replace("\r", "\n").split("\n")
    return "<br>".join(f"`{line}`" for line in lines if line.strip())
