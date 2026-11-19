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

"""No autouse fixtures here.

The recorder has to be stood up before ``hass`` (its own fixture asserts
as much), and ``enable_custom_integrations`` pulls ``hass`` in. Tests in
this directory drive Home Assistant's own statistics compiler and do not
load this integration at all, so they need neither.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations():  # type: ignore[no-untyped-def]
    """Override the suite-wide one, which stands ``hass`` up too early.

    ``recorder_mock`` asserts that ``hass`` has not been set up when it
    runs, and ``enable_custom_integrations`` requests ``hass``. Nothing in
    this directory loads a custom integration, so there is nothing to
    enable.
    """
    yield


@pytest.fixture(autouse=True)
def _force_brussels_timezone():  # type: ignore[no-untyped-def]
    """Override the suite-wide one, which requests ``hass`` too.

    Each test here sets the zone itself once ``hass`` exists.
    """
    yield


_HERE = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Time the tests here out with pytest-timeout's thread method.

    The default, a signal, stops a test while a worker thread waits on the
    recorder, and the test then hangs in its teardown, where Home
    Assistant's executor waits for seconds of a clock the test froze, and
    again at exit: stopped at 1.6 s, a year backfill test was still there
    two minutes later. The thread method ends the process instead. The
    seconds a test asks for are kept.
    """
    for item in items:
        if not item.path.is_relative_to(_HERE):
            continue
        own = item.get_closest_marker("timeout")
        args = own.args if own is not None else ()
        kwargs = {**(own.kwargs if own is not None else {}), "method": "thread"}
        item.add_marker(pytest.mark.timeout(*args, **kwargs), append=False)
