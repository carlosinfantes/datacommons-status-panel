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

import threading
import time
from datetime import UTC, datetime

from dc_status.assemble import PROBES, ProbeSpec, _budget_ms, collect_status
from dc_status.cache import TTLCache
from dc_status.config import EnvConfig
from dc_status.model import DEGRADED, DIMENSIONS, DOWN, HEALTHY, UNKNOWN, Probe
from dc_status.probes import COUNTS_BUDGET_SECONDS
from dc_status.rest import PUBLIC_TIMEOUT_SECONDS

NOW = datetime(2026, 8, 5, 18, 0, tzinfo=UTC)


def _config():
    return EnvConfig(
        env_id="prod",
        env_label="Production",
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
        counts_cache_ttl_seconds=300,
        schema_cache_ttl_seconds=3600,
    )


def _spec(probe_id, status, data=None, ttl=0, boom=False, detail=""):
    def run(_ctx):
        if boom:
            raise RuntimeError("probe exploded with Bearer ya29.leaked")
        return Probe(id=probe_id, status=status, detail=detail, data=data or {})

    return ProbeSpec(id=probe_id, run=run, ttl_seconds=ttl)


def _clients():
    return type(
        "Clients",
        (),
        {"rest": None, "public": None, "spanner_factory": None, "monitoring": None},
    )()


def _collect(probes, **kwargs):
    return collect_status(_config(), _clients(), TTLCache(), now=NOW, probes=probes, **kwargs)


def _probe(document, probe_id):
    return next(p for p in document["probes"] if p["id"] == probe_id)


def test_the_document_is_schema_version_2_with_the_deployment_and_its_targets():
    document = _collect((_spec("dc_api", HEALTHY),))
    assert document["schema_version"] == 2
    assert document["generated_at"] == "2026-08-05T18:00:00Z"
    assert document["deployment"] == {
        "id": "prod",
        "label": "Production",
        "dcp_version": None,
        "project_id": "p",
        "region": "us-central1",
    }
    assert document["targets"] == _config().targets.to_dict()


def test_dimensions_come_in_a_fixed_order_and_list_their_probes():
    document = _collect((_spec("dc_api", HEALTHY),))
    assert [d["id"] for d in document["dimensions"]] == [
        "system",
        "experience",
        "quality",
        "freshness",
    ]
    assert document["dimensions"][0]["probes"] == [
        "dc_api",
        "dc_service",
        "spanner",
        "schema",
        "version_consistency",
        "frontend",
    ]


def test_every_probe_belongs_to_exactly_one_dimension():
    listed = [probe_id for _dimension, ids in DIMENSIONS for probe_id in ids]
    assert len(listed) == len(set(listed))
    assert {spec.id for spec in PROBES} - {"signals"} <= set(listed)


def test_a_dimension_is_the_worst_of_its_probes_and_overall_the_worst_dimension():
    probes = (
        _spec("dc_api", HEALTHY),
        _spec("frontend", DEGRADED),
        _spec("counts", HEALTHY),
        _spec("ingestion_lock", DOWN),
    )
    document = _collect(probes)
    by_id = {d["id"]: d["status"] for d in document["dimensions"]}
    assert by_id["system"] == DEGRADED
    assert by_id["quality"] == HEALTHY
    assert by_id["freshness"] == DOWN
    assert document["overall"] == DOWN


def test_a_dimension_where_nothing_could_be_read_is_unknown_not_degraded():
    # Nothing known is not the same finding as something wrong, and the tile
    # must be able to tell the two apart.
    document = _collect((_spec("dc_api", HEALTHY), _spec("counts", HEALTHY, boom=True)))
    quality = next(d for d in document["dimensions"] if d["id"] == "quality")
    assert quality["status"] == UNKNOWN
    # ...while the overall verdict still ranks not knowing with degraded.
    assert document["overall"] == DEGRADED


def test_probes_are_emitted_in_dimension_order_with_their_dimension():
    probes = (_spec("counts", HEALTHY), _spec("frontend", HEALTHY), _spec("dc_api", HEALTHY))
    document = _collect(probes)
    assert [p["id"] for p in document["probes"]] == ["dc_api", "frontend", "counts"]
    assert [p["dimension"] for p in document["probes"]] == ["system", "system", "quality"]


def test_every_probe_carries_a_console_link_or_null():
    probes = (_spec("dc_service", HEALTHY), _spec("frontend", HEALTHY))
    document = _collect(probes)
    assert _probe(document, "dc_service")["console_url"] == (
        "https://console.cloud.google.com/run/detail/us-central1/dc/revisions?project=p"
    )
    # There is no Cloud Console page for a public URL.
    assert _probe(document, "frontend")["console_url"] is None


def test_a_probe_that_raises_still_links_to_its_console_page():
    document = _collect((_spec("spanner", HEALTHY, boom=True),))
    spanner = _probe(document, "spanner")
    assert spanner["status"] == UNKNOWN
    assert spanner["console_url"].startswith(
        "https://console.cloud.google.com/spanner/instances/inst"
    )


