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
    assert config.peers == ()
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


def test_parses_peers_from_json():
    peers = '[{"id":"prod","label":"Production","url":"https://prod.example"}]'
    config = load_config({**MINIMAL, "DCS_PEERS": peers})
    assert config.peers[0].id == "prod"
    assert config.peers[0].url == "https://prod.example"


def test_malformed_peer_json_is_a_config_error():
    with pytest.raises(ConfigError):
        load_config({**MINIMAL, "DCS_PEERS": "not json"})


def test_a_peer_missing_a_url_is_a_config_error():
    with pytest.raises(ConfigError):
        load_config({**MINIMAL, "DCS_PEERS": '[{"id":"prod"}]'})


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
    assert config.callers_enabled is False


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


def test_the_caller_allowlist_is_parsed_and_lowercased():
    config = load_auth_config({
        "DCS_SELF_AUDIENCE": "https://panel.example/",
        "DCS_ALLOWED_CALLERS": "One@x.iam.gserviceaccount.com, two@x.iam.gserviceaccount.com",
    })
    assert config.allowed_callers == {
        "one@x.iam.gserviceaccount.com",
        "two@x.iam.gserviceaccount.com",
    }
    # The peers mint tokens against a base URL, so a trailing slash would make
    # every audience comparison fail.
    assert config.self_audience == "https://panel.example"
    assert config.callers_enabled is True


def test_an_allowlist_without_an_audience_cannot_be_enforced():
    with pytest.raises(ConfigError):
        load_auth_config({"DCS_ALLOWED_CALLERS": "one@x.iam.gserviceaccount.com"})
