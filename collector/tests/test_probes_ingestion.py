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

from datetime import UTC, datetime, timedelta

from dc_status.config import Targets
from dc_status.model import DEGRADED, HEALTHY, UNKNOWN
from dc_status.probes import probe_ingestion_lock, probe_ingestions
from dc_status.rest import RestClient
from tests.conftest import FakeResponse, FakeSession

NOW = datetime(2026, 8, 5, 18, 0, tzinfo=UTC)


class FakeSpanner:
    """`routes` maps a fragment of the SQL to the rows that statement returns;
    anything unrouted gets `rows`."""

    def __init__(self, rows, routes=None):
        self._rows = rows
        self._routes = routes or {}
        self.queries = []

    def query(self, sql, **kwargs):
        self.queries.append(sql)
        for fragment, rows in self._routes.items():
            if fragment in sql:
                return rows
        return self._rows

    def close(self):
        pass


def _ctx(spanner, session):
    ctx = type("Ctx", (), {})()
    ctx.spanner_factory = lambda: spanner
    ctx.rest = RestClient(session, retries=0)
    ctx.project_id = "p"
    ctx.region = "us-central1"
    ctx.ingestion_workflow_name = "wf"
    ctx.targets = Targets()
    return ctx


def _executions(state, execution_id="exec-1"):
    return FakeSession(
        {
            "/executions": FakeResponse(
                payload={
                    "executions": [
                        {
                            "name": f"projects/p/locations/us-central1/workflows/wf/executions/{execution_id}",
                            "state": state,
                            "startTime": "2026-08-05T14:00:00Z",
                        }
                    ]
                }
            )
        }
    )


def test_latest_successful_ingestion_is_healthy():
    rows = [
        {
            "CreationTimestamp": "2026-08-05T14:14:00.825993Z",
            "CompletionTimestamp": "2026-08-05T14:40:00Z",
            "Status": "SUCCESS",
            "Stage": "postprocessing",
            "IngestionFailure": False,
            "ExecutionTime": 1560,
            "Imports": 1,
            "WorkflowExecutionID": "exec-1",
        }
    ]
    probe = probe_ingestions(_ctx(FakeSpanner(rows), _executions("SUCCEEDED")))
    assert probe.status == HEALTHY
    assert probe.data["ingestions"][0]["status"] == "SUCCESS"
    assert probe.data["ingestions"][0]["workflow_state"] == "SUCCEEDED"
    assert probe.data["ingestions"][0]["imports"] == 1


def test_a_failed_latest_ingestion_degrades():
    rows = [
        {
            "CreationTimestamp": "2026-08-05T14:14:00Z",
            "CompletionTimestamp": None,
            "Status": "FAILED",
            "Stage": "dataflow",
            "IngestionFailure": True,
            "ExecutionTime": None,
            "Imports": 1,
            "WorkflowExecutionID": "exec-1",
        }
    ]
    probe = probe_ingestions(_ctx(FakeSpanner(rows), _executions("FAILED")))
    assert probe.status == DEGRADED
    assert "FAILED" in probe.detail


def test_an_active_workflow_is_healthy_even_mid_run():
    rows = [
        {
            "CreationTimestamp": "2026-08-05T17:50:00Z",
            "CompletionTimestamp": None,
            "Status": "RUNNING",
            "Stage": "preprocessing",
            "IngestionFailure": False,
            "ExecutionTime": None,
            "Imports": 1,
            "WorkflowExecutionID": "exec-1",
        }
    ]
    probe = probe_ingestions(_ctx(FakeSpanner(rows), _executions("ACTIVE")))
    assert probe.status == HEALTHY
    # Without the active-workflow branch this row would still be HEALTHY, since
    # RUNNING is an accepted status. Only the detail proves which branch ran.
    assert probe.detail == "an ingestion is running"


def test_no_history_is_unknown():
    probe = probe_ingestions(_ctx(FakeSpanner([]), _executions("SUCCEEDED")))
    assert probe.status == UNKNOWN
    assert probe.data["ingestions"] == []


def test_a_running_row_whose_workflow_failed_degrades():
    rows = [
        {
            "CreationTimestamp": "2026-08-05T17:50:00Z",
            "CompletionTimestamp": None,
            "Status": "RUNNING",
            "Stage": "dataflow",
            "IngestionFailure": False,
            "ExecutionTime": None,
            "Imports": 1,
            "WorkflowExecutionID": "exec-1",
        }
    ]
    probe = probe_ingestions(_ctx(FakeSpanner(rows), _executions("FAILED")), now=NOW)
    assert probe.status == DEGRADED
    assert "workflow reported FAILED" in probe.detail