def test_a_probe_that_raises_becomes_unknown_without_escaping():
    document = _collect((_spec("dc_api", HEALTHY), _spec("counts", HEALTHY, boom=True)))
    counts = _probe(document, "counts")
    assert counts["status"] == UNKNOWN
    assert document["partial"] is True
    assert document["overall"] == DEGRADED
    assert "ya29" not in counts["detail"]


def test_a_complete_collection_is_not_partial():
    assert _collect((_spec("dc_api", HEALTHY),))["partial"] is False


def test_version_consistency_is_derived_from_the_service_and_schema_probes():
    probes = (
        _spec("dc_service", HEALTHY, {"dcp_version": "1.1.1"}),
        _spec("schema", HEALTHY, {"tables": ["Node", "Edge"]}),
    )
    document = _collect(probes)
    assert _probe(document, "version_consistency")["status"] == DOWN
    assert document["overall"] == DOWN
    assert document["deployment"]["dcp_version"] == "1.1.1"


def test_lifts_counts_ingestions_and_sources_to_the_top_level():
    probes = (
        _spec("counts", HEALTHY, {"counts": {"Node": 241}, "unavailable": []}),
        _spec("ingestions", HEALTHY, {"ingestions": [{"status": "SUCCESS"}]}),
        _spec(
            "data_sources",
            HEALTHY,
            {
                "sources": [{"prefix": "agency-a"}],
                "unmatched_provenances": [{"provenance": "X", "rows": 1}],
            },
        ),
    )
    document = _collect(probes)
    assert document["counts"] == {"Node": 241}
    assert document["ingestions"] == [{"status": "SUCCESS"}]
    assert document["data_sources"] == [{"prefix": "agency-a"}]
    assert document["unmatched_provenances"] == [{"provenance": "X", "rows": 1}]


def test_what_could_not_be_read_is_emitted_empty_rather_than_missing():
    # The page reads these keys unconditionally; a failed probe must not remove
    # them from the document.
    document = _collect((_spec("dc_api", HEALTHY),))
    assert document["counts"] == {}
    assert document["count_history"] == []
    assert document["ingestions"] == []
    assert document["data_sources"] == []
    assert document["unmatched_provenances"] == []
    assert document["imports"] is None
    assert document["signals"] is None
    assert document["freshness"] == {
        "last_success_at": None,
        "age_hours": None,
        "pending_uploads": [],
        "lock": {"held": None, "owner": None, "since": None},
    }


def test_cached_probes_are_not_rerun_within_their_ttl():
    calls = []

    def run(_ctx):
        calls.append(1)
        return Probe(id="counts", status=HEALTHY, data={"counts": {}, "unavailable": []})

    probes = (ProbeSpec(id="counts", run=run, ttl_seconds=300),)
    cache = TTLCache()
    collect_status(_config(), _clients(), cache, now=NOW, probes=probes)
    collect_status(_config(), _clients(), cache, now=NOW, probes=probes)
    assert len(calls) == 1


def test_every_probe_reports_the_budget_it_was_measured_against():
    document = _collect((_spec("dc_api", HEALTHY), _spec("spanner", HEALTHY)))
    assert [probe["budget_ms"] for probe in document["probes"]] == [25000, 25000]


def test_a_probe_reports_whichever_deadline_actually_binds_it():
    by_id = {spec.id: spec for spec in PROBES}
    assert _budget_ms(by_id["dc_api"]) == int(PUBLIC_TIMEOUT_SECONDS * 1000)
    assert _budget_ms(by_id["frontend"]) == int(PUBLIC_TIMEOUT_SECONDS * 1000)
    assert _budget_ms(by_id["counts"]) == int(COUNTS_BUDGET_SECONDS * 1000)
    assert _budget_ms(by_id["spanner"]) == 25000
    assert _budget_ms(by_id["schema"]) == 25000


def test_a_derived_check_reports_no_budget():
    probes = (
        _spec("dc_service", HEALTHY, data={"dcp_version": "1.1.0"}),
        _spec("schema", HEALTHY, data={"tables": ["TimeSeries"]}),
    )
    assert _probe(_collect(probes), "version_consistency")["budget_ms"] == 0


def test_a_probe_that_outlasts_the_deadline_reports_the_whole_budget_spent(monkeypatch):
    monkeypatch.setattr("dc_status.assemble._PROBE_DEADLINE_SECONDS", 0.2)
    release = threading.Event()

    def run(_ctx):
        release.wait(timeout=5)
        return Probe(id="dc_api", status=HEALTHY)

    try:
        document = _collect((ProbeSpec(id="dc_api", run=run),))
    finally:
        release.set()

    probe = _probe(document, "dc_api")
    assert probe["status"] == UNKNOWN
    assert probe["budget_ms"] == 200
    assert probe["elapsed_ms"] == 200
    assert "did not answer" in probe["detail"]
    assert document["partial"] is True


