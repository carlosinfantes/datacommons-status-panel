from dc_status.model import DEGRADED, HEALTHY, UNKNOWN
from dc_status.probes import probe_data_sources
from dc_status.rest import RestClient
from tests.conftest import FakeResponse, FakeSession


class FakeSpanner:
    def __init__(self, rows):
        self._rows = rows

    def query(self, sql, **kwargs):
        return self._rows

    def close(self):
        pass


def _ctx(session, rows, prefixes=("agency-a", "agency-b")):
    ctx = type("Ctx", (), {})()
    ctx.rest = RestClient(session, retries=0)
    ctx.spanner_factory = lambda: FakeSpanner(rows)
    ctx.artifacts_bucket_name = "bucket"
    ctx.input_prefix = "ingestion/input/"
    ctx.data_source_prefixes = prefixes
    return ctx


def _listing(items, next_token=None):
    payload = {"items": items}
    if next_token:
        payload["nextPageToken"] = next_token
    return FakeResponse(payload=payload)


def test_reports_files_bytes_last_update_and_rows_per_prefix():
    session = FakeSession(
        {
            "prefix=ingestion/input/agency-a/": _listing(
                [
                    {"name": "ingestion/input/agency-a/a.csv", "size": "100", "updated": "2026-08-01T10:00:00Z"},
                    {"name": "ingestion/input/agency-a/b.mcf", "size": "50", "updated": "2026-08-04T09:12:00Z"},
                ]
            ),
            "prefix=ingestion/input/agency-b/": _listing(
                [{"name": "ingestion/input/agency-b/c.csv", "size": "7", "updated": "2026-07-30T08:00:00Z"}]
            ),
        }
    )
    rows = [{"provenance": "AGENCY-A", "Rows": 4711}, {"provenance": "AGENCY-B", "Rows": 12}]
    probe = probe_data_sources(_ctx(session, rows))
    assert probe.status == HEALTHY
    first = probe.data["sources"][0]
    assert first == {
        "prefix": "agency-a",
        "files": 2,
        "bytes": 150,
        "last_updated": "2026-08-04T09:12:00Z",
        "rows": 4711,
    }


def test_a_prefix_with_no_files_degrades():
    session = FakeSession(
        {
            "prefix=ingestion/input/agency-a/": _listing([]),
            "prefix=ingestion/input/agency-b/": _listing(
                [{"name": "ingestion/input/agency-b/c.csv", "size": "7", "updated": "2026-07-30T08:00:00Z"}]
            ),
        }
    )
    probe = probe_data_sources(_ctx(session, [{"provenance": "AGENCY-B", "Rows": 12}]))
    assert probe.status == DEGRADED
    assert "agency-a" in probe.detail


def test_a_prefix_with_files_but_no_rows_reports_none():
    session = FakeSession(
        {
            "prefix=ingestion/input/agency-a/": _listing(
                [{"name": "ingestion/input/agency-a/a.csv", "size": "1", "updated": "2026-08-01T10:00:00Z"}]
            )
        }
    )
    probe = probe_data_sources(_ctx(session, [], prefixes=("agency-a",)))
    assert probe.data["sources"][0]["rows"] is None


def test_provenances_without_a_matching_prefix_are_surfaced_not_dropped():
    session = FakeSession(
        {
            "prefix=ingestion/input/agency-a/": _listing(
                [{"name": "ingestion/input/agency-a/a.csv", "size": "1", "updated": "2026-08-01T10:00:00Z"}]
            )
        }
    )
    rows = [{"provenance": "AGENCY-A", "Rows": 1}, {"provenance": "SOMEONE-ELSE", "Rows": 99}]
    probe = probe_data_sources(_ctx(session, rows, prefixes=("agency-a",)))
    assert probe.data["unmatched_provenances"] == [{"provenance": "SOMEONE-ELSE", "rows": 99}]


def test_an_empty_prefix_list_is_unknown_not_healthy():
    # The Terraform default for data_source_prefixes is []. A first apply that
    # forgets the variable must not report green about coverage it was never
    # told to check.
    session = FakeSession({})
    probe = probe_data_sources(_ctx(session, [], prefixes=()))
    assert probe.status == UNKNOWN
    assert probe.data["sources"] == []


def test_a_database_failure_is_not_reported_as_healthy():
    # The outage this probe would otherwise hide: GCS answers, Spanner does not,
    # and every source reports rows: None — indistinguishable from "nothing
    # matched" unless the failure is surfaced.
    class ExplodingSpanner:
        def query(self, sql, **kwargs):
            raise RuntimeError("permission denied")

        def close(self):
            pass

    session = FakeSession(
        {
            "prefix=ingestion/input/agency-a/": _listing(
                [{"name": "ingestion/input/agency-a/a.csv", "size": "1",
                  "updated": "2026-08-01T10:00:00Z"}]
            )
        }
    )
    ctx = _ctx(session, [], prefixes=("agency-a",))
    ctx.spanner_factory = lambda: ExplodingSpanner()
    probe = probe_data_sources(ctx)
    assert probe.status == UNKNOWN
    assert "rows served per source unavailable" in probe.detail
    assert probe.data["rows_known"] is False


def test_follows_pagination_and_flags_truncation_at_the_page_cap():
    pages = {
        "prefix=ingestion/input/agency-a/": [
            _listing([{"name": "a", "size": "1", "updated": "2026-08-01T10:00:00Z"}], next_token="t1"),
            _listing([{"name": "b", "size": "1", "updated": "2026-08-02T10:00:00Z"}], next_token="t2"),
        ]
    }
    session = FakeSession(pages)
    probe = probe_data_sources(_ctx(session, [], prefixes=("agency-a",)), max_pages=2)
    assert probe.data["truncated"] is True
