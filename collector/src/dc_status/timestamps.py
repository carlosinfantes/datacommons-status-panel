"""RFC3339 parsing that survives Spanner's nanosecond precision."""

from __future__ import annotations

import re
from datetime import datetime, timezone

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
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
