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
    ctx.canary_node = "country/GTM"
    ctx.canary_name = "Guatemala"
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


def test_frontend_404_degrades_with_a_generic_explanation():
    probe = probe_frontend(_ctx(FakePublic((404, "Not Found"))))
    assert probe.status == DEGRADED
    assert "404" in probe.detail
    # Nothing that presumes who is responsible for publishing the site.
    assert "team" not in probe.detail.lower()


def test_dc_api_resolves_the_configured_canary():
    public = FakePublic(
        (200, '{"data":{"country/FRA":{"arcs":{"name":{"nodes":[{"value":"France"}]}}}}}')
    )
    ctx = _ctx(public)
    ctx.canary_node = "country/FRA"
    ctx.canary_name = "France"
    probe = probe_dc_api(ctx)
    assert probe.status == HEALTHY
    assert "nodes=country/FRA" in public.urls[0]
    assert probe.data["canary"] == {"node": "country/FRA", "name": "France"}


def test_dc_api_fails_when_the_configured_canary_resolves_to_another_name():
    public = FakePublic(
        (200, '{"data":{"country/GTM":{"arcs":{"name":{"nodes":[{"value":"Guatemala"}]}}}}}')
    )
    ctx = _ctx(public)
    ctx.canary_name = "Guatemala City"
    probe = probe_dc_api(ctx)
    assert probe.status == DEGRADED
    assert "Guatemala City" in probe.detail


def test_dc_api_matches_a_non_ascii_canary_name():
    # A substring check over json.dumps compares against the escaped text and
    # never matches a name outside ASCII.
    body = '{"data":{"country/CIV":{"arcs":{"name":{"nodes":[{"value":"C\\u00f4te d\\u2019Ivoire"}]}}}}}'
    ctx = _ctx(FakePublic((200, body)))
    ctx.canary_node = "country/CIV"
    ctx.canary_name = "Côte d’Ivoire"
    assert probe_dc_api(ctx).status == HEALTHY
