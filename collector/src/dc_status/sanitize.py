"""Make error text safe to publish: redact credentials, then truncate."""

from __future__ import annotations

import re

DEFAULT_LIMIT = 300
_REDACTED = "[REDACTED]"

# The private-key rule is listed first so a PEM block is collapsed as one unit
# rather than having its body chewed on by the rules below. The remaining rules
# are independent of each other.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bya29\.[\w.\-]+"),
    re.compile(r"\bAIza[\w\-]{10,}"),
    re.compile(r"\beyJ[\w\-]+\.[\w\-]+\.[\w\-]+"),
    re.compile(r"(?i)\b(bearer)\s+\S+"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\b\s*[:=]\s*\S+"),
)


def sanitize(text: object, limit: int = DEFAULT_LIMIT) -> str:
    """Redact anything credential-shaped, collapse whitespace, truncate."""
    if text is None:
        return ""
    out = str(text)
    for pattern in _PATTERNS:
        out = pattern.sub(_REDACTED, out)
    out = " ".join(out.split())
    if len(out) > limit:
        out = out[: limit - 1] + "…"
    return out
