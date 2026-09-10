"""Queue invariants: ownership, retries, leases, cancellation, and recovery."""

import asyncio
import time

import pytest
from pydantic import ValidationError

from app.mcp_jobs import Brief, JobStore
from app.mcp_worker import run_job


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs.sqlite")


def brief(**changes):
    return Brief(topic="A board decision", brand_id="stripe", **changes)


def test_workspace_capability_is_scoped_and_not_stored_in_plaintext(store):
    a, b = store.open_workspace(), store.open_workspace()
    store.authorize(**a)
    with pytest.raises(PermissionError):
        store.authorize(a["workspace"], b["capability"])
    with pytest.raises(PermissionError):
        store.authorize("unknown", a["capability"])
    assert a["capability"].encode() not in store.path.read_bytes()


def test_retry_is_idempotent_and_a_changed_brief_is_rejected(store):
    workspace = store.open_workspace()["workspace"]
    first = store.submit(workspace, "click-1", brief())
    assert store.submit(workspace, "click-1", brief())["id"] == first["id"]
    with pytest.raises(ValueError, match="different brief"):
        store.submit(workspace, "click-1", brief(goal="Different"))
    with pytest.raises(ValueError, match="already running"):
        store.submit(workspace, "click-2", brief())


def test_jobs_and_drafts_survive_store_reopening(store):
    workspace = store.open_workspace()["workspace"]
    job = store.submit(workspace, "click", brief())
    store.claim("worker")
    store.record(
        job["id"],
        "worker",
        {"stage": "authoring"},
        slide={
            "index": 0,
            "title": "A decision",
            "html": "<h1>A decision</h1>",
            "revision": 1,
        },
    )
    reopened = JobStore(store.path)
    assert reopened.snapshot(workspace)["slides"][0]["title"] == "A decision"
    assert "<h1>" in reopened.slide(workspace, job["id"], 0)["html"]
    with pytest.raises(PermissionError):
        reopened.slide("someone-else", job["id"], 0)


def test_revisions_replace_a_draft_without_inflating_the_count(store):
    workspace = store.open_workspace()["workspace"]
    job = store.submit(workspace, "click", brief())
    store.claim("worker")
    for revision in (1, 2):
        store.record(
            job["id"],
            "worker",
            {},
            slide={
                "index": 0,
                "title": "Updated",
                "html": str(revision),
                "revision": revision,
            },
        )
    assert store.snapshot(workspace)["slides"] == [
        {"slide_index": 0, "title": "Updated", "revision": 2, "kind": "draft"}
    ]


def test_export_preview_replaces_draft_without_inline_job_images(store):
    from app.mcp_worker import export_previews

    workspace = store.open_workspace()["workspace"]
    job = store.submit(workspace, "export-proof", brief())
    store.claim("worker")
    fragments = {
        "__SLIDE_PREVIEW_IMAGE_01__": "data:image/png;base64,AA==",
        "__SLIDE_DRAFT_TITLE_0__": 'A "decision"',
        "__SLIDE_PREVIEW_IMAGE_02__": "https://untrusted.test/image.png",
    }
    previews = list(export_previews(fragments))
    assert len(previews) == 1
    store.record(job["id"], "worker", {}, slide=previews[0] | {"revision": 2})
    assert store.snapshot(workspace)["slides"][0]["kind"] == "export"
    assert "data:image/" not in str(store.snapshot(workspace))
    slide = store.slide(workspace, job["id"], 0)
    assert slide["kind"] == "export"
    assert "&quot;decision&quot;" in slide["html"]
    assert not list(
        export_previews(
            {"__SLIDE_PREVIEW_IMAGE_01__": "data:image/png;base64," + "a" * 2_000_000}
        )
    )


