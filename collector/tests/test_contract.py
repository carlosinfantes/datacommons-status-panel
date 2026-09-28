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

"""The page is built against tests/fixtures/demo.json. These tests hold the
collector to that shape, and hold the fixture to the rules of the document.

The assembled document comes from the REAL probe set run against faked
clients, not from hand-written probe results, so a key renamed anywhere
between a probe and the document fails here.
"""

import copy
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dc_status.assemble import PROBES, Clients, _dimension_status, collect_status
from dc_status.cache import TTLCache
from dc_status.config import EnvConfig, Targets
from dc_status.model import DIMENSIONS, UNKNOWN, worst
from dc_status.monitoring import window_end
from dc_status.rest import RestClient
from tests.conftest import FakeResponse, FakeSession

DEMO = json.loads((Path(__file__).parent / "fixtures" / "demo.json").read_text())

# Maps whose keys are data, not schema: table names, and each probe's own data.
_OPEN_MAPS = {("counts",), ("probes", "[]", "data")}


def _compare(expected, actual, path=()):
    """Same nested key structure. Dicts compare key sets recursively; lists
    compare their first elements; leaves (and nulls) are not compared."""
    if path in _OPEN_MAPS:
        assert isinstance(actual, dict), path
        return
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{'.'.join(path)}: expected an object, got {actual!r}"
        assert set(actual) == set(expected), (
            f"{'.'.join(path) or 'document'}: missing {sorted(set(expected) - set(actual))}, "
            f"unexpected {sorted(set(actual) - set(expected))}"
        )
        for key in expected:
            _compare(expected[key], actual[key], (*path, key))
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{'.'.join(path)}: expected a list, got {actual!r}"
        if expected and actual:
            _compare(expected[0], actual[0], (*path, "[]"))


# --- a whole deployment, faked at the client boundary ------------------------------

NOW = datetime.now(UTC).replace(microsecond=0)


def _iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


LAST_SUCCESS = NOW - timedelta(hours=48)
_TABLES = [
    "Edge",
    "ImportStatus",
    "IngestionHistory",
    "IngestionLock",
    "KeyValueStore",
    "Node",
    "Observation",
    "TimeSeries",
]


class FakeSpanner:
    def query(self, sql, **_kwargs):
        if "information_schema" in sql:
            return [{"table_name": name} for name in _TABLES]
        if re.fullmatch(r"SELECT COUNT\(\*\) AS Total FROM \w+", sql):
            return [{"Total": 1000}]
        if "FROM IngestionLock" in sql:
            return [{"Total": 1, "Held": 0, "OldestAcquired": None}]
        if "Status = 'SUCCESS'" in sql:
            return [
                {
                    "CompletionTimestamp": _iso(LAST_SUCCESS - timedelta(days=days)),
                    "NodeCount": 100,
                    "EdgeCount": 200,
                    "ObservationCount": 300,
                    "TimeSeriesCount": 40,
                }
                for days in (0, 7)
            ]
        if "FROM IngestionHistory" in sql:
            return [
                {
                    "CreationTimestamp": _iso(LAST_SUCCESS - timedelta(hours=1)),
                    "CompletionTimestamp": _iso(LAST_SUCCESS),
                    "Status": "SUCCESS",
                    "Stage": "DONE",
                    "IngestionFailure": False,
                    "ExecutionTime": 3600,
                    "Imports": 1,
                    "WorkflowExecutionID": "exec-1",
                }
            ]
        if "GROUP BY provenance" in sql:
            return [
                {"provenance": "HEALTH", "RowCount": 5},
                {"provenance": "LEGACY_IMPORT", "RowCount": 3},
            ]
        if "FROM ImportStatus" in sql:
            return [{"ImportName": "health", "State": "SUCCESS"}]
        raise AssertionError(f"unexpected SQL in the contract test: {sql}")

    def close(self):
        pass


class FakePublic:
    def get_text(self, url, timeout=None):
        if "/core/api/v2/node" in url:
            return (
                200,
                '{"data":{"country/GTM":{"arcs":{"name":{"nodes":[{"value":"Guatemala"}]}}}}}',
            )
        return 200, "<html></html>"


class FakeReader:
    def time_series(self, filter, **kwargs):
        end = window_end(datetime.now(UTC))

        def point(back, value, kind):
            return {
                "interval": {"endTime": _iso(end - timedelta(minutes=back))},
                "value": {kind: value},
            }

        if "request_count" in filter:
            return [
                {
                    "metric": {"labels": {"response_code_class": "2xx"}},
                    "points": [point(k, "2400", "int64Value") for k in range(60)],
                }
            ]
        if "instance_count" in filter:
            return [{"points": [point(0, "3", "int64Value")]}]

        def around(value):
            # A distribution with every sample just below `value`.
            return {
                "bucketOptions": {"explicitBuckets": {"bounds": [value * 0.999, value]}},
                "bucketCounts": ["0", "10", "0"],
            }

        if "request_latencies" in filter:
            return [{"points": [point(0, around(600.0), "distributionValue")]}]
        if "utilizations" in filter:
            return [{"points": [point(0, around(0.4), "distributionValue")]}]
        return [{"points": [point(0, 0.4, "doubleValue")]}]


