import json

from dc_status import cli
from dc_status.model import HEALTHY

MINIMAL_ENV = """
DCS_ENV_ID=staging
DCS_PROJECT_ID=proj-x
DCS_REGION=us-central1
DCS_SPANNER_INSTANCE_ID=inst
DCS_SPANNER_DATABASE_ID=db
DCS_DATACOMMONS_SERVICE_NAME=dc
DCS_INGESTION_WORKFLOW_NAME=wf
DCS_PREPROCESSING_JOB_NAME=job
DCS_ARTIFACTS_BUCKET_NAME=bucket
DCS_PUBLIC_ENDPOINT_URL=https://api.example
DCS_FRONTEND_URL=https://www.example
"""


def test_prints_the_document_as_json(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / "local.env"
    env_file.write_text(MINIMAL_ENV)
    monkeypatch.setattr(cli, "build_clients", lambda _config: object())
    monkeypatch.setattr(
        cli,
        "collect_self",
        lambda config, clients, cache: {"overall": HEALTHY, "environments": [{"id": config.env_id}]},
    )

    assert cli.main(["--env-file", str(env_file)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["overall"] == HEALTHY
    assert payload["environments"][0]["id"] == "staging"


def test_a_configuration_error_exits_nonzero_with_a_message(tmp_path, capsys):
    env_file = tmp_path / "local.env"
    env_file.write_text("DCS_ENV_ID=staging\n")
    assert cli.main(["--env-file", str(env_file)]) == 2
    assert "DCS_PROJECT_ID" in capsys.readouterr().err
