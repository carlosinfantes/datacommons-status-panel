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

"""RFC3339 parsing that survives Spanner's nanosecond precision."""

from __future__ import annotations

import re
from datetime import UTC, datetime

_FRACTION_RE = re.compile(r"\.(\d{1,9})")


def parse_timestamp(text: str | None) -> datetime | None:
    if not text:
        return None
    cleaned = text.strip().replace("Z", "+00:00")
    # datetime.fromisoformat accepts at most 6 fractional digits.
    cleaned = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6], cleaned)
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
