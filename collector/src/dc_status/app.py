"""WSGI application. No framework: four routes and a few files."""

from __future__ import annotations

import json
import os
from importlib.resources import files

from .assemble import collect_all, collect_self
from .auth import Denied, authorize
from .cache import TTLCache
from .config import load_auth_config, load_config
from .sanitize import sanitize

_ASSETS = {
    "app.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "favicon.svg": "image/svg+xml",
    "fonts/plex-sans-var.woff2": "font/woff2",
    "fonts/plex-mono-400.woff2": "font/woff2",
}
# The fonts are bundled with the image and only ever change under a new name, so
# they can be cached hard. The page's own CSS and JS deliberately are not.
_IMMUTABLE = frozenset({"fonts/plex-sans-var.woff2", "fonts/plex-mono-400.woff2"})
_JSON = "application/json; charset=utf-8"
_UNAVAILABLE = {"overall": "unknown", "partial": True, "environments": []}


def _asset(name: str) -> bytes:
    target = files("dc_status.web")
    for part in name.split("/"):
        target = target / part
    return target.read_bytes()


def create_app(
    config=None,
    clients=None,
    cache=None,
    collect_self_fn=collect_self,
    collect_all_fn=collect_all,
    replay: str | None = None,
    *,
    auth,
    verifier=None,
):
    """`auth` is keyword-only and has no default on purpose: an app cannot be
    built without stating who may read it."""
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

        # /healthz sits above the gate: Cloud Run's startup probe does not
        # traverse IAP, and the route says nothing beyond "the process is up".
        if path == "/healthz":
            return respond("200 OK", _JSON, json.dumps({"status": "ok"}).encode())

        # Everything below is admin-only — the document names projects, buckets,
        # tables and row counts, which is a reconnaissance map of the platform.
        try:
            authorize(environ, auth, verifier)
        except Denied:
            # The reason is already in the logs. Saying more here would tell an
            # unauthenticated caller which door they got closest to.
            return respond("403 Forbidden", _JSON, json.dumps({"error": "forbidden"}).encode())

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
                extra = {"Cache-Control": "public, max-age=604800"} if name in _IMMUTABLE else None
                return respond("200 OK", _ASSETS[name], _asset(name), extra)

        return respond("404 Not Found", _JSON, json.dumps({"error": "not found"}).encode())

    return application


def _build_default():
    config = load_config(os.environ)
    replay = os.environ.get("DCS_REPLAY_FILE")

    clients = None
    if not replay:
        # Only reached when something will actually be probed. Replay serves a
        # saved document and calls no API, so requiring credentials to build the
        # clients would defeat the one thing the affordance exists for: iterating
        # on the page without a deployment behind it.
        from .clients import build_clients

        clients = build_clients(config)

    return create_app(
        config=config,
        clients=clients,
        replay=replay,
        auth=load_auth_config(os.environ),
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
