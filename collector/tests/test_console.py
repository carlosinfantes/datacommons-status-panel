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

from dc_status.console import console_url, run_metrics_url, spanner_monitoring_url
from dc_status.model import DIMENSION_OF


class _Where:
    project_id = "example-project"
    region = "us-central1"
    spanner_instance_id = "example-instance"
    spanner_database_id = "example-db"
    datacommons_service_name = "example-dc-service"
    ingestion_workflow_name = "example-ingestion"
    artifacts_bucket_name = "example-artifacts"
    input_prefix = "ingestion/input/"


_BASE = "https://console.cloud.google.com"


@pytest.mark.parametrize(
    ("probe_id", "expected"),
    [
        (
            "dc_api",
            f"{_BASE}/run/detail/us-central1/example-dc-service/metrics?project=example-project",
        ),
        (
            "dc_service",
            f"{_BASE}/run/detail/us-central1/example-dc-service/revisions?project=example-project",
        ),
        (
            "spanner",
            f"{_BASE}/spanner/instances/example-instance/details/databases?project=example-project",
        ),
        (
            "schema",
            f"{_BASE}/spanner/instances/example-instance/databases/example-db/details/tables?project=example-project",
        ),
        ("version_consistency", None),
        ("frontend", None),
        (
            "errors",
            f"{_BASE}/run/detail/us-central1/example-dc-service/metrics?project=example-project",
        ),
        (
            "counts",
            f"{_BASE}/spanner/instances/example-instance/databases/example-db/details/tables?project=example-project",
        ),
        (
            "data_sources",
            f"{_BASE}/storage/browser/example-artifacts/ingestion/input?project=example-project",
        ),
        ("row_drift", None),
        (
            "ingestions",
            f"{_BASE}/workflows/workflow/us-central1/example-ingestion/executions?project=example-project",
        ),
        (
            "pending_uploads",
            f"{_BASE}/storage/browser/example-artifacts/ingestion/input?project=example-project",
        ),
    ],
)
def test_each_probe_links_to_the_most_specific_console_page(probe_id, expected):
    assert console_url(probe_id, _Where()) == expected


def test_every_probe_in_a_dimension_has_a_decision():
    # A new probe added to a dimension without a console decision fails here
    # instead of silently rendering no link.
    for probe_id in DIMENSION_OF:
        console_url(probe_id, _Where())


def test_saturation_can_point_at_either_resource():
    assert run_metrics_url(_Where()).endswith(
        "/run/detail/us-central1/example-dc-service/metrics?project=example-project"
    )
    assert spanner_monitoring_url(_Where()) == (
        f"{_BASE}/spanner/instances/example-instance/details/monitoring?project=example-project"
    )
