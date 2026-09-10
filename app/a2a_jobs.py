"""Conversation-scoped access to the workstation's durable deck queue.

The A2A adapter does not start workers or depend on an HTTP task lifetime.
Cloud Run must not use this local SQLite backend on its ephemeral filesystem.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app.mcp_jobs import ACTIVE, DEFAULT_SLIDES, Brief, JobStore


class ConversationJobs(JobStore):
    def workspace(self, *, app_name: str, user_id: str, session_id: str) -> str:
        # Identity comes from the ADK invocation, never from an action payload.
        owner = hashlib.sha256(
            json.dumps([app_name, user_id, session_id]).encode()
        ).hexdigest()
        workspace = f"a2a-{owner}"
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO workspaces (id, token_hash, owner) VALUES (?, ?, ?)",
                (workspace, "disabled", owner),
            )
        return workspace


def configured_jobs() -> ConversationJobs:
    if os.getenv("K_SERVICE"):
        raise RuntimeError("The workstation job store cannot run on Cloud Run.")
    value = os.environ.get("SLIDEGEN_A2A_JOB_DB", "")
    if not value or not Path(value).is_absolute():
        raise RuntimeError("An absolute SLIDEGEN_A2A_JOB_DB is required.")
    return ConversationJobs(Path(value))


def submission(values: dict) -> tuple[Brief, str]:
    def scalar(name: str, default=""):
        value = values.get(name, default)
        if isinstance(value, list):
            value = value[0] if value else default
        return str(value if value is not None else default).strip()

    brief = Brief(
        topic=scalar("topic"),
        audience=scalar("audience", "Leadership team"),
        goal=scalar("goal", "Clear, confident, and on-brand"),
        slide_count=scalar("slide_count", str(DEFAULT_SLIDES)),
        brand_id=scalar("brand_id"),
        style_id=scalar("style_id"),
        template_revision=scalar("template_revision"),
    )
    request_id = scalar("request_id")
    if len(request_id) > 128:
        raise ValueError("The submission identifier is too long. Reopen the brief.")
    # Old cards lack a nonce. Replaying their exact brief is still idempotent.
    return brief, request_id or hashlib.sha256(
        brief.model_dump_json().encode()
    ).hexdigest()


def handle_action(
    *, app_name: str, user_id: str, session_id: str, name: str, values: dict
) -> dict | None:
    store = configured_jobs()
    workspace = store.workspace(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    if name == "generate_deck":
        brief, request_id = submission(values)
        try:
            return store.submit(workspace, request_id, brief)
        except ValueError:
            active = store.snapshot(workspace)
            if active and active["status"] in ACTIVE:
                return {**active, "submission_conflict": True}
            raise
    job_id = values.get("job_id") or None
    if job_id is not None and (not isinstance(job_id, str) or len(job_id) > 128):
        raise PermissionError("This deck is unavailable in this conversation.")
    if name == "cancel_deck":
        if not job_id:
            raise ValueError("Choose the deck to cancel using its saved progress card.")
        return store.cancel(workspace, job_id)
    if name == "check_deck":
        return store.snapshot(workspace, job_id)
    raise ValueError("Unsupported deck job action.")


def job_summary(job: dict) -> tuple[str, str]:
    status = job["status"]
    count = job["brief"]["slide_count"]
    if status == "queued":
        detail = (
            "Waiting for the worker. Your request is saved; checking progress will not submit it again."
            if job["elapsed"] >= 15
            else "Your request is saved. Generation runs independently of this chat turn."
        )
        return f"Your {count}-slide deck is queued", detail
    if status == "running":
        progress = job.get("progress") or {}
        current, total = progress.get("current"), progress.get("total")
        detail = (
            f"{current} of {total} slides drafted. Validation and export still follow."
            if isinstance(current, int) and current > 0 and total
            else "Your deck is being prepared. No completed PowerPoint is available yet."
        )
        return str(progress.get("title") or "Preparing your deck"), detail
    return {
        "cancelling": (
            "Stopping your deck",
            "The worker is stopping. Check progress for confirmation.",
        ),
        "cancelled": (
            "Deck cancelled",
            "No new download was published. Your brief is saved.",
        ),
        "interrupted": (
            "Generation was interrupted",
            "The worker stopped responding. Your brief and recorded progress are saved. Review the brief to start a new attempt.",
        ),
        "failed": (
            "Deck generation stopped",
            job.get("error") or "Review your brief and try again.",
        ),
        "completed": (
            "Your editable deck is ready",
            "The PowerPoint is available to download.",
        ),
    }[status]
