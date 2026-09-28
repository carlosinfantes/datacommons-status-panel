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

"""Run the probes in parallel and shape the document the page consumes.

The document is schema version 2: one deployment, four dimensions.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from .config import EnvConfig
from .console import console_url
from .model import DIMENSION_OF, DIMENSIONS, UNKNOWN, Probe, ProbeContext, worst
from .probes import (
    COUNTS_BUDGET_SECONDS,
    probe_counts,
    probe_data_sources,
    probe_dc_api,
    probe_dc_service,
    probe_frontend,
    probe_import_status,
    probe_ingestion_lock,
    probe_ingestions,
    probe_pending_uploads,
    probe_row_drift,
    probe_schema,
    probe_spanner,
    probe_version_consistency,
)
from .rest import PUBLIC_TIMEOUT_SECONDS
from .sanitize import sanitize

SCHEMA_VERSION = 2

# One clock for the whole collection, not one per probe.
_PROBE_DEADLINE_SECONDS = 25.0


@dataclass(frozen=True)
class ProbeSpec:
    id: str
    run: Callable[[ProbeContext], Probe]
    ttl_seconds: int = 0
    # Name of an EnvConfig field whose value overrides ttl_seconds, for the
    # probes whose caching is operator-tunable.
    ttl_from_config: str = ""
    # The probe's own inner deadline, when it is tighter than the pool's. Reporting
    # the pool's 25 s for a check that actually gives up at 8 s would render a probe
    # one second from timing out as comfortably within budget — the exact reading
    # this figure exists to prevent. Zero means the pool deadline is what binds.
    budget_seconds: float = 0.0


@dataclass(frozen=True)
class Clients:
    rest: object
    public: object
    spanner_factory: object


# version_consistency, row_drift and pending_uploads are derived from these
# after they complete, so they are not listed.
PROBES: tuple[ProbeSpec, ...] = (
    ProbeSpec("dc_api", probe_dc_api, budget_seconds=PUBLIC_TIMEOUT_SECONDS),
    ProbeSpec("dc_service", probe_dc_service),
    ProbeSpec("spanner", probe_spanner),
    ProbeSpec("schema", probe_schema, ttl_from_config="schema_cache_ttl_seconds"),
    ProbeSpec(
        "counts",
        probe_counts,
        ttl_from_config="counts_cache_ttl_seconds",
        budget_seconds=COUNTS_BUDGET_SECONDS,
    ),
    ProbeSpec("ingestions", probe_ingestions),
    ProbeSpec("ingestion_lock", probe_ingestion_lock),
    ProbeSpec("data_sources", probe_data_sources, ttl_seconds=300),
    ProbeSpec("import_status", probe_import_status),
    ProbeSpec("frontend", probe_frontend, budget_seconds=PUBLIC_TIMEOUT_SECONDS),
)


def _context(config: EnvConfig, clients: Clients) -> ProbeContext:
    return ProbeContext(
        env_id=config.env_id,
        project_id=config.project_id,
        region=config.region,
        spanner_instance_id=config.spanner_instance_id,
        spanner_database_id=config.spanner_database_id,
        datacommons_service_name=config.datacommons_service_name,
        ingestion_workflow_name=config.ingestion_workflow_name,
        artifacts_bucket_name=config.artifacts_bucket_name,
        public_endpoint_url=config.public_endpoint_url,
        frontend_url=config.frontend_url,
        data_source_prefixes=tuple(config.data_source_prefixes),
        input_prefix=config.input_prefix,
        rest=clients.rest,
        public=clients.public,
        spanner_factory=clients.spanner_factory,
        canary_node=config.canary_node,
        canary_name=config.canary_name,
        targets=config.targets,
    )


def _ttl_for(spec: ProbeSpec, config: EnvConfig) -> float:
    if spec.ttl_from_config:
        return getattr(config, spec.ttl_from_config)
    return spec.ttl_seconds


def _budget_ms(spec: ProbeSpec | None = None) -> int:
    """The deadline that actually binds this probe: its own, or the pool's."""
    limit = _PROBE_DEADLINE_SECONDS
    if spec is not None and spec.budget_seconds:
        limit = min(limit, spec.budget_seconds)
    return int(limit * 1000)


