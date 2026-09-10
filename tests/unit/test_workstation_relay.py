"""Auth isolation, fixed routing, and unbuffered A2A response forwarding."""

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

spec = importlib.util.spec_from_file_location(
    "workstation_relay",
    Path(__file__).resolve().parents[2] / "workstation-relay" / "relay.py",
)
relay = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = relay
spec.loader.exec_module(relay)

ENV = {
    "WORKSTATION_RELAY_RESOURCE": "projects/p/locations/r/workstationClusters/c/workstationConfigs/c/workstations/preview",
    "WORKSTATION_RELAY_ORIGIN": "https://18090-preview.cluster.cloudworkstations.dev",
    "WORKSTATION_RELAY_CALLER_SA": "preview@project.iam.gserviceaccount.com",
    "WORKSTATION_RELAY_PORT": "18090",
}


@pytest.mark.parametrize(
    "origin",
    [
        "http://18090-preview.cluster.cloudworkstations.dev",
        "https://attacker.example",
        "https://18091-preview.cluster.cloudworkstations.dev",
        "https://18090-other.cluster.cloudworkstations.dev",
        ENV["WORKSTATION_RELAY_ORIGIN"] + "/path",
        ENV["WORKSTATION_RELAY_ORIGIN"] + "?url=evil",
        "https://user@18090-preview.cluster.cloudworkstations.dev",
    ],
)
def test_rejects_unapproved_origin(origin):
    with pytest.raises(ValueError):
        relay.Settings.from_env({**ENV, "WORKSTATION_RELAY_ORIGIN": origin})


def test_partial_configuration_cannot_start_normal_agent_instead():
    with pytest.raises(ValueError):
        relay.Settings.from_env({})


class Token:
    invalidated = False

    async def get(self):
        return "workstation-secret"

    def invalidate(self):
        self.invalidated = True


class Chunks(httpx.AsyncByteStream):
    def __init__(self):
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in (b'data: {"progress":true}\n\n', b'data: {"final":true}\n\n'):
            self.consumed += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def test_only_a2a_routes_are_exposed_and_headers_are_isolated():
    calls = []

    async def upstream(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer workstation-secret"
        assert request.headers["X-A2A-Extensions"] == "a2ui"
        assert "cookie" not in request.headers
        assert "x-serverless-authorization" not in request.headers
        assert request.url.host == "18090-preview.cluster.cloudworkstations.dev"
        assert await request.aread() == b'{"method":"message/stream"}'
        return httpx.Response(
            200,
            stream=Chunks(),
            headers={
                "Content-Type": "text/event-stream",
                "X-A2A-Extensions": "a2ui",
                "Set-Cookie": "secret=never-forward",
            },
        )

    transport = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = relay.create_app(
        relay.Settings.from_env(ENV), client=transport, token=Token()
    )
    with TestClient(app) as client:
        response = client.post(
            "/a2a/app",
            content=b'{"method":"message/stream"}',
            headers={
                "Authorization": "Bearer caller-secret",
                "Cookie": "user-session=secret",
                "X-Serverless-Authorization": "secret",
                "X-A2A-Extensions": "a2ui",
            },
        )
        assert response.status_code == 200
        assert response.headers["X-A2A-Extensions"] == "a2ui"
        assert response.headers["X-Pixelpitch-Execution"] == "cloud-workstation-relay"
        assert "set-cookie" not in response.headers
        assert b'"final":true' in response.content
        assert client.get("/dev-ui/").status_code == 404
        assert client.get("/healthz").json()["upstream_checked"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("status", [302, 401, 403])
def test_auth_failure_does_not_redirect_replay_or_expose_tokens(status):
    calls, token = [], Token()

    async def upstream(request):
        calls.append(request)
        return httpx.Response(
            status,
            content=b"workstation-secret",
            headers={"Location": "https://accounts.google.com"},
        )

    transport = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    with TestClient(
        relay.create_app(relay.Settings.from_env(ENV), client=transport, token=token)
    ) as client:
        response = client.post("/a2a/app", json={})
    assert response.status_code == 503
    assert "location" not in response.headers
    assert "workstation-secret" not in response.text
    assert len(calls) == 1
    assert token.invalidated


@pytest.mark.asyncio
async def test_first_chunk_is_returned_before_upstream_finishes_and_close_releases_it():
    chunks = Chunks()

    async def upstream(request):
        return httpx.Response(200, stream=chunks)

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "https",
        "path": "/a2a/app",
        "query_string": b"",
        "headers": [],
        "server": ("relay.run.app", 443),
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        forwarding = relay.Relay(relay.Settings.from_env(ENV), client, Token())
        response = await forwarding.forward(Request(scope, receive))
        assert chunks.consumed == 0
        assert await anext(response.body_iterator) == b'data: {"progress":true}\n\n'
        assert chunks.consumed == 1
        await response.body_iterator.aclose()
        assert chunks.closed


@pytest.mark.asyncio
async def test_parallel_requests_share_one_token_refresh(monkeypatch):
    token = relay.WorkstationToken(relay.Settings.from_env(ENV))
    calls = []

    def mint():
        calls.append(True)
        return "short-lived", time.time() + 800

    monkeypatch.setattr(token, "_mint", mint)
    assert (
        await asyncio.gather(token.get(), token.get(), token.get())
        == ["short-lived"] * 3
    )
    assert len(calls) == 1
    token.invalidate()
    await token.get()
    assert len(calls) == 2
