# Copyright 2026 Carlos Infantes
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Every source file carries the Apache-2.0 header, and none names a real tenant."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SKIP_DIRS = {".git", ".venv", ".superpowers", ".claude", ".agents", "__pycache__", ".terraform"}
_SUFFIXES = {".py", ".js", ".css", ".tf", ".html", ".svg", ".yml", ".yaml"}


def _sources() -> list[Path]:
    found = []
    for path in ROOT.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.is_file() and (path.suffix in _SUFFIXES or path.name == "Dockerfile"):
            found.append(path)
    return sorted(found)


@pytest.mark.parametrize("path", _sources(), ids=lambda p: str(p.relative_to(ROOT)))
def test_source_file_carries_the_license_header(path):
    head = path.read_text(encoding="utf-8")[:1200]
    assert "Licensed under the Apache License, Version 2.0" in head


# The repository is meant for any Data Commons Platform operator. A tenant's name
# in code, docs or tests is a sign that something deployment-specific leaked in.
_TENANT = re.compile(r"unicc|un-icc|unsd", re.IGNORECASE)


def _text_files() -> list[Path]:
    found = []
    for path in ROOT.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.is_file() and path.suffix not in {".woff2", ".png", ".lock"}:
            found.append(path)
    return sorted(found)


def test_no_tenant_name_appears_anywhere():
    offenders = [
        str(path.relative_to(ROOT))
        for path in _text_files()
        if path != Path(__file__).resolve()
        and _TENANT.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert offenders == []
