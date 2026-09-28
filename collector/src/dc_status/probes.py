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
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import quote

from .console import run_metrics_url, spanner_monitoring_url
from .model import (
    DEGRADED,
    DOWN,
    HEALTHY,
    REQUIRED_TABLES,
    TABLE_ALLOWLIST,
    UNKNOWN,
    Probe,
    ProbeContext,
    minor_key,
    minor_of,
    newest_known_minor,
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
    version = parse_image_version(image or "") or _template_version(service, image)
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
            "dcp_version": version,
            # The ceiling the saturation probe compares live instances against.
            # Read here, from the spec already fetched, rather than configured a
            # second time and left to drift from what Cloud Run enforces.
            "max_instances": _max_instances(service),
        },
    )


def _ingress_image(containers: list[dict]) -> str | None:
    # In a multi-container Cloud Run service exactly one container declares ports:
    # that is the ingress container. With a single container, take it.
    for container in containers:
        if container.get("ports"):
            return container.get("image")
    return containers[0].get("image") if containers else None


def _digest(image: str | None) -> str | None:
    return image.rsplit("@", 1)[1] if image and "@" in image else None


def _template_version(service: dict, live_image: str | None) -> str | None:
    """The version from the service template's tag, when it names the same image.

    Cloud Run records a revision's image by digest alone, so the tag carrying the
    platform version survives only in the template. The template is trusted only
    when its digest is the live one: during a rollout it names a newer image, and
    its tag would then describe something that is not serving.
    """
    template = _ingress_image((service.get("template") or {}).get("containers") or [])
    live = _digest(live_image)
    if not template or not live or _digest(template) != live:
        return None
    return parse_image_version(template)


def _max_instances(service: dict) -> int | None:
    raw = ((service.get("template") or {}).get("scaling") or {}).get("maxInstanceCount")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


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
    return _ingress_image(containers)


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
    checked_against = _table_set_for(minor)
    if checked_against is None:
        return Probe(
            id="version_consistency",
            status=UNKNOWN,
            detail=f"unrecognised platform version: {version!r}",
            data={"dcp_version": version, "missing_tables": [], "checked_against": None},
        )
    # Said out loud whenever the answer rests on a table set that was not
    # verified for this version, so a green result is never over-read.
    caveat = (
        ""
        if checked_against == minor
        else f"checked against {checked_against}; {minor} is not verified"
    )
    missing = sorted(REQUIRED_TABLES[checked_against] - tables)
    data = {"dcp_version": version, "missing_tables": missing, "checked_against": checked_against}
    if missing:
        detail = (
            f"the running image is {version} but the database is missing the tables "
            f"that version serves from: {', '.join(missing)}"
        )
        return Probe(
            id="version_consistency",
            status=DOWN,
            detail=f"{detail} ({caveat})" if caveat else detail,
            data=data,
        )
    return Probe(id="version_consistency", status=HEALTHY, detail=caveat, data=data)


def _table_set_for(minor: str | None) -> str | None:
    """The REQUIRED_TABLES key to judge `minor` against, or None.

    A known minor uses its own set. A minor newer than every known one uses the
    newest known set (D7): an operator who upgrades the platform before this
    panel should see the check still run, not a red panel. An older unknown
    minor has no sensible stand-in, so it stays unjudged.
    """
    if minor is None:
        return None
    if minor in REQUIRED_TABLES:
        return minor
    try:
        key = minor_key(minor)
    except ValueError:
        return None
    newest = newest_known_minor()
    return newest if key > minor_key(newest) else None


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

# Successful runs are read by their own query rather than filtered out of the
# ten above: after ten failures in a row, the last success is exactly the figure
# freshness needs, and it would have scrolled out of that window. The four count
# columns feed count_history and row_drift. Some platform versions leave them
# NULL; row_drift then reports them as not recorded rather than as a drop.
_SUCCESS_SQL = """
SELECT CompletionTimestamp, NodeCount, EdgeCount, ObservationCount, TimeSeriesCount
FROM IngestionHistory
WHERE Status = 'SUCCESS' AND CompletionTimestamp IS NOT NULL
ORDER BY CompletionTimestamp DESC
LIMIT 10
""".strip()

# Table name in the document -> IngestionHistory column.
_HISTORY_COUNTS = (
    ("Node", "NodeCount"),
    ("Edge", "EdgeCount"),
    ("Observation", "ObservationCount"),
    ("TimeSeries", "TimeSeriesCount"),
)

