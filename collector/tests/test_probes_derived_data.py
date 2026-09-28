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

"""import_status, and the two checks derived from other probes' data."""

from dc_status.model import DEGRADED, HEALTHY, UNKNOWN, Probe
from dc_status.probes import probe_import_status, probe_pending_uploads, probe_row_drift


class FakeSpanner:
    def __init__(self, tables, imports):
        self._tables = tables
        self._imports = imports
        self.queries = []

    def query(self, sql, **kwargs):
        self.queries.append(sql)
        if "information_schema" in sql:
            return [{"table_name": name} for name in self._tables]
        return self._imports

    def close(self):
        pass


class _Ctx:
    def __init__(self, spanner):
        self.spanner_factory = lambda: spanner


_TABLES = ["ImportStatus", "Node", "TimeSeries"]


def _imports(*pairs):
    return [{"ImportName": name, "State": state} for name, state in pairs]


def test_import_status_is_not_available_on_a_version_without_the_table():
    spanner = FakeSpanner(["Node", "TimeSeries"], [])
    probe = probe_import_status(_Ctx(spanner))
    assert probe.status == HEALTHY
    assert probe.detail == "not available on this version"
    assert probe.data["imports"] is None
    # The absent table is never queried: no SQL error to misread as an outage.
    assert not any("FROM ImportStatus" in sql for sql in spanner.queries)


def test_import_status_counts_successful_imports():
    spanner = FakeSpanner(_TABLES, _imports(("a", "SUCCESS"), ("b", "SUCCESS")))
    probe = probe_import_status(_Ctx(spanner))
    assert probe.status == HEALTHY
    assert probe.data["imports"] == {"total": 2, "succeeded": 2, "in_progress": 0, "failed": []}
    assert "SELECT ImportName, State FROM ImportStatus" in spanner.queries[-1]


def test_failed_and_retrying_imports_degrade_and_are_named():
    rows = _imports(("a", "SUCCESS"), ("b", "FAILURE"), ("c", "RETRY"))
    probe = probe_import_status(_Ctx(FakeSpanner(_TABLES, rows)))
    assert probe.status == DEGRADED
    assert probe.data["imports"]["failed"] == ["b", "c"]
    assert "b (FAILURE)" in probe.detail
    assert "c (RETRY)" in probe.detail


def test_pending_running_and_staging_imports_count_as_in_progress():
    rows = _imports(("a", "PENDING"), ("b", "RUNNING"), ("c", "STAGING"), ("d", "SUCCESS"))
    probe = probe_import_status(_Ctx(FakeSpanner(_TABLES, rows)))
    assert probe.status == HEALTHY
    assert probe.data["imports"]["in_progress"] == 3
    assert probe.detail == "3 imports in progress"


def _ingestions(history, last_success_at="2026-09-26T13:00:00Z", status=HEALTHY):
    return Probe(
        id="ingestions",
        status=status,
        data={
            "ingestions": [],
            "count_history": history,
            "last_success_at": last_success_at,
            "age_hours": 1.0,
        },
    )


def _counts(completed_at, node=1000, edge=2000, observation=3000, timeseries=400):
    return {
        "completed_at": completed_at,
        "Node": node,
        "Edge": edge,
        "Observation": observation,
        "TimeSeries": timeseries,
    }


def test_row_drift_is_healthy_when_counts_grow_or_hold():
    history = [_counts("2026-09-19T13:00:00Z"), _counts("2026-09-26T13:00:00Z", node=1100)]
    assert probe_row_drift(_ingestions(history), max_drop_pct=10).status == HEALTHY


def test_a_drop_beyond_the_target_degrades_and_names_the_table():
    history = [
        _counts("2026-09-19T13:00:00Z", observation=3000),
        _counts("2026-09-26T13:00:00Z", observation=2400),
    ]
    probe = probe_row_drift(_ingestions(history), max_drop_pct=10)
    assert probe.status == DEGRADED
    assert "Observation" in probe.detail
    assert "20.0 %" in probe.detail
    assert probe.data["drops"] == {"Observation": 20.0}


def test_a_drop_within_the_target_is_healthy():
    history = [
        _counts("2026-09-19T13:00:00Z", edge=2000),
        _counts("2026-09-26T13:00:00Z", edge=1900),
    ]
    assert probe_row_drift(_ingestions(history), max_drop_pct=10).status == HEALTHY


def test_row_drift_needs_two_successful_ingestions():
    probe = probe_row_drift(_ingestions([_counts("2026-09-26T13:00:00Z")]), max_drop_pct=10)
    assert probe.status == HEALTHY
    assert "fewer than two" in probe.detail


def test_row_drift_is_not_judged_when_the_platform_did_not_record_counts():
    # Some platform versions leave these columns NULL. That is not a drop.
    blank = {"Node": None, "Edge": None, "Observation": None, "TimeSeries": None}
    history = [
        {"completed_at": "2026-09-19T13:00:00Z", **blank},
        {"completed_at": "2026-09-26T13:00:00Z", **blank},
    ]
    probe = probe_row_drift(_ingestions(history), max_drop_pct=10)
    assert probe.status == HEALTHY
    assert "not recorded" in probe.detail


def test_row_drift_is_unknown_when_the_history_could_not_be_read():
    failed = Probe(id="ingestions", status=UNKNOWN, detail="boom")
    assert probe_row_drift(failed, max_drop_pct=10).status == UNKNOWN


def _sources(*pairs):
    return Probe(
        id="data_sources",
        status=HEALTHY,
        data={
            "sources": [
                {"prefix": prefix, "files": 1, "bytes": 1, "last_updated": updated, "rows": 1}
                for prefix, updated in pairs
            ]
        },
    )


def test_sources_updated_after_the_last_success_are_pending():
    sources = _sources(
        ("health", "2026-09-27T17:00:00Z"),
        ("labour", "2026-09-19T13:00:00Z"),
        ("education", "2026-09-28T07:00:00Z"),
        ("trade", None),
    )
    probe = probe_pending_uploads(sources, _ingestions([]))
    assert probe.status == DEGRADED
    assert probe.data["pending"] == [
        {"prefix": "health", "last_updated": "2026-09-27T17:00:00Z"},
        {"prefix": "education", "last_updated": "2026-09-28T07:00:00Z"},
    ]
    assert probe.detail == (
        "2 sources have input newer than the last successful ingestion: health, education"
    )


def test_one_pending_source_reads_in_the_singular():
    probe = probe_pending_uploads(_sources(("health", "2026-09-27T17:00:00Z")), _ingestions([]))
    assert probe.detail.startswith("1 source has input newer")


def test_nothing_pending_is_healthy():
    probe = probe_pending_uploads(_sources(("labour", "2026-09-19T13:00:00Z")), _ingestions([]))
    assert probe.status == HEALTHY
    assert probe.data["pending"] == []


def test_every_source_with_input_is_pending_before_the_first_success():
    probe = probe_pending_uploads(
        _sources(("health", "2026-09-27T17:00:00Z"), ("trade", None)),
        _ingestions([], last_success_at=None),
    )
    assert probe.status == DEGRADED
    assert [p["prefix"] for p in probe.data["pending"]] == ["health"]


def test_pending_uploads_is_unknown_when_either_input_is_missing():
    failed_ingestions = Probe(id="ingestions", status=UNKNOWN, detail="boom")
    failed_sources = Probe(id="data_sources", status=UNKNOWN, detail="boom")
    assert probe_pending_uploads(_sources(), failed_ingestions).status == UNKNOWN
    assert probe_pending_uploads(failed_sources, _ingestions([])).status == UNKNOWN
