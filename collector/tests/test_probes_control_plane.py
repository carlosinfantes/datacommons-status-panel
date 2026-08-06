from dc_status.model import DOWN, HEALTHY, ProbeContext
from dc_status.probes import probe_dc_service, probe_spanner
from dc_status.rest import RestClient
from tests.conftest import FakeResponse, FakeSession

DIGEST = "sha256:" + "4" * 64
REVISION = "projects/p/locations/us-central1/services/dc/revisions/dc-00042-abc"


# Route fragments are end-anchored with "$" because these URLs nest: the revision
# URL contains the service URL, and the database URL contains the instance URL.
# A plain substring route would match both, which the fixture guard rejects.
def _context(session):
    return ProbeContext(
        env_id="staging",
        project_id="p",
        region="us-central1",
        spanner_instance_id="inst",
        spanner_database_id="db",
        datacommons_service_name="dc",
        ingestion_workflow_name="wf",
        artifacts_bucket_name="bucket",
        public_endpoint_url="https://api.example",
        frontend_url="https://www.example",
        data_source_prefixes=("agency-a",),
        input_prefix="ingestion/input/",
        rest=RestClient(session, retries=0),
        public=None,
        spanner_factory=None,
    )


def test_dc_service_is_healthy_and_reports_the_live_version():
    session = FakeSession(
        {
            "/services/dc$": FakeResponse(
                payload={
                    "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
                    "latestReadyRevision": REVISION,
                }
            ),
            "/revisions/dc-00042-abc$": FakeResponse(
                payload={
                    "containers": [
                        {"image": "gcr.io/x/mcp-sidecar:0.9.0"},
                        {"image": f"gcr.io/x/datacommons-services:1.1.1@{DIGEST}",
                         "ports": [{"containerPort": 8080}]},
                    ]
                }
            ),
        }
    )
    probe = probe_dc_service(_context(session))
    assert probe.status == HEALTHY
    assert probe.data["dcp_version"] == "1.1.1"
    assert probe.data["latest_ready_revision"] == "dc-00042-abc"


def test_dc_service_falls_back_to_the_only_container():
    session = FakeSession(
        {
            "/services/dc$": FakeResponse(
                payload={"terminalCondition": {"state": "CONDITION_SUCCEEDED"},
                         "latestReadyRevision": REVISION}
            ),
            "/revisions/dc-00042-abc$": FakeResponse(
                payload={"containers": [{"image": "gcr.io/x/datacommons-services:1.1.1"}]}
            ),
        }
    )
    assert probe_dc_service(_context(session)).data["dcp_version"] == "1.1.1"


def test_dc_service_is_down_when_the_terminal_condition_failed():
    session = FakeSession(
        {
            "/services/dc$": FakeResponse(
                payload={
                    "terminalCondition": {"state": "CONDITION_FAILED", "message": "revision failed"},
                    "latestReadyRevision": "",
                }
            )
        }
    )
    probe = probe_dc_service(_context(session))
    assert probe.status == DOWN
    assert "revision failed" in probe.detail


def test_dc_service_survives_a_broken_revision_fetch_by_using_the_template():
    session = FakeSession(
        {
            "/services/dc$": FakeResponse(
                payload={
                    "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
                    "latestReadyRevision": REVISION,
                    "template": {
                        "containers": [
                            {"image": f"gcr.io/x/datacommons-services:1.1.1@{DIGEST}",
                             "ports": [{"containerPort": 8080}]}
                        ]
                    },
                }
            ),
            "/revisions/dc-00042-abc$": FakeResponse(status_code=503),
        }
    )
    probe = probe_dc_service(_context(session))
    assert probe.status == HEALTHY
    assert probe.data["dcp_version"] == "1.1.1"


def test_spanner_is_healthy_when_both_states_are_ready():
    session = FakeSession(
        {
            "/databases/db$": FakeResponse(
                payload={"state": "READY", "versionRetentionPeriod": "24h",
                         "earliestVersionTime": "2026-08-04T13:00:00Z"}
            ),
            "/instances/inst$": FakeResponse(payload={"state": "READY", "processingUnits": 1000}),
        }
    )
    probe = probe_spanner(_context(session))
    assert probe.status == HEALTHY
    assert probe.data["processing_units"] == 1000
    assert probe.data["version_retention_period"] == "24h"


def test_spanner_is_down_when_the_database_is_not_ready():
    session = FakeSession(
        {
            "/databases/db$": FakeResponse(payload={"state": "CREATING"}),
            "/instances/inst$": FakeResponse(payload={"state": "READY", "processingUnits": 1000}),
        }
    )
    probe = probe_spanner(_context(session))
    assert probe.status == DOWN
    assert "CREATING" in probe.detail
