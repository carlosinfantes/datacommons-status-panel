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

import json

from dc_status.app import create_app
from dc_status.config import AuthConfig, EnvConfig

_IAP_ISSUER = "https://cloud.google.com/iap"


def _ungated():
    """For the tests that are about routing, not about access."""
    return AuthConfig(require=False, iap_audience="")


def _gated():
    return AuthConfig(require=True, iap_audience="/projects/1/apps/p")


class _AcceptingVerifier:
    def iap(self, _token, _audience):
        return {"iss": _IAP_ISSUER, "email": "admin@example.org"}


def _config():
    return EnvConfig(
        env_id="prod",
        env_label="Production",
        project_id="p",
        region="us-central1",
        spanner_instance_id="i",
        spanner_database_id="d",
        datacommons_service_name="dc",
        ingestion_workflow_name="wf",
        artifacts_bucket_name="b",
        public_endpoint_url="https://api.example",
        frontend_url="https://www.example",
        data_source_prefixes=(),
        input_prefix="ingestion/input/",
        counts_cache_ttl_seconds=300,
        schema_cache_ttl_seconds=3600,
    )


def _call(app, path, **extra_environ):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"PATH_INFO": path, "REQUEST_METHOD": "GET", **extra_environ}
    body = b"".join(app(environ, start_response))
    return captured, body


def _app(document=None, replay=None, auth=None, verifier=None):
    document = document or {"overall": "healthy", "partial": False, "environments": []}
    return create_app(
        config=_config(),
        clients=object(),
        collect_self_fn=lambda *a, **k: document,
        replay=replay,
        auth=auth or _ungated(),
        verifier=verifier,
    )


def test_healthz_answers_200():
    captured, body = _call(_app(), "/healthz")
    assert captured["status"].startswith("200")
    assert json.loads(body) == {"status": "ok"}


def test_self_endpoint_returns_the_document():
    captured, body = _call(_app(), "/api/v1/self")
    assert captured["status"].startswith("200")
    assert json.loads(body)["overall"] == "healthy"
    assert captured["headers"]["Content-Type"] == "application/json; charset=utf-8"


def test_a_partial_document_sets_the_partial_header():
    document = {"overall": "degraded", "partial": True, "environments": []}
    captured, _body = _call(_app(document), "/api/v1/self")
    assert captured["headers"]["X-Status-Partial"] == "true"


def test_a_complete_document_does_not_set_the_partial_header():
    captured, _body = _call(_app(), "/api/v1/self")
    assert "X-Status-Partial" not in captured["headers"]


def test_a_collector_failure_still_answers_200():
    def boom(*_args, **_kwargs):
        raise RuntimeError("everything is on fire with Bearer ya29.leaked")

    app = create_app(
        config=_config(),
        clients=object(),
        collect_self_fn=boom,
        auth=_ungated(),
    )
    captured, body = _call(app, "/api/v1/self")
    assert captured["status"].startswith("200")
    payload = json.loads(body)
    assert payload["overall"] == "unknown"
    assert "ya29" not in json.dumps(payload)
    assert captured["headers"]["X-Status-Partial"] == "true"


def test_root_serves_the_page():
    captured, body = _call(_app(), "/")
    assert captured["status"].startswith("200")
    assert captured["headers"]["Content-Type"].startswith("text/html")
    assert b"<" in body


def test_static_assets_are_served_from_an_allowlist():
    captured, _body = _call(_app(), "/static/app.js")
    assert captured["status"].startswith("200")
    assert "javascript" in captured["headers"]["Content-Type"]


def test_fonts_are_served_and_cached():
    captured, body = _call(_app(), "/static/fonts/plex-sans-var.woff2")
    assert captured["status"].startswith("200")
    assert captured["headers"]["Content-Type"] == "font/woff2"
    # woff2 magic number, so a truncated or wrong-file bundle fails here.
    assert body[:4] == b"wOF2"
    assert "max-age" in captured["headers"]["Cache-Control"]


def test_the_page_css_and_js_are_not_cached_like_the_fonts():
    # They change on every deploy under the same name, so they must not carry the
    # fonts' long max-age.
    for path in ("/static/styles.css", "/static/app.js"):
        captured, _body = _call(_app(), path)
        assert captured["status"].startswith("200")
        assert "Cache-Control" not in captured["headers"]


def test_the_peer_fan_in_route_is_gone():
    captured, _body = _call(_app(), "/api/v1/all")
    assert captured["status"].startswith("404")


def test_path_traversal_is_refused():
    captured, _body = _call(_app(), "/static/../../etc/passwd")
    assert captured["status"].startswith("404")


def test_the_font_licence_is_not_a_route():
    # It ships in the wheel to satisfy the OFL, but it is not an asset the page
    # asks for, so it stays off the allowlist.
    captured, _body = _call(_app(), "/static/fonts/OFL.txt")
    assert captured["status"].startswith("404")


