"""Private Eventarc receiver and Cloud Tasks worker, separate from A2A/MCP."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from google.cloud import storage

from app.template_queue import enqueue
from app.template_versions import TemplateVersion, is_current, publish_bundle, ready_prefix

app = FastAPI(title="Pixelpitch template preparation")
logger = logging.getLogger(__name__)


def configured_source(version: TemplateVersion) -> bool:
    return version.is_source(os.environ.get("SLIDEGEN_GCS_BUCKET", ""), os.environ.get("SLIDEGEN_TEMPLATE_PREFIX", "templates/"))


def record_status(bucket, version: TemplateVersion, state: str, **details) -> None:
    bucket.blob(version.status_path).upload_from_string(json.dumps({
        **version.to_dict(), "task_id": version.task_id, "state": state,
        "updated_at": datetime.now(timezone.utc).isoformat(), **details,
    }), content_type="application/json")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/events")
async def storage_event(request: Request):
    if request.headers.get("ce-type") != "google.cloud.storage.object.v1.finalized":
        return Response(status_code=204)
    try:
        version = TemplateVersion.from_dict(await request.json())
    except (ValueError, TypeError, AttributeError):
        return JSONResponse(status_code=400, content={"error": "invalid storage event"})
    if not configured_source(version):
        return Response(status_code=204)
    # Enqueue only. Model work must not run inside Eventarc's delivery request.
    import asyncio
    try:
        task = await asyncio.to_thread(enqueue, version)
    except Exception:
        logger.exception("template event enqueue failed: %s", version.task_id)
        return JSONResponse(status_code=503, content={"error": "queue unavailable"})
    return {"task": task}


def prepare_version(version: TemplateVersion) -> dict:
    client = storage.Client()
    bucket = client.bucket(os.environ["SLIDEGEN_GCS_BUCKET"])
    if not is_current(client, version):
        record_status(bucket, version, "superseded")
        return {"state": "superseded"}
    existing = ready_prefix(bucket, version)
    if existing:
        record_status(bucket, version, "ready", bundle_prefix=existing)
        return {"state": "ready", "bundle_prefix": existing}
    record_status(bucket, version, "running")
    from app.deck_run import display_name_for_object, slug_for_object
    from app.tools import _build_catalog

    result = {}

    def publish(manifest, artifacts):
        if not is_current(client, version):
            result.update(state="superseded")
            return
        prefix = publish_bundle(bucket, version, manifest, artifacts)
        result.update(state="ready", bundle_prefix=prefix)

    try:
        _build_catalog(version.uri, slug_for_object(version.name), display_name_for_object(version.name), generation=version.generation, publisher=publish)
        if not result:
            raise RuntimeError("template preparation produced no publication result")
        record_status(bucket, version, **result)
        return result
    except Exception as exc:
        # A duplicate attempt cannot turn a committed bundle into a failure.
        if existing := ready_prefix(bucket, version):
            record_status(bucket, version, "ready", bundle_prefix=existing)
            return {"state": "ready", "bundle_prefix": existing}
        if not is_current(client, version):
            record_status(bucket, version, "superseded")
            return {"state": "superseded"}
        record_status(bucket, version, "failed", error=str(exc)[:800])
        raise


@app.post("/prepare")
def prepare(payload: dict):
    try:
        version = TemplateVersion.from_dict(payload)
    except (ValueError, TypeError, AttributeError):
        return JSONResponse(status_code=400, content={"error": "invalid template version"})
    if not configured_source(version):
        return JSONResponse(status_code=403, content={"error": "source not allowed"})
    try:
        return prepare_version(version)
    except Exception:
        logger.exception("template preparation failed: %s", version.task_id)
        return JSONResponse(status_code=503, content={"error": "preparation failed; Cloud Tasks may retry"})