def test_a_long_stuck_running_row_degrades_even_with_no_workflow_signal():
    # A workflow that died without writing a terminal Status. Nothing is ACTIVE,
    # IngestionFailure is False, and the row says RUNNING — the shape that used to
    # report green indefinitely.
    rows = [
        {
            "CreationTimestamp": "2026-08-02T09:00:00Z",
            "CompletionTimestamp": None,
            "Status": "RUNNING",
            "Stage": "dataflow",
            "IngestionFailure": False,
            "ExecutionTime": None,
            "Imports": 1,
            "WorkflowExecutionID": "exec-9",
        }
    ]
    probe = probe_ingestions(_ctx(FakeSpanner(rows), _executions("SUCCEEDED")), now=NOW)
    assert probe.status == DEGRADED
    assert "with no active workflow" in probe.detail


def test_a_recent_running_row_is_healthy():
    rows = [
        {
            "CreationTimestamp": "2026-08-05T17:30:00Z",
            "CompletionTimestamp": None,
            "Status": "RUNNING",
            "Stage": "preprocessing",
            "IngestionFailure": False,
            "ExecutionTime": None,
            "Imports": 1,
            "WorkflowExecutionID": "exec-1",
        }
    ]
    probe = probe_ingestions(_ctx(FakeSpanner(rows), _executions("SUCCEEDED")), now=NOW)
    assert probe.status == HEALTHY


def test_a_free_lock_row_with_null_owner_is_healthy():
    rows = [{"Total": 1, "Held": 0, "OldestAcquired": None}]
    spanner = FakeSpanner(rows)
    probe = probe_ingestion_lock(_ctx(spanner, _executions("SUCCEEDED")), now=NOW)
    assert probe.status == HEALTHY
    assert probe.data["held"] == 0
    # The fake returns canned rows without executing SQL, so no behavioural
    # assertion here can catch the one regression that matters: swapping
    # COUNTIF(LockOwner IS NOT NULL) for COUNT(*) would count the permanent
    # free-lock row as held and paint the panel amber forever. Pin the literal.
    assert "COUNTIF(LockOwner IS NOT NULL)" in spanner.queries[0]


