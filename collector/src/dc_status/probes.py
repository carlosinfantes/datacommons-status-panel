"""The probes. Each takes a ProbeContext and returns a Probe."""

from __future__ import annotations

from .model import (
    DOWN,
    HEALTHY,
    REQUIRED_TABLES,
    TABLE_ALLOWLIST,
    UNKNOWN,
    Probe,
    ProbeContext,
    minor_of,
    parse_image_version,
)

_RUN = "https://run.googleapis.com/v2"
_SPANNER = "https://spanner.googleapis.com/v1"


def probe_dc_service(ctx: ProbeContext) -> Probe:
    service = ctx.rest.get(
        f"{_RUN}/projects/{ctx.project_id}/locations/{ctx.region}"
        f"/services/{ctx.datacommons_service_name}"
    )
    condition = service.get("terminalCondition", {}) or {}
    state = condition.get("state", "")
    revision = service.get("latestReadyRevision", "") or ""
    image = _live_image(ctx, service, revision)
    status = HEALTHY if state == "CONDITION_SUCCEEDED" else DOWN
    detail = "" if status == HEALTHY else f"{state or 'no terminal condition'}: {condition.get('message', '')}"
    return Probe(
        id="dc_service",
        status=status,
        detail=detail,
        data={
            "state": state,
            "latest_ready_revision": revision.rsplit("/", 1)[-1] if revision else None,
            "image": image,
            "dcp_version": parse_image_version(image or ""),
        },
    )


def _live_image(ctx: ProbeContext, service: dict, revision: str) -> str | None:
    containers = []
    if revision:
        containers = (ctx.rest.get(f"{_RUN}/{revision}").get("containers") or [])
    if not containers:
        containers = ((service.get("template") or {}).get("containers") or [])
    if not containers:
        return None
    # In a multi-container Cloud Run service exactly one container declares ports:
    # that is the ingress container. With a single container, take it.
    for container in containers:
        if container.get("ports"):
            return container.get("image")
    return containers[0].get("image")


def probe_spanner(ctx: ProbeContext) -> Probe:
    prefix = f"{_SPANNER}/projects/{ctx.project_id}/instances/{ctx.spanner_instance_id}"
    instance = ctx.rest.get(prefix)
    database = ctx.rest.get(f"{prefix}/databases/{ctx.spanner_database_id}")
    instance_state = instance.get("state", "")
    database_state = database.get("state", "")
    ready = instance_state == "READY" and database_state == "READY"
    detail = "" if ready else f"instance {instance_state or '?'}, database {database_state or '?'}"
    return Probe(
        id="spanner",
        status=HEALTHY if ready else DOWN,
        detail=detail,
        data={
            "instance_state": instance_state,
            "database_state": database_state,
            "processing_units": int(instance.get("processingUnits", 0) or 0),
            "version_retention_period": database.get("versionRetentionPeriod"),
            "earliest_version_time": database.get("earliestVersionTime"),
        },
    )


_SCHEMA_SQL = "SELECT table_name FROM information_schema.tables WHERE table_schema = ''"


def list_tables(spanner) -> set[str]:
    """Every user table in the database. Read strongly: schema, not data."""
    rows = spanner.query(_SCHEMA_SQL, staleness_seconds=0)
    return {row["table_name"] for row in rows if row.get("table_name")}


def probe_schema(ctx) -> Probe:
    spanner = ctx.spanner_factory()
    try:
        found = list_tables(spanner)
    finally:
        spanner.close()
    known = sorted(found & TABLE_ALLOWLIST)
    unlisted = sorted(found - TABLE_ALLOWLIST)
    return Probe(
        id="schema",
        status=HEALTHY if known else DOWN,
        detail="" if known else "no known platform table is present in the database",
        data={"tables": known, "unlisted_tables": unlisted},
    )


def probe_version_consistency(service: Probe, schema: Probe) -> Probe:
    """Pure: cross the live image version with the tables that actually exist."""
    version = (service.data or {}).get("dcp_version")
    tables = set((schema.data or {}).get("tables") or [])
    minor = minor_of(version)
    if not tables:
        return Probe(
            id="version_consistency",
            status=UNKNOWN,
            detail="the schema could not be read, so consistency cannot be judged",
            data={"dcp_version": version, "missing_tables": []},
        )
    if minor is None or minor not in REQUIRED_TABLES:
        return Probe(
            id="version_consistency",
            status=UNKNOWN,
            detail=f"unrecognised platform version: {version!r}",
            data={"dcp_version": version, "missing_tables": []},
        )
    missing = sorted(REQUIRED_TABLES[minor] - tables)
    if missing:
        return Probe(
            id="version_consistency",
            status=DOWN,
            detail=(
                f"the running image is {version} but the database is missing the tables "
                f"that version serves from: {', '.join(missing)}"
            ),
            data={"dcp_version": version, "missing_tables": missing},
        )
    return Probe(
        id="version_consistency",
        status=HEALTHY,
        data={"dcp_version": version, "missing_tables": []},
    )
