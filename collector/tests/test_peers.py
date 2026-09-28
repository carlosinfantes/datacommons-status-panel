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

from dc_status.config import PeerConfig
from dc_status.peers import fetch_peer
from tests.conftest import FakeResponse, FakeSession

PEER = PeerConfig(id="staging", label="Staging", url="https://staging.example")


def test_sends_an_id_token_minted_for_the_peer_audience():
    session = FakeSession(
        {"/api/v1/self": FakeResponse(payload={"environments": [{"id": "staging"}]})}
    )
    audiences = []

    def token_fetcher(audience):
        audiences.append(audience)
        return "an-id-token"

    document = fetch_peer(PEER, token_fetcher=token_fetcher, session=session)
    assert document["environments"][0]["id"] == "staging"
    assert audiences == ["https://staging.example"]


def test_requests_the_self_endpoint_of_the_peer():
    session = FakeSession({"/api/v1/self": FakeResponse(payload={"environments": []})})
    fetch_peer(PEER, token_fetcher=lambda _a: "t", session=session)
    assert session.calls[0][1] == "https://staging.example/api/v1/self"


def test_a_non_200_from_the_peer_raises():
    session = FakeSession({"/api/v1/self": FakeResponse(status_code=403, payload={})})
    with pytest.raises(RuntimeError):
        fetch_peer(PEER, token_fetcher=lambda _a: "t", session=session)


def test_a_failure_to_mint_a_token_raises():
    def token_fetcher(_audience):
        raise RuntimeError("no metadata server")

    with pytest.raises(RuntimeError):
        fetch_peer(PEER, token_fetcher=token_fetcher, session=FakeSession({}))
