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
from datetime import UTC, datetime

from dc_status.assemble import PROBES, ProbeSpec, _budget_ms, collect_all, collect_self
from dc_status.cache import TTLCache
from dc_status.config import EnvConfig, PeerConfig
from dc_status.model import DEGRADED, DOWN, HEALTHY, UNKNOWN, Probe
from dc_status.probes import COUNTS_BUDGET_SECONDS
from dc_status.rest import PUBLIC_TIMEOUT_SECONDS

NOW = datetime(2026, 8, 5, 18, 0, tzinfo=UTC)


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


def test_a_probe_that_raises_becomes_unknown_without_escaping():
    probes = (_spec("dc_api", HEALTHY), _spec("counts", HEALTHY, boom=True))
    document = collect_self(_config(), _clients(), TTLCache(), now=NOW, probes=probes)
    counts = next(p for p in document["environments"][0]["probes"] if p["id"] == "counts")
    assert counts["status"] == UNKNOWN
    assert document["partial"] is True
    assert document["overall"] == DEGRADED
    # The redaction here is Probe.to_dict()'s work, not assemble's: this asserts
    # the emitted document is clean, not that _run_one sanitised anything.
    assert "ya29" not in counts["detail"]


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
        _spec(
            "data_sources",
            HEALTHY,
            {"sources": [{"prefix": "agency-a"}], "unmatched_provenances": []},
        ),
    )
    environment = collect_self(_config(), _clients(), TTLCache(), now=NOW, probes=probes)[
        "environments"
    ][0]
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


def test_a_peer_that_answers_without_an_overall_does_not_blank_the_page():
    # A peer that is itself degraded returns 200 with environments: [] — valid by
    # this system's own contract. The local card must survive it.
    peer = PeerConfig(id="staging", label="Staging", url="https://staging.example")
    probes = (_spec("dc_api", HEALTHY),)

    document = collect_all(
        _config(peers=(peer,)),
        _clients(),
        TTLCache(),
        lambda _peer: {"overall": "unknown", "partial": True, "environments": []},
        now=NOW,
        probes=probes,
    )
    local, remote = document["environments"]
    assert local["overall"] == HEALTHY
    assert local["probes"]
    assert remote["overall"] == UNKNOWN


def test_every_probe_reports_the_budget_it_was_measured_against():
    # elapsed_ms alone cannot say whether a check is comfortable or one second from
    # being dropped, so the deadline travels with it.
    probes = (_spec("dc_api", HEALTHY), _spec("spanner", HEALTHY))
    document = collect_self(_config(), _clients(), TTLCache(), now=NOW, probes=probes)
    emitted = document["environments"][0]["probes"]
    assert [probe["budget_ms"] for probe in emitted] == [25000, 25000]


def test_a_probe_reports_whichever_deadline_actually_binds_it():
    # dc_api and frontend give up at the public client's 8 s and counts at its own
    # 20 s, all tighter than the pool's 25 s. Reporting 25 s for those would draw a
    # check one second from timing out as comfortably inside its budget.
    by_id = {spec.id: spec for spec in PROBES}
    assert _budget_ms(by_id["dc_api"]) == int(PUBLIC_TIMEOUT_SECONDS * 1000)
    assert _budget_ms(by_id["frontend"]) == int(PUBLIC_TIMEOUT_SECONDS * 1000)
    assert _budget_ms(by_id["counts"]) == int(COUNTS_BUDGET_SECONDS * 1000)
    # The rest retry up to 30 s of HTTP, so the pool's deadline is what binds them.
    assert _budget_ms(by_id["spanner"]) == 25000
    assert _budget_ms(by_id["schema"]) == 25000


def test_a_derived_check_reports_no_budget():
    # version_consistency does no I/O and is never raced against the clock, so it
    # has no budget to report and the page must not draw it one.
    probes = (
        _spec("dc_service", HEALTHY, data={"dcp_version": "1.1.0"}),
        _spec("schema", HEALTHY, data={"tables": ["TimeSeries"]}),
    )
    document = collect_self(_config(), _clients(), TTLCache(), now=NOW, probes=probes)
    derived = next(
        probe
        for probe in document["environments"][0]["probes"]
        if probe["id"] == "version_consistency"
    )
    assert derived["budget_ms"] == 0


def test_a_probe_that_outlasts_its_deadline_reports_the_whole_budget_spent(monkeypatch):
    # A dropped probe is the slowest possible probe. Leaving elapsed at zero would
    # render it as the fastest one on the page.
    monkeypatch.setattr("dc_status.assemble._PROBE_DEADLINE_SECONDS", 0.2)
    release = threading.Event()

    def run(_ctx):
        release.wait(timeout=5)
        return Probe(id="dc_api", status=HEALTHY)

    try:
        document = collect_self(
            _config(), _clients(), TTLCache(), now=NOW, probes=(ProbeSpec(id="dc_api", run=run),)
        )
    finally:
        release.set()  # let the straggler finish before the test process exits

    probe = document["environments"][0]["probes"][0]
    assert probe["status"] == UNKNOWN
    assert probe["budget_ms"] == 200
    assert probe["elapsed_ms"] == 200
    assert "did not answer" in probe["detail"]
    assert document["partial"] is True


def test_a_peer_that_outlasts_the_budget_does_not_hold_up_the_probe():
    # Same shape as probe_counts's budget test: the fake blocks until the test
    # releases it, exercising the real future.result(timeout=...) path and the
    # shutdown(wait=False) that keeps a straggler from stalling the page.
    peer = PeerConfig(id="staging", label="Staging", url="https://staging.example")
    probes = (_spec("dc_api", HEALTHY),)
    release = threading.Event()

    def fetch_peer(_peer_config):
        release.wait(timeout=5)
        return {"environments": [{"overall": HEALTHY}]}

    try:
        document = collect_all(
            _config(peers=(peer,)),
            _clients(),
            TTLCache(),
            fetch_peer,
            now=NOW,
            probes=probes,
            peer_deadline_seconds=0.2,
        )
    finally:
        release.set()  # let the straggler finish before the test process exits

    local, remote = document["environments"]
    assert local["overall"] == HEALTHY
    assert remote["reachable"] is False
    assert remote["overall"] == UNKNOWN
    assert document["partial"] is True


def test_an_unreachable_peers_detail_is_sanitized():
    # The one place sanitisation is genuinely load-bearing in this module. A peer
    # failure becomes a plain dict that goes straight into the document — it never
    # passes through Probe.to_dict(), so nothing else would redact it.
    peer = PeerConfig(id="staging", label="Staging", url="https://staging.example")
    probes = (_spec("dc_api", HEALTHY),)

    def fetch_peer(_peer_config):
        raise RuntimeError("refused by proxy, token was Bearer ya29.leaked")

    document = collect_all(
        _config(peers=(peer,)), _clients(), TTLCache(), fetch_peer, now=NOW, probes=probes
    )
    remote = document["environments"][1]
    assert "ya29" not in remote["detail"]
    assert "[REDACTED]" in remote["detail"]