_LOCK_SQL = """
SELECT COUNT(*) AS Total, COUNTIF(LockOwner IS NOT NULL) AS Held,
       MIN(AcquiredTimestamp) AS OldestAcquired
FROM IngestionLock
""".strip()

# Only asked when the lock is held, so the common case stays one round trip.
_LOCK_OWNER_SQL = """
SELECT LockOwner FROM IngestionLock
WHERE LockOwner IS NOT NULL
ORDER BY AcquiredTimestamp
LIMIT 1
""".strip()


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


def _age_hours(timestamp: str | None, now: datetime | None) -> float | None:
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        return None
    reference = now or datetime.now(UTC)
    return round((reference - parsed).total_seconds() / 3600, 1)


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _count_history(successes: list[dict]) -> list[dict]:
    """Successful ingestions with their row counts, oldest first."""
    history = [
        {
            "completed_at": row.get("CompletionTimestamp"),
            **{name: _int_or_none(row.get(column)) for name, column in _HISTORY_COUNTS},
        }
        for row in successes
    ]
    return list(reversed(history))  # the query reads newest first


def _freshness(successes: list[dict], now: datetime | None, max_age_hours: float | None):
    """(last_success_at, age_hours, note). `note` is non-empty only when the age
    is judged, which it is only when the operator set a maximum."""
    last_success_at = successes[0].get("CompletionTimestamp") if successes else None
    age = _age_hours(last_success_at, now)
    if max_age_hours is None:
        return last_success_at, age, ""
    if last_success_at is None:
        return last_success_at, age, "no successful ingestion on record"
    if age is not None and age > max_age_hours:
        return (
            last_success_at,
            age,
            f"the last successful ingestion was {age:.1f} h ago, "
            f"above the {max_age_hours:g} h target",
        )
    return last_success_at, age, ""


def probe_ingestions(ctx, *, now: datetime | None = None, stale_after_hours: float = 6.0) -> Probe:
    spanner = ctx.spanner_factory()
    try:
        rows = spanner.query(_INGESTION_SQL, staleness_seconds=10)
        successes = spanner.query(_SUCCESS_SQL, staleness_seconds=10)
    finally:
        spanner.close()

    targets = getattr(ctx, "targets", None)
    max_age = targets.ingestion_max_age_hours if targets is not None else None
    last_success_at, age_hours, age_note = _freshness(successes, now, max_age)
    freshness = {
        "count_history": _count_history(successes),
        "last_success_at": last_success_at,
        "age_hours": age_hours,
    }

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
            status=DEGRADED if age_note else UNKNOWN,
            detail=" · ".join(
                note for note in (workflow_detail or "no ingestion history yet", age_note) if note
            ),
            data={"ingestions": [], **freshness},
        )

    latest = ingestions[0]
    status_text = (latest["status"] or "").upper()
    workflow_text = (latest["workflow_state"] or "").upper()
    # Both spellings of failure are accepted: the platform has written FAILED,
    # and FAILURE is the spelling of its own ImportStatus states.
    failed_statuses = {"FAILED", "FAILURE", "CANCELLED"}
    terminal = status_text in {"SUCCESS"} | failed_statuses
    notes = [workflow_detail] if workflow_detail else []

    if latest["failure"] or status_text in failed_statuses:
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

    if age_note:
        status = DEGRADED
        notes.append(age_note)

    return Probe(
        id="ingestions",
        status=status,
        detail=" · ".join(notes),
        data={"ingestions": ingestions, **freshness},
    )


def probe_ingestion_lock(
    ctx, *, now: datetime | None = None, stale_after_hours: float = 2.0
) -> Probe:
    spanner = ctx.spanner_factory()
    try:
        rows = spanner.query(_LOCK_SQL, staleness_seconds=10)
        row = rows[0] if rows else {}
        held = int(row.get("Held") or 0)
        owner = None
        if held:
            owner_rows = spanner.query(_LOCK_OWNER_SQL, staleness_seconds=10)
            owner = (owner_rows[0].get("LockOwner") if owner_rows else None) or None
    finally:
        spanner.close()

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
            "lock": {
                "held": bool(held),
                "owner": owner,
                "since": oldest if held else None,
            },
        },
    )


