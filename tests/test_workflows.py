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

"""The workflows under .github/workflows, as GitHub reads them."""

from __future__ import annotations

import re
from pathlib import Path

import yaml  # type: ignore[import-untyped]

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def test_every_action_and_git_install_names_a_commit() -> None:
    """A tag or a branch is whatever its repository makes it next, and every
    action here ran from one: the archive's push job, whose token writes
    this repository, downloaded its artifacts with one, and the release job
    checked out with one. Every action, and every package installed from
    git, names a commit."""
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text()
        for job in yaml.safe_load(text)["jobs"].values():
            refs = [job["uses"]] if "uses" in job else []
            refs += [s["uses"] for s in job.get("steps", []) if "uses" in s]
            for ref in refs:
                if not ref.startswith("./"):
                    assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), (path.name, ref)
        for url in re.findall(r"git\+https://[^\s\"']+", text):
            assert re.search(r"@[0-9a-f]{40}$", url), (path.name, url)