def _run_one(spec: ProbeSpec, ctx: ProbeContext, config: EnvConfig, cache) -> Probe:
    def produce() -> Probe:
        started = time.monotonic()
        try:
            probe = spec.run(ctx)
        except Exception as exc:
            return Probe(
                id=spec.id,
                status=UNKNOWN,
                detail=sanitize(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                budget_ms=_budget_ms(spec),
            )
        elapsed = int((time.monotonic() - started) * 1000)
        return replace(probe, elapsed_ms=elapsed, budget_ms=_budget_ms(spec))

    return cache.get_or_call(f"{config.env_id}:{spec.id}", _ttl_for(spec, config), produce)


def collect_status(
    config: EnvConfig,
    clients: Clients,
    cache,
    *,
    now: datetime | None = None,
    probes: tuple[ProbeSpec, ...] = PROBES,
) -> dict:
    """Run one collection and return the schema-version-2 document."""
    ctx = _context(config, clients)
    results = _run_all(probes, ctx, config, cache)

    # Only derive version_consistency when both halves of the pair were actually
    # requested. In production PROBES always includes both dc_service and schema,
    # so this is never false there — even if one of them raised, its Probe (with
    # UNKNOWN status) is still present in `results`. This guard exists for callers
    # that pass a reduced `probes` tuple containing neither: fabricating a phantom
    # UNKNOWN card in that case would misrepresent a probe that was never asked
    # for as one that ran and failed.
    if "dc_service" in results and "schema" in results:
        results["version_consistency"] = probe_version_consistency(
            results["dc_service"], results["schema"]
        )

    # The same guard for the derived data checks: each is derived only when
    # what it reads from was asked for.
    if "ingestions" in results:
        results["row_drift"] = probe_row_drift(
            results["ingestions"], max_drop_pct=config.targets.max_row_drop_pct
        )
        if "data_sources" in results:
            results["pending_uploads"] = probe_pending_uploads(
                results["data_sources"], results["ingestions"]
            )

    return _document(config, _with_console_links(results, config), signals=None, now=now)


def _run_all(probes, ctx: ProbeContext, config: EnvConfig, cache) -> dict[str, Probe]:
    """Run every probe in parallel against ONE deadline for the whole collection.

    Each future waits only for what is left of the shared clock. Granting every
    future its own full timeout, as v0 did, let N stuck probes hold the page for
    N deadlines.
    """
    results: dict[str, Probe] = {}
    spec_by_id = {spec.id: spec for spec in probes}
    deadline = time.monotonic() + _PROBE_DEADLINE_SECONDS
    pool = ThreadPoolExecutor(max_workers=max(1, len(probes)))
    try:
        futures = {spec.id: pool.submit(_run_one, spec, ctx, config, cache) for spec in probes}
        for probe_id, future in futures.items():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                results[probe_id] = future.result(timeout=remaining)
            except FuturesTimeout:
                # It did not merely fail, it used every millisecond it was given and
                # was dropped. Recording elapsed as the full budget is the honest
                # reading; leaving it at zero would report the slowest possible
                # probe as the fastest.
                results[probe_id] = Probe(
                    id=probe_id,
                    status=UNKNOWN,
                    detail=f"the check did not answer within {_PROBE_DEADLINE_SECONDS:g} s",
                    # The collection's clock is what dropped it, whatever its own
                    # inner limit was, so that is the budget it actually spent.
                    elapsed_ms=_budget_ms(),
                    budget_ms=_budget_ms(),
                )
            except Exception as exc:
                results[probe_id] = Probe(
                    id=probe_id,
                    status=UNKNOWN,
                    detail=sanitize(exc),
                    budget_ms=_budget_ms(spec_by_id.get(probe_id)),
                )
    finally:
        # NOT a `with` block, for the same reason as probe_counts: __exit__ calls
        # shutdown(wait=True), which waits for the probes the deadline just gave
        # up on. The counts probe deliberately leaves stragglers behind, so a
        # `with` here would hand the page back only after they finished.
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def _with_console_links(results: dict[str, Probe], config: EnvConfig) -> dict[str, Probe]:
    # Filled in here rather than inside each probe so a probe that raised or
    # timed out still links to the page that would explain why.
    return {
        probe_id: probe
        if probe.console_url or probe_id not in DIMENSION_OF
        else replace(probe, console_url=console_url(probe_id, config))
        for probe_id, probe in results.items()
    }


def _dimension_status(statuses: list[str]) -> str:
    """The worst of a dimension's probes, except that all-unknown stays unknown.

    `worst` ranks unknown with degraded, which is right for a verdict. For a
    tile it would turn "nothing here could be read" into "something here is
    wrong", which is a different finding.
    """
    if statuses and all(status == UNKNOWN for status in statuses):
        return UNKNOWN
    return worst(statuses)


def _document(
    config: EnvConfig, results: dict[str, Probe], *, signals: dict | None, now: datetime | None
) -> dict:
    dimensions = []
    ordered: list[Probe] = []
    for dimension, probe_ids in DIMENSIONS:
        present = [results[probe_id] for probe_id in probe_ids if probe_id in results]
        ordered.extend(present)
        dimensions.append(
            {
                "id": dimension,
                "status": _dimension_status([probe.status for probe in present]),
                "probes": list(probe_ids),
            }
        )

    data_of = {probe.id: (probe.data or {}) for probe in ordered}
    ingestions = data_of.get("ingestions", {})
    lock = data_of.get("ingestion_lock", {}).get("lock") or {
        "held": None,
        "owner": None,
        "since": None,
    }
    stamp = (now or datetime.now(UTC)).replace(microsecond=0)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": stamp.isoformat().replace("+00:00", "Z"),
        "partial": any(probe.status == UNKNOWN for probe in ordered),
        "overall": worst(dimension["status"] for dimension in dimensions),
        "deployment": {
            "id": config.env_id,
            "label": config.env_label,
            "dcp_version": data_of.get("dc_service", {}).get("dcp_version"),
            "project_id": config.project_id,
            "region": config.region,
        },
        "targets": config.targets.to_dict(),
        "dimensions": dimensions,
        "probes": [probe.to_dict() for probe in ordered],
        "signals": signals,
        "counts": data_of.get("counts", {}).get("counts", {}),
        "count_history": ingestions.get("count_history", []),
        "imports": data_of.get("import_status", {}).get("imports"),
        "ingestions": ingestions.get("ingestions", []),
        "freshness": {
            "last_success_at": ingestions.get("last_success_at"),
            "age_hours": ingestions.get("age_hours"),
            "pending_uploads": data_of.get("pending_uploads", {}).get("pending", []),
            "lock": lock,
        },
        "data_sources": data_of.get("data_sources", {}).get("sources", []),
        "unmatched_provenances": data_of.get("data_sources", {}).get("unmatched_provenances", []),
    }
