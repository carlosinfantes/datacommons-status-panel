from datetime import datetime, timezone

from dc_status.assemble import ProbeSpec, collect_all, collect_self
from dc_status.cache import TTLCache
from dc_status.config import EnvConfig, PeerConfig
from dc_status.model import DEGRADED, DOWN, HEALTHY, UNKNOWN, Probe

NOW = datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)


def _config(peers=()):
    return EnvConfig(
        env_id="prod",
        env_label="Production",
        project_id="p",
        region="us-central1",
        spanner_instance_id="inst",
        spanner_database_id="db",
        datacommons_service_name="dc",
        ingestion_workflow_name="wf",
        preprocessing_job_name="job",
        artifacts_bucket_name="bucket",
        public_endpoint_url="https://api.example",
        frontend_url="https://www.example",
        data_source_prefixes=("agency-a",),
        input_prefix="ingestion/input/",
        peers=peers,
        counts_cache_ttl_seconds=300,
        schema_cache_ttl_seconds=3600,
    )


def _spec(probe_id, status, data=None, ttl=0, boom=False):
    def run(_ctx):
        if boom:
            raise RuntimeError("probe exploded with Bearer ya29.leaked")
        return Probe(id=probe_id, status=status, data=data or {})

    return ProbeSpec(id=probe_id, run=run, ttl_seconds=ttl)


def _clients():
    return type("Clients", (), {"rest": None, "public": None, "spanner_factory": None})()


def test_collects_every_probe_and_derives_the_overall_status():
    probes = (_spec("dc_api", HEALTHY), _spec("frontend", DEGRADED))
    document = collect_self(_config(), _clients(), TTLCache(), now=NOW, probes=probes)
    assert document["overall"] == DEGRADED
    assert document["generated_at"] == "2026-08-05T18:00:00+00:00"
    environment = document["environments"][0]
    assert environment["id"] == "prod"
    assert environment["label"] == "Production"
    assert environment["self"] is True
    assert environment["reachable"] is True
    assert {probe["id"] for probe in environment["probes"]} >= {"dc_api", "frontend"}


def test_a_probe_that_raises_becomes_unknown_with_a_sanitized_detail():
    probes = (_spec("dc_api", HEALTHY), _spec("counts", HEALTHY, boom=True))
    document = collect_self(_config(), _clients(), TTLCache(), now=NOW, probes=probes)
    counts = next(p for p in document["environments"][0]["probes"] if p["id"] == "counts")
    assert counts["status"] == UNKNOWN
    assert "ya29" not in counts["detail"]
    assert document["partial"] is True
    assert document["overall"] == DEGRADED


def test_version_consistency_is_derived_from_the_service_and_schema_probes():
    probes = (
        _spec("dc_service", HEALTHY, {"dcp_version": "1.1.1"}),
        _spec("schema", HEALTHY, {"tables": ["Node", "Edge"]}),
    )
    document = collect_self(_config(), _clients(), TTLCache(), now=NOW, probes=probes)
    derived = next(
        p for p in document["environments"][0]["probes"] if p["id"] == "version_consistency"
    )
    assert derived["status"] == DOWN  # 1.1 needs TimeSeries, KeyValueStore, Observation
    assert document["overall"] == DOWN


def test_lifts_counts_tables_ingestions_and_sources_to_the_top_level():
    probes = (
        _spec("dc_service", HEALTHY, {"dcp_version": "1.1.1"}),
        _spec("schema", HEALTHY, {"tables": ["Node"]}),
        _spec("counts", HEALTHY, {"counts": {"Node": 241}, "unavailable": []}),
        _spec("ingestions", HEALTHY, {"ingestions": [{"status": "SUCCESS"}]}),
        _spec("data_sources", HEALTHY, {"sources": [{"prefix": "agency-a"}], "unmatched_provenances": []}),
    )
    environment = collect_self(_config(), _clients(), TTLCache(), now=NOW, probes=probes)["environments"][0]
    assert environment["dcp_version"] == "1.1.1"
    assert environment["schema_tables"] == ["Node"]
    assert environment["counts"] == {"Node": 241}
    assert environment["ingestions"] == [{"status": "SUCCESS"}]
    assert environment["data_sources"] == [{"prefix": "agency-a"}]


def test_cached_probes_are_not_rerun_within_their_ttl():
    calls = []

    def run(_ctx):
        calls.append(1)
        return Probe(id="counts", status=HEALTHY, data={"counts": {}, "unavailable": []})

    probes = (ProbeSpec(id="counts", run=run, ttl_seconds=300),)
    cache = TTLCache()
    collect_self(_config(), _clients(), cache, now=NOW, probes=probes)
    collect_self(_config(), _clients(), cache, now=NOW, probes=probes)
    assert len(calls) == 1


def test_collect_all_appends_the_peer_environment():
    peer = PeerConfig(id="staging", label="Staging", url="https://staging.example")
    probes = (_spec("dc_api", HEALTHY),)

    def fetch_peer(peer_config):
        return {
            "environments": [
                {"id": peer_config.id, "label": peer_config.label, "overall": HEALTHY, "probes": []}
            ]
        }

    document = collect_all(
        _config(peers=(peer,)), _clients(), TTLCache(), fetch_peer, now=NOW, probes=probes
    )
    assert [env["id"] for env in document["environments"]] == ["prod", "staging"]
    assert document["environments"][1]["self"] is False


def test_an_unreachable_peer_becomes_an_unknown_card_without_touching_the_local_one():
    peer = PeerConfig(id="staging", label="Staging", url="https://staging.example")
    probes = (_spec("dc_api", HEALTHY),)

    def fetch_peer(_peer_config):
        raise RuntimeError("connection refused")

    document = collect_all(
        _config(peers=(peer,)), _clients(), TTLCache(), fetch_peer, now=NOW, probes=probes
    )
    local, remote = document["environments"]
    assert local["overall"] == HEALTHY
    assert remote["reachable"] is False
    assert remote["overall"] == UNKNOWN
    assert document["partial"] is True
