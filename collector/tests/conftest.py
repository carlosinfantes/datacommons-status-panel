"""Test doubles shared across the suite."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class FakeResponse:
    status_code: int = 200
    payload: object = field(default_factory=dict)
    text_body: str | None = None

    @property
    def text(self) -> str:
        if self.text_body is not None:
            return self.text_body
        return json.dumps(self.payload)

    def json(self) -> object:
        return self.payload


class FakeSession:
    """Stands in for an AuthorizedSession.

    `routes` maps a fragment of the request *signature* to a FakeResponse, or to
    a list of them consumed in order (to exercise retries). The signature is
    "METHOD url?k=v&k=v" with the query sorted, so a route can discriminate on
    query parameters even though they travel outside the URL.

    A fragment ending in "$" must match the end of the signature. That is needed
    whenever one endpoint's URL is a prefix of another's — Spanner's
    ".../sessions" and ".../sessions/S1:executeSql" being the case in point,
    where a plain substring fragment would serve the wrong response silently.

    Two routes matching the same request is a fixture bug, and raises rather
    than picking a winner: a matcher that guesses is how a test ends up
    asserting against a response it was never meant to see.
    """

    def __init__(self, routes: dict[str, object] | None = None):
        self.routes = routes or {}
        self.calls: list[tuple[str, str, object]] = []
        self.timeouts: list[float | None] = []
        self.headers: dict = {}

    def request(self, method, url, params=None, json=None, timeout=None):
        # `params if not None else json`, not `params or json`: an empty dict is
        # falsy, and recording None for it would misreport what was sent.
        self.calls.append((method, url, json if params is None else params))
        self.timeouts.append(timeout)
        signature = f"{method} {url}"
        if params:
            signature += "?" + "&".join(f"{key}={value}" for key, value in sorted(params.items()))
        matches = [
            response for fragment, response in self.routes.items()
            if _fragment_matches(fragment, signature)
        ]
        if len(matches) > 1:
            raise AssertionError(f"{len(matches)} routes match {signature!r}; the fixture is ambiguous")
        if not matches:
            raise AssertionError(f"unexpected request in test: {signature!r}")
        response = matches[0]
        if isinstance(response, list):
            return response.pop(0) if len(response) > 1 else response[0]
        return response


def _fragment_matches(fragment: str, signature: str) -> bool:
    if fragment.endswith("$"):
        return signature.endswith(fragment[:-1])
    return fragment in signature
