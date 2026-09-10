# Copyright 2026 Google LLC

"""Native ADK event-stream tests for the custom Pixelpitch root agent."""

from __future__ import annotations

import asyncio
import json

import pytest
from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent
from app.pixelpitch_agent import _requested_slide_count, _user_facing_error
from app.progress import report_progress
from app.turns import encode_action_turn


def test_root_is_transport_agent_not_llm_agent():
    assert isinstance(root_agent, BaseAgent)
    assert not isinstance(root_agent, LlmAgent)
    assert root_agent.sub_agents == []


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["stripe", ["stripe"]])
async def test_generation_streams_real_progress_and_final_a2ui(monkeypatch, selection):
    def fake_list_brands(*, tool_context=None):
        return {
            "status": "ok",
            "brands": [
                {
                    "id": "stripe",
                    "name": "Stripe",
                    "palette": ["#061B31", "#533AFD"],
                }
            ],
        }

    async def fake_generate_deck(*, tool_context, **kwargs):
        report_progress(
            tool_context,
            stage="authoring",
            title="Building your deck",
            detail="Outline approved",
            stage_index=2,
            current=1,
            total=2,
        )
        await asyncio.sleep(0.25)
        report_progress(
            tool_context,
            stage="delivery",
            title="Deck delivered",
            detail="Editable PPTX uploaded",
            stage_index=5,
            current=2,
            total=2,
        )
        return {
            "status": "ok",
            "title": kwargs["topic"],
            "brand": "Stripe",
            "slide_count": 2,
            "slide_titles": ["One", "Two"],
            "preview_image_tokens": ["__SLIDE_PREVIEW_IMAGE_01__"],
            "https_url": "https://example.com/deck.pptx",
            "gs_uri": "gs://example/deck.pptx",
        }

    monkeypatch.setattr("app.pixelpitch_agent.list_brands", fake_list_brands)
    monkeypatch.setattr("app.pixelpitch_agent.generate_deck", fake_generate_deck)

    session_service = InMemorySessionService()
    session = await session_service.create_session(user_id="u", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")
    action = encode_action_turn(
        "generate_deck",
        {
            "topic": "Retail growth",
            "audience": "Board",
            "goal": "Decide the next investment",
            "slide_count": "2",
            "brand_id": selection,
            "style_id": [],
            "template_revision": [],
        },
    )

    events = []
    async for event in runner.run_async(
        new_message=types.Content(
            role="user", parts=[types.Part.from_text(text=action)]
        ),
        user_id="u",
        session_id=session.id,
        run_config=RunConfig(streaming_mode=StreamingMode.SSE),
    ):
        events.append(event)

    texts = [
        part.text
        for event in events
        if event.content
        for part in (event.content.parts or [])
        if part.text
    ]
    assert "Deck request accepted" in texts[0]
    assert any("Outline approved" in text for text in texts)
    assert any("Editable PPTX uploaded" in text for text in texts)
    assert "<a2ui-json>" in texts[-1]
    assert events[-1].partial is False

    stored = await session_service.get_session(
        app_name="test", user_id="u", session_id=session.id
    )
    assert stored is not None
    assert stored.state["pixelpitch_brief"]["topic"] == "Retail growth"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Generate a 20 slide deck on the agent platform", 20),
        ("a 12-slide board update", 12),
        ("make me 3 slides", 3),
        ("no size mentioned here", None),
        ("a 200 slide monster", None),
        ("Q3 2026 revenue", None),
    ],
)
def test_prose_slide_count_is_read_only_when_it_is_a_size(text, expected):
    assert _requested_slide_count(text) == expected


