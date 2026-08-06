"""Server-to-server fan-out: ask a peer for its own document."""

from __future__ import annotations

from .config import PeerConfig
from .rest import RestClient
from .sanitize import sanitize


def _default_token_fetcher(audience: str, timeout: float = 10.0) -> str:
    """An ID token from the metadata server, minted for the peer's audience.

    google.oauth2.id_token.fetch_id_token(request, audience) takes no timeout
    parameter of its own (checked against the installed google-auth: its
    signature is exactly `(request, audience)`). Some of its internal call
    paths (the OAuth token-endpoint grant, used when a service account key is
    active) call `request(...)` without a timeout at all, which inherits
    transport.requests.Request.__call__'s 120s default. Wrapping the request
    so a timeout is always present bounds that path without needing
    fetch_id_token itself to accept one.
    """
    import google.oauth2.id_token
    from google.auth.transport.requests import Request

    request = Request()

    def bounded_request(*args, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return request(*args, **kwargs)

    return google.oauth2.id_token.fetch_id_token(bounded_request, audience)


def fetch_peer(
    peer: PeerConfig,
    *,
    token_fetcher=None,
    session=None,
    timeout: float = 8.0,
) -> dict:
    """Ask the peer for its own document. The audience is the peer's base URL."""
    fetcher = token_fetcher or _default_token_fetcher
    token = fetcher(peer.url)
    if session is None:
        import requests

        session = requests.Session()
    # The bearer token rides on the session's default headers, so RestClient
    # stays free of any notion of authentication.
    session.headers = {**getattr(session, "headers", {}), "Authorization": f"Bearer {token}"}
    client = RestClient(session, timeout=timeout, retries=1)
    try:
        return client.get(f"{peer.url.rstrip('/')}/api/v1/self")
    except Exception as exc:
        raise RuntimeError(sanitize(exc)) from None
