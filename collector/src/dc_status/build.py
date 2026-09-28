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


"""Which build of the panel is serving. Set at image build time by the release
workflow (Dockerfile ARGs → DCS_BUILD_*); a local run falls back to the package
version, with no commit and no source."""

from __future__ import annotations

import os
from collections.abc import Mapping

from . import __version__


def build_info(environ: Mapping[str, str] | None = None) -> dict:
    env = os.environ if environ is None else environ
    commit = (env.get("DCS_BUILD_COMMIT") or "").strip()
    return {
        "version": (env.get("DCS_BUILD_VERSION") or "").strip() or __version__,
        # Twelve characters: unambiguous in a repository this size, short enough to read.
        "commit": commit[:12] or None,
        "source": (env.get("DCS_BUILD_SOURCE") or "").strip().rstrip("/") or None,
    }