def test_only_one_worker_claims_a_job_and_stale_worker_cannot_write(store):
    workspace = store.open_workspace()["workspace"]
    job = store.submit(workspace, "click", brief())
    assert store.claim("worker-a")["id"] == job["id"]
    assert store.claim("worker-b") is None
    with store.connect() as db:
        db.execute("UPDATE jobs SET lease=?", (time.time() - 1,))
    assert not store.heartbeat(job["id"], "worker-a")
    assert store.claim("worker-b") is None
    store.finish(
        job["id"],
        "worker-a",
        "completed",
        result={"https_url": "https://example.com/wrong"},
    )
    saved = store.snapshot(workspace)
    assert saved["status"] == "interrupted"
    assert saved["result"] is None


def test_cancel_queued_job_never_reaches_a_worker(store):
    workspace = store.open_workspace()["workspace"]
    job = store.submit(workspace, "click", brief())
    assert store.cancel(workspace, job["id"])["status"] == "cancelled"
    assert store.claim("worker") is None
    assert store.cancel(workspace, job["id"])["status"] == "cancelled"


def test_cancellation_wins_a_completion_race_without_publishing_the_result(store):
    workspace = store.open_workspace()["workspace"]
    job = store.submit(workspace, "click", brief())
    store.claim("worker")
    store.cancel(workspace, job["id"])
    store.finish(
        job["id"],
        "worker",
        "completed",
        result={"https_url": "https://example.com/deck.pptx"},
    )
    saved = store.snapshot(workspace)
    assert saved["status"] == "cancelled"
    assert saved["result"] is None


@pytest.mark.asyncio
async def test_running_cancellation_reaches_the_pipeline(store):
    workspace = store.open_workspace()["workspace"]
    submitted = store.submit(workspace, "click", brief())
    job = store.claim("worker")
    started, stopped = asyncio.Event(), asyncio.Event()

    async def pipeline(*_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = asyncio.create_task(run_job(job, store, "worker", pipeline=pipeline))
    await started.wait()
    assert store.cancel(workspace, submitted["id"])["status"] == "cancelling"
    await asyncio.wait_for(task, timeout=2)
    assert stopped.is_set()
    assert store.snapshot(workspace)["status"] == "cancelled"


@pytest.mark.asyncio
async def test_completion_is_written_only_after_pipeline_returns(store):
    workspace = store.open_workspace()["workspace"]
    store.submit(workspace, "click", brief())
    job = store.claim("worker")

    async def pipeline(*_):
        assert store.snapshot(workspace)["status"] == "running"
        return {"https_url": "https://example.com/deck.pptx"}

    await run_job(job, store, "worker", pipeline=pipeline)
    assert store.snapshot(workspace)["status"] == "completed"


@pytest.mark.parametrize(
    "values",
    [
        {"topic": " ", "brand_id": "stripe"},
        {"topic": "Hello"},
        {"topic": "Hello", "brand_id": "stripe", "slide_count": 0},
    ],
)
def test_invalid_briefs_are_rejected_before_queueing(values):
    with pytest.raises(ValidationError):
        Brief.model_validate(values)


@pytest.mark.asyncio
async def test_shutdown_stops_pipeline_even_when_storage_fails(store, monkeypatch):
    workspace = store.open_workspace()["workspace"]
    store.submit(workspace, "click", brief())
    job = store.claim("worker")
    started, stopped = asyncio.Event(), asyncio.Event()

    async def pipeline(*_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    def unavailable(*args, **kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(store, "finish", unavailable)
    task = asyncio.create_task(run_job(job, store, "worker", pipeline=pipeline))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_job_timeout_stops_pipeline_and_releases_queue(store):
    workspace = store.open_workspace()["workspace"]
    store.submit(workspace, "click", brief())
    job = store.claim("worker")
    stopped = asyncio.Event()

    async def pipeline(*_):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    await run_job(
        job,
        store,
        "worker",
        pipeline=pipeline,
        timeout_seconds=0.03,
        heartbeat_seconds=0.01,
    )
    assert stopped.is_set()
    assert store.snapshot(workspace)["status"] == "failed"
    assert "time limit" in store.snapshot(workspace)["error"]
    store.submit(workspace, "new-attempt", brief())
