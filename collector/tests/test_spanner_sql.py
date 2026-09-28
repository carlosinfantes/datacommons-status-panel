import pytest

from dc_status.rest import RestClient, RestError
from dc_status.spanner_sql import SpannerSQL
from tests.conftest import FakeResponse, FakeSession

SESSION_NAME = "projects/p/instances/i/databases/d/sessions/S1"


def _sql_response(fields, rows):
    return FakeResponse(
        payload={
            "metadata": {
                "rowType": {"fields": [{"name": n, "type": {"code": c}} for n, c in fields]}
            },
            "rows": rows,
        }
    )


def _client(sql_response):
    # End-anchored fragments: the executeSql URL ends in ":executeSql" but also
    # *contains* "/sessions", so a plain substring route would serve the
    # session-creation payload to every query.
    session = FakeSession(
        {
            "/sessions$": FakeResponse(payload={"name": SESSION_NAME}),
            ":executeSql$": sql_response,
        }
    )
    return session, SpannerSQL(RestClient(session), "p", "i", "d")


def test_creates_a_session_then_queries():
    session, spanner = _client(_sql_response([("n", "INT64")], [["7"]]))
    spanner.query("SELECT 1 AS n")
    urls = [call[1] for call in session.calls]
    assert urls[0].endswith("/databases/d/sessions")
    assert urls[1].endswith(f"{SESSION_NAME}:executeSql")


def test_reuses_the_session_across_queries():
    session, spanner = _client(_sql_response([("n", "INT64")], [["7"]]))
    spanner.query("SELECT 1 AS n")
    spanner.query("SELECT 2 AS n")
    assert sum(1 for call in session.calls if call[1].endswith("/sessions")) == 1


def test_decodes_int64_to_int():
    _, spanner = _client(_sql_response([("Total", "INT64")], [["1234567"]]))
    assert spanner.query("SELECT COUNT(*) AS Total FROM Observation") == [{"Total": 1234567}]


def test_decodes_bool_and_keeps_timestamps_as_strings():
    fields = [("IngestionFailure", "BOOL"), ("CreationTimestamp", "TIMESTAMP")]
    _, spanner = _client(_sql_response(fields, [[False, "2026-08-05T14:14:00.825993Z"]]))
    assert spanner.query("SELECT 1") == [
        {"IngestionFailure": False, "CreationTimestamp": "2026-08-05T14:14:00.825993Z"}
    ]


def test_pads_missing_trailing_nulls():
    fields = [("LockID", "STRING"), ("LockOwner", "STRING"), ("AcquiredTimestamp", "TIMESTAMP")]
    _, spanner = _client(_sql_response(fields, [["global_ingestion_lock"]]))
    assert spanner.query("SELECT 1") == [
        {"LockID": "global_ingestion_lock", "LockOwner": None, "AcquiredTimestamp": None}
    ]


def test_no_rows_yields_an_empty_list():
    _, spanner = _client(FakeResponse(payload={"metadata": {"rowType": {"fields": []}}}))
    assert spanner.query("SELECT 1") == []


def test_uses_exact_staleness_by_default():
    session, spanner = _client(_sql_response([("n", "INT64")], [["1"]]))
    spanner.query("SELECT 1 AS n", staleness_seconds=10)
    body = session.calls[1][2]
    assert body["transaction"] == {"singleUse": {"readOnly": {"exactStaleness": "10s"}}}


def test_zero_staleness_requests_a_strong_read():
    session, spanner = _client(_sql_response([("n", "INT64")], [["1"]]))
    spanner.query("SELECT 1 AS n", staleness_seconds=0)
    body = session.calls[1][2]
    assert body["transaction"] == {"singleUse": {"readOnly": {"strong": True}}}


def test_recreates_the_session_once_when_it_expired():
    responses = [FakeResponse(status_code=404), _sql_response([("n", "INT64")], [["1"]])]
    session = FakeSession(
        {"/sessions$": FakeResponse(payload={"name": SESSION_NAME}), ":executeSql$": responses}
    )
    spanner = SpannerSQL(RestClient(session, retries=0, sleep=lambda _s: None), "p", "i", "d")
    assert spanner.query("SELECT 1 AS n") == [{"n": 1}]
    assert sum(1 for call in session.calls if call[1].endswith("/sessions")) == 2


def test_propagates_a_permission_error():
    session = FakeSession(
        {"/sessions$": FakeResponse(status_code=403, payload={"error": {"message": "no access"}})}
    )
    spanner = SpannerSQL(RestClient(session, retries=0), "p", "i", "d")
    with pytest.raises(RestError):
        spanner.query("SELECT 1")