@pytest.mark.asyncio
async def test_new_text_replaces_the_prior_topic_and_keeps_the_slide_count(monkeypatch):
    """A second brief in an existing session must not come back as the first.

    The old build folded new text into ``goal`` and re-sent the previous
    ``topic``, so the only way to change subject was to start a new session.
    """
    monkeypatch.setattr(
        "app.pixelpitch_agent.list_brands",
        lambda *, tool_context=None: {
            "status": "ok",
            "brands": [{"id": "stripe", "name": "Stripe"}],
        },
    )
    monkeypatch.setattr(
        "app.pixelpitch_agent.list_styles",
        lambda **kwargs: {"status": "ok", "styles": []},
    )
    monkeypatch.setattr(
        "app.pixelpitch_agent.list_templates",
        lambda **kwargs: {"status": "ok", "templates": []},
    )

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        user_id="u",
        app_name="test",
        state={
            "pixelpitch_brief": {
                "topic": "Retail growth",
                "audience": "Board",
                "goal": "Decide the next investment",
                "slide_count": 20,
                "brand_id": "stripe",
                "style_id": "",
                "template_revision": "",
            }
        },
    )
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    texts = [
        part.text
        async for event in runner.run_async(
            new_message=types.Content(
                role="user",
                parts=[types.Part.from_text(text="Benefits of the agent platform")],
            ),
            user_id="u",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
        if event.content
        for part in (event.content.parts or [])
        if part.text
    ]

    wire = texts[-1]
    payload = json.loads(
        wire.split("<a2ui-json>\n", 1)[1].split("\n</a2ui-json>", 1)[0]
    )
    brief = next(
        message["updateDataModel"]["value"]["brief"]
        for message in payload
        if "updateDataModel" in message
    )

    assert brief["topic"] == "Benefits of the agent platform"
    assert brief["slide_count"] == "20"
    assert "Follow-up request" not in wire


_GLYPH_LEAK = (
    "', 'five', 'fl', 'four', 'g', 'greater', 'h', 'hyphen', 'i', 'j', 'k', "
    "'l', 'l.alt', 'less', 'm', 'n', 'nine', 'zero'] Glyph IDs: [0, 1, 2] "
    "Retaining 120 glyphs hmtx subsetted GDEF pruned"
)


def test_a_glyph_dump_never_reaches_the_transcript():
    """What shipped to the user under "Deck generation stopped".

    An upstream string is whatever the failing process last wrote. This is
    the final hop before a person reads it, so it fails safe.
    """
    assert _user_facing_error(_GLYPH_LEAK, "fallback sentence") == "fallback sentence"


def test_a_real_message_is_passed_through_intact():
    message = "Renderer returned 500: slidify timed out after 600s."

    assert _user_facing_error(message, "fallback") == message


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_an_absent_error_becomes_the_fallback(empty):
    assert _user_facing_error(empty, "fallback") == "fallback"


def test_an_overlong_message_is_bounded_rather_than_dumped():
    result = _user_facing_error("word " * 200, "fallback")

    assert len(result) <= 240
    assert result.endswith("...")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "context", "expected_goal"),
    [
        (
            "revise_deck",
            {"instruction": "punchier", "detail": "Keep slide 2 unchanged."},
            "Decide. Use punchier, shorter slide titles. Keep slide 2 unchanged.",
        ),
        (
            "revise_deck",
            {"instruction": "", "detail": "Explain the investment on slide 4."},
            "Decide. Explain the investment on slide 4.",
        ),
        ("revise_deck", {"instruction": "", "detail": ""}, "Decide."),
        (
            "edit_brief",
            {
                "topic": "",
                "audience": "New audience",
                "goal": "New goal",
                "slide_count": "12",
                "brand_id": "stripe",
            },
            "New goal",
        ),
    ],
)
async def test_revision_and_recovery_reopen_a_brief_without_generating(
    monkeypatch, name, context, expected_goal
):
    monkeypatch.setattr(
        "app.pixelpitch_agent.list_brands",
        lambda **kw: {
            "status": "ok",
            "brands": [{"id": "stripe", "name": "Stripe"}],
        },
    )
    monkeypatch.setattr(
        "app.pixelpitch_agent.list_styles",
        lambda **kw: {
            "status": "ok",
            "styles": [],
        },
    )
    monkeypatch.setattr(
        "app.pixelpitch_agent.list_templates",
        lambda **kw: {
            "status": "ok",
            "templates": [],
        },
    )

    async def unexpected_generation(**kw):
        pytest.fail("Review and recovery must not start a deck run.")

    monkeypatch.setattr("app.pixelpitch_agent.generate_deck", unexpected_generation)
    sessions = InMemorySessionService()
    session = await sessions.create_session(
        user_id="u",
        app_name="ux",
        state={
            "pixelpitch_brief": {
                "topic": "Retail",
                "audience": "Board",
                "goal": "Decide.",
                "slide_count": 8,
                "brand_id": "stripe",
            },
            "pixelpitch_result": {"https_url": "https://example.com/original.pptx"},
        },
    )
    runner = Runner(agent=root_agent, session_service=sessions, app_name="ux")
    events = [
        event
        async for event in runner.run_async(
            new_message=types.Content(
                role="user",
                parts=[types.Part.from_text(text=encode_action_turn(name, context))],
            ),
            user_id="u",
            session_id=session.id,
        )
    ]
    text = "".join(part.text or "" for part in events[-1].content.parts)
    messages = json.loads(text.split("<a2ui-json>\n")[1].split("\n</a2ui-json>")[0])
    draft = next(
        m["updateDataModel"]["value"]["brief"]
        for m in messages
        if "updateDataModel" in m
    )
    assert draft["goal"] == expected_goal
    assert draft["topic"] == ("" if name == "edit_brief" else "Retail")
    assert draft["slide_count"] == ("12" if name == "edit_brief" else "8")
    stored = await sessions.get_session(
        user_id="u", app_name="ux", session_id=session.id
    )
    assert (
        stored.state["pixelpitch_result"]["https_url"]
        == "https://example.com/original.pptx"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection",
    [
        {"template_revision": "removed-template"},
        {"brand_id": "removed-brand"},
        {"style_id": "removed-style"},
    ],
)
async def test_stale_selection_recovery_retains_the_submitted_brief(
    monkeypatch, selection
):
    monkeypatch.setattr(
        "app.pixelpitch_agent.list_brands",
        lambda **kw: {
            "status": "ok",
            "brands": [{"id": "stripe", "name": "Stripe"}],
        },
    )
    monkeypatch.setattr(
        "app.pixelpitch_agent.list_styles",
        lambda **kw: {
            "status": "ok",
            "styles": [],
        },
    )
    monkeypatch.setattr(
        "app.pixelpitch_agent.list_templates",
        lambda **kw: {
            "status": "ok",
            "templates": [],
        },
    )
    submitted = {
        "topic": "Updated investment plan",
        "audience": "Finance committee",
        "goal": "Approve the budget",
        "slide_count": "7",
        "brand_id": "stripe",
        "style_id": "",
        "template_revision": "",
        **selection,
    }
    sessions = InMemorySessionService()
    session = await sessions.create_session(user_id="u", app_name="recovery")
    runner = Runner(agent=root_agent, session_service=sessions, app_name="recovery")

    async def turn(name, context):
        texts = [
            part.text
            async for event in runner.run_async(
                new_message=types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=encode_action_turn(name, context))
                    ],
                ),
                user_id="u",
                session_id=session.id,
            )
            if event.content
            for part in event.content.parts
            if part.text
        ]
        return json.loads(
            texts[-1].split("<a2ui-json>\n")[1].split("\n</a2ui-json>")[0]
        )

    failure = await turn("generate_deck", submitted)
    components = next(
        m["updateComponents"]["components"] for m in failure if "updateComponents" in m
    )
    recovery = next(c["action"]["event"] for c in components if c["id"] == "recovery")
    assert recovery == {"name": "edit_brief", "context": submitted}
    reopened = await turn(recovery["name"], recovery["context"])
    draft = next(
        m["updateDataModel"]["value"]["brief"]
        for m in reopened
        if "updateDataModel" in m
    )
    for key in ("topic", "audience", "goal", "slide_count"):
        assert draft[key] == submitted[key]
