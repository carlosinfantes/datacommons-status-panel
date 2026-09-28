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
