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

"""Serve a saved document instead of collecting one. For page work and demos.

With `shift_time`, every ISO-8601 timestamp in the document moves by
(now - generated_at), so a document recorded weeks ago reads as current: the
snapshot is fresh, the last ingestion is as old as it was when recorded, and the
page's relative times and 30-day timeline look as they did then. Development
only; a deployed panel collects, it does not replay.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from .timestamps import parse_timestamp

# A whole string that is a timestamp, not a string that merely contains one.
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?P<fraction>\.\d+)?(?P<zone>Z|[+-]\d{2}:\d{2})$"
)


def load_replay(path: str, *, shift_time: bool, now: datetime | None = None):
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    if not shift_time or not isinstance(document, dict):
        return document
    generated = parse_timestamp(document.get("generated_at"))
    if generated is None:
        return document
    return _shift(document, (now or datetime.now(UTC)) - generated)


def _shift(node, delta: timedelta):
    if isinstance(node, dict):
        return {key: _shift(value, delta) for key, value in node.items()}
    if isinstance(node, list):
        return [_shift(item, delta) for item in node]
    if isinstance(node, str):
        match = _TIMESTAMP.match(node)
        if match is None:
            return node
        parsed = parse_timestamp(node)
        if parsed is None:
            return node
        moved = parsed + delta
        timespec = "microseconds" if match.group("fraction") else "seconds"
        text = moved.isoformat(timespec=timespec)
        return text.replace("+00:00", "Z") if match.group("zone") == "Z" else text
    return node
