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

"""Thin authenticated REST access. Knows nothing about probes or Data Commons."""

from __future__ import annotations

import time
from typing import Any

from .sanitize import sanitize

_RETRYABLE = frozenset({429, 500, 502, 503, 504})
_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


class RestError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


def build_authorized_session():
    """Real credentials. Isolated so tests never import google.auth."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=list(_SCOPES))
    return AuthorizedSession(credentials)


class RestClient:
    def __init__(self, session, *, timeout: float = 10.0, retries: int = 2, sleep=time.sleep):
        self._session = session
        self._timeout = timeout
        self._retries = retries
        self._sleep = sleep

    def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> dict:
        return self._call("GET", url, params=params, timeout=timeout)

    def post(self, url: str, body: dict | None = None, timeout: float | None = None) -> dict:
        return self._call("POST", url, json=body, timeout=timeout)

    def delete(self, url: str, timeout: float | None = None) -> None:
        self._call("DELETE", url, timeout=timeout)

    def _call(self, method: str, url: str, *, params=None, json=None, timeout=None) -> dict:
        attempts = self._retries + 1
        last: RestError | None = None
        for attempt in range(attempts):
            try:
                response = self._session.request(
                    method, url, params=params, json=json, timeout=timeout or self._timeout
                )
            except Exception as exc:  # network-level failure
                last = RestError(sanitize(exc))
            else:
                if response.status_code < 300:
                    return self._decode(response)
                last = RestError(self._error_message(response), response.status_code)
                if response.status_code not in _RETRYABLE:
                    raise last
            if attempt < attempts - 1:
                self._sleep(0.4 * (2**attempt))
        raise last if last else RestError("request failed")

    @staticmethod
    def _decode(response) -> dict:
        if not response.text:
            return {}
        payload = response.json()
        return payload if isinstance(payload, dict) else {"value": payload}

    @staticmethod
    def _error_message(response) -> str:
        detail: Any = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("error", {}).get("message", "")
        except Exception:
            detail = response.text
        return sanitize(detail or f"HTTP {response.status_code}")


PUBLIC_TIMEOUT_SECONDS = 8.0


class PublicClient:
    """Unauthenticated HTTP, for probing the public endpoints (dc_api, frontend)."""

    def __init__(self, session=None, *, timeout: float = PUBLIC_TIMEOUT_SECONDS):
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        self._timeout = timeout

    def get_text(self, url: str, timeout: float | None = None) -> tuple[int, str]:
        try:
            response = self._session.request("GET", url, timeout=timeout or self._timeout)
        except Exception as exc:
            raise RestError(sanitize(exc)) from None
        return response.status_code, response.text
