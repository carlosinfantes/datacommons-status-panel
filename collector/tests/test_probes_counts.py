import pytest

from dc_status.model import DEGRADED, HEALTHY, UNKNOWN
from dc_status.probes import probe_counts


class ScriptedSpanner:
    """Answers the schema query, then COUNT(*) per table. Shared query log."""

    def __init__(self, tables, counts, log, failing=()):
        self._tables = tables
        self._counts = counts
        self._failing = set(failing)
        self.log = log

    def query(self, sql, **kwargs):
        self.log.append(sql)
        if "information_schema" in sql:
            return [{"table_name": name} for name in self._tables]
        table = sql.rsplit(" ", 1)[-1]
        if table in self._failing:
            raise TimeoutError("deadline exceeded")
        return [{"Total": self._counts[table]}]

    def close(self):
        pass


def _ctx(tables, counts, failing=()):
    log = []
    ctx = type("Ctx", (), {})()
    ctx.spanner_factory = lambda: ScriptedSpanner(tables, counts, log, failing)
    ctx.query_log = log
    return ctx


def test_counts_every_present_table():
    ctx = _ctx(["Node", "Edge"], {"Node": 59407, "Edge": 456138})
    probe = probe_counts(ctx)
    assert probe.status == HEALTHY
    assert probe.data["counts"] == {"Edge": 456138, "Node": 59407}
    assert probe.data["unavailable"] == []


def test_one_statement_per_table():
    ctx = _ctx(["Node", "Edge"], {"Node": 1, "Edge": 2})
    probe_counts(ctx, workers=1)
    count_statements = [sql for sql in ctx.query_log if sql.startswith("SELECT COUNT(*)")]
    assert sorted(count_statements) == [
        "SELECT COUNT(*) AS Total FROM Edge",
        "SELECT COUNT(*) AS Total FROM Node",
    ]


def test_tables_outside_the_allowlist_never_reach_the_sql():
    ctx = _ctx(["Node", "Robert'); DROP TABLE Node;--"], {"Node": 1})
    probe = probe_counts(ctx, workers=1)
    assert probe.data["counts"] == {"Node": 1}
    assert all("DROP" not in sql for sql in ctx.query_log)


def test_a_failing_table_degrades_without_losing_the_others():
    ctx = _ctx(["Node", "Observation"], {"Node": 241}, failing=["Observation"])
    probe = probe_counts(ctx, workers=1)
    assert probe.status == DEGRADED
    assert probe.data["counts"]["Node"] == 241
    assert probe.data["counts"]["Observation"] is None
    assert probe.data["unavailable"] == ["Observation"]
    assert "Observation" in probe.detail


def test_unknown_when_the_schema_cannot_be_read():
    ctx = _ctx([], {})
    probe = probe_counts(ctx)
    assert probe.status == UNKNOWN


def test_uses_a_stale_read_for_data():
    class Recorder(ScriptedSpanner):
        kwargs_log: list = []

        def query(self, sql, **kwargs):
            Recorder.kwargs_log.append((sql, kwargs))
            return super().query(sql, **kwargs)

    log = []
    ctx = type("Ctx", (), {})()
    ctx.spanner_factory = lambda: Recorder(["Node"], {"Node": 1}, log)
    probe_counts(ctx, workers=1)
    count_calls = [kw for sql, kw in Recorder.kwargs_log if sql.startswith("SELECT COUNT(*)")]
    assert count_calls[0]["staleness_seconds"] == 10
