"""Run the probes in parallel and shape the document the page consumes."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .config import EnvConfig, PeerConfig
from .model import UNKNOWN, Probe, ProbeContext, worst
from .probes import (
    COUNTS_BUDGET_SECONDS,
    probe_counts,
    probe_data_sources,
    probe_dc_api,
    probe_dc_service,
    probe_frontend,
    probe_ingestion_lock,
    probe_ingestions,
    probe_schema,
    probe_spanner,
    probe_version_consistency,
)
from .rest import PUBLIC_TIMEOUT_SECONDS
from .sanitize import sanitize

_PROBE_DEADLINE_SECONDS = 25.0
_PEER_DEADLINE_SECONDS = 20.0


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


# S1-S4 and S6-S10. S5 (version_consistency) is derived after these complete.
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
        return Probe(
            id=probe.id,
            status=probe.status,
            detail=probe.detail,
            elapsed_ms=elapsed,
            budget_ms=_budget_ms(spec),
            data=probe.data,
        )

    return cache.get_or_call(f"{config.env_id}:{spec.id}", _ttl_for(spec, config), produce)


def collect_self(
    config: EnvConfig,
    clients: Clients,
    cache,
    *,
    now: datetime | None = None,
    probes: tuple[ProbeSpec, ...] = PROBES,
) -> dict:
    ctx = _context(config, clients)
    results: dict[str, Probe] = {}
    spec_by_id = {spec.id: spec for spec in probes}
    pool = ThreadPoolExecutor(max_workers=max(1, len(probes)))
    try:
        futures = {spec.id: pool.submit(_run_one, spec, ctx, config, cache) for spec in probes}
        for probe_id, future in futures.items():
            try:
                results[probe_id] = future.result(timeout=_PROBE_DEADLINE_SECONDS)
            except FuturesTimeout:
                # It did not merely fail, it used every millisecond it was given and
                # was dropped. Recording elapsed as the full budget is the honest
                # reading; leaving it at zero would report the slowest possible
                # probe as the fastest.
                results[probe_id] = Probe(
                    id=probe_id,
                    status=UNKNOWN,
                    detail=f"the check did not answer within {_PROBE_DEADLINE_SECONDS:g} s",
                    # The pool's clock is what dropped it, whatever its own inner
                    # limit was, so that is the budget it actually spent.
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

    ordered = [results[key] for key in sorted(results)]
    environment = _environment_document(config, ordered)
    partial = any(probe.status == UNKNOWN for probe in ordered)
    return _wrap([environment], now=now, partial=partial)


def _environment_document(config: EnvConfig, probes: list[Probe]) -> dict:
    data_of = {probe.id: (probe.data or {}) for probe in probes}
    return {
        "id": config.env_id,
        "label": config.env_label,
        "self": True,
        "reachable": True,
        "overall": worst(probe.status for probe in probes),
        "dcp_version": data_of.get("dc_service", {}).get("dcp_version"),
        "schema_tables": data_of.get("schema", {}).get("tables", []),
        "counts": data_of.get("counts", {}).get("counts", {}),
        "ingestions": data_of.get("ingestions", {}).get("ingestions", []),
        "data_sources": data_of.get("data_sources", {}).get("sources", []),
        "unmatched_provenances": data_of.get("data_sources", {}).get("unmatched_provenances", []),
        "probes": [probe.to_dict() for probe in probes],
    }


def _wrap(environments: list[dict], *, now: datetime | None, partial: bool) -> dict:
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    return {
        "generated_at": stamp,
        "overall": worst(environment.get("overall", UNKNOWN) for environment in environments),
        "partial": partial,
        "environments": environments,
    }


def _unreachable_peer(peer: PeerConfig, exc: Exception) -> dict:
    return {
        "id": peer.id,
        "label": peer.label,
        "self": False,
        "reachable": False,
        "overall": UNKNOWN,
        "detail": sanitize(exc),
        "dcp_version": None,
        "schema_tables": [],
        "counts": {},
        "ingestions": [],
        "data_sources": [],
        "unmatched_provenances": [],
        "probes": [],
    }


def collect_all(
    config: EnvConfig,
    clients: Clients,
    cache,
    fetch_peer: Callable[[PeerConfig], dict],
    *,
    now: datetime | None = None,
    probes: tuple[ProbeSpec, ...] = PROBES,
    peer_deadline_seconds: float = _PEER_DEADLINE_SECONDS,
) -> dict:
    local = collect_self(config, clients, cache, now=now, probes=probes)
    environments = list(local["environments"])
    partial = bool(local["partial"])

    remote_by_id: dict[str, dict] = {}
    if config.peers:
        started = time.monotonic()
        pool = ThreadPoolExecutor(max_workers=max(1, len(config.peers)))
        try:
            futures = {peer.id: pool.submit(fetch_peer, peer) for peer in config.peers}
            for peer in config.peers:
                remaining = peer_deadline_seconds - (time.monotonic() - started)
                try:
                    payload = futures[peer.id].result(timeout=max(0.1, remaining))
                    remote = (payload.get("environments") or [{}])[0]
                    if not isinstance(remote, dict):
                        # A well-formed-but-degraded peer can legitimately answer
                        # 200 with environments: []. Falling back to {} above is
                        # fine; anything else in that slot is not, and must be
                        # treated as a failure rather than crash the aggregation.
                        raise TypeError("peer environment payload was not an object")
                    remote = dict(remote)
                    remote.setdefault("id", peer.id)
                    remote.setdefault("label", peer.label)
                    remote.setdefault("overall", UNKNOWN)
                    remote["self"] = False
                    remote["reachable"] = True
                except Exception as exc:
                    partial = True
                    remote = _unreachable_peer(peer, exc)
                remote_by_id[peer.id] = remote
        finally:
            # NOT a `with` block, for the same reason as collect_self: __exit__
            # calls shutdown(wait=True), which would wait for a peer fetch the
            # deadline just gave up on.
            pool.shutdown(wait=False, cancel_futures=True)

    environments.extend(remote_by_id[peer.id] for peer in config.peers)
    return _wrap(environments, now=now, partial=partial)
