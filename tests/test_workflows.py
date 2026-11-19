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
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
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


def _component(root: Path) -> Path:
    """The component as the checkout leaves it, and beside it the runner's
    temp folder with the credentials file the checkout writes there."""
    component = root / "custom_components" / "be_water_prices"
    (component / "providers" / "__pycache__").mkdir(parents=True)
    (component / "manifest.json").write_text("{}\n")
    (component / "providers" / "a.py").write_text("x = 1\n")
    (component / "providers" / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"")
    temp = root / "_temp"
    temp.mkdir()
    (temp / "git-credentials-1.config").write_text("extraheader = basic t0ken\n")
    return component


def _build_zip(root: Path) -> subprocess.CompletedProcess[str]:
    """The release job's zip step, run in ``root``."""
    steps = yaml.safe_load((WORKFLOWS / "autorelease.yml").read_text())["jobs"]["release"]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == "Build release zip")
    return subprocess.run(
        ["bash", "-e", "-c", script], cwd=root, capture_output=True, text=True, check=False
    )


@pytest.mark.skipif(
    shutil.which("zip") is None or shutil.which("unzip") is None,
    reason="needs zip and unzip, which the release runner has",
)
def test_the_release_zip_holds_the_component(tmp_path: Path) -> None:
    """The zip HACS installs: the component's files, without the bytecode."""
    _component(tmp_path)
    done = _build_zip(tmp_path)
    assert done.returncode == 0, done.stderr
    with zipfile.ZipFile(tmp_path / "dist" / "be_water_prices.zip") as built:
        assert sorted(built.namelist()) == ["manifest.json", "providers/", "providers/a.py"]


@pytest.mark.parametrize("where", ["file", "folder", "component"])
def test_the_release_zip_refuses_a_symbolic_link(tmp_path: Path, where: str) -> None:
    """zip stores what a link points at, a folder's files included, so a link
    in the component to the runner's temp folder, where the checkout keeps
    the job's write token, shipped the token to every installation. A link
    anywhere from custom_components down fails the step before anything is
    zipped."""
    component = _component(tmp_path)
    temp = tmp_path / "_temp"
    if where == "file":
        (component / "notes.txt").symlink_to(temp / "git-credentials-1.config")
    elif where == "folder":
        (component / "providers" / "cache").symlink_to(temp)
    else:
        component.rename(tmp_path / "elsewhere")
        component.symlink_to(tmp_path / "elsewhere")
    done = _build_zip(tmp_path)
    assert done.returncode != 0
    assert "symbolic links under custom_components" in done.stderr
    assert not (tmp_path / "dist").exists()


def _rendered(body: str) -> str:
    """The lines of ``body`` outside its fenced code blocks, which GitHub
    renders as markdown. By CommonMark's rule a block opens on a line of
    three or more backticks or tildes, indented at most three spaces, and
    closes on a line of at least as many of the same character and nothing
    else but spaces and tabs. A CR ends a line as an LF does."""
    outside: list[str] = []
    fence = ""
    for line in re.split(r"\r\n|\r|\n", body):
        text = line.lstrip(" ")
        run = re.match(r"`{3,}|~{3,}", text) if len(line) - len(text) <= 3 else None
        if not fence:
            if run is not None and (run[0][0] == "~" or "`" not in text[run.end() :]):
                fence = run[0]
            else:
                outside.append(line)
        elif (
            run is not None
            and run[0][0] == fence[0]
            and len(run[0]) >= len(fence)
            and not text[run.end() :].strip(" \t")
        ):
            fence = ""
    return "\n".join(outside)


# What a report must never get rendered: a mention, an image, a link, a tag,
# and the fences and line endings that would let them out of a code block,
# down to a last line with no newline.
_CRAFTED_REPORT = (
    "# Water extractor live check\n\n| utility | region | status | detail |\n"
    "| Acme | flanders | FAIL | `HTTP 404` |\n"
    "```\n@someone a mention\n"
    "   `````\n![an image](https://evil.example/x.png)\n"
    "~~~~\n[a link](https://evil.example)\n"
    "```\r```\n<img src=https://evil.example/y.png>\n"
    "```"
)
_CRAFTED = ("@someone", "![an image]", "[a link]", "evil.example", "<img")


@pytest.mark.parametrize(
    ("workflow", "step", "report"),
    [
        ("live_check.yml", "Open or update extractor-broken issue", "fail_report.md"),
        ("fixture_drift.yml", "Open or update fixture-drift issue", "report.md"),
    ],
)
def test_the_report_is_posted_in_a_code_block_it_cannot_close(
    tmp_path: Path, workflow: str, step: str, report: str
) -> None:
    """The report is written by the job that runs the readers on what the
    utilities serve, and was posted as markdown by the job that writes
    issues: a report that job was made to write could mention, link and
    embed as the bot. It is posted in a code block, its CRs gone and every
    line indented past what a closing fence may have."""
    work = tmp_path / "work"
    (work / "tmp" / "report").mkdir(parents=True)
    (work / "tmp" / "report" / report).write_bytes(_CRAFTED_REPORT.encode())
    (work / "scripts").mkdir()
    (work / "scripts" / "file_ci_issue.sh").write_text(
        'while [ $# -gt 0 ]; do case "$1" in --body) cp "$2" filed_body.md ;; esac; shift; done\n'
    )
    steps = yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"]["file"]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == step)
    subprocess.run(
        ["bash", "-e", "-c", re.sub(r"\$\{\{[^}]*\}\}", "x", script)], cwd=work, check=True
    )
    body = (work / "filed_body.md").read_bytes().decode("utf-8")
    rendered = _rendered(body)
    for crafted in _CRAFTED:
        assert crafted in body
        assert crafted not in rendered
    assert re.fullmatch(r"## Run\n\nx/x/actions/runs/x\n\n## [^\n]+\n\n", rendered)
