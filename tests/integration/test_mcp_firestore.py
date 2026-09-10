"""Shared queue tests against Google's real Firestore emulator, never production."""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("google.cloud.firestore")

from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore

from app.mcp_firestore import QUEUE_WAIT_SECONDS, FirestoreJobStore
from app.mcp_jobs import Brief


class Drafts:
    def __init__(self):
        self.values = {}

    def put(self, key, value):
        self.values.setdefault(key, value)

    def get(self, key):
        return self.values[key]


@pytest.fixture
def store():
    host = os.getenv("FIRESTORE_EMULATOR_HOST", "")
    if not host:
        pytest.skip("Run scripts/check_mcp.py cloud to start the local emulator")
    assert host.startswith("127.0.0.1:"), (
        "These tests only write to a loopback emulator"
    )
    db = firestore.Client(
        project=f"mcp-test-{uuid.uuid4().hex[:12]}", credentials=AnonymousCredentials()
    )
    yield FirestoreJobStore(db, Drafts(), capacity=2)
    db.close()


def submit(store, owner="alice", topic="Retail"):
    connection = store.open_workspace(owner)
    return connection, store.submit(
        connection["workspace"], "click", Brief(topic=topic, brand_id="stripe")
    )


def test_owner_bound_capabilities_and_persistent_reconnect(store):
    connection, job = submit(store)
    other_process = FirestoreJobStore(store.db, store.drafts)
    other_process.authorize(**connection, owner="alice")
    with pytest.raises(PermissionError):
        other_process.authorize(**connection, owner="bob")
    assert other_process.snapshot(connection["workspace"])["id"] == job["id"]
    with pytest.raises(PermissionError):
        other_process.slide("another-workspace", job["id"], 0)


def test_duplicate_submission_and_owner_quota_across_workspaces(store):
    connection, job = submit(store)
    assert (
        store.submit(
            connection["workspace"], "click", Brief(topic="Retail", brand_id="stripe")
        )["id"]
        == job["id"]
    )
    with pytest.raises(ValueError, match="different brief"):
        store.submit(
            connection["workspace"], "click", Brief(topic="Changed", brand_id="stripe")
        )
    with pytest.raises(ValueError, match="already running"):
        submit(store)
    submit(store, "bob")
    with pytest.raises(ValueError, match="queue is full"):
        submit(store, "carol")


def test_two_workers_cannot_claim_the_same_deck(store):
    _, job = submit(store)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(store.claim, ("one", "two")))
    assert [item["id"] for item in results if item] == [job["id"]]


def test_stale_worker_cannot_publish_and_capacity_is_released(store):
    connection, job = submit(store)
    store.claim("old")
    store.jobs.document(job["id"]).update({"lease": time.time() - 1})
    assert not store.heartbeat(job["id"], "old")
    store.finish(
        job["id"], "old", "completed", result={"https_url": "https://example.com/wrong"}
    )
    assert store.claim("new") is None
    saved = store.snapshot(connection["workspace"])
    assert saved["status"] == "interrupted" and saved["result"] is None
    assert store.control.get().to_dict()["active"] == 0
    submit(store)


def test_large_drafts_are_private_objects_not_firestore_documents(store):
    connection, job = submit(store)
    store.claim("worker")
    html = "<h1>世界</h1>" + "x" * 1_100_000
    for revision in (1, 2):
        store.record(
            job["id"],
            "worker",
            {},
            slide={"index": 0, "title": "World", "html": html, "revision": revision},
        )
    assert store.slide(connection["workspace"], job["id"], 0)["html"] == html
    saved = store.snapshot(connection["workspace"])
    assert saved["slides"] == [
        {"slide_index": 0, "title": "World", "revision": 2, "kind": "draft"}
    ]
    assert "html" not in str(
        store.jobs.document(job["id"]).get().to_dict()["slides"]["0"].keys()
    )
    assert len(store.drafts.values) == 1
    store.record(
        job["id"],
        "worker",
        {},
        slide={
            "index": 0,
            "title": "World",
            "html": html,
            "revision": 3,
            "kind": "export",
        },
    )
    assert store.slide(connection["workspace"], job["id"], 0)["kind"] == "export"
    assert store.snapshot(connection["workspace"])["slides"][0]["kind"] == "export"


def test_cancellation_dominates_completion_and_releases_quota_once(store):
    connection, job = submit(store)
    store.claim("worker")
    store.cancel(connection["workspace"], job["id"])
    assert not store.heartbeat(job["id"], "worker")
    for _ in range(2):
        store.finish(
            job["id"],
            "worker",
            "completed",
            result={"https_url": "https://example.com/no"},
        )
    saved = store.snapshot(connection["workspace"])
    assert saved["status"] == "cancelled" and saved["result"] is None
    assert store.control.get().to_dict()["active"] == 0
    submit(store)


def test_abandoned_queue_expires_without_running_and_active_jobs_have_no_ttl(store):
    connection, job = submit(store)
    ref = store.jobs.document(job["id"])
    assert "expires_at" not in ref.get().to_dict()
    ref.update({"created": time.time() - QUEUE_WAIT_SECONDS - 1})
    assert store.claim("worker") is None
    assert store.snapshot(connection["workspace"])["status"] == "interrupted"
    assert "expires_at" in ref.get().to_dict()
    assert store.control.get().to_dict()["active"] == 0
    submit(store)
