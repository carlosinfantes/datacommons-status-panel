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

"""Configuration from the environment. Twelve-factor, so Cloud Run just works."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

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


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


class ConfigError(Exception):
    pass


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
    counts_cache_ttl_seconds: int
    schema_cache_ttl_seconds: int


@dataclass(frozen=True)
class AuthConfig:
    """Who may read the document. IAP is the only way in.

    `iap_audience` names the IAP-protected resource this service sits behind: a
    request carrying an IAP assertion for that audience is a human IAP has
    already authorised. It is not derived, because it cannot be: it is a property
    of the deployed backend service, known only once IAP is in front of it.
    """

    require: bool
    iap_audience: str

    @property
    def iap_enabled(self) -> bool:
        return bool(self.iap_audience)


def load_auth_config(environ: Mapping[str, str]) -> AuthConfig:
    raw = _value(environ, "REQUIRE_AUTH").lower()
    if raw in _FALSE:
        require = False
    elif raw in _TRUE or not raw:
        require = True  # the default is closed
    else:
        raise ConfigError(f"{_PREFIX}REQUIRE_AUTH must be a boolean, got {raw!r}")

    config = AuthConfig(require=require, iap_audience=_value(environ, "IAP_AUDIENCE"))
    if require and not config.iap_enabled:
        # Failing here rather than 403ing every request: a panel nobody can reach
        # is a misconfiguration, and it should say so once instead of looking
        # like an access problem to every admin who tries.
        raise ConfigError(
            f"{_PREFIX}REQUIRE_AUTH is on but {_PREFIX}IAP_AUDIENCE is unset, so no "
            f"request could ever be accepted. Set {_PREFIX}IAP_AUDIENCE to the "
            f"audience of the IAP-protected backend. Set {_PREFIX}REQUIRE_AUTH=false "
            f"only where the perimeter is the only control, such as a local run."
        )
    return config


def _value(environ: Mapping[str, str], key: str, default: str = "") -> str:
    return (environ.get(f"{_PREFIX}{key}") or default).strip()


def load_config(environ: Mapping[str, str]) -> EnvConfig:
    missing = [f"{_PREFIX}{key}" for key in _REQUIRED if not environ.get(f"{_PREFIX}{key}")]
    if missing:
        raise ConfigError("missing required configuration: " + ", ".join(missing))

    def value(key: str, default: str = "") -> str:
        return _value(environ, key, default)

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
        counts_cache_ttl_seconds=int_value("COUNTS_CACHE_TTL_SECONDS", 300),
        schema_cache_ttl_seconds=int_value("SCHEMA_CACHE_TTL_SECONDS", 3600),
    )


def _split_list(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


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
