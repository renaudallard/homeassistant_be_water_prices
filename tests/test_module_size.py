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

"""Every module of the integration stays under a thousand lines.

Past that a module holds two jobs: the coordinator was split into the
fetch, the year to date, the meter's year, the fold, the recorder reads
and the Stores, along seams it already had. Split along one like those
rather than raising the limit.
"""

from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "be_water_prices"
_MAX_LINES = 1000


def test_every_integration_module_stays_under_a_thousand_lines() -> None:
    """Lines are counted as newlines, the way an editor and ``wc -l`` count
    them."""
    too_long: dict[str, int] = {}
    for path in sorted(_PACKAGE.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").count("\n")
        if lines > _MAX_LINES:
            too_long[str(path.relative_to(_PACKAGE))] = lines
    assert not too_long, too_long