def _config():
    return EnvConfig(
        env_id="prod",
        env_label="Production",
        project_id="example-project",
        region="us-central1",
        spanner_instance_id="example-instance",
        spanner_database_id="example-db",
        datacommons_service_name="example-dc-service",
        ingestion_workflow_name="example-ingestion",
        artifacts_bucket_name="example-artifacts",
        public_endpoint_url="https://api.example",
        frontend_url="https://www.example",
        data_source_prefixes=("health",),
        input_prefix="ingestion/input/",
        counts_cache_ttl_seconds=300,
        schema_cache_ttl_seconds=3600,
    )


def _clients():
    session = FakeSession(
        {
            "/services/example-dc-service$": FakeResponse(
                payload={
                    "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
                    "latestReadyRevision": "",
                    "template": {
                        "scaling": {"maxInstanceCount": 6},
                        "containers": [{"image": "example.invalid/datacommons-services:1.1.4"}],
                    },
                }
            ),
            "/instances/example-instance$": FakeResponse(
                payload={"state": "READY", "processingUnits": 1000}
            ),
            "/databases/example-db$": FakeResponse(payload={"state": "READY"}),
            "/executions": FakeResponse(
                payload={
                    "executions": [
                        {
                            "name": "projects/p/locations/r/workflows/w/executions/exec-1",
                            "state": "SUCCEEDED",
                        }
                    ]
                }
            ),
            "/b/example-artifacts/o": FakeResponse(
                payload={
                    "items": [
                        {
                            "name": "ingestion/input/health/a.csv",
                            "size": "10",
                            "updated": _iso(NOW - timedelta(hours=2)),
                        }
                    ]
                }
            ),
        }
    )
    return Clients(
        rest=RestClient(session, retries=0),
        public=FakePublic(),
        spanner_factory=FakeSpanner,
        monitoring=FakeReader(),
    )


def _assembled():
    return collect_status(_config(), _clients(), TTLCache(), now=NOW, probes=PROBES)


def test_the_assembled_document_has_the_shape_of_the_demo_document():
    _compare(DEMO, _assembled())


def test_the_comparison_catches_a_renamed_or_missing_nested_key():
    # A comparator that passes everything would make the test above worthless.
    renamed = copy.deepcopy(DEMO)
    renamed["freshness"]["lock"]["holder"] = renamed["freshness"]["lock"].pop("owner")
    with pytest.raises(AssertionError, match="freshness.lock"):
        _compare(DEMO, renamed)
    missing = copy.deepcopy(DEMO)
    del missing["count_history"][0]["TimeSeries"]
    with pytest.raises(AssertionError, match="count_history"):
        _compare(DEMO, missing)


def test_the_assembled_document_carries_every_probe_the_demo_does():
    document = _assembled()
    assert [p["id"] for p in document["probes"]] == [p["id"] for p in DEMO["probes"]]
    # Nothing in the faked deployment is broken, so a probe that still comes
    # back unknown is a wiring fault, not a finding.
    unknown = {p["id"]: p["detail"] for p in document["probes"] if p["status"] == UNKNOWN}
    assert unknown == {}
    # The faked uploads are newer than the last success, so the list the page
    # draws is exercised, not just its empty case.
    assert document["freshness"]["pending_uploads"]


# --- the fixture follows the document's own rules ----------------------------------


def test_the_demo_is_schema_version_2():
    assert DEMO["schema_version"] == 2


def test_the_demo_dimensions_are_the_fixed_four_in_order():
    assert [(d["id"], d["probes"]) for d in DEMO["dimensions"]] == [
        (dimension, list(probe_ids)) for dimension, probe_ids in DIMENSIONS
    ]


def test_every_demo_probe_is_listed_in_exactly_one_dimension_and_says_which():
    listed = [probe_id for d in DEMO["dimensions"] for probe_id in d["probes"]]
    assert len(listed) == len(set(listed))
    assert sorted(listed) == sorted(p["id"] for p in DEMO["probes"])
    owner = {probe_id: d["id"] for d in DEMO["dimensions"] for probe_id in d["probes"]}
    for probe in DEMO["probes"]:
        assert probe["dimension"] == owner[probe["id"]], probe["id"]


def test_the_demo_statuses_aggregate_the_way_the_collector_aggregates():
    status_of = {p["id"]: p["status"] for p in DEMO["probes"]}
    for dimension in DEMO["dimensions"]:
        expected = _dimension_status([status_of[p] for p in dimension["probes"]])
        assert dimension["status"] == expected, dimension["id"]
    assert DEMO["overall"] == worst(d["status"] for d in DEMO["dimensions"])
    assert DEMO["partial"] == any(status == UNKNOWN for status in status_of.values())


def test_the_demo_targets_are_the_documented_targets():
    assert DEMO["targets"].keys() == Targets().to_dict().keys()


def test_the_demo_series_have_one_point_per_step():
    signals = DEMO["signals"]
    steps = signals["window_minutes"] * 60 // signals["step_seconds"]
    assert len(signals["traffic"]["series"]) == steps
    assert len(signals["errors"]["series"]) == steps
    assert len(signals["latency"]["series_p95"]) == steps


def test_the_demo_count_history_is_oldest_first_and_at_most_ten():
    stamps = [entry["completed_at"] for entry in DEMO["count_history"]]
    assert len(stamps) <= 10
    assert stamps == sorted(stamps)


def test_the_demo_is_a_valid_replay_of_the_collector_status_values():
    allowed = {"healthy", "degraded", "down", "unknown"}
    assert {p["status"] for p in DEMO["probes"]} <= allowed
    assert {d["status"] for d in DEMO["dimensions"]} <= allowed
    assert DEMO["overall"] in allowed