_IMPORT_STATUS_SQL = "SELECT ImportName, State FROM ImportStatus"
_IMPORT_FAILED = frozenset({"FAILURE", "RETRY"})
_IMPORT_IN_PROGRESS = frozenset({"PENDING", "RUNNING", "STAGING"})


def probe_import_status(ctx) -> Probe:
    """Per-import state from the platform's own ImportStatus table.

    FAILURE and RETRY degrade: RETRY means the import already failed at least
    once. PENDING, RUNNING and STAGING are work in progress, not findings.
    Versions without the table get a plain "not available", as healthy: it is
    a missing feature of that version, not a fault of this deployment.
    """
    spanner = ctx.spanner_factory()
    try:
        # Checked first rather than inferred from a failed query, so a real SQL
        # failure is never mistaken for an absent table or the other way round.
        if "ImportStatus" not in list_tables(spanner):
            return Probe(
                id="import_status",
                status=HEALTHY,
                detail="not available on this version",
                data={"imports": None},
            )
        rows = spanner.query(_IMPORT_STATUS_SQL, staleness_seconds=10)
    finally:
        spanner.close()

    states = [
        (str(row.get("ImportName") or ""), str(row.get("State") or "").upper()) for row in rows
    ]
    failed = sorted((name, state) for name, state in states if state in _IMPORT_FAILED)
    in_progress = sum(1 for _name, state in states if state in _IMPORT_IN_PROGRESS)
    imports = {
        "total": len(states),
        "succeeded": sum(1 for _name, state in states if state == "SUCCESS"),
        "in_progress": in_progress,
        "failed": [name for name, _state in failed],
    }
    if failed:
        named = ", ".join(f"{name} ({state})" for name, state in failed)
        phrase = (
            "import failed or is retrying" if len(failed) == 1 else "imports failed or are retrying"
        )
        return Probe(
            id="import_status",
            status=DEGRADED,
            detail=f"{len(failed)} {phrase}: {named}",
            data={"imports": imports},
        )
    detail = ""
    if in_progress:
        detail = f"{in_progress} import{'' if in_progress == 1 else 's'} in progress"
    return Probe(id="import_status", status=HEALTHY, detail=detail, data={"imports": imports})


def probe_row_drift(ingestions: Probe, *, max_drop_pct: float) -> Probe:
    """Pure: compare the last two successful ingestions' row counts.

    Growth is never a finding. A fall bigger than `max_drop_pct` in any of the
    four tables is: an ingestion that "succeeded" while losing a fifth of the
    observations is the failure no status column records.
    """
    data = ingestions.data or {}
    if "count_history" not in data:
        return Probe(
            id="row_drift",
            status=UNKNOWN,
            detail="the ingestion history could not be read",
            data={"drops": {}},
        )
    history = data["count_history"]
    if len(history) < 2:
        return Probe(
            id="row_drift",
            status=HEALTHY,
            detail="fewer than two successful ingestions to compare",
            data={"drops": {}},
        )
    previous, latest = history[-2], history[-1]
    drops: dict[str, float] = {}
    compared = 0
    for name, _column in _HISTORY_COUNTS:
        before, after = previous.get(name), latest.get(name)
        if before is None or after is None or before <= 0:
            continue
        compared += 1
        fell = round((before - after) / before * 100, 1)
        if fell > max_drop_pct:
            drops[name] = fell
    if not compared:
        return Probe(
            id="row_drift",
            status=HEALTHY,
            detail="row counts are not recorded by this platform version; not judged",
            data={"drops": {}},
        )
    if drops:
        named = ", ".join(f"{name} fell {pct:.1f} %" for name, pct in drops.items())
        return Probe(
            id="row_drift",
            status=DEGRADED,
            detail=f"since the previous successful ingestion: {named}, "
            f"above the {max_drop_pct:g} % target",
            data={"drops": drops},
        )
    return Probe(id="row_drift", status=HEALTHY, data={"drops": {}})