def test_an_unknown_path_is_404_json():
    captured, body = _call(_app(), "/nope")
    assert captured["status"].startswith("404")
    assert json.loads(body)["error"] == "not found"


def test_replay_short_circuits_the_collector(tmp_path):
    replay_file = tmp_path / "replay.json"
    replay_file.write_text(json.dumps({"overall": "down", "partial": False, "environments": []}))

    def boom(*_a, **_k):
        raise AssertionError("the collector must not run in replay mode")

    app = create_app(
        config=_config(),
        clients=object(),
        collect_self_fn=boom,
        replay=str(replay_file),
        auth=_ungated(),
    )
    _captured, body = _call(app, "/api/v1/self")
    assert json.loads(body)["overall"] == "down"


def test_an_app_cannot_be_built_without_an_access_policy():
    # auth is keyword-only with no default, so forgetting it is a TypeError at
    # construction rather than an unguarded app at runtime.
    try:
        create_app(config=_config(), clients=object())
    except TypeError as exc:
        assert "auth" in str(exc)
    else:
        raise AssertionError("create_app accepted no access policy")


def test_an_unauthenticated_request_for_the_document_is_refused():
    app = _app(auth=_gated(), verifier=_AcceptingVerifier())
    for path in ("/api/v1/self",):
        captured, body = _call(app, path)
        assert captured["status"].startswith("403")
        # Nothing but the refusal: no hint about which credential was missing.
        assert json.loads(body) == {"error": "forbidden"}


def test_an_unauthenticated_request_for_the_page_is_refused():
    # The shell leaks little on its own, but one rule is easier to keep true than
    # a per-route exception list.
    app = _app(auth=_gated(), verifier=_AcceptingVerifier())
    for path in ("/", "/static/app.js", "/static/fonts/plex-sans-var.woff2"):
        captured, _body = _call(app, path)
        assert captured["status"].startswith("403")


def test_healthz_stays_open_when_access_is_gated():
    captured, body = _call(_app(auth=_gated(), verifier=_AcceptingVerifier()), "/healthz")
    assert captured["status"].startswith("200")
    assert json.loads(body) == {"status": "ok"}


def test_a_verified_iap_assertion_reaches_the_document():
    app = _app(auth=_gated(), verifier=_AcceptingVerifier())
    captured, body = _call(
        app, "/api/v1/self", HTTP_X_GOOG_IAP_JWT_ASSERTION="a.verified.assertion"
    )
    assert captured["status"].startswith("200")
    assert json.loads(body)["overall"] == "healthy"


def test_an_unknown_path_is_still_403_before_it_is_404():
    # Probing for routes is reconnaissance too, so the gate answers first.
    captured, _body = _call(_app(auth=_gated(), verifier=_AcceptingVerifier()), "/nope")
    assert captured["status"].startswith("403")


_MINIMAL_ENV = {
    "DCS_ENV_ID": "prod",
    "DCS_PROJECT_ID": "p",
    "DCS_REGION": "us-central1",
    "DCS_SPANNER_INSTANCE_ID": "i",
    "DCS_SPANNER_DATABASE_ID": "d",
    "DCS_DATACOMMONS_SERVICE_NAME": "dc",
    "DCS_INGESTION_WORKFLOW_NAME": "wf",
    "DCS_ARTIFACTS_BUCKET_NAME": "b",
    "DCS_PUBLIC_ENDPOINT_URL": "https://api.example",
    "DCS_FRONTEND_URL": "https://www.example",
}


def test_replay_needs_no_credentials(monkeypatch, tmp_path):
    # The affordance exists to iterate on the page without a deployment behind
    # it, so building the app must not reach for Application Default Credentials.
    # It used to: build_clients ran before the replay branch was consulted.
    import dc_status.app as app_module
    import dc_status.clients as clients_module

    replay_file = tmp_path / "replay.json"
    replay_file.write_text(json.dumps({"overall": "healthy", "partial": False, "environments": []}))

    def explode(*_a, **_k):
        raise AssertionError("replay must not build authorized clients")

    monkeypatch.setattr(clients_module, "build_clients", explode)
    monkeypatch.setattr(
        app_module.os,
        "environ",
        {**_MINIMAL_ENV, "DCS_REPLAY_FILE": str(replay_file), "DCS_REQUIRE_AUTH": "false"},
    )

    app = app_module._build_default()
    _captured, body = _call(app, "/api/v1/self")
    assert json.loads(body)["overall"] == "healthy"


def test_a_live_deployment_still_builds_its_clients(monkeypatch):
    # The mirror of the test above: without a replay file the credentials path is
    # the one that must run, so the branch cannot simply have been deleted.
    import dc_status.app as app_module
    import dc_status.clients as clients_module

    built = []
    monkeypatch.setattr(
        clients_module, "build_clients", lambda config: built.append(config) or object()
    )
    monkeypatch.setattr(app_module.os, "environ", {**_MINIMAL_ENV, "DCS_REQUIRE_AUTH": "false"})

    app_module._build_default()
    assert len(built) == 1
