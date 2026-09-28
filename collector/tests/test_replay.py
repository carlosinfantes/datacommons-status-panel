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

import json
from datetime import UTC, datetime
from pathlib import Path

from dc_status.replay import load_replay

DEMO = Path(__file__).parent / "fixtures" / "demo.json"


def test_without_shifting_the_file_is_served_as_is():
    assert load_replay(str(DEMO), shift_time=False) == json.loads(DEMO.read_text())


def test_shifting_moves_every_timestamp_by_the_same_amount():
    # A demo recorded weeks ago should read as current: generated just now,
    # last ingestion two days ago, exactly as it was when it was recorded.
    now = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)  # generated_at + 7 days
    shifted = load_replay(str(DEMO), shift_time=True, now=now)
    assert shifted["generated_at"] == "2026-10-05T13:00:00Z"
    assert shifted["freshness"]["last_success_at"] == "2026-10-03T13:00:00Z"
    assert shifted["ingestions"][0]["creation"] == "2026-10-03T11:45:00Z"
    assert shifted["count_history"][0]["completed_at"] == "2026-08-06T14:15:00Z"
    assert shifted["freshness"]["pending_uploads"][0]["last_updated"] == "2026-10-04T17:00:00Z"
    assert shifted["data_sources"][0]["last_updated"] == "2026-10-04T17:00:00Z"


def test_shifting_leaves_everything_that_is_not_a_timestamp_alone():
    now = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
    original = json.loads(DEMO.read_text())
    shifted = load_replay(str(DEMO), shift_time=True, now=now)
    assert shifted["freshness"]["age_hours"] == original["freshness"]["age_hours"]
    assert shifted["signals"] == original["signals"]
    assert shifted["deployment"] == original["deployment"]
    assert shifted["data_sources"][4]["last_updated"] is None


def test_a_document_without_generated_at_is_served_unshifted(tmp_path):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps({"when": "2026-01-01T00:00:00Z"}))
    assert load_replay(str(path), shift_time=True) == {"when": "2026-01-01T00:00:00Z"}


def test_offsets_and_fractions_survive_a_shift(tmp_path):
    path = tmp_path / "replay.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-01-01T00:00:00Z",
                "a": "2025-12-31T12:00:00.825993Z",
                "b": "2025-12-31T12:00:00+00:00",
            }
        )
    )
    shifted = load_replay(str(path), shift_time=True, now=datetime(2026, 1, 2, 0, 0, tzinfo=UTC))
    assert shifted["a"] == "2026-01-01T12:00:00.825993Z"
    assert shifted["b"] == "2026-01-01T12:00:00+00:00"
