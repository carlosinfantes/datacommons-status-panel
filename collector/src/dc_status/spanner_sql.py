"""The only place that knows Spanner's REST shape."""

from __future__ import annotations

from .rest import RestClient, RestError

_BASE = "https://spanner.googleapis.com/v1"
_INT_CODES = frozenset({"INT64"})


class SpannerSQL:
    def __init__(self, rest: RestClient, project_id: str, instance_id: str, database_id: str):
        self._rest = rest
        self._database = (
            f"projects/{project_id}/instances/{instance_id}/databases/{database_id}"
        )
        self._session: str | None = None

    def query(self, sql: str, *, staleness_seconds: int = 10, timeout: float = 8.0) -> list[dict]:
        body = {"sql": sql, "transaction": _transaction(staleness_seconds)}
        try:
            payload = self._execute(body, timeout)
        except RestError as exc:
            if exc.status != 404:
                raise
            self._session = None  # the session expired or was garbage-collected
            payload = self._execute(body, timeout)
        return _decode(payload)

    def close(self) -> None:
        if self._session:
            try:
                self._rest.delete(f"{_BASE}/{self._session}")
            except RestError:
                pass
            self._session = None

    def _execute(self, body: dict, timeout: float) -> dict:
        session = self._ensure_session()
        return self._rest.post(f"{_BASE}/{session}:executeSql", body=body, timeout=timeout)

    def _ensure_session(self) -> str:
        if self._session is None:
            created = self._rest.post(f"{_BASE}/{self._database}/sessions")
            self._session = created["name"]
        return self._session


def _transaction(staleness_seconds: int) -> dict:
    if staleness_seconds <= 0:
        return {"singleUse": {"readOnly": {"strong": True}}}
    return {"singleUse": {"readOnly": {"exactStaleness": f"{staleness_seconds}s"}}}


def _decode(payload: dict) -> list[dict]:
    fields = payload.get("metadata", {}).get("rowType", {}).get("fields", [])
    names = [field.get("name", f"f{i}") for i, field in enumerate(fields)]
    codes = [field.get("type", {}).get("code", "STRING") for field in fields]
    rows = payload.get("rows", []) or []
    decoded: list[dict] = []
    for row in rows:
        # Trailing NULLs can be absent; pad by the field count, never by len(row).
        padded = list(row) + [None] * (len(names) - len(row))
        decoded.append(
            {name: _value(value, code) for name, code, value in zip(names, codes, padded)}
        )
    return decoded


def _value(value, code: str):
    if value is None:
        return None
    if code in _INT_CODES:
        return int(value)
    return value
