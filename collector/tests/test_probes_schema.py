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

from dc_status.model import DOWN, HEALTHY, UNKNOWN, Probe
from dc_status.probes import probe_schema, probe_version_consistency

V111 = [
    "Edge",
    "ImportStatus",
    "ImportVersionHistory",
    "IngestionHistory",
    "IngestionLock",
    "KeyValueStore",
    "Node",
    "NodeEmbedding",
    "Observation",
    "TimeSeries",
]


class FakeSpanner:
    def __init__(self, rows, recorder=None):
        self._rows = rows
        self.queries = recorder if recorder is not None else []

    def query(self, sql, **kwargs):
        self.queries.append((sql, kwargs))
        return self._rows

    def close(self):
        pass


class _Ctx:
    def __init__(self, spanner):
        self.spanner_factory = lambda: spanner


def test_schema_lists_the_allowlisted_tables_present():
    spanner = FakeSpanner([{"table_name": name} for name in V111])
    probe = probe_schema(_Ctx(spanner))
    assert probe.status == HEALTHY
    assert probe.data["tables"] == sorted(V111)
    assert probe.data["unlisted_tables"] == []


def test_schema_reads_strongly_not_stale():
    spanner = FakeSpanner([{"table_name": "Node"}])
    probe_schema(_Ctx(spanner))
    assert spanner.queries[0][1]["staleness_seconds"] == 0


def test_schema_separates_tables_outside_the_allowlist():
    spanner = FakeSpanner([{"table_name": "Node"}, {"table_name": "SomethingNew"}])
    probe = probe_schema(_Ctx(spanner))
    assert probe.data["tables"] == ["Node"]
    assert probe.data["unlisted_tables"] == ["SomethingNew"]


def test_schema_is_down_when_no_known_table_exists():
    probe = probe_schema(_Ctx(FakeSpanner([])))
    assert probe.status == DOWN


def _service(version):
    return Probe(id="dc_service", status=HEALTHY, data={"dcp_version": version})


def _schema(tables):
    return Probe(id="schema", status=HEALTHY, data={"tables": tables})


def test_version_consistency_is_healthy_when_the_schema_matches():
    probe = probe_version_consistency(_service("1.1.1"), _schema(V111))
    assert probe.status == HEALTHY
    assert probe.data["missing_tables"] == []


def test_version_consistency_is_down_when_a_required_table_is_missing():
    without_timeseries = [t for t in V111 if t != "TimeSeries"]
    probe = probe_version_consistency(_service("1.1.1"), _schema(without_timeseries))
    assert probe.status == DOWN
    assert probe.data["missing_tables"] == ["TimeSeries"]
    assert "TimeSeries" in probe.detail


def test_version_consistency_is_unknown_for_an_unrecognised_version():
    probe = probe_version_consistency(_service(None), _schema(V111))
    assert probe.status == UNKNOWN


def test_version_consistency_is_unknown_when_the_schema_is_unknown():
    probe = probe_version_consistency(_service("1.1.1"), _schema([]))
    assert probe.status == UNKNOWN
