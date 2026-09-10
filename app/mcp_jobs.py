"""Local durable job queue for the MCP App. No dependency on HTTP lifetimes."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.tools import DEFAULT_SLIDES, MAX_SLIDES

ACTIVE = ("queued", "running", "cancelling")
TERMINAL = ("completed", "failed", "cancelled", "interrupted")
LEASE_SECONDS = 30


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=1, max_length=4000)
    audience: str = Field(default="Leadership team", max_length=1000)
    goal: str = Field(default="Clear, confident, and on-brand", max_length=2000)
    slide_count: int = Field(default=DEFAULT_SLIDES, ge=1, le=MAX_SLIDES)
    brand_id: str = Field(default="", max_length=200)
    style_id: str = Field(default="", max_length=200)
    template_revision: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def direction_required(self):
        if not self.brand_id and not self.template_revision:
            raise ValueError("Choose a brand or a reference template.")
        return self


class JobStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT 'local'
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
                    request_id TEXT NOT NULL, brief TEXT NOT NULL,
                    status TEXT NOT NULL, created REAL NOT NULL,
                    updated REAL NOT NULL, finished REAL,
                    worker TEXT, lease REAL, progress TEXT NOT NULL DEFAULT '{}',
                    result TEXT, error TEXT,
                    UNIQUE(workspace, request_id)
                );
                CREATE TABLE IF NOT EXISTS slides (
                    job TEXT NOT NULL, slide_index INTEGER NOT NULL,
                    title TEXT NOT NULL, html TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    PRIMARY KEY(job, slide_index)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(workspaces)")}
            if "owner" not in columns:
                db.execute(
                    "ALTER TABLE workspaces ADD COLUMN owner TEXT NOT NULL DEFAULT 'local'"
                )
            slide_columns = {row[1] for row in db.execute("PRAGMA table_info(slides)")}
            if "kind" not in slide_columns:
                db.execute(
                    "ALTER TABLE slides ADD COLUMN kind TEXT NOT NULL DEFAULT 'draft'"
                )
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def open_workspace(self, owner: str = "local") -> dict:
        workspace, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute(
                "INSERT INTO workspaces (id,token_hash,owner) VALUES (?, ?, ?)",
                (
                    workspace,
                    hashlib.sha256(token.encode()).hexdigest(),
                    owner,
                ),
            )
        return {"workspace": workspace, "capability": token}

    def authorize(self, workspace: str, capability: str, owner: str = "local") -> None:
        with self.connect() as db:
            row = db.execute(
                "SELECT token_hash FROM workspaces WHERE id=? AND owner=?",
                (workspace, owner),
            ).fetchone()
        supplied = hashlib.sha256(capability.encode()).hexdigest()
        if not hmac.compare_digest(row[0] if row else "", supplied):
            raise PermissionError(
                "This workspace is unavailable. Reopen Pixelpitch from chat."
            )

    def submit(self, workspace: str, request_id: str, brief: Brief) -> dict:
        encoded = brief.model_dump_json()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id, brief FROM jobs WHERE workspace=? AND request_id=?",
                (workspace, request_id),
            ).fetchone()
            if existing:
                if existing["brief"] != encoded:
                    raise ValueError(
                        "This request already belongs to a different brief."
                    )
                job_id = existing["id"]
            else:
                active = db.execute(
                    "SELECT id FROM jobs WHERE workspace=? AND status IN ('queued','running','cancelling')",
                    (workspace,),
                ).fetchone()
                if active:
                    raise ValueError(
                        "A deck is already running. Reconnect to it before starting another."
                    )
                pending = db.execute(
                    "SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancelling')"
                ).fetchone()[0]
                if pending >= 64:
                    raise ValueError(
                        "The local queue is full. Try again after a deck finishes."
                    )
                job_id = uuid.uuid4().hex
                now = time.time()
                db.execute(
                    "INSERT INTO jobs (id,workspace,request_id,brief,status,created,updated) VALUES (?,?,?,?,?,?,?)",
                    (job_id, workspace, request_id, encoded, "queued", now, now),
                )
        return self.snapshot(workspace, job_id)

    def snapshot(self, workspace: str, job_id: str | None = None) -> dict | None:
        with self.connect() as db:
            now = time.time()
            db.execute(
                "UPDATE jobs SET status='interrupted', finished=?, updated=?, error=? "
                "WHERE workspace=? AND status IN ('running','cancelling') AND lease<?",
                (
                    now,
                    now,
                    "The worker stopped responding. Review your brief and start a new attempt.",
                    workspace,
                    now,
                ),
            )
            if job_id:
                row = db.execute(
                    "SELECT * FROM jobs WHERE workspace=? AND id=?", (workspace, job_id)
                ).fetchone()
                if row is None:
                    raise PermissionError("This deck is unavailable in this workspace.")
            else:
                row = db.execute(
                    "SELECT * FROM jobs WHERE workspace=? ORDER BY created DESC LIMIT 1",
                    (workspace,),
                ).fetchone()
                if row is None:
                    return None
            slides = db.execute(
                "SELECT slide_index, title, revision, kind FROM slides WHERE job=? ORDER BY slide_index",
                (row["id"],),
            ).fetchall()
        return {
            "id": row["id"],
            "status": row["status"],
            "created": row["created"],
            "updated": row["updated"],
            "elapsed": round((row["finished"] or time.time()) - row["created"], 1),
            "brief": json.loads(row["brief"]),
            "progress": json.loads(row["progress"]),
            "slides": [dict(slide) for slide in slides],
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
        }

    def slide(self, workspace: str, job_id: str, index: int) -> dict:
        self.snapshot(workspace, job_id)
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM slides WHERE job=? AND slide_index=?", (job_id, index)
            ).fetchone()
        if row is None:
            raise ValueError("This draft is not available yet.")
        return {key: row[key] for key in ("html", "title", "revision", "kind")}

    def cancel(self, workspace: str, job_id: str) -> dict:
        self.snapshot(workspace, job_id)
        now = time.time()
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status='cancelled', finished=?, updated=? WHERE id=? AND status='queued'",
                (now, now, job_id),
            )
            db.execute(
                "UPDATE jobs SET status='cancelling', updated=? WHERE id=? AND status='running'",
                (now, job_id),
            )
        return self.snapshot(workspace, job_id)

    def claim(self, worker: str) -> dict | None:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE jobs SET status='interrupted', finished=?, updated=?, error=? WHERE status IN ('running','cancelling') AND lease<?",
                (
                    now,
                    now,
                    "The worker stopped responding. Review your brief and start a new attempt.",
                    now,
                ),
            )
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET status='running', worker=?, lease=?, updated=? WHERE id=?",
                (worker, now + LEASE_SECONDS, now, row["id"]),
            )
        return {
            "id": row["id"],
            "workspace": row["workspace"],
            "brief": json.loads(row["brief"]),
        }

    def heartbeat(self, job_id: str, worker: str) -> bool:
        with self.connect() as db:
            updated = db.execute(
                "UPDATE jobs SET lease=? WHERE id=? AND worker=? AND status='running' AND lease>?",
                (time.time() + LEASE_SECONDS, job_id, worker, time.time()),
            ).rowcount
        return bool(updated)

    def record(
        self, job_id: str, worker: str, progress: dict, *, slide: dict | None = None
    ) -> None:
        with self.connect() as db:
            updated = db.execute(
                "UPDATE jobs SET progress=?, updated=? WHERE id=? AND worker=? AND status='running' AND lease>?",
                (json.dumps(progress), time.time(), job_id, worker, time.time()),
            ).rowcount
            if updated and slide and len(slide["html"]) <= 2_000_000:
                db.execute(
                    "INSERT INTO slides (job,slide_index,title,html,revision,kind) VALUES (?,?,?,?,?,?) ON CONFLICT(job,slide_index) DO UPDATE SET title=excluded.title,html=excluded.html,revision=excluded.revision,kind=excluded.kind",
                    (
                        job_id,
                        slide["index"],
                        slide["title"],
                        slide["html"],
                        slide["revision"],
                        slide.get("kind", "draft"),
                    ),
                )

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
        now = time.time()
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=CASE WHEN status='cancelling' THEN 'cancelled' ELSE ? END, result=CASE WHEN status='cancelling' THEN NULL ELSE ? END, error=?, updated=?, finished=? WHERE id=? AND worker=? AND status IN ('running','cancelling') AND lease>?",
                (
                    status,
                    json.dumps(result) if result else None,
                    error,
                    now,
                    now,
                    job_id,
                    worker,
                    now,
                ),
            )
