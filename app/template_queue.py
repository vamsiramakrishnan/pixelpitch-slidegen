"""Durable template preparation requests, shared by Eventarc and A2A."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
import re

from app.template_versions import TemplateVersion


@dataclass(frozen=True)
class TemplateQueue:
    resource: str
    worker_url: str
    service_account: str

    @classmethod
    def from_env(cls) -> TemplateQueue | None:
        queue = os.getenv("SLIDEGEN_TEMPLATE_QUEUE", "")
        if not queue:
            return None
        url = os.getenv("SLIDEGEN_TEMPLATE_WORKER_URL", "").rstrip("/")
        account = os.getenv("SLIDEGEN_TEMPLATE_TASK_SERVICE_ACCOUNT", "")
        if not re.fullmatch(r"projects/[^/]+/locations/[^/]+/queues/[^/]+", queue):
            raise ValueError("invalid SLIDEGEN_TEMPLATE_QUEUE resource")
        if not re.fullmatch(r"https://[a-z0-9.-]+\.run\.app", url) or not account.endswith(".iam.gserviceaccount.com"):
            raise ValueError("template queue requires a private Cloud Run URL and task service account")
        return cls(queue, url, account)


def task_body(version: TemplateVersion, queue: TemplateQueue, *, retry_id: str = "") -> dict:
    if retry_id and not re.fullmatch(r"[a-zA-Z0-9-]{1,40}", retry_id):
        raise ValueError("retry id must be 1-40 letters, digits, or hyphens")
    task_id = version.task_id + ("-" + retry_id if retry_id else "")
    return {"task": {
        "name": f"{queue.resource}/tasks/{task_id}",
        "dispatchDeadline": "1800s",
        "httpRequest": {
            "httpMethod": "POST", "url": queue.worker_url + "/prepare",
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(json.dumps(version.to_dict()).encode()).decode(),
            "oidcToken": {"serviceAccountEmail": queue.service_account, "audience": queue.worker_url},
        },
    }}


def enqueue(version: TemplateVersion, *, retry_id: str = "", session=None) -> str:
    queue = TemplateQueue.from_env()
    if queue is None:
        raise RuntimeError("durable template preparation is not configured")
    payload = task_body(version, queue, retry_id=retry_id)
    if session is None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        with AuthorizedSession(credentials) as authorized:
            return _submit(authorized, queue, payload)
    return _submit(session, queue, payload)


def _submit(session, queue: TemplateQueue, payload: dict) -> str:
    response = session.post(f"https://cloudtasks.googleapis.com/v2/{queue.resource}/tasks", json=payload, timeout=30)
    if response.status_code != 409:
        response.raise_for_status()
    return payload["task"]["name"]
