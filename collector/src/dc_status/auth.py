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

"""Who may ask. No header is trusted before it has been verified.

The Terraform module puts IAP in front of the service and grants nobody by
default, so in a correct deployment nothing unauthenticated ever reaches this
process. This module exists for the deployment that is *not* correct: an
operator who flips `enable_iap` off, an over-broad `run.invoker` binding, a copy
of the service stood up by hand. The perimeter stays the control; this is the
backstop behind it.

Authorisation is deliberately not re-implemented here. Once an IAP assertion
verifies, IAP has already decided the caller holds
`roles/iap.httpsResourceAccessor`, and copying that list into an env var would
only let the two drift apart.

There is exactly one door. A bearer ID token is not a way in, whoever minted
it: it proves who the caller is and nothing about what they may do, and a
second door is a second thing to get wrong.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from .config import AuthConfig
from .sanitize import sanitize

# IAP signs its assertions with its own key set and its own issuer, not Google's
# OAuth ones, so both are named explicitly.
_IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
_IAP_ISSUER = "https://cloud.google.com/iap"

_IAP_HEADER = "HTTP_X_GOOG_IAP_JWT_ASSERTION"

_LOG = logging.getLogger(__name__)


class Denied(Exception):
    """No acceptable credential. The reason is logged, never sent to the caller."""


def authorize(environ: Mapping[str, str], config: AuthConfig, verifier=None) -> str:
    """Return the verified caller's email, or raise Denied.

    A request with no IAP assertion never reaches a verifier. Anything else it
    carries, an Authorization header included, is ignored.
    """
    if not config.require:
        return "unverified"

    assertion = environ.get(_IAP_HEADER, "").strip()
    if not assertion:
        raise _deny("no IAP assertion")
    if not config.iap_enabled:
        raise _deny("an IAP assertion arrived but DCS_IAP_AUDIENCE is unset")
    verifier = verifier or _default_verifier()
    payload = _verify(verifier.iap, assertion, config.iap_audience, "IAP assertion")
    return _identity(payload, issuers={_IAP_ISSUER})


def _verify(verify, token: str, audience: str, kind: str) -> Mapping:
    try:
        payload = verify(token, audience)
    except Exception as exc:
        # sanitize, because a verification failure loves to quote the token back.
        raise _deny(f"{kind} failed verification: {sanitize(exc)}") from None
    if not isinstance(payload, Mapping):
        raise _deny(f"{kind} verified to a {type(payload).__name__}, not a claim set")
    return payload


def _identity(payload: Mapping, *, issuers) -> str:
    # verify_token checks signature, expiry and audience but NOT the issuer, so
    # without this an assertion from the wrong signer would pass on a valid
    # signature alone.
    issuer = str(payload.get("iss", ""))
    if issuer not in issuers:
        raise _deny(f"unexpected issuer {issuer!r}")
    email = str(payload.get("email", "")).strip().lower()
    if not email:
        raise _deny("the claim set carries no email")
    return email


def _deny(reason: str) -> Denied:
    """Build the refusal and log it. Every deny path goes through here, so a 403
    is always explained in the logs and never in the response."""
    _LOG.warning("access denied: %s", reason)
    return Denied(reason)


_DEFAULT_VERIFIER = None


def _default_verifier():
    """Real verification, built once per process on first use.

    Isolated behind a function so tests never import google.auth, the same
    reason rest.build_authorized_session is. Two threads racing here would build
    it twice, which is harmless: it holds no state beyond a pooled HTTP session.
    """
    global _DEFAULT_VERIFIER
    if _DEFAULT_VERIFIER is None:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        request = google_requests.Request()

        class _GoogleVerifier:
            def iap(self, token: str, audience: str) -> Mapping:
                return google_id_token.verify_token(
                    token, request, audience=audience, certs_url=_IAP_CERTS_URL
                )

        _DEFAULT_VERIFIER = _GoogleVerifier()
    return _DEFAULT_VERIFIER
