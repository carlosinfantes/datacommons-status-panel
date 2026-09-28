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

import pytest

from dc_status.config import ConfigError, load_auth_config, load_config

MINIMAL = {
    "DCS_ENV_ID": "staging",
    "DCS_PROJECT_ID": "proj-x",
    "DCS_REGION": "us-central1",
    "DCS_SPANNER_INSTANCE_ID": "inst",
    "DCS_SPANNER_DATABASE_ID": "db",
    "DCS_DATACOMMONS_SERVICE_NAME": "dc",
    "DCS_INGESTION_WORKFLOW_NAME": "wf",
    "DCS_ARTIFACTS_BUCKET_NAME": "bucket",
    "DCS_PUBLIC_ENDPOINT_URL": "https://api.example",
    "DCS_FRONTEND_URL": "https://www.example",
}


def test_loads_the_minimal_configuration_with_defaults():
    config = load_config(MINIMAL)
    assert config.env_id == "staging"
    assert config.env_label == "staging"  # defaults to the id
    assert config.data_source_prefixes == ()
    assert not hasattr(config, "peers")
    assert config.input_prefix == "ingestion/input/"
    assert config.counts_cache_ttl_seconds == 300
    assert config.schema_cache_ttl_seconds == 3600


def test_reports_every_missing_key_at_once():
    with pytest.raises(ConfigError) as excinfo:
        load_config({"DCS_ENV_ID": "staging"})
    message = str(excinfo.value)
    assert "DCS_PROJECT_ID" in message
    assert "DCS_SPANNER_INSTANCE_ID" in message


def test_parses_a_comma_separated_prefix_list_and_trims_it():
    config = load_config({**MINIMAL, "DCS_DATA_SOURCE_PREFIXES": " agency-a , agency-b ,"})
    assert config.data_source_prefixes == ("agency-a", "agency-b")


def test_a_malformed_ttl_is_a_config_error_not_a_traceback():
    with pytest.raises(ConfigError) as excinfo:
        load_config({**MINIMAL, "DCS_COUNTS_CACHE_TTL_SECONDS": "soon"})
    assert "DCS_COUNTS_CACHE_TTL_SECONDS" in str(excinfo.value)


def test_env_file_parsing_ignores_comments_and_blank_lines(tmp_path):
    from dc_status.config import load_env_file

    path = tmp_path / "local.env"
    path.write_text("# a comment\n\nDCS_ENV_ID=staging\nDCS_REGION = us-central1 \n")
    assert load_env_file(str(path)) == {"DCS_ENV_ID": "staging", "DCS_REGION": "us-central1"}


def test_access_is_required_by_default():
    config = load_auth_config({"DCS_IAP_AUDIENCE": "/projects/1/apps/p"})
    assert config.require is True
    assert config.iap_enabled is True


def test_requiring_access_with_no_usable_credential_is_refused():
    # A panel nobody can reach is a misconfiguration, not an access problem, and
    # it should fail loudly once instead of 403ing every admin who tries.
    with pytest.raises(ConfigError) as caught:
        load_auth_config({})
    assert "DCS_IAP_AUDIENCE" in str(caught.value)


def test_access_can_be_switched_off_explicitly():
    config = load_auth_config({"DCS_REQUIRE_AUTH": "false"})
    assert config.require is False


def test_a_non_boolean_require_auth_is_refused():
    with pytest.raises(ConfigError):
        load_auth_config({"DCS_REQUIRE_AUTH": "maybe"})


def test_the_retired_machine_door_cannot_stand_in_for_iap():
    # DCS_ALLOWED_CALLERS and DCS_SELF_AUDIENCE belonged to peer fan-in, which is
    # gone. Setting them must not count as a usable credential.
    with pytest.raises(ConfigError):
        load_auth_config(
            {
                "DCS_SELF_AUDIENCE": "https://panel.example",
                "DCS_ALLOWED_CALLERS": "one@x.iam.gserviceaccount.com",
            }
        )


