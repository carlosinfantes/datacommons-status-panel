"""The probes. Each takes a ProbeContext and returns a Probe."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .model import (
    DEGRADED,
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
from .timestamps import parse_timestamp

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


_COUNT_TIMEOUT_SECONDS = 8.0


def probe_counts(ctx, *, budget_seconds: float = 20.0, workers: int = 4) -> Probe:
    started = time.monotonic()
    schema_session = ctx.spanner_factory()
    try:
        tables = sorted(list_tables(schema_session) & TABLE_ALLOWLIST)
    except Exception as exc:
        return Probe(id="counts", status=UNKNOWN, detail=str(exc), data={"counts": {}, "unavailable": []})
    finally:
        schema_session.close()

    if not tables:
        return Probe(
            id="counts",
            status=UNKNOWN,
            detail="no known table to count",
            data={"counts": {}, "unavailable": []},
        )

    def count_one(table: str):
        # `table` is guaranteed to come from TABLE_ALLOWLIST above; nothing else
        # is ever interpolated into a statement.
        session = ctx.spanner_factory()
        try:
            rows = session.query(
                f"SELECT COUNT(*) AS Total FROM {table}",
                staleness_seconds=10,
                timeout=_COUNT_TIMEOUT_SECONDS,
            )
            return rows[0]["Total"] if rows else None
        finally:
            session.close()

    counts: dict[str, int | None] = {}
    unavailable: list[str] = []
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        futures = {table: pool.submit(count_one, table) for table in tables}
        for table, future in futures.items():
            remaining = budget_seconds - (time.monotonic() - started)
            try:
                counts[table] = future.result(timeout=max(0.1, remaining))
            except Exception:
                counts[table] = None
                unavailable.append(table)
    finally:
        # NOT a `with` block: its __exit__ calls shutdown(wait=True), which waits
        # for the very workers the budget just gave up on, so the budget would
        # bound nothing. A straggler finishes in the background and closes its
        # own session in count_one's finally; it must not hold up the page.
        pool.shutdown(wait=False, cancel_futures=True)

    if unavailable and len(unavailable) == len(tables):
        status = UNKNOWN
    elif unavailable:
        status = DEGRADED
    else:
        status = HEALTHY
    detail = "" if not unavailable else f"could not count: {', '.join(sorted(unavailable))}"
    return Probe(
        id="counts",
        status=status,
        detail=detail,
        data={"counts": counts, "unavailable": sorted(unavailable)},
    )


_WORKFLOWS = "https://workflowexecutions.googleapis.com/v1"

_INGESTION_SQL = """
SELECT CreationTimestamp, CompletionTimestamp, Status, Stage, IngestionFailure,
       ExecutionTime, ARRAY_LENGTH(IngestedImports) AS Imports, WorkflowExecutionID
FROM IngestionHistory
ORDER BY CreationTimestamp DESC
LIMIT 10
""".strip()

_LOCK_SQL = """
SELECT COUNT(*) AS Total, COUNTIF(LockOwner IS NOT NULL) AS Held,
       MIN(AcquiredTimestamp) AS OldestAcquired
FROM IngestionLock
""".strip()

# The columns NodeCount / EdgeCount / ObservationCount / TimeSeriesCount exist on
# IngestionHistory but are NULL in every row of both environments. Counts come
# from probe_counts instead.


def _workflow_executions(ctx) -> list[dict]:
    url = (
        f"{_WORKFLOWS}/projects/{ctx.project_id}/locations/{ctx.region}"
        f"/workflows/{ctx.ingestion_workflow_name}/executions"
    )
    payload = ctx.rest.get(url, params={"pageSize": 10})
    return payload.get("executions") or []


def _state_by_execution_id(executions: list[dict]) -> dict[str, str]:
    return {
        execution.get("name", "").rsplit("/", 1)[-1]: execution.get("state", "")
        for execution in executions
        if execution.get("name")
    }


def _any_active(executions: list[dict]) -> bool:
    return any(execution.get("state") == "ACTIVE" for execution in executions)


def probe_ingestions(ctx) -> Probe:
    spanner = ctx.spanner_factory()
    try:
        rows = spanner.query(_INGESTION_SQL, staleness_seconds=10)
    finally:
        spanner.close()

    try:
        executions = _workflow_executions(ctx)
    except Exception as exc:
        executions = []
        workflow_detail = f"workflow executions unavailable: {exc}"
    else:
        workflow_detail = ""

    states = _state_by_execution_id(executions)
    ingestions = [
        {
            "creation": row.get("CreationTimestamp"),
            "completion": row.get("CompletionTimestamp"),
            "status": row.get("Status"),
            "stage": row.get("Stage"),
            "failure": bool(row.get("IngestionFailure")),
            "execution_seconds": row.get("ExecutionTime"),
            "imports": row.get("Imports"),
            "workflow_execution_id": row.get("WorkflowExecutionID"),
            "workflow_state": states.get(row.get("WorkflowExecutionID") or ""),
        }
        for row in rows
    ]

    if not ingestions:
        return Probe(
            id="ingestions",
            status=UNKNOWN,
            detail=workflow_detail or "no ingestion history yet",
            data={"ingestions": []},
        )

    latest = ingestions[0]
    if _any_active(executions):
        status, detail = HEALTHY, "an ingestion is running"
    elif latest["failure"] or (latest["status"] or "").upper() not in {"SUCCESS", "RUNNING"}:
        status = DEGRADED
        detail = f"the latest ingestion reported {latest['status']} at stage {latest['stage']}"
    else:
        status, detail = HEALTHY, ""
    return Probe(
        id="ingestions",
        status=status,
        detail=detail or workflow_detail,
        data={"ingestions": ingestions},
    )


def probe_ingestion_lock(ctx, *, now: datetime | None = None, stale_after_hours: float = 2.0) -> Probe:
    spanner = ctx.spanner_factory()
    try:
        rows = spanner.query(_LOCK_SQL, staleness_seconds=10)
    finally:
        spanner.close()

    row = rows[0] if rows else {}
    held = int(row.get("Held") or 0)
    oldest = row.get("OldestAcquired")
    acquired = parse_timestamp(oldest)
    reference = now or datetime.now(timezone.utc)
    age_minutes = int((reference - acquired).total_seconds() // 60) if acquired else None

    try:
        active = _any_active(_workflow_executions(ctx))
    except Exception:
        active = False

    if held == 0:
        status, detail = HEALTHY, ""
    elif active:
        status, detail = HEALTHY, "an ingestion is running"
    elif age_minutes is not None and age_minutes > stale_after_hours * 60:
        status = DEGRADED
        detail = f"the ingestion lock has been held for {age_minutes} minutes with no active workflow"
    else:
        status, detail = HEALTHY, "the ingestion lock is held"
    return Probe(
        id="ingestion_lock",
        status=status,
        detail=detail,
        data={
            "held": held,
            "oldest_acquired": oldest,
            "age_minutes": age_minutes,
            "workflow_active": active,
        },
    )
