"""Vocabulary shared by every probe: statuses, results, and the schema allowlist."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .sanitize import sanitize

HEALTHY = "healthy"
DEGRADED = "degraded"
DOWN = "down"
UNKNOWN = "unknown"

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
# 1.1 is verified against both live environments. 1.0 comes from the upstream
# v1.0.0 schema and is NOT verified against a running instance: no v1.0
# deployment exists any more. Unknown versions yield `unknown`, not a failure.
REQUIRED_TABLES: dict[str, frozenset[str]] = {
    "1.1": frozenset({"TimeSeries", "Observation", "Node", "Edge", "KeyValueStore"}),
    "1.0": frozenset({"Observation", "Node", "Edge", "Cache"}),
}

_IMAGE_RE = re.compile(r"^(?P<repo>[^:@]+)(?::(?P<tag>[^:@]+))?(?:@(?P<digest>sha256:[0-9a-f]{64}))?$")
_SEMVER_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class Probe:
    id: str
    status: str
    detail: str = ""
    elapsed_ms: int = 0
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "detail": sanitize(self.detail),
            "elapsed_ms": self.elapsed_ms,
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
    preprocessing_job_name: str
    artifacts_bucket_name: str
    public_endpoint_url: str
    frontend_url: str
    data_source_prefixes: tuple[str, ...]
    input_prefix: str
    rest: object
    public: object
    spanner_factory: object  # callable() -> SpannerSQL