def test_a_recently_held_lock_is_healthy():
    acquired = (NOW - timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
    rows = [{"Total": 1, "Held": 1, "OldestAcquired": acquired}]
    # SUCCEEDED, not ACTIVE: with an active workflow the probe short-circuits and
    # the age arithmetic never runs, which is the branch this test is named for.
    probe = probe_ingestion_lock(_ctx(FakeSpanner(rows), _executions("SUCCEEDED")), now=NOW)
    assert probe.status == HEALTHY
    assert probe.data["age_minutes"] == 20
    assert probe.detail == "the ingestion lock is held"


def test_an_old_lock_without_an_active_workflow_degrades():
    acquired = (NOW - timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    rows = [{"Total": 1, "Held": 1, "OldestAcquired": acquired}]
    probe = probe_ingestion_lock(_ctx(FakeSpanner(rows), _executions("SUCCEEDED")), now=NOW)
    assert probe.status == DEGRADED
    assert probe.data["age_minutes"] == 300
    assert "300 minutes" in probe.detail


def test_an_old_lock_with_an_active_workflow_stays_healthy():
    acquired = (NOW - timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    rows = [{"Total": 1, "Held": 1, "OldestAcquired": acquired}]
    probe = probe_ingestion_lock(_ctx(FakeSpanner(rows), _executions("ACTIVE")), now=NOW)
    assert probe.status == HEALTHY
    assert probe.data["workflow_active"] is True


def test_an_old_lock_does_not_claim_the_workflow_is_idle_when_it_could_not_ask():
    acquired = (NOW - timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    rows = [{"Total": 1, "Held": 1, "OldestAcquired": acquired}]
    session = FakeSession({"/executions": FakeResponse(status_code=503)})
    probe = probe_ingestion_lock(_ctx(FakeSpanner(rows), session), now=NOW)
    assert probe.status == DEGRADED
    assert "no workflow is active" not in probe.detail
    assert "workflow state unavailable" in probe.detail
    assert probe.data["workflow_state_known"] is False


_SUCCESS = "Status = 'SUCCESS'"


def _row(status="SUCCESS", created="2026-08-05T14:14:00Z", completed="2026-08-05T14:40:00Z"):
    return {
        "CreationTimestamp": created,
        "CompletionTimestamp": completed,
        "Status": status,
        "Stage": "DONE",
        "IngestionFailure": status != "SUCCESS",
        "ExecutionTime": 1560,
        "Imports": 1,
        "WorkflowExecutionID": "exec-1",
    }


def _success(completed, node=100, edge=200, observation=300, timeseries=40):
    return {
        "CompletionTimestamp": completed,
        "NodeCount": node,
        "EdgeCount": edge,
        "ObservationCount": observation,
        "TimeSeriesCount": timeseries,
    }


def test_count_history_lists_successful_ingestions_oldest_first():
    successes = [  # the query returns newest first
        _success("2026-08-05T14:40:00Z", node=120),
        _success("2026-08-01T10:00:00Z", node=110),
    ]
    spanner = FakeSpanner([_row()], routes={_SUCCESS: successes})
    probe = probe_ingestions(_ctx(spanner, _executions("SUCCEEDED")), now=NOW)
    assert probe.data["count_history"] == [
        {
            "completed_at": "2026-08-01T10:00:00Z",
            "Node": 110,
            "Edge": 200,
            "Observation": 300,
            "TimeSeries": 40,
        },
        {
            "completed_at": "2026-08-05T14:40:00Z",
            "Node": 120,
            "Edge": 200,
            "Observation": 300,
            "TimeSeries": 40,
        },
    ]
    success_sql = next(sql for sql in spanner.queries if _SUCCESS in sql)
    for column in ("NodeCount", "EdgeCount", "ObservationCount", "TimeSeriesCount"):
        assert column in success_sql
    assert "LIMIT 10" in success_sql


def test_freshness_reports_the_last_success_and_its_age():
    spanner = FakeSpanner([_row()], routes={_SUCCESS: [_success("2026-08-05T12:00:00Z")]})
    probe = probe_ingestions(_ctx(spanner, _executions("SUCCEEDED")), now=NOW)
    assert probe.data["last_success_at"] == "2026-08-05T12:00:00Z"
    assert probe.data["age_hours"] == 6.0
    # No max age configured: shown, not judged.
    assert probe.status == HEALTHY


def test_the_last_success_is_found_even_behind_ten_failures():
    # Read by its own query, so a run of failures cannot push the last success
    # out of view and blank the freshness figure exactly when it matters.
    failures = [_row(status="FAILURE") for _ in range(10)]
    spanner = FakeSpanner(failures, routes={_SUCCESS: [_success("2026-07-01T00:00:00Z")]})
    probe = probe_ingestions(_ctx(spanner, _executions("FAILED")), now=NOW)
    assert probe.data["last_success_at"] == "2026-07-01T00:00:00Z"


def test_an_ingestion_older_than_the_max_age_degrades():
    spanner = FakeSpanner([_row()], routes={_SUCCESS: [_success("2026-08-03T18:00:00Z")]})
    ctx = _ctx(spanner, _executions("SUCCEEDED"))
    ctx.targets = Targets(ingestion_max_age_hours=36)
    probe = probe_ingestions(ctx, now=NOW)
    assert probe.status == DEGRADED
    assert "48.0 h" in probe.detail
    assert "36 h" in probe.detail


def test_an_ingestion_within_the_max_age_is_healthy():
    spanner = FakeSpanner([_row()], routes={_SUCCESS: [_success("2026-08-05T12:00:00Z")]})
    ctx = _ctx(spanner, _executions("SUCCEEDED"))
    ctx.targets = Targets(ingestion_max_age_hours=36)
    assert probe_ingestions(ctx, now=NOW).status == HEALTHY


def test_no_success_on_record_degrades_when_a_max_age_is_set():
    spanner = FakeSpanner([_row(status="FAILURE")], routes={_SUCCESS: []})
    ctx = _ctx(spanner, _executions("FAILED"))
    ctx.targets = Targets(ingestion_max_age_hours=36)
    probe = probe_ingestions(ctx, now=NOW)
    assert probe.status == DEGRADED
    assert probe.data["last_success_at"] is None
    assert probe.data["age_hours"] is None
    assert "no successful ingestion" in probe.detail


def test_a_failure_status_spelled_failure_degrades():
    spanner = FakeSpanner([_row(status="FAILURE")], routes={_SUCCESS: []})
    probe = probe_ingestions(_ctx(spanner, _executions("FAILED")), now=NOW)
    assert probe.status == DEGRADED


def test_a_free_lock_is_reported_as_not_held():
    rows = [{"Total": 1, "Held": 0, "OldestAcquired": None}]
    probe = probe_ingestion_lock(_ctx(FakeSpanner(rows), _executions("SUCCEEDED")), now=NOW)
    assert probe.data["lock"] == {"held": False, "owner": None, "since": None}


def test_a_held_lock_reports_its_owner_and_since_when():
    acquired = "2026-08-05T17:40:00Z"
    spanner = FakeSpanner(
        [{"Total": 1, "Held": 1, "OldestAcquired": acquired}],
        routes={"SELECT LockOwner": [{"LockOwner": "exec-42"}]},
    )
    probe = probe_ingestion_lock(_ctx(spanner, _executions("SUCCEEDED")), now=NOW)
    assert probe.data["lock"] == {"held": True, "owner": "exec-42", "since": acquired}
    owner_sql = next(sql for sql in spanner.queries if "SELECT LockOwner" in sql)
    assert "LockOwner IS NOT NULL" in owner_sql
