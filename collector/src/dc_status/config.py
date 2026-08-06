"""Configuration from the environment. Twelve-factor, so Cloud Run just works."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

_PREFIX = "DCS_"

_REQUIRED = (
    "ENV_ID",
    "PROJECT_ID",
    "REGION",
    "SPANNER_INSTANCE_ID",
    "SPANNER_DATABASE_ID",
    "DATACOMMONS_SERVICE_NAME",
    "INGESTION_WORKFLOW_NAME",
    "ARTIFACTS_BUCKET_NAME",
    "PUBLIC_ENDPOINT_URL",
    "FRONTEND_URL",
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class PeerConfig:
    id: str
    label: str
    url: str


@dataclass(frozen=True)
class EnvConfig:
    env_id: str
    env_label: str
    project_id: str
    region: str
    spanner_instance_id: str
    spanner_database_id: str
    datacommons_service_name: str
    ingestion_workflow_name: str
    artifacts_bucket_name: str
    public_endpoint_url: str
    frontend_url: str
    data_source_prefixes: tuple[str, ...]
    input_prefix: str
    peers: tuple[PeerConfig, ...]
    counts_cache_ttl_seconds: int
    schema_cache_ttl_seconds: int


def load_config(environ: Mapping[str, str]) -> EnvConfig:
    missing = [f"{_PREFIX}{key}" for key in _REQUIRED if not environ.get(f"{_PREFIX}{key}")]
    if missing:
        raise ConfigError("missing required configuration: " + ", ".join(missing))

    def value(key: str, default: str = "") -> str:
        return (environ.get(f"{_PREFIX}{key}") or default).strip()

    def int_value(key: str, default: int) -> int:
        raw = value(key)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"{_PREFIX}{key} must be an integer, got {raw!r}") from None

    env_id = value("ENV_ID")
    return EnvConfig(
        env_id=env_id,
        env_label=value("ENV_LABEL") or env_id,
        project_id=value("PROJECT_ID"),
        region=value("REGION"),
        spanner_instance_id=value("SPANNER_INSTANCE_ID"),
        spanner_database_id=value("SPANNER_DATABASE_ID"),
        datacommons_service_name=value("DATACOMMONS_SERVICE_NAME"),
        ingestion_workflow_name=value("INGESTION_WORKFLOW_NAME"),
        artifacts_bucket_name=value("ARTIFACTS_BUCKET_NAME"),
        public_endpoint_url=value("PUBLIC_ENDPOINT_URL"),
        frontend_url=value("FRONTEND_URL"),
        data_source_prefixes=_split_list(value("DATA_SOURCE_PREFIXES")),
        input_prefix=value("INPUT_PREFIX") or "ingestion/input/",
        peers=_parse_peers(value("PEERS")),
        counts_cache_ttl_seconds=int_value("COUNTS_CACHE_TTL_SECONDS", 300),
        schema_cache_ttl_seconds=int_value("SCHEMA_CACHE_TTL_SECONDS", 3600),
    )


def _split_list(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _parse_peers(raw: str) -> tuple[PeerConfig, ...]:
    if not raw:
        return ()
    try:
        entries = json.loads(raw)
    except ValueError as exc:
        raise ConfigError(f"{_PREFIX}PEERS is not valid JSON: {exc}") from None
    if not isinstance(entries, list):
        raise ConfigError(f"{_PREFIX}PEERS must be a JSON array")
    peers = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("url"):
            raise ConfigError(f"{_PREFIX}PEERS entries need at least an id and a url")
        peers.append(
            PeerConfig(id=entry["id"], label=entry.get("label") or entry["id"], url=entry["url"].rstrip("/"))
        )
    return tuple(peers)


def load_env_file(path: str) -> dict[str, str]:
    """Read a KEY=VALUE file. For local runs only; Cloud Run uses real env vars."""
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, raw = stripped.partition("=")
            values[key.strip()] = raw.strip().strip('"').strip("'")
    return values
