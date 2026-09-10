"""A submitted chat turn is not a completed deck; saved jobs survive API loss."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.a2a_jobs import ConversationJobs, configured_jobs, handle_action, submission
from app.a2ui_composer import compose_intake, compose_job
from app.agent import root_agent
from app.mcp_worker import generate, run_job
from app.turns import encode_action_turn
from tests.unit.test_a2ui_catalog_contract import _components, catalog_violations

IDENTITY = {"app_name": "app", "user_id": "u", "session_id": "conversation"}
BRIEF = {
    "topic": "A six-slide introduction",
    "brand_id": "stripe",
    "slide_count": "6",
    "request_id": "one-click",
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("SLIDEGEN_A2A_JOB_DB", str(tmp_path / "a2a.sqlite"))
    return configured_jobs()


def action(name, values=None, **identity):
    return handle_action(**{**IDENTITY, **identity}, name=name, values=values or {})


async def turn(name, values=None, *, session_id="conversation", user_id="u"):
    # A fresh session service models the API process losing all ADK memory.
    sessions = InMemorySessionService()
    await sessions.create_session(
        app_name="app", user_id=user_id, session_id=session_id
    )
    runner = Runner(
        agent=root_agent.model_copy(update={"queue_decks": True}),
        session_service=sessions,
        app_name="app",
    )
    try:
        return [
            event
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=encode_action_turn(name, values))],
                ),
                run_config=RunConfig(streaming_mode=StreamingMode.SSE),
            )
        ]
    finally:
        await runner.close()


def event_text(events):
    return "\n".join(
        part.text
        for event in events
        if event.content
        for part in event.content.parts or []
        if part.text
    )


def test_duplicate_clicks_share_one_job_even_with_concurrent_requests(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: action("generate_deck", BRIEF)["id"], range(8)))
    assert len(set(ids)) == 1
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_job_access_uses_invocation_identity_not_action_fields(store):
    job = action("generate_deck", BRIEF)
    for identity in (
        {"user_id": "other"},
        {"session_id": "other"},
        {"app_name": "other"},
    ):
        with pytest.raises(PermissionError):
            action("check_deck", {"job_id": job["id"], **IDENTITY}, **identity)
        with pytest.raises(PermissionError):
            action("cancel_deck", {"job_id": job["id"]}, **identity)
    assert action("check_deck")["status"] == "queued"


def test_changed_brief_does_not_silently_start_another_deck(store):
    job = action("generate_deck", BRIEF)
    conflict = action(
        "generate_deck",
        {**BRIEF, "topic": "Another topic", "request_id": "another-click"},
    )
    assert conflict["id"] == job["id"]
    assert conflict["brief"]["topic"] == BRIEF["topic"]
    assert conflict["submission_conflict"]


@pytest.mark.asyncio
async def test_ack_finishes_before_any_generation_and_status_survives_new_runner(
    store, monkeypatch
):
    async def forbidden(**kwargs):
        raise AssertionError("Submission and status turns must not invoke authoring")

    monkeypatch.setattr("app.pixelpitch_agent.generate_deck", forbidden)
    start = time.monotonic()
    events = await asyncio.wait_for(turn("generate_deck", BRIEF), 2)
    assert time.monotonic() - start < 2
    assert not events[-1].partial
    assert events[-1].turn_complete
    assert events[-1].custom_metadata["pixelpitch"]["status"] == "queued"
    assert "Check progress" in event_text(events)
    assert "Your editable deck is ready" not in event_text(events)
    original_id = events[-1].custom_metadata["pixelpitch"]["job_id"]
    checked = await turn("check_deck", {"job_id": original_id})
    assert checked[-1].custom_metadata["pixelpitch"]["job_id"] == original_id
    assert checked[-1].custom_metadata["pixelpitch"]["status"] == "queued"


@pytest.mark.asyncio
async def test_final_result_is_retrieved_from_store_after_all_session_memory_is_lost(
    store,
):
    job = action("generate_deck", BRIEF)
    claimed = store.claim("worker")
    image = "data:image/png;base64,aGVsbG8="
    store.finish(
        job["id"],
        "worker",
        "completed",
        result={
            "https_url": "https://example.com/deck.pptx",
            "gs_uri": "gs://decks/deck.pptx",
            "slide_count": 6,
            "slide_titles": ["Opening"],
            "preview_images": [image],
        },
    )
    reopened = ConversationJobs(store.path)
    assert reopened.snapshot(claimed["workspace"], job["id"])["status"] == "completed"
    events = await turn("check_deck", {"job_id": job["id"]})
    text = event_text(events)
    assert "Your editable deck is ready" in text
    assert "https://example.com/deck.pptx" in text
    assert image in text


@pytest.mark.asyncio
async def test_stale_worker_is_interrupted_and_cannot_publish_late_completion(store):
    job = action("generate_deck", BRIEF)
    store.claim("worker")
    with store.connect() as db:
        db.execute("UPDATE jobs SET lease=?", (time.time() - 1,))
    checked = await turn("check_deck", {"job_id": job["id"]})
    assert checked[-1].custom_metadata["pixelpitch"]["status"] == "interrupted"
    assert "Review brief" in event_text(checked)
    store.finish(
        job["id"],
        "worker",
        "completed",
        result={"https_url": "https://example.com/late.pptx"},
    )
    assert action("check_deck")["result"] is None


def test_cancelled_submission_can_be_retried_only_with_a_new_form_nonce(store):
    job = action("generate_deck", BRIEF)
    assert action("cancel_deck", {"job_id": job["id"]})["status"] == "cancelled"
    assert action("generate_deck", BRIEF)["id"] == job["id"]
    assert store.claim("worker") is None
    assert (
        action("generate_deck", {**BRIEF, "request_id": "fresh-form"})["id"]
        != job["id"]
    )


@pytest.mark.parametrize(
    "status", ["queued", "running", "cancelling", "cancelled", "failed", "interrupted"]
)
def test_every_job_card_matches_the_actual_ge_catalog_without_fake_percentage(
    store, status
):
    job = action("generate_deck", BRIEF)
    job.update(status=status, elapsed=70, updated=time.time() - 40)
    wire = compose_job(job)
    assert not catalog_violations(_components(wire))
    assert "%" not in " ".join(str(c.get("text", "")) for c in _components(wire))
    assert "deck-pet" not in wire
    names = [
        c["action"]["event"]["name"]
        for c in _components(wire)
        if c["component"] == "MaterialButton"
    ]
    assert ("check_deck" in names) == (status in {"queued", "running", "cancelling"})


def test_form_nonce_is_stable_within_a_card_and_changes_on_a_new_card():
    def card():
        wire = compose_intake(
            topic="Test",
            audience="Board",
            goal="Decide",
            slide_count=1,
            brands=[{"id": "stripe", "name": "Stripe"}],
            styles=[],
            templates=[],
        )
        messages = json.loads(wire.split("<a2ui-json>\n")[1].split("\n</a2ui-json>")[0])
        data = next(
            m["updateDataModel"]["value"] for m in messages if "updateDataModel" in m
        )
        button = next(c for c in _components(wire) if c["id"] == "generate")
        assert button["action"]["event"]["context"]["request_id"] == {
            "path": "/request_id"
        }
        return data["request_id"]

    assert card() != card()


def test_cloud_run_cannot_accidentally_use_ephemeral_sqlite(store, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "slidegen-agent")
    with pytest.raises(RuntimeError, match="Cloud Run"):
        configured_jobs()


def test_old_cards_and_choice_arrays_are_normalized_and_idempotent():
    brief, key = submission(
        {
            "topic": "Test",
            "brand_id": ["stripe"],
            "style_id": [],
            "template_revision": [],
            "slide_count": "2",
        }
    )
    assert brief.slide_count == 2 and brief.brand_id == "stripe"
    assert submission(brief.model_dump())[1] == key


@pytest.mark.asyncio
async def test_shared_worker_executes_inline_and_persists_images_without_recursive_submission(
    store, monkeypatch
):
    from app.tools import cache_fragment

    job = action("generate_deck", BRIEF)
    claimed = store.claim("worker")
    calls = []

    monkeypatch.setattr(
        "app.pixelpitch_agent.list_brands",
        lambda **kwargs: {"brands": [{"id": "stripe", "name": "Stripe"}]},
    )
    monkeypatch.setattr(root_agent, "queue_decks", True)

    async def pipeline(*, tool_context, **kwargs):
        calls.append(kwargs)
        cache_fragment(
            f"session:{tool_context.session.id}",
            "__SLIDE_PREVIEW_IMAGE_01__",
            "data:image/png;base64,aGVsbG8=",
        )
        return {
            "status": "ok",
            "https_url": "https://example.com/native.pptx",
            "gs_uri": "gs://decks/native.pptx",
            "slide_count": 6,
        }

    monkeypatch.setattr("app.pixelpitch_agent.generate_deck", pipeline)
    result = await generate(claimed, store, "worker", persist_previews=True)
    assert len(calls) == 1
    assert result["preview_images"] == ["data:image/png;base64,aGVsbG8="]
    assert result["https_url"] == "https://example.com/native.pptx"
    assert action("check_deck")["status"] == "running"
    store.finish(job["id"], "worker", "completed", result=result)


@pytest.mark.asyncio
async def test_cancel_action_reaches_independent_worker(store):
    job = action("generate_deck", BRIEF)
    claimed = store.claim("worker")
    started, stopped = asyncio.Event(), asyncio.Event()

    async def pipeline(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = asyncio.create_task(
        run_job(claimed, store, "worker", pipeline=pipeline, heartbeat_seconds=0.01)
    )
    await started.wait()
    events = await turn("cancel_deck", {"job_id": job["id"]})
    assert events[-1].custom_metadata["pixelpitch"]["status"] in {
        "cancelling",
        "cancelled",
    }
    await asyncio.wait_for(task, 2)
    assert stopped.is_set()
    assert action("check_deck")["status"] == "cancelled"