def probe_pending_uploads(data_sources: Probe, ingestions: Probe) -> Probe:
    """Pure: sources whose input changed after the last successful ingestion.

    That input is uploaded but not yet served. Before the first success, every
    source with any input is pending. Either side unreadable means unknown: an
    empty list here would read as "nothing waiting", which is a claim.
    """
    sources = (data_sources.data or {}).get("sources")
    ingestion_data = ingestions.data or {}
    if not sources or "last_success_at" not in ingestion_data:
        return Probe(
            id="pending_uploads",
            status=UNKNOWN,
            detail="needs both the source listing and the ingestion history",
            data={"pending": []},
        )
    last_success = parse_timestamp(ingestion_data["last_success_at"])
    pending = []
    for source in sources:
        updated = parse_timestamp(source.get("last_updated"))
        if updated is None:
            continue
        if last_success is None or updated > last_success:
            pending.append({"prefix": source.get("prefix"), "last_updated": source["last_updated"]})
    if not pending:
        return Probe(id="pending_uploads", status=HEALTHY, data={"pending": []})
    names = ", ".join(str(item["prefix"]) for item in pending)
    if last_success is None:
        detail = f"no successful ingestion yet; input waiting under: {names}"
    elif len(pending) == 1:
        detail = f"1 source has input newer than the last successful ingestion: {names}"
    else:
        detail = (
            f"{len(pending)} sources have input newer than the last successful ingestion: {names}"
        )
    return Probe(id="pending_uploads", status=DEGRADED, detail=detail, data={"pending": pending})


_STORAGE = "https://storage.googleapis.com/storage/v1"

# `RowCount`, not `Rows`: ROWS is a GoogleSQL reserved keyword and Spanner rejects it
# as an alias (tests/test_sql.py guards every statement here).
_PROVENANCE_SQL = "SELECT provenance, COUNT(*) AS RowCount FROM TimeSeries GROUP BY provenance"


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


