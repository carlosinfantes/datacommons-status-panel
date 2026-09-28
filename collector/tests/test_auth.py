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

from dc_status.auth import Denied, authorize
from dc_status.config import AuthConfig

_IAP_ISSUER = "https://cloud.google.com/iap"
_GOOGLE_ISSUER = "https://accounts.google.com"


class RecordingVerifier:
    """Accepts whatever it is given, and records the audience it was asked for.

    The audience is the whole point of the check, so a test that never asserts on
    it would pass against a verifier that ignored it.
    """

    def __init__(self, iap_payload=None):
        self._iap_payload = iap_payload or {"iss": _IAP_ISSUER, "email": "admin@example.org"}
        self.audiences = []

    def iap(self, _token, audience):
        self.audiences.append(audience)
        return self._iap_payload


class RejectingVerifier:
    def iap(self, token, _audience):
        raise ValueError(f"Could not verify token: {token}")


def _config(**overrides):
    base = dict(require=True, iap_audience="/projects/1/apps/example")
    return AuthConfig(**{**base, **overrides})


def _iap(token="a.b.c"):
    return {"HTTP_X_GOOG_IAP_JWT_ASSERTION": token}


def _bearer(token="a.b.c"):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def test_a_request_with_no_credential_is_denied():
    with pytest.raises(Denied):
        authorize({}, _config(), RecordingVerifier())


def test_a_verified_iap_assertion_yields_the_email():
    assert authorize(_iap(), _config(), RecordingVerifier()) == "admin@example.org"


def test_the_iap_assertion_is_checked_against_the_configured_audience():
    verifier = RecordingVerifier()
    authorize(_iap(), _config(iap_audience="/projects/9/apps/other"), verifier)
    assert verifier.audiences == ["/projects/9/apps/other"]


def test_an_unverifiable_assertion_is_denied():
    with pytest.raises(Denied):
        authorize(_iap(), _config(), RejectingVerifier())


def test_the_denial_does_not_carry_the_token_back():
    # The reason is logged, so it must not quote the credential that failed.
    with pytest.raises(Denied) as caught:
        authorize(
            _iap("eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"),
            _config(),
            RejectingVerifier(),
        )
    assert "eyJhbGciOiJSUzI1NiJ9" not in str(caught.value)


def test_an_assertion_from_the_wrong_issuer_is_denied():
    # verify_token checks the signature and audience but not the issuer, so this
    # is the check that stops a validly-signed token from elsewhere.
    verifier = RecordingVerifier(iap_payload={"iss": _GOOGLE_ISSUER, "email": "admin@example.org"})
    with pytest.raises(Denied):
        authorize(_iap(), _config(), verifier)


def test_an_assertion_without_an_email_is_denied():
    verifier = RecordingVerifier(iap_payload={"iss": _IAP_ISSUER})
    with pytest.raises(Denied):
        authorize(_iap(), _config(), verifier)


def test_an_assertion_is_denied_when_no_iap_audience_is_configured():
    with pytest.raises(Denied):
        authorize(_iap(), _config(iap_audience=""), RecordingVerifier())


def test_a_bearer_token_is_never_a_way_in():
    # The machine door (peer panels presenting an ID token) is gone: IAP is the
    # only way in. A bearer token must not even reach a verifier.
    verifier = RecordingVerifier()
    with pytest.raises(Denied):
        authorize(_bearer(), _config(), verifier)
    assert verifier.audiences == []


def test_a_non_bearer_authorization_header_is_ignored():
    with pytest.raises(Denied):
        authorize(
            {"HTTP_AUTHORIZATION": "Basic YWRtaW46aHVudGVyMg=="}, _config(), RecordingVerifier()
        )


def test_an_iap_assertion_is_honoured_even_alongside_a_bearer_token():
    verifier = RecordingVerifier()
    assert authorize({**_iap(), **_bearer()}, _config(), verifier) == "admin@example.org"


def test_a_claim_set_that_is_not_a_mapping_is_denied():
    class Weird:
        def iap(self, _token, _audience):
            return "not-a-claim-set"

    with pytest.raises(Denied):
        authorize(_iap(), _config(), Weird())


def test_verification_is_skipped_entirely_when_it_is_switched_off():
    def explode(*_args, **_kwargs):
        raise AssertionError("no verifier should be built when require is false")

    class Exploding:
        iap = explode

    assert authorize({}, _config(require=False), Exploding()) == "unverified"
