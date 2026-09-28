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

from dc_status.sanitize import sanitize


def test_passes_short_clean_text_through():
    assert sanitize("database is READY") == "database is READY"


def test_truncates_to_the_limit_with_an_ellipsis():
    out = sanitize("x" * 500)
    assert len(out) == 300
    assert out.endswith("…")


def test_redacts_bearer_tokens():
    out = sanitize("failed: Authorization: Bearer ya29.a0ARrdaM-not-a-real-token")
    assert "ya29" not in out
    assert "[REDACTED]" in out


def test_redacts_api_keys():
    out = sanitize("url=...?key=AIzaSyC-not-a-real-key-000000000000000")
    assert "AIzaSy" not in out
    assert "[REDACTED]" in out


def test_redacts_jwt_shaped_strings():
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
    assert "eyJhbGciOiJSUzI1NiJ9" not in sanitize(f"token was {jwt}")


def test_redacts_private_key_blocks():
    out = sanitize("-----BEGIN PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----")
    assert "MIIEvQ" not in out
    assert "[REDACTED]" in out


def test_redacts_a_bare_token_that_never_says_bearer():
    # Pins the ya29 rule as load-bearing. Without this case the generic bearer
    # rule swallows every tested token on its own, and deleting the ya29 rule
    # would not fail a single test.
    out = sanitize("callback ?access=ya29.a0ARrdaM-not-a-real-token&next=/x")
    assert "ya29" not in out
    assert "[REDACTED]" in out


def test_accepts_non_string_input():
    assert sanitize(ValueError("boom")) == "boom"
    assert sanitize(None) == ""


def test_collapses_newlines_and_whitespace():
    assert sanitize("line one\n\n   line two") == "line one line two"
