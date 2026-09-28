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
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\b\s*[:=]\s*\S+"
    ),
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
