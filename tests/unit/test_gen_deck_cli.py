"""The diagnostic CLI must never default to another tenant's deployment."""

import pytest

from scripts import gen_deck


def test_requires_an_explicit_server_before_authentication(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["gen_deck.py", "Our team introduction"])
    monkeypatch.setattr(
        gen_deck, "identity_token", lambda *_: pytest.fail("must not authenticate")
    )
    with pytest.raises(SystemExit) as exc:
        gen_deck.main()
    assert exc.value.code == 2
    assert "--server" in capsys.readouterr().err


def test_uses_only_the_requested_server(monkeypatch):
    server = "https://a2a.example.com"
    monkeypatch.setattr("sys.argv", ["gen_deck.py", "Our team", "--server", server])
    calls = []

    def token(origin):
        calls.append(("auth", origin))
        return "test-token"

    def stream(origin, credential, brief):
        calls.append(("stream", origin, credential, brief))
        return [], {"status": "ok", "slide_count": 1}

    monkeypatch.setattr(gen_deck, "identity_token", token)
    monkeypatch.setattr(gen_deck, "stream", stream)
    assert gen_deck.main() == 0
    assert calls == [("auth", server), ("stream", server, "test-token", "Our team")]