def test_canary_signals_window_and_targets_default_to_the_documented_values():
    config = load_config(MINIMAL)
    assert config.canary_node == "country/GTM"
    assert config.canary_name == "Guatemala"
    assert config.signals_window_minutes == 60
    assert config.targets.to_dict() == {
        "availability_pct": 99.5,
        "latency_p95_ms": 1000,
        "run_cpu_pct": 80,
        "run_memory_pct": 80,
        "spanner_cpu_pct": 65,
        "min_requests_per_hour": 100,
        "ingestion_max_age_hours": None,
        "max_row_drop_pct": 10,
    }


def test_whole_number_targets_are_emitted_as_integers():
    # The document is read by people as well as by the page: "1000" reads as a
    # threshold, "1000.0" reads as a measurement.
    emitted = load_config(MINIMAL).targets.to_dict()
    assert isinstance(emitted["latency_p95_ms"], int)
    assert isinstance(emitted["availability_pct"], float)


def test_every_target_can_be_overridden():
    config = load_config(
        {
            **MINIMAL,
            "DCS_CANARY_NODE": "country/FRA",
            "DCS_CANARY_NAME": "France",
            "DCS_SIGNALS_WINDOW_MINUTES": "30",
            "DCS_TARGET_AVAILABILITY_PCT": "99.9",
            "DCS_TARGET_LATENCY_P95_MS": "750",
            "DCS_TARGET_RUN_CPU_PCT": "70",
            "DCS_TARGET_RUN_MEMORY_PCT": "75",
            "DCS_TARGET_SPANNER_CPU_PCT": "45",
            "DCS_TARGET_MIN_REQUESTS_PER_HOUR": "0",
            "DCS_TARGET_INGESTION_MAX_AGE_HOURS": "36",
            "DCS_TARGET_MAX_ROW_DROP_PCT": "2.5",
        }
    )
    assert (config.canary_node, config.canary_name) == ("country/FRA", "France")
    assert config.signals_window_minutes == 30
    targets = config.targets
    assert targets.availability_pct == 99.9
    assert targets.latency_p95_ms == 750
    assert targets.run_cpu_pct == 70
    assert targets.run_memory_pct == 75
    assert targets.spanner_cpu_pct == 45
    assert targets.min_requests_per_hour == 0
    assert targets.ingestion_max_age_hours == 36
    assert targets.max_row_drop_pct == 2.5


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("TARGET_AVAILABILITY_PCT", "100.1"),
        ("TARGET_AVAILABILITY_PCT", "-1"),
        ("TARGET_RUN_CPU_PCT", "101"),
        ("TARGET_RUN_MEMORY_PCT", "nan"),
        ("TARGET_SPANNER_CPU_PCT", "-0.5"),
        ("TARGET_MAX_ROW_DROP_PCT", "150"),
        ("TARGET_LATENCY_P95_MS", "-5"),
        ("TARGET_LATENCY_P95_MS", "inf"),
        ("TARGET_MIN_REQUESTS_PER_HOUR", "-1"),
        ("TARGET_INGESTION_MAX_AGE_HOURS", "-2"),
        ("TARGET_AVAILABILITY_PCT", "high"),
        ("SIGNALS_WINDOW_MINUTES", "0"),
        ("SIGNALS_WINDOW_MINUTES", "-10"),
        ("SIGNALS_WINDOW_MINUTES", "1441"),
        ("SIGNALS_WINDOW_MINUTES", "an hour"),
    ],
)
def test_an_out_of_range_or_malformed_setting_names_its_variable(key, raw):
    with pytest.raises(ConfigError) as caught:
        load_config({**MINIMAL, f"DCS_{key}": raw})
    assert f"DCS_{key}" in str(caught.value)


def test_an_empty_max_age_means_shown_but_not_judged():
    config = load_config({**MINIMAL, "DCS_TARGET_INGESTION_MAX_AGE_HOURS": ""})
    assert config.targets.ingestion_max_age_hours is None
