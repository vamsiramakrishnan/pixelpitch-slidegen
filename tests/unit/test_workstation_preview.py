"""The experiment must not broaden IAM or mistake progress for a usable brief."""

import argparse
import copy
import json

import pytest

from scripts import workstation_preview as preview


def test_port_policy_preserves_other_users_conditions_and_etag():
    original = {
        "version": 3,
        "etag": "unchanged-etag",
        "bindings": [
            {
                "role": "roles/workstations.user",
                "members": ["user:owner@example.com"],
                "condition": {
                    "title": "existing",
                    "expression": "destination.port == 80",
                },
            }
        ],
    }
    before = copy.deepcopy(original)
    result = preview.port_policy(
        original, "preview@project.iam.gserviceaccount.com", 18090
    )
    assert original == before
    assert result["etag"] == before["etag"]
    assert result["bindings"][0] == before["bindings"][0]
    assert (
        result["bindings"][1]["condition"]["expression"] == "destination.port == 18090"
    )
    assert (
        preview.port_policy(result, "preview@project.iam.gserviceaccount.com", 18090)
        == result
    )


@pytest.mark.parametrize("port", ["80", "10000", "65536"])
def test_low_or_invalid_ports_rejected(port):
    with pytest.raises(argparse.ArgumentTypeError):
        preview.port_number(port)


def test_tokens_cannot_be_forwarded_to_different_origins():
    base = "https://18090-example.cloudworkstations.dev"
    assert preview.same_origin(base, base + "/dev-ui/main.js")
    assert not preview.same_origin(base, "https://evil.example/main.js")
    assert not preview.same_origin(base, "http://18090-example.cloudworkstations.dev")
    assert not preview.same_origin(base, "https://18091-example.cloudworkstations.dev")


class Response:
    status_code = 200

    def __init__(self, events):
        self.events = events
        self.headers = {"X-A2A-Extensions": preview.EXTENSION}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def iter_lines(self):
        for event in self.events:
            yield "data: " + json.dumps({"result": event})
        raise AssertionError("The completed turn must stop the measurement")


class Session:
    def __init__(self, events):
        self.events = events
        self.headers = {}

    def stream(self, method, url, **kwargs):
        assert method == "POST"
        assert "generate_deck" not in json.dumps(kwargs["json"])
        assert not kwargs["follow_redirects"]
        return Response(self.events)


def test_progress_is_not_counted_as_a_completed_a2ui_brief(monkeypatch):
    session = Session(
        [
            {
                "status": {
                    "state": "completed",
                    "message": {
                        "parts": [{"kind": "text", "text": "Preparing your deck brief"}]
                    },
                },
                "final": True,
            }
        ]
    )
    monkeypatch.setattr(preview.httpx, "stream", session.stream)
    with pytest.raises(RuntimeError, match="No complete editable A2UI brief"):
        preview.intake(session, "http://127.0.0.1:18090")


def test_measures_first_text_components_and_completion_separately(monkeypatch):
    session = Session(
        [
            {
                "status": {
                    "message": {
                        "role": "agent",
                        "parts": [{"kind": "text", "text": "Loading options"}],
                    }
                }
            },
            {
                "artifact": {
                    "parts": [
                        {
                            "kind": "data",
                            "data": {"createSurface": {"catalogId": preview.CATALOG}},
                        },
                        {
                            "kind": "data",
                            "data": {
                                "updateComponents": {
                                    "components": [
                                        {"id": "root", "component": "Column"}
                                    ]
                                }
                            },
                        },
                    ]
                }
            },
            {"final": True, "status": {"state": "completed"}},
        ]
    )
    monkeypatch.setattr(preview.httpx, "stream", session.stream)
    result = preview.intake(session, "http://127.0.0.1:18090")
    assert result["first_text_ms"] <= result["first_surface_ms"]
    assert result["first_components_ms"] <= result["complete_ms"]
    assert result["surface_messages"] == 1
    assert result["completed"]


def test_proxy_token_is_port_scoped_short_lived_and_never_printed(monkeypatch, capsys):
    class TokenResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"accessToken": "never-print-this"}

    class API:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            assert url.endswith(":generateAccessToken")
            assert kwargs["json"] == {"port": 18090, "ttl": "900s"}
            return TokenResponse()

    monkeypatch.setattr(preview, "api_session", lambda account: API())
    assert (
        preview.proxy_token("projects/p/locations/l/workstations/w", "sa", 18090)
        == "never-print-this"
    )
    assert not capsys.readouterr().out
