"""Production-only MCP API and independent always-CPU worker entrypoints."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.mcp_auth import GoogleTokenVerifier
from app.mcp_firestore import FirestoreJobStore, GcsDrafts
from app.mcp_server import create_app as create_mcp_app
from app.mcp_worker import run_job

logger = logging.getLogger(__name__)


class CloudConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    role: Literal["api", "worker"]
    project: str = Field(pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
    database: str = Field(pattern=r"^[a-z][a-z0-9-]{2,61}[a-z0-9]$")
    bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    artifact_bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    renderer_url: str
    public_url: str
    oauth_client_id: str = Field(
        pattern=r"^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$"
    )
    allowed_domains: frozenset[str] = Field(min_length=1)
    queue_capacity: int = Field(default=64, ge=1, le=256)
    job_timeout_seconds: int = Field(default=1800, ge=60, le=7200)

    @model_validator(mode="after")
    def validate_hosts(self):
        renderer = urlsplit(self.renderer_url)
        if (
            renderer.scheme != "https"
            or not renderer.hostname
            or renderer.path
            or renderer.query
            or renderer.fragment
            or renderer.username
        ):
            raise ValueError("SLIDEGEN_RENDERER_URL must be an HTTPS service origin")
        if self.artifact_bucket == self.bucket:
            raise ValueError(
                "MCP drafts need a dedicated bucket, separate from renderer artifacts"
            )
        parsed = urlsplit(self.public_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".run.app")
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.port
        ):
            raise ValueError(
                "MCP_PUBLIC_URL must be the exact HTTPS Cloud Run origin without a trailing slash"
            )
        import re

        if any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", domain)
            for domain in self.allowed_domains
        ):
            raise ValueError("Specify exact lowercase email domains, without wildcards")
        return self

    @classmethod
    def from_env(cls):
        if os.getenv("FIRESTORE_EMULATOR_HOST") or os.getenv("STORAGE_EMULATOR_HOST"):
            raise ValueError(
                "Production entrypoints cannot use emulated cloud services"
            )
        return cls(
            role=os.environ["MCP_ROLE"],
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            database=os.environ["MCP_DATABASE"],
            bucket=os.environ["MCP_DRAFT_BUCKET"],
            artifact_bucket=os.environ["SLIDEGEN_GCS_BUCKET"],
            renderer_url=os.environ["SLIDEGEN_RENDERER_URL"],
            public_url=os.environ["MCP_PUBLIC_URL"],
            oauth_client_id=os.environ["MCP_OAUTH_CLIENT_ID"],
            allowed_domains=frozenset(
                filter(None, os.environ["MCP_ALLOWED_EMAIL_DOMAINS"].split(","))
            ),
            queue_capacity=int(os.getenv("MCP_QUEUE_CAPACITY", "64")),
            job_timeout_seconds=int(os.getenv("MCP_JOB_TIMEOUT_SECONDS", "1800")),
        )


def create_app():
    from google.cloud import firestore, storage

    config = CloudConfig.from_env()
    db = firestore.Client(project=config.project, database=config.database)
    blobs = storage.Client(project=config.project)
    store = FirestoreJobStore(
        db, GcsDrafts(blobs.bucket(config.bucket)), capacity=config.queue_capacity
    )
    if config.role == "api":
        html = (
            Path(__file__).resolve().parent.parent / "mcp-app" / "dist" / "index.html"
        )
        if not html.is_file():
            raise RuntimeError("The MCP App bundle is missing")
        client = httpx.AsyncClient(follow_redirects=False)
        app = create_mcp_app(
            store=store,
            html_path=html,
            public_url=config.public_url,
            verifier=GoogleTokenVerifier(
                config.oauth_client_id, config.allowed_domains, client
            ),
        )
        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app):
            try:
                await asyncio.to_thread(store.control.get, retry=None, timeout=5)
                async with original_lifespan(app):
                    yield
            finally:
                await client.aclose()
                db.close()
                blobs.close()

        app.router.lifespan_context = lifespan
        return app

    @asynccontextmanager
    async def worker_lifespan(app):
        await asyncio.to_thread(store.control.get, retry=None, timeout=5)
        worker = uuid.uuid4().hex

        async def consume():
            while True:
                try:
                    job = await asyncio.to_thread(store.claim, worker)
                    if job:
                        await run_job(
                            job,
                            store,
                            worker,
                            heartbeat_seconds=2,
                            timeout_seconds=config.job_timeout_seconds,
                        )
                    else:
                        await asyncio.sleep(2)
                except Exception:
                    logger.exception("MCP worker could not access the shared queue")
                    await asyncio.sleep(5)

        task = asyncio.create_task(consume())
        app.state.worker = task
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            db.close()
            blobs.close()

    async def health(request):
        running = not request.app.state.worker.done()
        return JSONResponse(
            {"status": "ok" if running else "unavailable", "mode": "worker"},
            200 if running else 503,
        )

    return Starlette(routes=[Route("/health", health)], lifespan=worker_lifespan)
