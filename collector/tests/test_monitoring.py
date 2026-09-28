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

"""The Cloud Monitoring reader, and the golden signals built from it."""

from datetime import UTC, datetime, timedelta

from dc_status.config import Targets
from dc_status.model import DEGRADED, HEALTHY, UNKNOWN
from dc_status.monitoring import (
    MonitoringReader,
    read_signals,
    shape_signals,
    window_end,
)
from dc_status.probes import probe_errors, probe_latency, probe_saturation
from dc_status.rest import RestClient
from tests.conftest import FakeResponse, FakeSession

NOW = datetime(2026, 9, 28, 13, 0, 30, tzinfo=UTC)
END = window_end(NOW)


def _stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _point(steps_back: int, value, kind="int64Value"):
    return {
        "interval": {"endTime": _stamp(END - timedelta(minutes=steps_back))},
        "value": {kind: str(value) if kind == "int64Value" else value},
    }


# --- the REST reader -------------------------------------------------------------


def test_the_window_ends_on_a_whole_minute_behind_the_ingestion_delay():
    # Cloud Run and Spanner points become visible up to 2-3 minutes late. Ending
    # the window on "now" would draw that delay as a traffic cliff every time.
    assert datetime(2026, 9, 28, 12, 57, tzinfo=UTC) == END


def test_the_reader_sends_the_documented_list_parameters():
    session = FakeSession({"/timeSeries": FakeResponse(payload={"timeSeries": [{"points": []}]})})
    reader = MonitoringReader(RestClient(session, retries=0), "example-project")
    series = reader.time_series(
        'metric.type="run.googleapis.com/request_count"',
        start=END - timedelta(minutes=60),
        end=END,
        alignment_seconds=60,
        aligner="ALIGN_DELTA",
        reducer="REDUCE_SUM",
        group_by=("metric.labels.response_code_class",),
    )
    assert series == [{"points": []}]
    method, url, params = session.calls[0]
    assert method == "GET"
    assert url == "https://monitoring.googleapis.com/v3/projects/example-project/timeSeries"
    assert params == {
        "filter": 'metric.type="run.googleapis.com/request_count"',
        "interval.startTime": "2026-09-28T11:57:00Z",
        "interval.endTime": "2026-09-28T12:57:00Z",
        "aggregation.alignmentPeriod": "60s",
        "aggregation.perSeriesAligner": "ALIGN_DELTA",
        "aggregation.crossSeriesReducer": "REDUCE_SUM",
        "aggregation.groupByFields": ["metric.labels.response_code_class"],
        "view": "FULL",
    }


def test_the_reader_follows_page_tokens():
    session = FakeSession(
        {
            "pageToken=next": FakeResponse(payload={"timeSeries": [{"id": 2}]}),
            # Only the first page has interval.startTime right before view: the
            # sorted query puts pageToken between them on the second.
            "57:00Z&view=FULL$": FakeResponse(
                payload={"timeSeries": [{"id": 1}], "nextPageToken": "next"}
            ),
        }
    )
    reader = MonitoringReader(RestClient(session, retries=0), "p")
    series = reader.time_series("f", start=END, end=END, alignment_seconds=60, aligner="ALIGN_MEAN")
    assert series == [{"id": 1}, {"id": 2}]


# --- reading the signals ---------------------------------------------------------


class FakeReader:
    """Routes a query by (metric fragment, aligner) to canned series."""

    def __init__(self, routes, fail=None):
        self.routes = routes
        self.fail = fail
        self.queries = []

    def time_series(self, filter, **kwargs):
        self.queries.append((filter, kwargs))
        if self.fail:
            raise self.fail
        for (fragment, aligner), series in self.routes.items():
            if fragment in filter and aligner == kwargs["aligner"]:
                return series
        return []


def _traffic(per_minute_2xx, per_minute_5xx, minutes=60):
    return [
        {
            "metric": {"labels": {"response_code_class": "2xx"}},
            "points": [_point(k, per_minute_2xx) for k in range(minutes)],
        },
        {
            "metric": {"labels": {"response_code_class": "5xx"}},
            "points": [_point(k, per_minute_5xx) for k in range(minutes)],
        },
    ]


def _single(value):
    return [{"points": [_point(0, value, "doubleValue")]}]


