# Copyright 2026 Google LLC

"""Progress streaming tests for long-running synchronous deck tools."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from a2a.types import DataPart, Part

from app.app_utils.a2a import (
    _A2uiActionExecutor,
    _promote_download_links_in_events,
    _translate_incoming_a2ui_actions,
)
from app.turns import decode_action_turn


class _Queue:
    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


class _Delegate:
    async def execute(self, context, event_queue):
        await event_queue.enqueue_event("native-adk-event")

    async def cancel(self, context, event_queue):
        return None


class _QuietDelegate:
    async def execute(self, context, event_queue):
        return None

    async def cancel(self, context, event_queue):
        return None


def test_a_landed_slide_advances_the_authoring_counter(monkeypatch):
    from app import tools
    from app.progress import display_percent, get_session_progress_updates

    def fake_designed_deck(*, slide_ready, **_kwargs):
        for index, title in (
            (0, "Where we are"),
            (1, "What changed"),
            (0, "Where we are, revised"),
        ):
            slide_ready({"index": index, "title": title, "html": "<section></section>"})
        return {"status": "error", "error": "stopped once the watcher had fired"}

    monkeypatch.setattr("app.deck_run.run_designed_deck", fake_designed_deck)
    context = SimpleNamespace(session=SimpleNamespace(id="authoring-counter"))

    tools._generate_deck_sync(
        topic="t",
        audience="a",
        goal="g",
        slide_count=3,
        brand_id="stripe",
        tool_context=context,
    )

    landed = [
        update
        for update in get_session_progress_updates("authoring-counter")
        if update.slide_title
    ]
    # The third call is a rewrite of the first slide. The numerator counts
    # distinct slides, so it holds at 2 instead of overrunning the total.
    assert [
        (update.current, update.total, update.slide_index, update.slide_title)
        for update in landed
    ] == [
        (1, 3, 0, "Where we are"),
        (2, 3, 1, "What changed"),
        (2, 3, 0, "Where we are, revised"),
    ]
    # Without `current` every one of these reported 20 + 10.
    assert [
        display_percent(f"landed-{i}", update) for i, update in enumerate(landed)
    ] == [36, 53, 53]


def test_the_template_lane_claims_no_slide_counter(monkeypatch):
    from app import tools
    from app.progress import display_percent, get_session_progress_updates

    def fake_template_deck(*, on_event, **_kwargs):
        on_event({"agy_evt": "tool", "name": "write_file"})
        return {"status": "error", "error": "stopped once the stream had fired"}

    monkeypatch.setattr("app.deck_run.run_template_deck", fake_template_deck)
    monkeypatch.setattr(
        tools,
        "get_reference",
        lambda _key: {"name": "Example Retail", "template_gs_uri": "gs://b/t.pptx"},
    )
    # The lane's error branch kicks off a real GCS catalog build otherwise.
    monkeypatch.setattr(tools, "maybe_build_catalog", lambda *_a: False)
    context = SimpleNamespace(session=SimpleNamespace(id="template-counter"))

    tools._generate_deck_sync(
        topic="t",
        audience="a",
        goal="g",
        slide_count=6,
        brand_id=tools.REFERENCE_BRAND_ID,
        tool_context=context,
    )

    authoring = [
        update
        for update in get_session_progress_updates("template-counter")
        if update.stage == "authoring"
    ]
    # This lane harvests one patches.json at the end, so nothing lands while it
    # runs. Claiming 0-of-6 would score 20 and sit there for the whole build.
    assert authoring
    assert all(
        (update.current, update.total) == (None, None) for update in authoring
    )
    assert {display_percent("template-counter", u) for u in authoring} == {30}


@pytest.mark.asyncio
async def test_executor_forwards_native_adk_stream_without_duplicate_progress():
    context = SimpleNamespace(
        message=None,
        context_id="context-1",
        task_id="task-1",
        requested_extensions=set(),
    )
    queue = _Queue()

    await _A2uiActionExecutor(_Delegate()).execute(context, queue)

    assert queue.events == ["native-adk-event"]


@pytest.mark.asyncio
async def test_executor_does_not_fabricate_an_intake_status():
    context = SimpleNamespace(
        message=SimpleNamespace(parts=[]),
        context_id="context-intake",
        task_id="task-intake",
        requested_extensions=set(),
    )
    queue = _Queue()

    await _A2uiActionExecutor(_QuietDelegate()).execute(context, queue)

    assert queue.events == []


def test_a2ui_action_is_translated_to_canonical_envelope():
    message = SimpleNamespace(
        parts=[
            Part(
                root=DataPart(
                    data={
                        "version": "v0.9",
                        "action": {
                            "event": {
                                "name": "generate_deck",
                                "context": {"topic": "Retail growth"},
                            }
                        },
                    }
                )
            )
        ]
    )

    assert _translate_incoming_a2ui_actions(message) is True
    turn = decode_action_turn(message.parts[0].root.text)
    assert turn is not None
    assert turn.name == "generate_deck"
    assert turn.context == {"topic": "Retail growth"}


def test_download_text_is_promoted_to_native_open_url_button():
    event = SimpleNamespace(
        parts=[
            SimpleNamespace(
                root=DataPart(
                    data={
                        "version": "v0.9",
                        "updateComponents": {
                            "surfaceId": "deck_result_surface",
                            "components": [
                                {
                                    "id": "download",
                                    "component": "Text",
                                    "text": "Download PPTX: https://example.com/deck.pptx",
                                }
                            ],
                        },
                    }
                )
            )
        ]
    )

    _promote_download_links_in_events([event])

    components = event.parts[0].root.data["updateComponents"]["components"]
    assert components[0]["component"] == "Button"
    assert components[0]["child"] == "download_label"
    assert components[0]["action"]["functionCall"] == {
        "call": "openUrl",
        "args": {"url": "https://example.com/deck.pptx"},
        "returnType": "void",
    }
    assert components[1]["text"] == "Download PPTX"
