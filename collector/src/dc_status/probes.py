"""The probes. Each takes a ProbeContext and returns a Probe."""

from __future__ import annotations

from .model import DOWN, HEALTHY, Probe, ProbeContext, parse_image_version

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
