"""Exercise actual MCP SDK parsing, resource metadata, and tools over ASGI."""

import time

import pytest
from starlette.testclient import TestClient

pytest.importorskip("mcp")

from app.mcp_server import UI_MIME, UI_URI, create_app


@pytest.fixture
def client(tmp_path):
    html = tmp_path / "index.html"
    html.write_text("<!doctype html><title>Pixelpitch test</title>")
    with TestClient(
        create_app(tmp_path / "jobs.sqlite", html_path=html),
        base_url="http://127.0.0.1:18091",
    ) as client:
        yield client


def rpc(client, method, params):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def call(client, name, arguments=None):
    return rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})


def test_open_has_no_catalog_dependency_and_advertises_the_app(client, monkeypatch):
    def forbidden(*_, **__):
        raise AssertionError("Opening the UI must not load catalogs or generate")

    monkeypatch.setattr("app.tools.list_brands", forbidden)
    monkeypatch.setattr("app.tools.list_templates", forbidden)
    started = time.monotonic()
    opened = call(client, "open_pixelpitch", {"topic": "Retail outlook"})
    assert time.monotonic() - started < 1
    assert opened["structuredContent"]["topic"] == "Retail outlook"
    assert "capability" not in str(opened["content"])
    tools = rpc(client, "tools/list", {})["tools"]
    assert tools[0]["_meta"]["ui"]["resourceUri"] == UI_URI
    assert all(tool["_meta"]["ui"]["visibility"] == ["app"] for tool in tools[1:])
    resource = rpc(client, "resources/read", {"uri": UI_URI})["contents"][0]
    assert resource["mimeType"] == UI_MIME
    assert resource["_meta"]["ui"]["csp"]["connectDomains"] == []


def test_submit_status_retry_and_cross_workspace_denial(client):
    a = call(client, "open_pixelpitch")["_meta"]["pixelpitch"]
    b = call(client, "open_pixelpitch")["_meta"]["pixelpitch"]
    args = {
        **a,
        "request_id": "click",
        "brief": {"topic": "Retail", "brand_id": "stripe"},
    }
    first = call(client, "start_deck", args)["structuredContent"]["job"]
    assert first["status"] == "queued"
    assert (
        call(client, "start_deck", args)["structuredContent"]["job"]["id"]
        == first["id"]
    )
    assert (
        call(client, "deck_status", a)["structuredContent"]["job"]["id"] == first["id"]
    )
    assert call(client, "deck_status", {**b, "job_id": first["id"]})["isError"]
    assert call(client, "cancel_deck", {**b, "job_id": first["id"]})["isError"]
    assert (
        call(client, "cancel_deck", {**a, "job_id": first["id"]})["structuredContent"][
            "job"
        ]["status"]
        == "cancelled"
    )


def test_dns_rebinding_and_foreign_origin_are_rejected(client):
    response = client.post("/mcp", json={}, headers={"Host": "evil.example"})
    assert response.status_code == 421
    response = client.post("/mcp", json={}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
