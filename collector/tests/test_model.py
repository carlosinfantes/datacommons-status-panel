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

from dc_status.model import (
    DEGRADED,
    DOWN,
    HEALTHY,
    REQUIRED_TABLES,
    TABLE_ALLOWLIST,
    UNKNOWN,
    Probe,
    minor_of,
    parse_image_version,
    worst,
)


def test_probe_serializes_with_a_sanitized_detail():
    probe = Probe(id="dc_api", status=HEALTHY, detail="Bearer ya29.leaked", elapsed_ms=12)
    payload = probe.to_dict()
    assert payload["id"] == "dc_api"
    assert payload["status"] == HEALTHY
    assert "ya29" not in payload["detail"]
    assert payload["elapsed_ms"] == 12
    assert payload["data"] == {}


def test_probe_serializes_its_dimension_and_console_link_in_document_order():
    probe = Probe(id="errors", status=HEALTHY, console_url="https://console.cloud.google.com/x")
    payload = probe.to_dict()
    assert list(payload) == [
        "id",
        "dimension",
        "status",
        "detail",
        "elapsed_ms",
        "budget_ms",
        "console_url",
        "data",
    ]
    assert payload["dimension"] == "experience"
    assert payload["console_url"] == "https://console.cloud.google.com/x"


def test_worst_of_all_healthy_is_healthy():
    assert worst([HEALTHY, HEALTHY]) == HEALTHY


def test_worst_prefers_down():
    assert worst([HEALTHY, DEGRADED, DOWN]) == DOWN


def test_unknown_aggregates_as_degraded_never_as_down():
    assert worst([HEALTHY, UNKNOWN]) == DEGRADED
    assert worst([UNKNOWN, UNKNOWN]) == DEGRADED
    assert worst([UNKNOWN, DOWN]) == DOWN


def test_worst_of_nothing_is_unknown():
    assert worst([]) == UNKNOWN


def test_parses_the_version_from_a_digest_pinned_image():
    image = "example.invalid/datacommons-services:1.1.1@sha256:" + "4" * 64
    assert parse_image_version(image) == "1.1.1"


def test_parses_the_version_from_a_plain_tag():
    assert parse_image_version("gcr.io/x/datacommons-services:1.1.1") == "1.1.1"


def test_non_semver_tags_are_not_versions():
    assert parse_image_version("gcr.io/x/y:stable") is None
    assert parse_image_version("gcr.io/x/y:latest") is None


def test_digest_only_image_has_no_version():
    assert parse_image_version("gcr.io/x/y@sha256:" + "a" * 64) is None


def test_allowlist_is_the_verified_v111_schema():
    assert (
        frozenset(
            {
                "Edge",
                "ImportStatus",
                "ImportVersionHistory",
                "IngestionHistory",
                "IngestionLock",
                "KeyValueStore",
                "Node",
                "NodeEmbedding",
                "Observation",
                "TimeSeries",
            }
        )
        == TABLE_ALLOWLIST
    )


def test_required_tables_reference_known_tables_plus_the_legacy_cache():
    # Asserted per version rather than as one loop with an `or`: a disjunction
    # would pass whichever branch held, and stop telling us which.
    assert REQUIRED_TABLES["1.1"] <= TABLE_ALLOWLIST
    # 1.0 names Cache, which no live environment has any more. It stays outside
    # the allowlist on purpose, so no SQL can ever be built from it.
    assert REQUIRED_TABLES["1.0"] - TABLE_ALLOWLIST == {"Cache"}


def test_minor_of_drops_the_patch_level():
    assert minor_of("1.1.1") == "1.1"
    assert minor_of("1.1") == "1.1"
    assert minor_of("2") is None
    assert minor_of(None) is None
