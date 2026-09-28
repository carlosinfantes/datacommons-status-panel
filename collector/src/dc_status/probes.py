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

"""The probes. Each takes a ProbeContext and returns a Probe."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

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
    detail = (
        ""
        if status == HEALTHY
        else f"{state or 'no terminal condition'}: {condition.get('message', '')}"
    )
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
        try:
            containers = ctx.rest.get(f"{_RUN}/{revision}").get("containers") or []
        except Exception:
            # Keep the state we already read rather than discarding the whole
            # probe; the service template below still tells us the desired image.
            containers = []
    if not containers:
        containers = (service.get("template") or {}).get("containers") or []
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
COUNTS_BUDGET_SECONDS = 20.0


def probe_counts(ctx, *, budget_seconds: float = COUNTS_BUDGET_SECONDS, workers: int = 4) -> Probe:
    started = time.monotonic()
    schema_session = ctx.spanner_factory()
    try:
        tables = sorted(list_tables(schema_session) & TABLE_ALLOWLIST)
    except Exception as exc:
        return Probe(
            id="counts", status=UNKNOWN, detail=str(exc), data={"counts": {}, "unavailable": []}
        )
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


def _age_minutes(timestamp: str | None, now: datetime | None) -> int | None:
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        return None
    reference = now or datetime.now(UTC)
    return int((reference - parsed).total_seconds() // 60)


def probe_ingestions(ctx, *, now: datetime | None = None, stale_after_hours: float = 6.0) -> Probe:
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
    status_text = (latest["status"] or "").upper()
    workflow_text = (latest["workflow_state"] or "").upper()
    terminal = status_text in {"SUCCESS", "FAILED", "CANCELLED"}
    notes = [workflow_detail] if workflow_detail else []

    if latest["failure"] or status_text in {"FAILED", "CANCELLED"}:
        status = DEGRADED
        notes.insert(
            0, f"the latest ingestion reported {latest['status']} at stage {latest['stage']}"
        )
    elif workflow_text in {"FAILED", "CANCELLED", "CRASHED"}:
        # The row never reached a terminal Status but its own workflow did, and it
        # failed. Trust the workflow: a row left at RUNNING is how a crashed
        # ingestion hides from a status check.
        status = DEGRADED
        notes.insert(
            0,
            f"the latest ingestion is {latest['status']} but its workflow reported {latest['workflow_state']}",
        )
    elif _any_active(executions):
        status = HEALTHY
        notes.insert(0, "an ingestion is running")
    elif not terminal:
        age = _age_minutes(latest["creation"], now)
        if age is None:
            status = UNKNOWN
            notes.insert(
                0, f"the latest ingestion is {latest['status']} and its age could not be read"
            )
        elif age > stale_after_hours * 60:
            status = DEGRADED
            notes.insert(
                0,
                f"the latest ingestion has been {latest['status']} for {age} minutes with no active workflow",
            )
        else:
            status = HEALTHY
            notes.insert(0, f"an ingestion is {latest['status']}")
    else:
        status = HEALTHY

    return Probe(
        id="ingestions",
        status=status,
        detail=" · ".join(notes),
        data={"ingestions": ingestions},
    )


def probe_ingestion_lock(
    ctx, *, now: datetime | None = None, stale_after_hours: float = 2.0
) -> Probe:
    spanner = ctx.spanner_factory()
    try:
        rows = spanner.query(_LOCK_SQL, staleness_seconds=10)
    finally:
        spanner.close()

    row = rows[0] if rows else {}
    held = int(row.get("Held") or 0)
    oldest = row.get("OldestAcquired")
    age_minutes = _age_minutes(oldest, now)

    workflow_detail = ""
    try:
        active = _any_active(_workflow_executions(ctx))
    except Exception as exc:
        active = False
        workflow_detail = f"workflow state unavailable: {exc}"

    if held == 0:
        status, detail = HEALTHY, ""
    elif active:
        status, detail = HEALTHY, "an ingestion is running"
    elif age_minutes is not None and age_minutes > stale_after_hours * 60:
        status = DEGRADED
        # Never claim "no workflow is active" when the API could not be asked:
        # that turns not knowing into a diagnosis, inside the status that raises
        # the alarm.
        detail = f"the ingestion lock has been held for {age_minutes} minutes and " + (
            workflow_detail or "no workflow is active"
        )
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
            "workflow_state_known": not workflow_detail,
        },
    )


_STORAGE = "https://storage.googleapis.com/storage/v1"

_PROVENANCE_SQL = "SELECT provenance, COUNT(*) AS Rows FROM TimeSeries GROUP BY provenance"


def _list_objects(ctx, prefix: str, max_pages: int) -> tuple[list[dict], bool]:
    items: list[dict] = []
    token: str | None = None
    for _ in range(max_pages):
        params = {
            "prefix": prefix,
            "maxResults": 1000,
            "fields": "items(name,size,updated),nextPageToken",
        }
        if token:
            params["pageToken"] = token
        payload = ctx.rest.get(f"{_STORAGE}/b/{ctx.artifacts_bucket_name}/o", params=params)
        items.extend(payload.get("items") or [])
        token = payload.get("nextPageToken")
        if not token:
            return items, False
    return items, True  # page cap reached: say so rather than silently truncating


def probe_data_sources(ctx, *, max_pages: int = 5) -> Probe:
    provenance_detail = ""
    try:
        spanner = ctx.spanner_factory()
        try:
            rows = spanner.query(_PROVENANCE_SQL, staleness_seconds=10)
        finally:
            spanner.close()
    except Exception as exc:
        # A database failure must not look like "nothing matched". Without this
        # every source would report rows: None and the probe would stay green
        # through a full outage — the one state this panel exists to catch.
        rows = []
        provenance_detail = f"rows served per source unavailable: {exc}"
    by_provenance = {
        str(row.get("provenance") or "").upper(): row.get("Rows")
        for row in rows
        if row.get("provenance")
    }

    if not ctx.data_source_prefixes:
        return Probe(
            id="data_sources",
            status=UNKNOWN,
            detail="no data source prefixes are configured, so coverage is unknown",
            data={
                "sources": [],
                "unmatched_provenances": [],
                "truncated": False,
                "rows_known": not provenance_detail,
            },
        )

    sources: list[dict] = []
    empty: list[str] = []
    truncated = False
    matched: set[str] = set()
    for prefix in ctx.data_source_prefixes:
        full_prefix = f"{ctx.input_prefix}{prefix}/"
        items, hit_cap = _list_objects(ctx, full_prefix, max_pages)
        truncated = truncated or hit_cap
        key = prefix.upper()
        if key in by_provenance:
            matched.add(key)
        updates = [item.get("updated") for item in items if item.get("updated")]
        sources.append(
            {
                "prefix": prefix,
                "files": len(items),
                "bytes": sum(int(item.get("size") or 0) for item in items),
                "last_updated": max(updates) if updates else None,
                "rows": by_provenance.get(key),
            }
        )
        if not items:
            empty.append(prefix)

    unmatched = [
        {"provenance": provenance, "rows": count}
        for provenance, count in sorted(by_provenance.items())
        if provenance not in matched
    ]

    parts = []
    if empty:
        parts.append(f"no input files under: {', '.join(empty)}")
    if provenance_detail:
        parts.append(provenance_detail)
    if truncated:
        parts.append("object listing hit the page cap")

    if empty:
        status = DEGRADED  # a named, verified gap
    elif provenance_detail:
        status = UNKNOWN  # not knowing, which aggregates as degraded, not as down
    else:
        status = HEALTHY
    return Probe(
        id="data_sources",
        status=status,
        detail=" · ".join(parts),
        data={
            "sources": sources,
            "unmatched_provenances": unmatched,
            "truncated": truncated,
            "rows_known": not provenance_detail,
        },
    )


# Mirrors the readiness check the platform's own MCP sidecar performs against the
# mixer at startup: if this fails the sidecar exits and the service returns 502.
_API_PATH = "/core/api/v2/node?nodes=country/GTM&property=->name"
_API_EXPECTED = "Guatemala"


def _resolved(body: str) -> bool:
    """True only when the query actually resolved the entity.

    A bare `_API_EXPECTED in body` would accept an error payload that merely
    mentions the name, or an HTML proxy page. Requiring valid JSON with the value
    inside its `data` branch rules both out, without hard-coding a response shape
    that changes between platform versions.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    return _API_EXPECTED in json.dumps(payload.get("data") or {})


