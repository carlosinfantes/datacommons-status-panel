"""Server-to-server fan-out: ask a peer for its own document."""

from __future__ import annotations

from .config import PeerConfig
from .rest import RestClient
from .sanitize import sanitize


def _default_token_fetcher(audience: str) -> str:
    """An ID token from the metadata server, minted for the peer's audience."""
    import google.oauth2.id_token
    from google.auth.transport.requests import Request

    return google.oauth2.id_token.fetch_id_token(Request(), audience)


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
