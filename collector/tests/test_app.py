import json

from dc_status.app import create_app
from dc_status.config import EnvConfig


def _config():
    return EnvConfig(
        env_id="prod", env_label="Production", project_id="p", region="us-central1",
        spanner_instance_id="i", spanner_database_id="d", datacommons_service_name="dc",
        ingestion_workflow_name="wf", preprocessing_job_name="job",
        artifacts_bucket_name="b", public_endpoint_url="https://api.example",
        frontend_url="https://www.example", data_source_prefixes=(), input_prefix="ingestion/input/",
        peers=(), counts_cache_ttl_seconds=300, schema_cache_ttl_seconds=3600,
    )


def _call(app, path):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app({"PATH_INFO": path, "REQUEST_METHOD": "GET"}, start_response))
    return captured, body


def _app(document=None, replay=None):
    document = document or {"overall": "healthy", "partial": False, "environments": []}
    return create_app(
        config=_config(),
        clients=object(),
        collect_self_fn=lambda *a, **k: document,
        collect_all_fn=lambda *a, **k: document,
        replay=replay,
    )


def test_healthz_answers_without_probing():
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
    captured, _body = _call(_app(document), "/api/v1/all")
    assert captured["headers"]["X-Status-Partial"] == "true"


def test_a_complete_document_does_not_set_the_partial_header():
    captured, _body = _call(_app(), "/api/v1/all")
    assert "X-Status-Partial" not in captured["headers"]


def test_a_collector_failure_still_answers_200():
    def boom(*_args, **_kwargs):
        raise RuntimeError("everything is on fire with Bearer ya29.leaked")

    app = create_app(config=_config(), clients=object(), collect_self_fn=boom, collect_all_fn=boom)
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


def test_path_traversal_is_refused():
    captured, _body = _call(_app(), "/static/../../etc/passwd")
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
        config=_config(), clients=object(), collect_self_fn=boom, collect_all_fn=boom,
        replay=str(replay_file),
    )
    _captured, body = _call(app, "/api/v1/all")
    assert json.loads(body)["overall"] == "down"
