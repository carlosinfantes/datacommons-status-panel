from dc_status.model import DEGRADED, DOWN, HEALTHY
from dc_status.probes import probe_dc_api, probe_frontend


class FakePublic:
    def __init__(self, result):
        self._result = result
        self.urls = []

    def get_text(self, url, timeout=None):
        self.urls.append(url)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _ctx(public):
    ctx = type("Ctx", (), {})()
    ctx.public = public
    ctx.public_endpoint_url = "https://api.example"
    ctx.frontend_url = "https://www.example"
    return ctx


def test_dc_api_is_healthy_when_the_known_entity_resolves():
    public = FakePublic(
        (200, '{"data":{"country/GTM":{"arcs":{"name":{"nodes":[{"value":"Guatemala"}]}}}}}')
    )
    probe = probe_dc_api(_ctx(public))
    assert probe.status == HEALTHY
    assert "country/GTM" in public.urls[0]
    assert probe.data["http_status"] == 200


def test_dc_api_502_is_down_with_the_likely_cause():
    probe = probe_dc_api(_ctx(FakePublic((502, "Bad Gateway"))))
    assert probe.status == DOWN
    # Both halves, not `or`: the 502 branch exists precisely to name the two
    # causes. A generic "HTTP 502" message would satisfy neither.
    assert "sidecar" in probe.detail.lower()
    assert "schema" in probe.detail.lower()


def test_dc_api_200_without_the_expected_value_degrades():
    probe = probe_dc_api(_ctx(FakePublic((200, '{"data":{}}'))))
    assert probe.status == DEGRADED


def test_dc_api_other_error_codes_are_down():
    probe = probe_dc_api(_ctx(FakePublic((403, "Forbidden"))))
    assert probe.status == DOWN
    assert "403" in probe.detail


def test_dc_api_network_failure_is_down_and_the_emitted_detail_is_sanitized():
    probe = probe_dc_api(_ctx(FakePublic(RuntimeError("connection reset Bearer ya29.leaked"))))
    assert probe.status == DOWN
    # Sanitisation happens at the emission boundary, Probe.to_dict(), which is
    # what a consumer actually sees. Asserting on probe.detail would read the raw
    # field and fail for the wrong reason.
    emitted = probe.to_dict()["detail"]
    assert "ya29" not in emitted
    assert "[REDACTED]" in emitted


def test_dc_api_rejects_an_error_body_that_merely_mentions_the_value():
    # A substring check over the whole body would call this healthy.
    probe = probe_dc_api(_ctx(FakePublic((200, '{"error": "no name found for Guatemala"}'))))
    assert probe.status == DEGRADED


def test_dc_api_rejects_a_body_that_is_not_json():
    # What a proxy or an error page in front of the endpoint would return.
    probe = probe_dc_api(_ctx(FakePublic((200, "<html>Guatemala</html>"))))
    assert probe.status == DEGRADED


def test_frontend_network_failure_degrades():
    probe = probe_frontend(_ctx(FakePublic(RuntimeError("connection refused"))))
    assert probe.status == DEGRADED
    assert "connection refused" in probe.to_dict()["detail"]


def test_frontend_other_error_codes_degrade():
    probe = probe_frontend(_ctx(FakePublic((503, "Service Unavailable"))))
    assert probe.status == DEGRADED
    assert "503" in probe.detail


def test_frontend_200_is_healthy():
    assert probe_frontend(_ctx(FakePublic((200, "<html></html>")))).status == HEALTHY


def test_frontend_404_degrades_with_the_empty_bucket_explanation():
    probe = probe_frontend(_ctx(FakePublic((404, "Not Found"))))
    assert probe.status == DEGRADED
    assert "empty" in probe.detail.lower()
