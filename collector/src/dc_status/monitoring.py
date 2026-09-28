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

"""The golden signals, read from Cloud Monitoring over REST.

REST through rest.py rather than the client library (D3): the dependency set
stays google-auth, requests and gunicorn. Every query is read ONCE per
collection and shared by the errors, latency and saturation probes.

Aggregation choices, and why:

- Traffic and errors: request_count aligned with ALIGN_DELTA per minute and
  summed across revisions, grouped by response_code_class. Exact counts, so
  availability is computed, not estimated.
- Latency: ALIGN_PERCENTILE_xx per revision, then REDUCE_MAX across revisions.
  Percentiles cannot be merged exactly after the fact; taking the worst
  revision is conservative and exact whenever one revision serves, which is
  the steady state. The window figures use one alignment period spanning the
  whole window; the p95 series uses one per minute.
- Cloud Run CPU and memory: the utilization distributions, as p95 across the
  window, worst revision. p95 rather than mean because one pinned instance is
  saturation even when the fleet average looks calm.
- Instances: instance_count with state "active", summed across revisions, the
  newest minute. Compared with the service's maxInstanceCount.
- Spanner: high-priority CPU, mean across the window, summed across is_system
  and databases, which is how Google states its recommended ceiling.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from .timestamps import parse_timestamp

_MONITORING = "https://monitoring.googleapis.com/v3"
STEP_SECONDS = 60
# Cloud Run and Spanner metrics become visible up to 2-3 minutes after they are
# sampled. A window ending at "now" would draw that delay as a traffic cliff in
# every document, so it ends this far back, on a whole minute.
_VISIBILITY_LAG = timedelta(minutes=3)
_MAX_PAGES = 5


def window_end(now: datetime) -> datetime:
    return now.replace(second=0, microsecond=0) - _VISIBILITY_LAG


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


class MonitoringReader:
    """`projects.timeSeries.list`, and nothing else."""

    def __init__(self, rest, project_id: str):
        self._rest = rest
        self._url = f"{_MONITORING}/projects/{project_id}/timeSeries"

    def time_series(
        self,
        filter: str,
        *,
        start: datetime,
        end: datetime,
        alignment_seconds: int,
        aligner: str,
        reducer: str | None = None,
        group_by: tuple[str, ...] = (),
    ) -> list[dict]:
        # Object-typed query parameters travel flattened with dots, the standard
        # mapping for GET requests in Google's REST APIs; repeated fields such
        # as groupByFields are sent as the same key repeated.
        params: dict = {
            "filter": filter,
            "interval.startTime": _rfc3339(start),
            "interval.endTime": _rfc3339(end),
            "aggregation.alignmentPeriod": f"{alignment_seconds}s",
            "aggregation.perSeriesAligner": aligner,
            "view": "FULL",
        }
        if reducer:
            params["aggregation.crossSeriesReducer"] = reducer
        if group_by:
            params["aggregation.groupByFields"] = list(group_by)

        series: list[dict] = []
        for _ in range(_MAX_PAGES):
            payload = self._rest.get(self._url, params=params)
            series.extend(payload.get("timeSeries") or [])
            token = payload.get("nextPageToken")
            if not token:
                break
            params = {**params, "pageToken": token}
        return series


def _run_filter(metric: str, service_name: str, extra: str = "") -> str:
    # resource.type is not optional: request_count and request_latencies are
    # also published on cloud_run_instance, and every request would count twice.
    text = (
        f'metric.type="run.googleapis.com/{metric}" '
        f'AND resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{service_name}"'
    )
    return f"{text} AND {extra}" if extra else text


def _spanner_filter(instance_id: str) -> str:
    return (
        'metric.type="spanner.googleapis.com/instance/cpu/utilization_by_priority" '
        'AND resource.type="spanner_instance" '
        f'AND resource.labels.instance_id="{instance_id}" '
        'AND metric.labels.priority="high"'
    )


def _number(point: dict) -> float | None:
    value = point.get("value") or {}
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "int64Value" in value:
        return float(int(value["int64Value"]))
    return None


def _grid(points: list[dict], end: datetime, steps: int) -> list[float | None]:
    """One slot per step, oldest first; a step with no point stays None.

    Points are placed by their end time, rounded to the nearest step, so an
    alignment that lands a few seconds off the minute still finds its slot.
    """
    slots: list[float | None] = [None] * steps
    for point in points:
        ended = parse_timestamp((point.get("interval") or {}).get("endTime"))
        value = _number(point)
        if ended is None or value is None:
            continue
        back = round((end - ended).total_seconds() / STEP_SECONDS)
        index = steps - 1 - back
        if 0 <= index < steps:
            slots[index] = (slots[index] or 0.0) + value
    return slots


def _newest(series: list[dict]) -> float | None:
    """The newest point of the first series. Points arrive newest first."""
    for item in series:
        for point in item.get("points") or []:
            value = _number(point)
            if value is not None:
                return value
    return None


def read_signals(
    reader,
    *,
    service_name: str,
    spanner_instance_id: str,
    window_minutes: int,
    now: datetime | None = None,
) -> dict:
    """Run every query, in parallel, and return the raw figures.

    Any query failing raises: the three probes built on these figures then all
    report unknown together, and the document carries `signals: null`, rather
    than a half-read picture that looks complete.
    """
    end = window_end(now or datetime.now(UTC))
    start = end - timedelta(minutes=window_minutes)
    window_seconds = window_minutes * 60

    def per_minute(filter, aligner, reducer, group_by=()):
        return lambda: reader.time_series(
            filter,
            start=start,
            end=end,
            alignment_seconds=STEP_SECONDS,
            aligner=aligner,
            reducer=reducer,
            group_by=group_by,
        )

    def whole_window(filter, aligner, reducer):
        return lambda: reader.time_series(
            filter,
            start=start,
            end=end,
            alignment_seconds=window_seconds,
            aligner=aligner,
            reducer=reducer,
        )

    latencies = _run_filter("request_latencies", service_name)
    queries = {
        "traffic": per_minute(
            _run_filter("request_count", service_name),
            "ALIGN_DELTA",
            "REDUCE_SUM",
            ("metric.labels.response_code_class",),
        ),
        "p95_series": per_minute(latencies, "ALIGN_PERCENTILE_95", "REDUCE_MAX"),
        "p50": whole_window(latencies, "ALIGN_PERCENTILE_50", "REDUCE_MAX"),
        "p95": whole_window(latencies, "ALIGN_PERCENTILE_95", "REDUCE_MAX"),
        "p99": whole_window(latencies, "ALIGN_PERCENTILE_99", "REDUCE_MAX"),
        "run_cpu": whole_window(
            _run_filter("container/cpu/utilizations", service_name),
            "ALIGN_PERCENTILE_95",
            "REDUCE_MAX",
        ),
        "run_memory": whole_window(
            _run_filter("container/memory/utilizations", service_name),
            "ALIGN_PERCENTILE_95",
            "REDUCE_MAX",
        ),
        "instances": per_minute(
            _run_filter("container/instance_count", service_name, 'metric.labels.state="active"'),
            "ALIGN_MAX",
            "REDUCE_SUM",
        ),
        "spanner_cpu": whole_window(
            _spanner_filter(spanner_instance_id), "ALIGN_MEAN", "REDUCE_SUM"
        ),
    }
    # A `with` block is right here, unlike in assemble: every query is bounded
    # by the REST client's own timeout, and the collection's deadline still
    # drops this whole fetch if it runs long.
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        futures = {name: pool.submit(query) for name, query in queries.items()}
        results = {name: future.result() for name, future in futures.items()}

    totals = [0.0] * window_minutes
    server_errors = [0.0] * window_minutes
    for item in results["traffic"]:
        response_class = ((item.get("metric") or {}).get("labels") or {}).get(
            "response_code_class", ""
        )
        for index, value in enumerate(_grid(item.get("points") or [], end, window_minutes)):
            if value is None:
                continue
            totals[index] += value
            if response_class == "5xx":
                server_errors[index] += value

    p95_series: list[float | None] = [None] * window_minutes
    for item in results["p95_series"]:
        for index, value in enumerate(_grid(item.get("points") or [], end, window_minutes)):
            if value is not None:
                p95_series[index] = max(value, p95_series[index] or 0.0)

    return {
        "window_minutes": window_minutes,
        "totals": totals,
        "server_errors": server_errors,
        "p95_series": p95_series,
        "p50": _newest(results["p50"]),
        "p95": _newest(results["p95"]),
        "p99": _newest(results["p99"]),
        "run_cpu": _newest(results["run_cpu"]),
        "run_memory": _newest(results["run_memory"]),
        "instances": _newest(results["instances"]),
        "spanner_cpu": _newest(results["spanner_cpu"]),
    }


def _pct(fraction: float | None) -> float | None:
    return None if fraction is None else round(fraction * 100, 1)


def _ms(value: float | None) -> int | None:
    return None if value is None else round(value)


def shape_signals(raw: dict, targets, max_instances: int | None) -> dict:
    """The document's `signals` object, from the raw figures."""
    window = raw["window_minutes"]
    totals, server_errors = raw["totals"], raw["server_errors"]
    requests = sum(totals)
    failed = sum(server_errors)
    # D6: below the floor, errors and latency are shown but not judged. The
    # floor is per hour and scales with the window.
    floor = targets.min_requests_per_hour * window / 60
    judged = requests > 0 and requests >= floor
    availability = round(100 * (1 - failed / requests), 3) if requests else None
    error_pct = round(100 * failed / requests, 3) if requests else None
    instances = raw["instances"]
    return {
        "window_minutes": window,
        "step_seconds": STEP_SECONDS,
        "traffic": {
            "rps": round(requests / (window * 60), 1),
            "requests": int(requests),
            "series": [round(total / STEP_SECONDS, 1) for total in totals],
        },
        "errors": {
            "availability_pct": availability,
            "error_pct": error_pct,
            "judged": judged,
            "series": [
                round(100 * errors / total, 3) if total else 0.0
                for errors, total in zip(server_errors, totals, strict=True)
            ],
        },
        "latency": {
            "p50_ms": _ms(raw["p50"]),
            "p95_ms": _ms(raw["p95"]),
            "p99_ms": _ms(raw["p99"]),
            "judged": judged,
            "series_p95": [
                None if value is None else round(value, 1) for value in raw["p95_series"]
            ],
        },
        "saturation": {
            "run_cpu_pct": _pct(raw["run_cpu"]),
            "run_memory_pct": _pct(raw["run_memory"]),
            "instances": None if instances is None else int(instances),
            "max_instances": max_instances,
            "spanner_cpu_pct": _pct(raw["spanner_cpu"]),
        },
    }
