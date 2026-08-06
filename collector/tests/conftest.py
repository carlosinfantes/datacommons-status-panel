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

    `routes` maps a substring of the URL to a FakeResponse or to a list of
    FakeResponses consumed in order (to exercise retries).
    """

    def __init__(self, routes: dict[str, object] | None = None):
        self.routes = routes or {}
        self.calls: list[tuple[str, str, object]] = []
        self.timeouts: list[float | None] = []

    def request(self, method, url, params=None, json=None, timeout=None):
        self.calls.append((method, url, params or json))
        self.timeouts.append(timeout)
        for fragment, response in self.routes.items():
            if fragment in url:
                if isinstance(response, list):
                    return response.pop(0) if len(response) > 1 else response[0]
                return response
        raise AssertionError(f"unexpected URL in test: {url}")
