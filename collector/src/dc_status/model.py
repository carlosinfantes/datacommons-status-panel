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

"""Vocabulary shared by every probe: statuses, results, and the schema allowlist."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from .sanitize import sanitize

HEALTHY = "healthy"
DEGRADED = "degraded"
DOWN = "down"
UNKNOWN = "unknown"

# The four questions an operator asks, in the order they ask them (D4). The
# order here is the order of the document and of the page; every probe belongs
# to exactly one dimension.
DIMENSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("system", ("dc_api", "dc_service", "spanner", "schema", "version_consistency", "frontend")),
    ("experience", ("errors", "latency", "saturation")),
    ("quality", ("counts", "data_sources", "import_status", "row_drift")),
    ("freshness", ("ingestions", "ingestion_lock", "pending_uploads")),
)
DIMENSION_OF: dict[str, str] = {
    probe_id: dimension for dimension, probe_ids in DIMENSIONS for probe_id in probe_ids
}

# `unknown` ranks with `degraded` on purpose: not knowing is not being down.
_RANK = {HEALTHY: 0, DEGRADED: 1, UNKNOWN: 1, DOWN: 2}
_LABEL_BY_RANK = {0: HEALTHY, 1: DEGRADED, 2: DOWN}

TABLE_ALLOWLIST = frozenset(
    {
        "Edge",
        "ImportStatus",
        "ImportVersionHistory",
        "IngestionHistory",
        "IngestionLock",
        "KeyValueStore",
        "Node",
        "NodeEmbedding",
        "Observation",
        "TimeSeries",
    }
)

# Which tables a given platform minor version needs in order to serve.
# 1.1 is verified against running deployments. 1.0 comes from the upstream
# v1.0.0 schema and is NOT verified against a running instance. A minor newer
# than every entry here is checked against the newest entry (D7); an older
# unknown one yields `unknown`, not a failure.
REQUIRED_TABLES: dict[str, frozenset[str]] = {
    "1.1": frozenset({"TimeSeries", "Observation", "Node", "Edge", "KeyValueStore"}),
    "1.0": frozenset({"Observation", "Node", "Edge", "Cache"}),
}

_IMAGE_RE = re.compile(
    r"^(?P<repo>[^:@]+)(?::(?P<tag>[^:@]+))?(?:@(?P<digest>sha256:[0-9a-f]{64}))?$"
)
_SEMVER_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class Probe:
    id: str
    status: str
    detail: str = ""
    elapsed_ms: int = 0
    # How long this probe was allowed to take. `elapsed_ms` on its own says a check
    # took 10 s without saying whether that is comfortable or one second from being
    # dropped; the page needs both numbers to show the difference. Zero means the
    # probe was never raced against a clock — a derived check like
    # version_consistency does no I/O, so it has no budget to report.
    budget_ms: int = 0
    data: dict = field(default_factory=dict)
    # The most specific Cloud Console page for the thing checked. A probe sets it
    # only when the right page depends on what it found (saturation); otherwise
    # the collector fills in the probe's default page, so even a probe that
    # raised still links somewhere useful.
    console_url: str | None = None

    @property
    def dimension(self) -> str | None:
        return DIMENSION_OF.get(self.id)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "dimension": self.dimension,
            "status": self.status,
            "detail": sanitize(self.detail),
            "elapsed_ms": self.elapsed_ms,
            "budget_ms": self.budget_ms,
            "console_url": self.console_url,
            "data": self.data,
        }


def worst(statuses: Iterable[str]) -> str:
    ranks = [_RANK.get(status, 1) for status in statuses]
    if not ranks:
        return UNKNOWN
    return _LABEL_BY_RANK[max(ranks)]


def parse_image_version(image: str) -> str | None:
    """Return the semver tag of a container image, or None."""
    match = _IMAGE_RE.match(image or "")
    if not match:
        return None
    tag = match.group("tag")
    if tag and _SEMVER_RE.match(tag):
        return tag
    return None


def minor_of(version: str | None) -> str | None:
    if not version:
        return None
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else None


def minor_key(minor: str) -> tuple[int, ...]:
    """Numeric ordering for "1.10" vs "1.2", which text ordering gets backwards."""
    return tuple(int(part) for part in minor.split("."))


def newest_known_minor() -> str:
    return max(REQUIRED_TABLES, key=minor_key)


@dataclass
class ProbeContext:
    """Everything a probe needs. Built once per collection."""

    env_id: str
    project_id: str
    region: str
    spanner_instance_id: str
    spanner_database_id: str
    datacommons_service_name: str
    ingestion_workflow_name: str
    artifacts_bucket_name: str
    public_endpoint_url: str
    frontend_url: str
    data_source_prefixes: tuple[str, ...]
    input_prefix: str
    rest: object
    public: object
    spanner_factory: object  # callable() -> SpannerSQL
    canary_node: str = "country/GTM"
    canary_name: str = "Guatemala"