def _routes(per_minute_2xx=2400, per_minute_5xx=1, p95=610.0, spanner=0.40, instances=3):
    return {
        ("request_count", "ALIGN_DELTA"): _traffic(per_minute_2xx, per_minute_5xx),
        ("request_latencies", "ALIGN_PERCENTILE_50"): _single(180.0),
        ("request_latencies", "ALIGN_PERCENTILE_95"): _single(p95),
        ("request_latencies", "ALIGN_PERCENTILE_99"): _single(1400.0),
        ("cpu/utilizations", "ALIGN_PERCENTILE_95"): _single(0.38),
        ("memory/utilizations", "ALIGN_PERCENTILE_95"): _single(0.61),
        ("instance_count", "ALIGN_MAX"): [
            {"points": [_point(0, instances), _point(1, instances + 1)]}
        ],
        ("utilization_by_priority", "ALIGN_MEAN"): _single(spanner),
    }


def _read(routes, window=60):
    return read_signals(
        FakeReader(routes),
        service_name="example-dc-service",
        spanner_instance_id="example-instance",
        window_minutes=window,
        now=NOW,
    )


def test_every_query_is_scoped_to_this_service_or_instance():
    reader = FakeReader(_routes())
    read_signals(
        reader,
        service_name="example-dc-service",
        spanner_instance_id="example-instance",
        window_minutes=60,
        now=NOW,
    )
    filters = [query[0] for query in reader.queries]
    run = [f for f in filters if "run.googleapis.com" in f]
    spanner = [f for f in filters if "spanner.googleapis.com" in f]
    assert run and spanner
    for f in run:
        # request_count also exists on cloud_run_instance; without the resource
        # type every request would be counted twice.
        assert 'resource.type="cloud_run_revision"' in f
        assert 'resource.labels.service_name="example-dc-service"' in f
    assert any('metric.labels.state="active"' in f for f in run)
    assert spanner == [
        'metric.type="spanner.googleapis.com/instance/cpu/utilization_by_priority" '
        'AND resource.type="spanner_instance" '
        'AND resource.labels.instance_id="example-instance" '
        'AND metric.labels.priority="high"'
    ]


def test_traffic_is_split_by_response_class_across_revisions():
    reader = FakeReader(_routes())
    read_signals(reader, service_name="s", spanner_instance_id="i", window_minutes=60, now=NOW)
    traffic = next(q for q in reader.queries if "request_count" in q[0])[1]
    assert traffic["reducer"] == "REDUCE_SUM"
    assert traffic["group_by"] == ("metric.labels.response_code_class",)
    assert traffic["alignment_seconds"] == 60


def test_normal_traffic_is_shaped_one_point_per_step_oldest_first():
    raw = _read(_routes(per_minute_2xx=2400, per_minute_5xx=1))
    signals = shape_signals(raw, Targets(), max_instances=6)
    assert signals["window_minutes"] == 60
    assert signals["step_seconds"] == 60
    traffic = signals["traffic"]
    assert traffic["requests"] == 2401 * 60
    assert traffic["rps"] == round(2401 * 60 / 3600, 1)
    assert len(traffic["series"]) == 60
    assert traffic["series"][0] == round(2401 / 60, 1)
    errors = signals["errors"]
    assert errors["judged"] is True
    assert errors["availability_pct"] == round(100 * (1 - 1 / 2401), 3)
    assert errors["error_pct"] == round(100 / 2401, 3)
    assert len(errors["series"]) == 60
    latency = signals["latency"]
    assert (latency["p50_ms"], latency["p95_ms"], latency["p99_ms"]) == (180, 610, 1400)
    assert latency["judged"] is True
    assert signals["saturation"] == {
        "run_cpu_pct": 38.0,
        "run_memory_pct": 61.0,
        "instances": 3,  # the newest point, not the window's peak
        "max_instances": 6,
        "spanner_cpu_pct": 40.0,
    }


def test_a_step_with_no_data_is_zero_traffic_and_no_latency():
    routes = _routes()
    routes[("request_count", "ALIGN_DELTA")] = [
        {
            "metric": {"labels": {"response_code_class": "2xx"}},
            "points": [_point(0, 600), _point(2, 600)],
        }
    ]
    routes[("request_latencies", "ALIGN_PERCENTILE_95")] = [
        {"points": [_point(0, 500.0, "doubleValue")]}
    ]
    signals = shape_signals(_read(routes, window=3), Targets(min_requests_per_hour=0), None)
    assert signals["traffic"]["series"] == [10.0, 0.0, 10.0]
    # No requests in a step means no latency to report, not a latency of zero.
    assert signals["latency"]["series_p95"] == [None, None, 500.0]


