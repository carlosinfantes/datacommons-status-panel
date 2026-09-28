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

"""WSGI application. No framework: three routes and a few files."""

from __future__ import annotations

import json
import os
from importlib.resources import files
from urllib.parse import parse_qs

from .assemble import collect_status
from .auth import Denied, authorize
from .cache import DocumentCache, TTLCache
from .config import load_auth_config, load_config, load_flag
from .replay import load_replay
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

# On every response, the refusal and the 404 included. The page loads only
# same-origin files and runs no inline script, so default-src 'self' costs it
# nothing; styles set from script through element.style go through the CSSOM,
# which CSP does not govern. frame-ancestors 'none' stops the panel being
# framed by another site; no-referrer keeps project and bucket names in the
# URL from leaking to wherever a console link leads.
_SECURITY_HEADERS = (
    ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
)


def _asset(name: str) -> bytes:
    target = files("dc_status.web")
    for part in name.split("/"):
        target = target / part
    return target.read_bytes()


def _wants_fresh(environ) -> bool:
    values = parse_qs(environ.get("QUERY_STRING", "")).get("fresh", [])
    return any(value in ("1", "true") for value in values)


def create_app(
    config=None,
    clients=None,
    cache=None,
    collect_status_fn=collect_status,
    replay: str | None = None,
    *,
    auth,
    verifier=None,
    document_cache: DocumentCache | None = None,
    replay_shift_time: bool = False,
):
    """`auth` is keyword-only and has no default on purpose: an app cannot be
    built without stating who may read it."""
    cache = cache or TTLCache()
    documents = document_cache or DocumentCache()

    def _collect() -> dict:
        return collect_status_fn(config, clients, cache)

    def _document(fresh: bool) -> dict:
        if replay:
            # Read on every request, uncached: it is a local file, and editing it
            # while the page is open is the point of the affordance.
            return load_replay(replay, shift_time=replay_shift_time)
        return documents.get(_collect, fresh=fresh)

    def application(environ, start_response):
        path = environ.get("PATH_INFO", "/")

        def respond(status: str, content_type: str, body: bytes, extra: dict | None = None):
            headers = [("Content-Type", content_type), ("Content-Length", str(len(body)))]
            headers.extend(_SECURITY_HEADERS)
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

        if path == "/api/v1/status":
            # no-store on every /api answer, errors included: a stale verdict
            # replayed from a browser or proxy cache is worse than none.
            extra = {"Cache-Control": "no-store"}
            try:
                document = _document(_wants_fresh(environ))
            except Exception as exc:
                # A probe failing is a finding and still answers 200 inside the
                # document. Reaching here means there is no document at all, so
                # say so with a 5xx; the page shows `detail`.
                body = {"error": "unavailable", "detail": sanitize(exc)}
                return respond("500 Internal Server Error", _JSON, json.dumps(body).encode(), extra)
            if document.get("partial"):
                extra["X-Status-Partial"] = "true"
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
    auth = load_auth_config(os.environ)
    replay = (os.environ.get("DCS_REPLAY_FILE") or "").strip()
    if replay:
        # Replay serves a saved document and calls no API, so it needs neither
        # credentials nor any deployment configuration: requiring either would
        # defeat the one thing the affordance exists for, iterating on the page
        # or giving a demo without a deployment behind it. The access gate is
        # read all the same, so a replaying panel is no more open than a live one.
        return create_app(
            replay=replay,
            replay_shift_time=load_flag(os.environ, "REPLAY_SHIFT_TIME"),
            auth=auth,
        )

    from .clients import build_clients

    config = load_config(os.environ)
    return create_app(config=config, clients=build_clients(config), auth=auth)


class _Lazy:
    """Defer configuration until the first request so import never fails hard."""

    def __init__(self):
        self._app = None

    def __call__(self, environ, start_response):
        if self._app is None:
            self._app = _build_default()
        return self._app(environ, start_response)


application = _Lazy()
