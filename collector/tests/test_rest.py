import pytest

from dc_status.rest import RestClient, RestError
from tests.conftest import FakeResponse, FakeSession


def test_get_returns_the_decoded_payload():
    session = FakeSession({"/instances/abc": FakeResponse(payload={"state": "READY"})})
    client = RestClient(session)
    assert client.get("https://spanner.googleapis.com/v1/instances/abc") == {"state": "READY"}


def test_get_passes_params_and_timeout():
    session = FakeSession({"/o": FakeResponse(payload={"items": []})})
    client = RestClient(session, timeout=7.0)
    client.get("https://storage.googleapis.com/storage/v1/b/x/o", params={"prefix": "a/"})
    method, url, sent = session.calls[0]
    assert method == "GET"
    assert sent == {"prefix": "a/"}
    assert session.timeouts == [7.0]  # the client's default reaches the session


def test_a_per_call_timeout_overrides_the_client_default():
    session = FakeSession({"/o": FakeResponse(payload={"items": []})})
    client = RestClient(session, timeout=7.0)
    client.get("https://storage.googleapis.com/storage/v1/b/x/o", timeout=2.5)
    assert session.timeouts == [2.5]


def test_retries_on_503_then_succeeds():
    session = FakeSession(
        {"/x": [FakeResponse(status_code=503), FakeResponse(payload={"ok": True})]}
    )
    client = RestClient(session, retries=2, sleep=lambda _s: None)
    assert client.get("https://run.googleapis.com/v2/x") == {"ok": True}
    assert len(session.calls) == 2


def test_does_not_retry_on_403_and_raises():
    session = FakeSession({"/x": FakeResponse(status_code=403, payload={"error": {"message": "denied"}})})
    client = RestClient(session, retries=2, sleep=lambda _s: None)
    with pytest.raises(RestError) as excinfo:
        client.get("https://run.googleapis.com/v2/x")
    assert excinfo.value.status == 403
    assert "denied" in excinfo.value.message
    # The call count is what proves "does not retry": without it this test
    # passes even if the non-retryable guard is deleted, because an exhausted
    # retry budget raises a RestError carrying the same status and message.
    assert len(session.calls) == 1


def test_gives_up_after_the_retry_budget():
    session = FakeSession({"/x": FakeResponse(status_code=500)})
    client = RestClient(session, retries=2, sleep=lambda _s: None)
    with pytest.raises(RestError):
        client.get("https://run.googleapis.com/v2/x")
    assert len(session.calls) == 3  # 1 attempt + 2 retries


def test_post_sends_the_body():
    session = FakeSession({":executeSql": FakeResponse(payload={"rows": []})})
    client = RestClient(session)
    client.post("https://spanner.googleapis.com/v1/s:executeSql", body={"sql": "SELECT 1"})
    assert session.calls[0][2] == {"sql": "SELECT 1"}


def test_error_message_is_sanitized():
    long_message = "Bearer ya29.leaked " + "x" * 500
    session = FakeSession({"/x": FakeResponse(status_code=400, payload={"error": {"message": long_message}})})
    client = RestClient(session, retries=0)
    with pytest.raises(RestError) as excinfo:
        client.get("https://run.googleapis.com/v2/x")
    assert "ya29" not in excinfo.value.message
    assert len(excinfo.value.message) <= 300