def test_low_traffic_is_shown_but_not_judged():
    # 1 request a minute is 60 an hour, under the default 100.
    signals = shape_signals(_read(_routes(per_minute_2xx=0, per_minute_5xx=1)), Targets(), 6)
    assert signals["errors"]["judged"] is False
    assert signals["latency"]["judged"] is False
    assert signals["errors"]["availability_pct"] == 0.0  # shown all the same
    errors = probe_errors(signals, Targets())
    assert errors.status == HEALTHY
    assert errors.detail == "low traffic, not judged"
    assert probe_latency(signals, Targets()).detail == "low traffic, not judged"


def test_the_low_traffic_floor_scales_with_the_window():
    # 100 an hour over a 30-minute window is 50. 60 requests clear it.
    raw = _read({("request_count", "ALIGN_DELTA"): _traffic(2, 0, minutes=30)}, window=30)
    assert shape_signals(raw, Targets(), None)["errors"]["judged"] is True


def test_no_requests_at_all_has_no_availability():
    raw = _read({})
    signals = shape_signals(raw, Targets(), None)
    assert signals["traffic"]["requests"] == 0
    assert signals["errors"]["availability_pct"] is None
    assert signals["errors"]["judged"] is False


def test_an_error_spike_below_the_availability_target_degrades():
    signals = shape_signals(_read(_routes(per_minute_2xx=990, per_minute_5xx=10)), Targets(), 6)
    probe = probe_errors(signals, Targets())
    assert probe.status == DEGRADED
    assert "99 %" in probe.detail
    assert "99.5 % target" in probe.detail


def test_availability_at_or_above_the_target_is_healthy():
    signals = shape_signals(_read(_routes()), Targets(), 6)
    assert probe_errors(signals, Targets()).status == HEALTHY


def test_p95_above_the_latency_target_degrades():
    signals = shape_signals(_read(_routes(p95=1400.0)), Targets(), 6)
    probe = probe_latency(signals, Targets())
    assert probe.status == DEGRADED
    assert "1400 ms" in probe.detail
    assert "1000 ms target" in probe.detail


def test_p95_within_the_latency_target_is_healthy():
    signals = shape_signals(_read(_routes()), Targets(), 6)
    assert probe_latency(signals, Targets()).status == HEALTHY


class _Where:
    project_id = "example-project"
    region = "us-central1"
    spanner_instance_id = "example-instance"
    datacommons_service_name = "example-dc-service"


def test_spanner_cpu_above_its_target_degrades_and_links_to_spanner():
    signals = shape_signals(_read(_routes(spanner=0.71)), Targets(), 6)
    probe = probe_saturation(signals, Targets(), _Where())
    assert probe.status == DEGRADED
    assert probe.detail == "Spanner high-priority CPU at 71 %, above the 65 % target"
    assert probe.console_url == (
        "https://console.cloud.google.com/spanner/instances/example-instance/details/"
        "monitoring?project=example-project"
    )


def test_running_at_the_configured_maximum_instances_degrades():
    signals = shape_signals(_read(_routes(instances=6)), Targets(), 6)
    probe = probe_saturation(signals, Targets(), _Where())
    assert probe.status == DEGRADED
    assert "6 of 6 instances" in probe.detail
    assert "/run/detail/" in probe.console_url


def test_a_value_exactly_at_its_target_counts():
    signals = shape_signals(_read(_routes(spanner=0.65)), Targets(), 6)
    probe = probe_saturation(signals, Targets(), _Where())
    assert probe.status == DEGRADED
    assert "at the 65 % target" in probe.detail


def test_saturation_within_every_target_is_healthy():
    signals = shape_signals(_read(_routes()), Targets(), 6)
    probe = probe_saturation(signals, Targets(), _Where())
    assert probe.status == HEALTHY
    assert probe.console_url is None  # the collector fills in the default page


def test_saturation_with_no_data_at_all_is_unknown():
    signals = shape_signals(_read({}), Targets(), None)
    assert probe_saturation(signals, Targets(), _Where()).status == UNKNOWN