def probe_dc_api(ctx) -> Probe:
    url = f"{ctx.public_endpoint_url.rstrip('/')}{_API_PATH}"
    try:
        code, body = ctx.public.get_text(url)
    except Exception as exc:
        return Probe(id="dc_api", status=DOWN, detail=str(exc), data={"probe_url": url})
    data = {"http_status": code, "probe_url": url}
    if code == 502:
        return Probe(
            id="dc_api",
            status=DOWN,
            detail="502 from the endpoint: the MCP sidecar failed to start, or the Spanner schema is absent",
            data=data,
        )
    if code != 200:
        return Probe(id="dc_api", status=DOWN, detail=f"HTTP {code} from the endpoint", data=data)
    if not _resolved(body):
        return Probe(
            id="dc_api",
            status=DEGRADED,
            detail=f"the endpoint answered 200 but the known entity did not resolve to {_API_EXPECTED}",
            data=data,
        )
    return Probe(id="dc_api", status=HEALTHY, data=data)


def probe_frontend(ctx) -> Probe:
    url = f"{ctx.frontend_url.rstrip('/')}/"
    try:
        code, _body = ctx.public.get_text(url)
    except Exception as exc:
        return Probe(id="frontend", status=DEGRADED, detail=str(exc), data={})
    if code == 200:
        return Probe(id="frontend", status=HEALTHY, data={"http_status": code})
    if code == 404:
        return Probe(
            id="frontend",
            status=DEGRADED,
            detail="404: the frontend bucket is empty, pending the development team",
            data={"http_status": code},
        )
    return Probe(id="frontend", status=DEGRADED, detail=f"HTTP {code}", data={"http_status": code})
