"""WSGI application. No framework: four routes and a few files."""

from __future__ import annotations

import json
import os
from importlib.resources import files

from .assemble import collect_all, collect_self
from .cache import TTLCache
from .config import load_config
from .sanitize import sanitize

_ASSETS = {
    "app.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}
_JSON = "application/json; charset=utf-8"
_UNAVAILABLE = {"overall": "unknown", "partial": True, "environments": []}


def _asset(name: str) -> bytes:
    return (files("dc_status.web") / name).read_bytes()


def create_app(
    config=None,
    clients=None,
    cache=None,
    collect_self_fn=collect_self,
    collect_all_fn=collect_all,
    replay: str | None = None,
):
    cache = cache or TTLCache()

    def _document(include_peers: bool) -> tuple[dict, bool]:
        try:
            if replay:
                with open(replay, encoding="utf-8") as handle:
                    document = json.load(handle)
            elif include_peers:
                from .peers import fetch_peer

                document = collect_all_fn(config, clients, cache, fetch_peer)
            else:
                document = collect_self_fn(config, clients, cache)
        except Exception as exc:
            document = {**_UNAVAILABLE, "detail": sanitize(exc)}
        return document, bool(document.get("partial"))

    def application(environ, start_response):
        path = environ.get("PATH_INFO", "/")

        def respond(status: str, content_type: str, body: bytes, extra: dict | None = None):
            headers = [
                ("Content-Type", content_type),
                ("Content-Length", str(len(body))),
                ("X-Content-Type-Options", "nosniff"),
            ]
            headers.extend((extra or {}).items())
            start_response(status, headers)
            return [body]

        if path == "/healthz":
            return respond("200 OK", _JSON, json.dumps({"status": "ok"}).encode())

        if path in ("/api/v1/self", "/api/v1/all"):
            document, partial = _document(path.endswith("/all"))
            extra = {"X-Status-Partial": "true"} if partial else {}
            extra["Cache-Control"] = "no-store"
            return respond("200 OK", _JSON, json.dumps(document).encode(), extra)

        if path in ("/", "/index.html"):
            return respond("200 OK", "text/html; charset=utf-8", _asset("index.html"))

        if path.startswith("/static/"):
            name = path[len("/static/") :]
            if name in _ASSETS:  # an allowlist, so traversal has nothing to reach
                return respond("200 OK", _ASSETS[name], _asset(name))

        return respond("404 Not Found", _JSON, json.dumps({"error": "not found"}).encode())

    return application


def _build_default():
    from .clients import build_clients

    config = load_config(os.environ)
    return create_app(
        config=config,
        clients=build_clients(config),
        replay=os.environ.get("DCS_REPLAY_FILE"),
    )


class _Lazy:
    """Defer configuration until the first request so import never fails hard."""

    def __init__(self):
        self._app = None

    def __call__(self, environ, start_response):
        if self._app is None:
            self._app = _build_default()
        return self._app(environ, start_response)


application = _Lazy()
