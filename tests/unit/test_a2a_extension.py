# Copyright 2026 Google LLC

"""An A2A extension is negotiated, not declared.

Advertising A2UI on the agent card only says the agent can speak it. The client
then asks for it on a request with the ``X-A2A-Extensions`` header, and the
server has to answer with the same header naming what it actually turned on.
Until that answer arrives the extension is off, and a conformant client has no
choice but to treat an A2UI ``DataPart`` as an opaque blob. Gemini Enterprise
draws it as an attachment chip captioned with the part's mime type, which is
where ``application/json+a2ui: Unsupported attachment`` came from. Not a
malformed surface. A surface nobody was told to read.

The SDK writes that response header from
``ServerCallContext.activated_extensions``, and nothing was putting anything in
it. ADK activates only its own executor extension, and only on the code path
this agent deliberately does not take (``use_legacy=True``, required for A2UI
over streaming). So the set stayed empty on every single turn.

These tests pin both halves: that the right URI gets activated, and that it is
activated early enough to reach the wire. The second half is the one that bites.
A streaming turn hands the SDK an async generator that has not started running,
and the SDK builds the response headers from the context right then. Anything
that activates from inside the executor is already too late.
"""

import json

import pytest
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard
from a2a.utils.message import new_agent_text_message
from fastapi.testclient import TestClient

from app.app_utils.a2a import (
    _A2UI_EXTENSION_URI,
    _ADK_AGENT_EXECUTOR_EXTENSION_URI,
    _A2uiActivatingContextBuilder,
    _default_capabilities,
)

HEADER = "X-A2A-Extensions"

# What Gemini Enterprise actually sends, duplicate and all.
GEMINI_ENTERPRISE_HEADER = (
    f"{_A2UI_EXTENSION_URI}, {_A2UI_EXTENSION_URI},"
    f"{_ADK_AGENT_EXECUTOR_EXTENSION_URI}"
)


def _card() -> AgentCard:
    return AgentCard(
        name="slidegen",
        description="test card",
        url="http://testserver/a2a/app",
        version="0.0.0",
        capabilities=_default_capabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],
    )


class _Echo:
    """The smallest executor that produces a turn."""

    async def execute(self, context, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(new_agent_text_message("ok"))

    async def cancel(self, context, event_queue: EventQueue) -> None:
        return None


def _client() -> TestClient:
    card = _card()
    handler = DefaultRequestHandler(
        agent_executor=_Echo(), task_store=InMemoryTaskStore()
    )
    application = A2AFastAPIApplication(
        agent_card=card,
        http_handler=handler,
        context_builder=_A2uiActivatingContextBuilder(card),
    )
    return TestClient(application.build())


def _activated(header: str | None) -> set[str]:
    """POST one turn and report which extensions the server confirmed."""
    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "hi"}],
                "messageId": "m1",
                "kind": "message",
            }
        },
    }
    headers = {HEADER: header} if header is not None else {}
    response = _client().post("/", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    echoed = response.headers.get(HEADER)
    return {part.strip() for part in echoed.split(",") if part.strip()} if echoed else set()


# --------------------------------------------------------------------------
# activation


def test_gemini_enterprise_gets_its_a2ui_activation_confirmed() -> None:
    assert _A2UI_EXTENSION_URI in _activated(GEMINI_ENTERPRISE_HEADER)


def test_the_legacy_executor_extension_is_never_claimed() -> None:
    """We run the legacy executor, so confirming that extension would be a lie.

    Only A2UI is negotiated here. Echoing the ADK executor URI back would tell
    the caller to expect a protocol this agent does not run.
    """
    assert _ADK_AGENT_EXECUTOR_EXTENSION_URI not in _activated(
        GEMINI_ENTERPRISE_HEADER
    )


def test_a_client_that_asks_for_nothing_is_told_nothing() -> None:
    assert _activated(None) == set()


def test_a_version_we_do_not_advertise_is_refused() -> None:
    assert _activated("https://a2ui.org/a2a-extension/a2ui/v0.8") == set()


@pytest.mark.parametrize(
    "header",
    [
        _A2UI_EXTENSION_URI,
        f" {_A2UI_EXTENSION_URI} ",
        f"{_ADK_AGENT_EXECUTOR_EXTENSION_URI},{_A2UI_EXTENSION_URI}",
    ],
)
def test_the_uri_is_found_however_the_header_is_packed(header: str) -> None:
    assert _activated(header) == {_A2UI_EXTENSION_URI}


# --------------------------------------------------------------------------
# reaching the wire in time


def test_a_streaming_turn_carries_the_confirmation_too() -> None:
    """The header has to be set before the event generator is consumed.

    ``message/stream`` returns an unstarted async generator, and the SDK reads
    ``activated_extensions`` to build the SSE response headers at that moment.
    Activating from inside the executor passes the non-streaming test above and
    still leaves Gemini Enterprise, which streams, with an attachment chip.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message/stream",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "hi"}],
                "messageId": "m1",
                "kind": "message",
            }
        },
    }
    with _client().stream(
        "POST", "/", json=payload, headers={HEADER: GEMINI_ENTERPRISE_HEADER}
    ) as response:
        assert response.status_code == 200
        echoed = response.headers.get(HEADER)
        response.read()
    assert echoed is not None, "no activation confirmed on the streaming response"
    assert _A2UI_EXTENSION_URI in echoed


# --------------------------------------------------------------------------
# the card the negotiation matches against


def test_the_card_advertises_the_uri_clients_ask_for() -> None:
    """Activation matches request URI against card URI, so they must agree.

    A drift here is silent: the card still looks A2UI-capable, the client still
    asks, and nothing matches, so the surface degrades to an attachment with no
    error anywhere.
    """
    uris = {ext.uri for ext in _default_capabilities().extensions or []}
    assert _A2UI_EXTENSION_URI in uris


def test_a_surface_is_labelled_with_the_mime_type_v0_9_clients_expect() -> None:
    """v0.9 pairs with the older mime type, and the pairing is not ours to pick.

    ``application/json+a2ui`` reads like the bug because it is the caption on
    the failing attachment chip, and the library calls it deprecated. It is
    still what a client negotiating v0.9 expects; the library maps the version
    to it on purpose. Changing it would break the renderer that does work.
    """
    from a2ui.a2a.parts import create_a2ui_part
    from a2ui.schema.constants import VERSION_0_9

    part = create_a2ui_part({"createSurface": {}}, version=VERSION_0_9)
    assert part.root.metadata["mimeType"] == "application/json+a2ui"
    assert json.dumps(part.root.data)
