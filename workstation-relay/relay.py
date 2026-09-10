"""Private, streaming A2A front door for a single approved workstation port.

Cloud Run IAM authenticates the caller. This process exchanges its workload
identity for a workstation token; it never runs an agent or replays a request.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import google.auth
import httpx
from fastapi import FastAPI, Request
from google.auth import impersonated_credentials
from google.auth.transport.requests import AuthorizedSession
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("pixelpitch.workstation_relay")
_RESOURCE = re.compile(
    r"projects/[^/]+/locations/[^/]+/workstationClusters/[^/]+/"
    r"workstationConfigs/[^/]+/workstations/([a-z0-9-]+)"
)
_REQUEST_HEADERS = {
    "accept",
    "content-type",
    "content-length",
    "x-a2a-extensions",
    "traceparent",
    "x-cloud-trace-context",
}
_RESPONSE_HEADERS = {
    "content-type",
    "content-encoding",
    "content-length",
    "x-a2a-extensions",
}


@dataclass(frozen=True)
class Settings:
    resource: str
    origin: str
    account: str
    port: int

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        resource = env.get("WORKSTATION_RELAY_RESOURCE", "")
        match = _RESOURCE.fullmatch(resource)
        origin = env.get("WORKSTATION_RELAY_ORIGIN", "")
        parsed = urlsplit(origin)
        port = int(env.get("WORKSTATION_RELAY_PORT", "18090"))
        account = env.get("WORKSTATION_RELAY_CALLER_SA", "")
        if not match or not 10000 < port <= 65535:
            raise ValueError("An exact workstation resource and high port are required")
        expected = f"{port}-{match.group(1)}."
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.startswith(expected)
            or not parsed.hostname.endswith(".cloudworkstations.dev")
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Relay origin must be the exact approved workstation HTTPS port origin"
            )
        if not re.fullmatch(
            r"[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com", account
        ):
            raise ValueError("An explicit relay caller service account is required")
        return cls(resource, origin, account, port)


class WorkstationToken:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = asyncio.Lock()
        self._token = ""
        self._refresh_at = 0.0

    def _mint(self) -> tuple[str, float]:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        delegated = impersonated_credentials.Credentials(
            source_credentials=credentials,
            target_principal=self.settings.account,
            target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
            lifetime=900,
        )
        with AuthorizedSession(delegated) as session:
            response = session.post(
                f"https://workstations.googleapis.com/v1/{self.settings.resource}:generateAccessToken",
                json={"port": self.settings.port, "ttl": "900s"},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        expires = datetime.fromisoformat(
            data["expireTime"].replace("Z", "+00:00")
        ).timestamp()
        return data["accessToken"], min(expires - 60, time.time() + 840)

    async def get(self) -> str:
        async with self._lock:
            if time.time() >= self._refresh_at:
                self._token, self._refresh_at = await asyncio.to_thread(self._mint)
            return self._token

    def invalidate(self):
        self._refresh_at = 0.0


class Relay:
    def __init__(
        self, settings: Settings, client: httpx.AsyncClient, token: WorkstationToken
    ):
        self.settings, self.client, self.token = settings, client, token

    async def forward(self, request: Request):
        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        headers = {
            key: value
            for key, value in request.headers.items()
            if key in _REQUEST_HEADERS
        }
        try:
            headers["Authorization"] = "Bearer " + await self.token.get()
            headers["Accept-Encoding"] = "identity"
            headers["X-Pixelpitch-Relay-Request-ID"] = request_id
            # The target is fixed configuration, never derived from a request URL.
            target = self.settings.origin + request.url.path
            if request.url.query:
                target += "?" + request.url.query
            upstream = await self.client.send(
                self.client.build_request(
                    request.method, target, headers=headers, content=request.stream()
                ),
                stream=True,
                follow_redirects=False,
            )
        except Exception as error:
            # Token values and upstream bodies never enter logs or responses.
            logger.error(
                "relay_unavailable request_id=%s error_type=%s",
                request_id,
                type(error).__name__,
            )
            return JSONResponse(
                {
                    "error": "Workstation unavailable. Retry after the preview server is running.",
                    "request_id": request_id,
                },
                status_code=503,
            )

        if upstream.status_code in {401, 403} or 300 <= upstream.status_code < 400:
            await upstream.aclose()
            self.token.invalidate()
            logger.error(
                "relay_auth_rejected request_id=%s status=%s",
                request_id,
                upstream.status_code,
            )
            return JSONResponse(
                {
                    "error": "Workstation proxy authentication failed.",
                    "request_id": request_id,
                },
                status_code=503,
            )

        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key in _RESPONSE_HEADERS
        }
        response_headers.update(
            {
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "X-Pixelpitch-Execution": "cloud-workstation-relay",
                "X-Pixelpitch-Relay-Request-ID": request_id,
            }
        )
        logger.info(
            "relay_headers request_id=%s method=%s path=%s status=%s elapsed_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            upstream.status_code,
            (time.perf_counter() - started) * 1000,
        )

        async def body():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                logger.info(
                    "relay_closed request_id=%s elapsed_ms=%.1f",
                    request_id,
                    (time.perf_counter() - started) * 1000,
                )

        return StreamingResponse(
            body(),
            status_code=upstream.status_code,
            headers=response_headers,
            background=BackgroundTask(upstream.aclose),
        )


def create_app(settings: Settings | None = None, *, client=None, token=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        config = settings or Settings.from_env()
        transport = client or httpx.AsyncClient(
            timeout=httpx.Timeout(900, connect=10, pool=10),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
        )
        application.state.relay = Relay(
            config, transport, token or WorkstationToken(config)
        )
        try:
            yield
        finally:
            if client is None:
                await transport.aclose()

    application = FastAPI(
        lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )

    @application.get("/healthz")
    async def health():
        return {"mode": "cloud-workstation-relay", "upstream_checked": False}

    @application.post("/a2a/app")
    @application.get("/a2a/app/.well-known/agent-card.json")
    async def forward(request: Request):
        return await application.state.relay.forward(request)

    return application


app = create_app()