def _source_key(name: str) -> str:
    """Normalise a folder name or a provenance so the two can be compared.

    Deployments name them independently: a folder `iom-dtm` holds the input for
    provenance `UNDATA/P/IOM_DTM`. Compare the last path segment, case-folded,
    with every run of non-alphanumerics read as one separator.
    """
    last = name.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"[^A-Z0-9]+", "_", last.upper()).strip("_")


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
        str(row.get("provenance")): row.get("RowCount") for row in rows if row.get("provenance")
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
        # A zero-byte object named like a folder is the console's placeholder for
        # the folder itself, not an input file.
        items = [item for item in items if not str(item.get("name", "")).endswith("/")]
        truncated = truncated or hit_cap
        key = _source_key(prefix)
        hits = [p for p in by_provenance if _source_key(p) == key]
        matched.update(hits)
        counts = [by_provenance[p] for p in hits if isinstance(by_provenance[p], int)]
        updates = [item.get("updated") for item in items if item.get("updated")]
        sources.append(
            {
                "prefix": prefix,
                "files": len(items),
                "bytes": sum(int(item.get("size") or 0) for item in items),
                "last_updated": max(updates) if updates else None,
                "rows": sum(counts) if counts else None,
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


# The same shape of check the platform's own MCP sidecar performs against the
# mixer at startup (with its default node): if that fails the sidecar exits and
# the service returns 502. The canary is configurable because a custom instance
# may not serve the base graph's entities.
def _api_path(node: str) -> str:
    return f"/core/api/v2/node?nodes={quote(node, safe='/')}&property=->name"


def _resolved(body: str, expected: str) -> bool:
    """True only when the query actually resolved the entity to `expected`.

    A bare substring test over the body would accept an error payload that
    merely mentions the name, or an HTML proxy page. Requiring valid JSON and an
    exact string value inside its `data` branch rules both out, without
    hard-coding a response shape that changes between platform versions.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    return expected in _strings(payload.get("data"))


def _strings(node) -> set[str]:
    if isinstance(node, str):
        return {node}
    if isinstance(node, dict):
        node = list(node.values())
    if isinstance(node, list):
        found: set[str] = set()
        for item in node:
            found |= _strings(item)
        return found
    return set()


def probe_dc_api(ctx) -> Probe:
    url = f"{ctx.public_endpoint_url.rstrip('/')}{_api_path(ctx.canary_node)}"
    canary = {"node": ctx.canary_node, "name": ctx.canary_name}
    try:
        code, body = ctx.public.get_text(url)
    except Exception as exc:
        return Probe(
            id="dc_api", status=DOWN, detail=str(exc), data={"probe_url": url, "canary": canary}
        )
    data = {"http_status": code, "probe_url": url, "canary": canary}
    if code == 502:
        return Probe(
            id="dc_api",
            status=DOWN,
            detail="502 from the endpoint: the MCP sidecar failed to start, or the Spanner schema is absent",
            data=data,
        )
    if code != 200:
        return Probe(id="dc_api", status=DOWN, detail=f"HTTP {code} from the endpoint", data=data)
    if not _resolved(body, ctx.canary_name):
        return Probe(
            id="dc_api",
            status=DEGRADED,
            detail=(
                f"the endpoint answered 200 but {ctx.canary_node} did not resolve "
                f"to {ctx.canary_name}"
            ),
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
            detail="HTTP 404 at the frontend root: nothing is published there",
            data={"http_status": code},
        )
    return Probe(id="frontend", status=DEGRADED, detail=f"HTTP {code}", data={"http_status": code})


# The three Experience probes judge the `signals` object the document carries,
# so what the page draws and what was judged are the same numbers (D5). They do
# no I/O of their own: monitoring.read_signals fetched everything once.

_NOT_JUDGED = "low traffic, not judged"


def probe_errors(signals: dict, targets) -> Probe:
    errors = signals["errors"]
    window = signals["window_minutes"]
    if not errors["judged"]:
        return Probe(id="errors", status=HEALTHY, detail=_NOT_JUDGED)
    availability = errors["availability_pct"]
    if availability < targets.availability_pct:
        return Probe(
            id="errors",
            status=DEGRADED,
            detail=(
                f"availability {availability:g} % over the last {window} min, "
                f"below the {targets.availability_pct:g} % target"
            ),
        )
    return Probe(id="errors", status=HEALTHY)


def probe_latency(signals: dict, targets) -> Probe:
    latency = signals["latency"]
    window = signals["window_minutes"]
    if not latency["judged"]:
        return Probe(id="latency", status=HEALTHY, detail=_NOT_JUDGED)
    p95 = latency["p95_ms"]
    if p95 is None:
        # Enough requests to judge, yet no latency distribution: the metric
        # itself is missing, which is not the same as fast.
        return Probe(id="latency", status=UNKNOWN, detail="no latency data in the window")
    if p95 > targets.latency_p95_ms:
        return Probe(
            id="latency",
            status=DEGRADED,
            detail=(
                f"p95 latency {p95} ms over the last {window} min, "
                f"above the {targets.latency_p95_ms:g} ms target"
            ),
        )
    return Probe(id="latency", status=HEALTHY)


def _against(label: str, value: float, target: float) -> str:
    relation = "above" if value > target else "at"
    return f"{label} at {value:g} %, {relation} the {target:g} % target"


def probe_saturation(signals: dict, targets, where) -> Probe:
    """Any figure at or above its target, or instances at the configured maximum.

    "At" counts: a service pinned exactly at its instance ceiling is already
    turning requests away. The console link follows the finding, because the
    page to open for a hot Spanner instance is not the Cloud Run one.
    """
    saturation = signals["saturation"]
    run_findings: list[str] = []
    spanner_findings: list[str] = []
    checks = (
        ("run_cpu_pct", "Cloud Run CPU", targets.run_cpu_pct, run_findings),
        ("run_memory_pct", "Cloud Run memory", targets.run_memory_pct, run_findings),
        (
            "spanner_cpu_pct",
            "Spanner high-priority CPU",
            targets.spanner_cpu_pct,
            spanner_findings,
        ),
    )
    for key, label, target, findings in checks:
        value = saturation.get(key)
        if value is not None and value >= target:
            findings.append(_against(label, value, target))
    instances, ceiling = saturation.get("instances"), saturation.get("max_instances")
    if instances is not None and ceiling and instances >= ceiling:
        run_findings.insert(
            0, f"{instances} of {ceiling} instances running, the configured maximum"
        )

    figures = ("run_cpu_pct", "run_memory_pct", "instances", "spanner_cpu_pct")
    if all(saturation.get(key) is None for key in figures):
        return Probe(id="saturation", status=UNKNOWN, detail="no saturation data in the window")
    if not (run_findings or spanner_findings):
        return Probe(id="saturation", status=HEALTHY)
    link = run_metrics_url(where) if run_findings else spanner_monitoring_url(where)
    return Probe(
        id="saturation",
        status=DEGRADED,
        detail=" · ".join(run_findings + spanner_findings),
        console_url=link,
    )