def test_the_deadline_is_shared_by_the_whole_collection_not_granted_per_probe(monkeypatch):
    # Granting each future its own full timeout would let three stuck probes
    # hold the page for three deadlines. One clock for the whole collection.
    monkeypatch.setattr("dc_status.assemble._PROBE_DEADLINE_SECONDS", 0.2)
    release = threading.Event()

    def stuck(probe_id):
        def run(_ctx):
            release.wait(timeout=5)
            return Probe(id=probe_id, status=HEALTHY)

        return ProbeSpec(id=probe_id, run=run)

    started = time.monotonic()
    try:
        document = _collect((stuck("dc_api"), stuck("spanner"), stuck("counts")))
    finally:
        release.set()
    assert time.monotonic() - started < 0.4
    assert all(p["status"] == UNKNOWN for p in document["probes"])


def _history_data():
    return {
        "ingestions": [{"status": "SUCCESS"}],
        "count_history": [
            {
                "completed_at": "2026-08-01T10:00:00Z",
                "Node": 10,
                "Edge": 10,
                "Observation": 10,
                "TimeSeries": 10,
            },
            {
                "completed_at": "2026-08-04T10:00:00Z",
                "Node": 5,
                "Edge": 10,
                "Observation": 10,
                "TimeSeries": 10,
            },
        ],
        "last_success_at": "2026-08-04T10:00:00Z",
        "age_hours": 32.0,
    }


def test_row_drift_and_pending_uploads_are_derived_and_lifted():
    probes = (
        _spec("ingestions", HEALTHY, _history_data()),
        _spec(
            "data_sources",
            HEALTHY,
            {
                "sources": [{"prefix": "agency-a", "last_updated": "2026-08-05T09:00:00Z"}],
                "unmatched_provenances": [],
            },
        ),
        _spec(
            "ingestion_lock",
            HEALTHY,
            {"lock": {"held": False, "owner": None, "since": None}},
        ),
        _spec("import_status", HEALTHY, {"imports": {"total": 1}}),
    )
    document = _collect(probes)
    drift = _probe(document, "row_drift")
    assert drift["status"] == DEGRADED  # Node halved, above the default 10 %
    assert drift["budget_ms"] == 0
    pending = _probe(document, "pending_uploads")
    assert pending["status"] == DEGRADED
    assert pending["console_url"].startswith("https://console.cloud.google.com/storage/browser/")
    assert document["count_history"] == _history_data()["count_history"]
    assert document["imports"] == {"total": 1}
    assert document["freshness"] == {
        "last_success_at": "2026-08-04T10:00:00Z",
        "age_hours": 32.0,
        "pending_uploads": [{"prefix": "agency-a", "last_updated": "2026-08-05T09:00:00Z"}],
        "lock": {"held": False, "owner": None, "since": None},
    }


def test_the_production_probe_set_includes_import_status_and_the_signals_fetch():
    ids = {spec.id for spec in PROBES}
    assert "import_status" in ids
    assert "signals" in ids


def _signals_spec(raw=None, boom=None, calls=None):
    def run(_ctx):
        if calls is not None:
            calls.append(1)
        if boom:
            raise boom
        return Probe(id="signals", status=HEALTHY, data={"raw": raw})

    return ProbeSpec(id="signals", run=run)


def _raw(requests_per_minute=2400, spanner=0.4):
    return {
        "window_minutes": 60,
        "totals": [float(requests_per_minute)] * 60,
        "server_errors": [0.0] * 60,
        "p95_series": [600.0] * 60,
        "p50": 180.0,
        "p95": 600.0,
        "p99": 1200.0,
        "run_cpu": 0.3,
        "run_memory": 0.5,
        "instances": 2.0,
        "spanner_cpu": spanner,
    }


def test_monitoring_is_read_once_and_feeds_all_three_experience_probes():
    calls = []
    probes = (
        _spec("dc_service", HEALTHY, {"dcp_version": "1.1.4", "max_instances": 6}),
        _signals_spec(_raw(spanner=0.71), calls=calls),
    )
    document = _collect(probes)
    assert len(calls) == 1
    assert [p["id"] for p in document["probes"] if p["dimension"] == "experience"] == [
        "errors",
        "latency",
        "saturation",
    ]
    assert document["signals"]["saturation"]["max_instances"] == 6
    assert document["signals"]["traffic"]["requests"] == 2400 * 60
    saturation = _probe(document, "saturation")
    assert saturation["status"] == DEGRADED
    assert "/spanner/instances/inst/details/monitoring" in saturation["console_url"]
    errors = _probe(document, "errors")
    assert errors["budget_ms"] == 25000
    assert errors["console_url"].endswith("/run/detail/us-central1/dc/metrics?project=p")
    # The internal fetch is not a probe of its own.
    assert "signals" not in {p["id"] for p in document["probes"]}


def test_unreadable_monitoring_makes_the_experience_probes_unknown_and_signals_null():
    probes = (_signals_spec(boom=RuntimeError("403 monitoring.timeSeries.list denied")),)
    document = _collect(probes)
    assert document["signals"] is None
    for probe_id in ("errors", "latency", "saturation"):
        probe = _probe(document, probe_id)
        assert probe["status"] == UNKNOWN
        assert "Cloud Monitoring could not be read" in probe["detail"]
        assert "denied" in probe["detail"]
    assert document["partial"] is True
    experience = next(d for d in document["dimensions"] if d["id"] == "experience")
    assert experience["status"] == UNKNOWN
