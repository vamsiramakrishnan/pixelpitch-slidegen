"""Transactional shared queue with immutable, private draft objects."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.mcp_jobs import ACTIVE, LEASE_SECONDS, TERMINAL, Brief

PREFIX = "pixelpitch_mcp"
QUEUE_WAIT_SECONDS = 3600
INTERRUPTED = (
    "The worker stopped responding. Review your brief and start a new attempt."
)


class GcsDrafts:
    def __init__(self, bucket):
        self.bucket = bucket

    def put(self, key: str, content: bytes) -> None:
        from google.api_core.exceptions import PreconditionFailed

        try:
            self.bucket.blob(key).upload_from_string(
                content,
                content_type="text/html; charset=utf-8",
                if_generation_match=0,
                timeout=15,
            )
        except PreconditionFailed:
            pass  # The name contains the content hash; a retry is the same object.

    def get(self, key: str) -> bytes:
        return self.bucket.blob(key).download_as_bytes(timeout=15, checksum="auto")


class FirestoreJobStore:
    def __init__(self, db, drafts, *, capacity=64, retention_days=7):
        self.db, self.drafts, self.capacity, self.retention_days = (
            db,
            drafts,
            capacity,
            retention_days,
        )
        self.jobs = db.collection(f"{PREFIX}_jobs")
        self.workspaces = db.collection(f"{PREFIX}_workspaces")
        self.owners = db.collection(f"{PREFIX}_owners")
        self.control = db.collection(f"{PREFIX}_control").document("queue")

    def _expires(self):
        return datetime.now(UTC) + timedelta(days=self.retention_days)

    def _transaction(self, callback):
        return firestore.transactional(callback)(self.db.transaction(max_attempts=5))

    def open_workspace(self, owner: str = "local") -> dict:
        workspace, capability = uuid.uuid4().hex, secrets.token_urlsafe(32)
        self.workspaces.document(workspace).create(
            {
                "owner": owner,
                "token_hash": hashlib.sha256(capability.encode()).hexdigest(),
                "expires_at": self._expires(),
                "latest_job": None,
            }
        )
        return {"workspace": workspace, "capability": capability}

    def authorize(self, workspace: str, capability: str, owner: str = "local") -> None:
        # Tool schemas bound lengths; reject separators at the storage boundary.
        if "/" in workspace or not workspace:
            raise PermissionError("This workspace is unavailable.")
        data = self.workspaces.document(workspace).get().to_dict() or {}
        if (
            data.get("owner") != owner
            or data.get("expires_at", datetime.min.replace(tzinfo=UTC))
            <= datetime.now(UTC)
            or not hmac.compare_digest(
                data.get("token_hash", ""),
                hashlib.sha256(capability.encode()).hexdigest(),
            )
        ):
            raise PermissionError(
                "This workspace is unavailable. Reopen Pixelpitch from chat."
            )

    def submit(self, workspace: str, request_id: str, brief: Brief) -> dict:
        job_id = hashlib.sha256(f"{workspace}|{request_id}".encode()).hexdigest()
        ref, space = self.jobs.document(job_id), self.workspaces.document(workspace)
        encoded = brief.model_dump()

        def create(tx):
            current = ref.get(transaction=tx).to_dict()
            if current:
                if current["brief"] != encoded:
                    raise ValueError(
                        "This request already belongs to a different brief."
                    )
                return
            ws = space.get(transaction=tx).to_dict()
            if not ws or ws["expires_at"] <= datetime.now(UTC):
                raise PermissionError(
                    "This workspace expired. Reopen Pixelpitch from chat."
                )
            owner_ref = self.owners.document(ws["owner"])
            owner = owner_ref.get(transaction=tx).to_dict() or {}
            queue = self.control.get(transaction=tx).to_dict() or {"active": 0}
            active = (
                self.jobs.document(owner["active_job"]).get(transaction=tx).to_dict()
                if owner.get("active_job")
                else None
            )
            if active and active["status"] in ACTIVE:
                raise ValueError(
                    "A deck is already running for your account. Finish or cancel it before starting another."
                )
            if queue["active"] >= self.capacity:
                raise ValueError("The queue is full. Try again after a deck finishes.")
            now = time.time()
            tx.create(
                ref,
                {
                    "id": job_id,
                    "workspace": workspace,
                    "owner": ws["owner"],
                    "brief": encoded,
                    "status": "queued",
                    "created": now,
                    "updated": now,
                    "finished": None,
                    "worker": None,
                    "lease": 0,
                    "progress": {},
                    "slides": {},
                    "result": None,
                    "error": None,
                },
            )
            tx.set(self.control, {"active": queue["active"] + 1})
            tx.set(owner_ref, {"active_job": job_id})
            tx.update(space, {"latest_job": job_id, "expires_at": self._expires()})

        self._transaction(create)
        return self.snapshot(workspace, job_id)

    def _owned(self, workspace, job_id):
        if not job_id or "/" in job_id:
            raise PermissionError("This deck is unavailable in this workspace.")
        job = self.jobs.document(job_id).get().to_dict()
        if (
            not job
            or job["workspace"] != workspace
            or job.get("expires_at", datetime.max.replace(tzinfo=UTC))
            <= datetime.now(UTC)
        ):
            raise PermissionError("This deck is unavailable in this workspace.")
        return job

    def _terminal(self, tx, ref, job, status, *, result=None, error=None):
        # Read before any writes, including the global capacity counter.
        queue = self.control.get(transaction=tx).to_dict() or {"active": 0}
        now = time.time()
        cancelled = job["status"] == "cancelling" or status == "cancelled"
        tx.update(
            ref,
            {
                "status": "cancelled" if cancelled else status,
                "result": None if cancelled else result,
                "error": error,
                "updated": now,
                "finished": now,
                "expires_at": self._expires(),
            },
        )
        tx.set(self.control, {"active": max(0, queue["active"] - 1)})
        tx.set(
            self.owners.document(job["owner"]),
            {"active_job": None, "expires_at": self._expires()},
        )

    def _expire(self, job_id):
        ref = self.jobs.document(job_id)

        def expire(tx):
            job = ref.get(transaction=tx).to_dict()
            if job and self._stale(job):
                self._terminal(tx, ref, job, "interrupted", error=INTERRUPTED)

        self._transaction(expire)

    def snapshot(self, workspace: str, job_id: str | None = None) -> dict | None:
        if not job_id:
            ws = self.workspaces.document(workspace).get().to_dict() or {}
            job_id = ws.get("latest_job")
            if not job_id:
                return None
        job = self._owned(workspace, job_id)
        if self._stale(job):
            self._expire(job_id)
            job = self._owned(workspace, job_id)
        return {
            key: job[key]
            for key in (
                "id",
                "status",
                "created",
                "updated",
                "brief",
                "progress",
                "result",
                "error",
            )
        } | {
            "elapsed": round((job["finished"] or time.time()) - job["created"], 1),
            "slides": [
                {key: item[key] for key in ("slide_index", "title", "revision")}
                | {"kind": item.get("kind", "draft")}
                for item in sorted(
                    job["slides"].values(), key=lambda item: item["slide_index"]
                )
            ],
        }

    def slide(self, workspace: str, job_id: str, index: int) -> dict:
        job = self._owned(workspace, job_id)
        item = job["slides"].get(str(index))
        if not item:
            raise ValueError("This draft is not available yet.")
        content = self.drafts.get(item["object"])
        if (
            len(content) > 8_000_000
            or hashlib.sha256(content).hexdigest() != item["sha256"]
        ):
            raise ValueError("This draft could not be verified. Please retry.")
        return {
            "html": content.decode("utf-8"),
            "title": item["title"],
            "revision": item["revision"],
            "kind": item.get("kind", "draft"),
        }

    def cancel(self, workspace: str, job_id: str) -> dict:
        self._owned(workspace, job_id)
        ref = self.jobs.document(job_id)

        def cancel(tx):
            job = ref.get(transaction=tx).to_dict()
            if job["status"] == "queued":
                self._terminal(tx, ref, job, "cancelled")
            elif job["status"] == "running":
                tx.update(ref, {"status": "cancelling", "updated": time.time()})

        self._transaction(cancel)
        return self.snapshot(workspace, job_id)

    def claim(self, worker: str) -> dict | None:
        # There are at most capacity active jobs. This single-field query needs
        # no composite index and also reaps dead workers when no widget is open.
        candidates = list(
            self.jobs.where(filter=FieldFilter("status", "in", list(ACTIVE))).stream()
        )
        for snapshot in sorted(candidates, key=lambda item: item.to_dict()["created"]):
            ref = snapshot.reference

            def claim(tx, ref=ref):
                job = ref.get(transaction=tx).to_dict()
                if not job or job["status"] not in ACTIVE:
                    return None
                now = time.time()
                if self._stale(job):
                    self._terminal(tx, ref, job, "interrupted", error=INTERRUPTED)
                    return None
                if job["status"] != "queued":
                    return None
                tx.update(
                    ref,
                    {
                        "status": "running",
                        "worker": worker,
                        "lease": now + LEASE_SECONDS,
                        "updated": now,
                    },
                )
                return {
                    "id": job["id"],
                    "workspace": job["workspace"],
                    "brief": job["brief"],
                }

            claimed = self._transaction(claim)
            if claimed:
                return claimed
        return None

    def heartbeat(self, job_id: str, worker: str) -> bool:
        ref = self.jobs.document(job_id)

        def beat(tx):
            job = ref.get(transaction=tx).to_dict()
            if not self._live(job, worker):
                return False
            tx.update(ref, {"lease": time.time() + LEASE_SECONDS})
            return True

        return self._transaction(beat)

    @staticmethod
    def _stale(job):
        if job["status"] == "queued":
            return job["created"] + QUEUE_WAIT_SECONDS <= time.time()
        return (
            job["status"] in ("running", "cancelling") and job["lease"] <= time.time()
        )

    @staticmethod
    def _live(job, worker):
        return (
            job
            and job["worker"] == worker
            and job["status"] == "running"
            and job["lease"] > time.time()
        )

    def record(
        self, job_id: str, worker: str, progress: dict, *, slide: dict | None = None
    ) -> None:
        ref = self.jobs.document(job_id)
        item = None
        if slide and len(slide["html"]) <= 2_000_000:
            content = slide["html"].encode("utf-8")
            digest = hashlib.sha256(content).hexdigest()
            key = f"drafts/{job_id}/{slide['index']}/{digest}.html"
            self.drafts.put(key, content)
            item = {
                "slide_index": slide["index"],
                "title": slide["title"][:500],
                "revision": slide["revision"],
                "kind": slide.get("kind", "draft"),
                "object": key,
                "sha256": digest,
            }

        def record(tx):
            job = ref.get(transaction=tx).to_dict()
            if self._live(job, worker):
                changes = {"progress": progress, "updated": time.time()}
                if item:
                    changes[f"slides.{slide['index']}"] = item
                tx.update(ref, changes)

        self._transaction(record)

    def finish(
        self,
        job_id: str,
        worker: str,
        status: str,
        *,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        if status not in TERMINAL:
            raise ValueError("A terminal job status is required")
        ref = self.jobs.document(job_id)

        def finish(tx):
            job = ref.get(transaction=tx).to_dict()
            if (
                job
                and job["worker"] == worker
                and job["status"] in ("running", "cancelling")
                and job["lease"] > time.time()
            ):
                self._terminal(tx, ref, job, status, result=result, error=error)

        self._transaction(finish)
