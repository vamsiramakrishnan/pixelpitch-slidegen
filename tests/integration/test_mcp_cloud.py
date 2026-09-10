"""Production MCP middleware through the real SDK and ASGI transport."""

import httpx
import pytest
from starlette.testclient import TestClient

pytest.importorskip("google.cloud.firestore")
pytest.importorskip("mcp")

from app.mcp_auth import GoogleTokenVerifier
from app.mcp_cloud import CloudConfig
from app.mcp_jobs import JobStore
from app.mcp_server import create_app

CLIENT = "123-pixelpitch.apps.googleusercontent.com"
BASE = "https://pixelpitch-mcp-123.us-central1.run.app"


@pytest.fixture
def cloud(tmp_path):
    import time

    def handler(request):
        who = request.content.decode().partition("=")[2]
        return httpx.Response(
            200,
            json={
                "aud": CLIENT,
                "azp": CLIENT,
                "sub": who,
                "scope": "openid",
                "expires_in": "300",
                "exp": str(int(time.time()) + 300),
                "email": "user@example.com",
                "email_verified": True,
            },
        )

    html = tmp_path / "index.html"
    html.write_text("<!doctype html><title>Cloud fixture</title>")
    verifier = GoogleTokenVerifier(
        CLIENT,
        frozenset({"example.com"}),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    store = JobStore(tmp_path / "jobs.sqlite")
    with TestClient(
        create_app(store=store, html_path=html, public_url=BASE, verifier=verifier),
        base_url=BASE,
    ) as client:
        yield client


def call(client, user, name, args=None):
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        },
        headers={
            "Authorization": f"Bearer {user}",
            "Accept": "application/json, text/event-stream",
        },
    )


def test_cloud_requires_the_user_token_not_the_service_token(cloud):
    assert (
        cloud.post(
            "/mcp",
            json={},
            headers={"X-Serverless-Authorization": "Bearer service-token"},
        ).status_code
        == 401
    )
    assert cloud.get("/health").json()["mode"] == "cloud"
    assert cloud.get("/", headers={"Authorization": "Bearer alice"}).status_code == 404


def test_stolen_capability_does_not_cross_authenticated_users(cloud):
    a = call(cloud, "alice", "open_pixelpitch").json()["result"]["_meta"]["pixelpitch"]
    b = call(cloud, "bob", "open_pixelpitch").json()["result"]["_meta"]["pixelpitch"]
    assert (
        call(cloud, "alice", "deck_status", a).json()["result"]["structuredContent"][
            "job"
        ]
        is None
    )
    assert call(cloud, "bob", "deck_status", a).json()["result"]["isError"]
    assert call(cloud, "alice", "deck_status", b).json()["result"]["isError"]
    assert call(
        cloud,
        "bob",
        "start_deck",
        {**a, "request_id": "stolen", "brief": {"topic": "No", "brand_id": "stripe"}},
    ).json()["result"]["isError"]


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "https://evil.example",
        "https://a.run.app/",
        "https://user@a.run.app",
        "https://a.run.app?secret=1",
    ],
)
def test_invalid_cloud_origin_cannot_start(url):
    with pytest.raises(ValueError):
        CloudConfig(
            role="api",
            project="pixelpitch-test",
            database="pixelpitch-mcp",
            bucket="pixelpitch-test-drafts",
            artifact_bucket="pixelpitch-test-decks",
            renderer_url="https://renderer.run.app",
            public_url=url,
            oauth_client_id=CLIENT,
            allowed_domains={"example.com"},
        )


def test_cloud_configuration_requires_separate_buckets():
    config = {
        "role": "api",
        "project": "pixelpitch-test",
        "database": "pixelpitch-mcp",
        "bucket": "pixelpitch-test-drafts",
        "artifact_bucket": "pixelpitch-test-decks",
        "renderer_url": "https://renderer.run.app",
        "public_url": BASE,
        "oauth_client_id": CLIENT,
        "allowed_domains": {"example.com"},
    }
    assert CloudConfig(**config).role == "api"
    with pytest.raises(ValueError, match="dedicated bucket"):
        CloudConfig(**{**config, "artifact_bucket": config["bucket"]})


def test_production_entrypoint_rejects_emulator_configuration(monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:12345")
    with pytest.raises(ValueError, match="emulated"):
        CloudConfig.from_env()
