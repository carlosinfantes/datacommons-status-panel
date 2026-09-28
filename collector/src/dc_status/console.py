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

"""Where in the Cloud Console an operator goes next, per probe.

Kept apart from the probes so a probe that raised or timed out still links to
the page that would explain it: the link depends on what was checked, not on
what was found. `where` is anything carrying the deployment's resource names
(EnvConfig or ProbeContext).
"""

from __future__ import annotations

_BASE = "https://console.cloud.google.com"


def run_metrics_url(where) -> str:
    return (
        f"{_BASE}/run/detail/{where.region}/{where.datacommons_service_name}"
        f"/metrics?project={where.project_id}"
    )


def spanner_monitoring_url(where) -> str:
    return (
        f"{_BASE}/spanner/instances/{where.spanner_instance_id}/details/monitoring"
        f"?project={where.project_id}"
    )


def _run_revisions(where) -> str:
    return (
        f"{_BASE}/run/detail/{where.region}/{where.datacommons_service_name}"
        f"/revisions?project={where.project_id}"
    )


def _spanner_databases(where) -> str:
    return (
        f"{_BASE}/spanner/instances/{where.spanner_instance_id}/details/databases"
        f"?project={where.project_id}"
    )


def _spanner_tables(where) -> str:
    return (
        f"{_BASE}/spanner/instances/{where.spanner_instance_id}/databases"
        f"/{where.spanner_database_id}/details/tables?project={where.project_id}"
    )


def _input_bucket(where) -> str:
    prefix = where.input_prefix.strip("/")
    path = f"{where.artifacts_bucket_name}/{prefix}" if prefix else where.artifacts_bucket_name
    return f"{_BASE}/storage/browser/{path}?project={where.project_id}"


def _workflow_executions(where) -> str:
    return (
        f"{_BASE}/workflows/workflow/{where.region}/{where.ingestion_workflow_name}"
        f"/executions?project={where.project_id}"
    )


def _none(_where) -> None:
    return None


# None is a decision, not an omission: a public URL (frontend) or a derivation
# over other probes (version_consistency, row_drift) has no console page of its
# own. dc_api points at the service's metrics because that is where a failing
# API shows up; the endpoint itself has no console page.
_BY_PROBE = {
    "dc_api": run_metrics_url,
    "dc_service": _run_revisions,
    "spanner": _spanner_databases,
    "schema": _spanner_tables,
    "version_consistency": _none,
    "frontend": _none,
    "errors": run_metrics_url,
    "latency": run_metrics_url,
    "saturation": run_metrics_url,
    "counts": _spanner_tables,
    "data_sources": _input_bucket,
    "import_status": _spanner_tables,
    "row_drift": _none,
    "ingestions": _workflow_executions,
    "ingestion_lock": _spanner_tables,
    "pending_uploads": _input_bucket,
}


def console_url(probe_id: str, where) -> str | None:
    link = _BY_PROBE.get(probe_id)
    if link is None:
        raise KeyError(f"no console decision for probe {probe_id!r}")
    return link(where)
