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

from datetime import UTC

from dc_status.timestamps import parse_timestamp


def test_parses_microsecond_precision_with_z():
    parsed = parse_timestamp("2026-08-05T14:14:00.825993Z")
    assert parsed.year == 2026 and parsed.minute == 14
    assert parsed.tzinfo == UTC


def test_parses_nanosecond_precision_by_truncating():
    assert parse_timestamp("2026-08-05T14:14:00.825993123Z").microsecond == 825993


def test_parses_without_fractional_seconds():
    assert parse_timestamp("2026-08-04T13:45:13Z").second == 13


def test_returns_none_for_empty_or_malformed_input():
    assert parse_timestamp(None) is None
    assert parse_timestamp("") is None
    assert parse_timestamp("not a timestamp") is None
