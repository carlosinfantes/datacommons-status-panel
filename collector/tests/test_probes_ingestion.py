from datetime import datetime, timedelta, timezone

from dc_status.model import DEGRADED, HEALTHY, UNKNOWN
from dc_status.probes import probe_ingestion_lock, probe_ingestions
from dc_status.rest import RestClient
from tests.conftest import FakeResponse, FakeSession

NOW = datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)


class FakeSpanner:
    def __init__(self, rows):
        self._rows = rows
        self.queries = []

    def query(self, sql, **kwargs):
        self.queries.append(sql)
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


def test_a_free_lock_row_with_null_owner_is_healthy():
    rows = [{"Total": 1, "Held": 0, "OldestAcquired": None}]
    probe = probe_ingestion_lock(_ctx(FakeSpanner(rows), _executions("SUCCEEDED")), now=NOW)
    assert probe.status == HEALTHY
    assert probe.data["held"] == 0


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
