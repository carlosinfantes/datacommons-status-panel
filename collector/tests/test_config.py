import pytest

from dc_status.config import ConfigError, load_config

MINIMAL = {
    "DCS_ENV_ID": "staging",
    "DCS_PROJECT_ID": "proj-x",
    "DCS_REGION": "us-central1",
    "DCS_SPANNER_INSTANCE_ID": "inst",
    "DCS_SPANNER_DATABASE_ID": "db",
    "DCS_DATACOMMONS_SERVICE_NAME": "dc",
    "DCS_INGESTION_WORKFLOW_NAME": "wf",
    "DCS_PREPROCESSING_JOB_NAME": "job",
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


def test_env_file_parsing_ignores_comments_and_blank_lines(tmp_path):
    from dc_status.config import load_env_file

    path = tmp_path / "local.env"
    path.write_text("# a comment\n\nDCS_ENV_ID=staging\nDCS_REGION = us-central1 \n")
    assert load_env_file(str(path)) == {"DCS_ENV_ID": "staging", "DCS_REGION": "us-central1"}
