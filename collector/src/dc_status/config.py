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

import math
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


def _plain_number(value: float | None) -> float | int | None:
    # 1000.0 -> 1000, so a threshold reads as a threshold in the document.
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


@dataclass(frozen=True)
class Targets:
    """What each judged figure is compared with.

    These travel inside the document (D5): the page draws exactly the target the
    collector judged against, so what is drawn and what is judged cannot drift.
    No default names a deployment; each is the documented generic starting point.
    """

    availability_pct: float = 99.5
    latency_p95_ms: float = 1000
    run_cpu_pct: float = 80
    run_memory_pct: float = 80
    # Google's recommended ceiling for high-priority CPU on a regional instance.
    # Multi-region instances should use 45.
    spanner_cpu_pct: float = 65
    # Below this rate, errors and latency are shown but not judged (D6): three
    # requests and one 500 is not a 67 % availability incident.
    min_requests_per_hour: float = 100
    # None means the age of the last successful ingestion is shown, not judged.
    # Ingestion cadence is a deployment decision nothing here can guess.
    ingestion_max_age_hours: float | None = None
    max_row_drop_pct: float = 10

    def to_dict(self) -> dict:
        return {
            "availability_pct": _plain_number(self.availability_pct),
            "latency_p95_ms": _plain_number(self.latency_p95_ms),
            "run_cpu_pct": _plain_number(self.run_cpu_pct),
            "run_memory_pct": _plain_number(self.run_memory_pct),
            "spanner_cpu_pct": _plain_number(self.spanner_cpu_pct),
            "min_requests_per_hour": _plain_number(self.min_requests_per_hour),
            "ingestion_max_age_hours": _plain_number(self.ingestion_max_age_hours),
            "max_row_drop_pct": _plain_number(self.max_row_drop_pct),
        }


# (field, variable suffix, kind). "pct" is bounded to 0-100; "min0" is any finite
# non-negative number.
_TARGET_FIELDS = (
    ("availability_pct", "TARGET_AVAILABILITY_PCT", "pct"),
    ("latency_p95_ms", "TARGET_LATENCY_P95_MS", "min0"),
    ("run_cpu_pct", "TARGET_RUN_CPU_PCT", "pct"),
    ("run_memory_pct", "TARGET_RUN_MEMORY_PCT", "pct"),
    ("spanner_cpu_pct", "TARGET_SPANNER_CPU_PCT", "pct"),
    ("min_requests_per_hour", "TARGET_MIN_REQUESTS_PER_HOUR", "min0"),
    ("ingestion_max_age_hours", "TARGET_INGESTION_MAX_AGE_HOURS", "min0"),
    ("max_row_drop_pct", "TARGET_MAX_ROW_DROP_PCT", "pct"),
)

# One point per minute. A day is already 1440 points per series in every
# document; anything longer belongs in Cloud Monitoring, not on this page.
_MAX_SIGNALS_WINDOW_MINUTES = 1440


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
    # The entity dc_api resolves, and the name it must resolve to. The defaults
    # are in the base graph every Data Commons instance serves.
    canary_node: str = "country/GTM"
    canary_name: str = "Guatemala"
    signals_window_minutes: int = 60
    targets: Targets = Targets()


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

    window = int_value("SIGNALS_WINDOW_MINUTES", 60)
    if not 1 <= window <= _MAX_SIGNALS_WINDOW_MINUTES:
        raise ConfigError(
            f"{_PREFIX}SIGNALS_WINDOW_MINUTES must be between 1 and "
            f"{_MAX_SIGNALS_WINDOW_MINUTES}, got {window}"
        )

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
        canary_node=value("CANARY_NODE") or "country/GTM",
        canary_name=value("CANARY_NAME") or "Guatemala",
        signals_window_minutes=window,
        targets=load_targets(environ),
    )


def load_targets(environ: Mapping[str, str]) -> Targets:
    """Read and validate every target. An unset or empty variable keeps its default.

    Validated at start-up rather than at judgement time: a typo in a threshold
    would otherwise surface as a panel that is quietly always green.
    """
    defaults = Targets()
    values: dict[str, float | None] = {}
    for field_name, key, kind in _TARGET_FIELDS:
        raw = _value(environ, key)
        if not raw:
            values[field_name] = getattr(defaults, field_name)
            continue
        name = f"{_PREFIX}{key}"
        try:
            number = float(raw)
        except ValueError:
            raise ConfigError(f"{name} must be a number, got {raw!r}") from None
        # isfinite first: NaN compares false with everything, so a range check
        # alone would wave it through, and inf is no threshold at all.
        if not math.isfinite(number):
            raise ConfigError(f"{name} must be a finite number, got {raw!r}")
        if kind == "pct" and not 0 <= number <= 100:
            raise ConfigError(f"{name} is a percentage and must be between 0 and 100, got {raw}")
        if kind == "min0" and number < 0:
            raise ConfigError(f"{name} must not be negative, got {raw}")
        values[field_name] = number
    return Targets(**values)


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
