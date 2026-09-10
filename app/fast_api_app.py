# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextlib
import os
from collections.abc import AsyncIterator

import google.auth
from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from google.cloud import logging as google_cloud_logging

from app.app_utils import services
from app.app_utils.a2a import attach_a2a_routes
from app.app_utils.reasoning_engine_adapter import (
    attach_reasoning_engine_routes,
)
from app.app_utils.telemetry import (
    setup_agent_engine_telemetry,
    setup_telemetry,
)
from app.app_utils.typing import Feedback

load_dotenv()
setup_telemetry()
# Must run before get_fast_api_app to set the tracer provider resource.
setup_agent_engine_telemetry()
_, project_id = google.auth.default()
logging_client = google_cloud_logging.Client()
logger = logging_client.logger(__name__)
allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Runner for the A2A path, sharing the same session/artifact services as the
    # adk_api and reasoning_engine paths (see services.py). Imported here so the
    # agent is built after env/telemetry setup.
    from app.agent import app as adk_app
    from app.agent import root_agent

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    # Shared by the A2A path and the reasoning_engine adapter routes.
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name
    await attach_a2a_routes(
        app,
        agent=root_agent,
        runner=runner,
        task_store=InMemoryTaskStore(),
        rpc_path=f"/a2a/{adk_app.name}",
    )
    yield


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=False,
    lifespan=lifespan,
)
app.title = "agents/pixelpitch-slidegen"
app.description = "API for interacting with the Agent agents/pixelpitch-slidegen"


# Proxy routes so the Vertex AI Console Playground (reasoning_engine SDK) can
# talk to this agent alongside the native adk_api routes.
attach_reasoning_engine_routes(app)


if os.getenv("A2A_DEBUG_HEADERS"):

    @app.middleware("http")
    async def _log_a2a_request_headers(request, call_next):  # type: ignore[no-untyped-def]
        """Log inbound A2A request headers to diagnose extension activation.

        A2A extensions are opt-in: a client signals activation with the
        ``X-A2A-Extensions`` header. Enable via the ``A2A_DEBUG_HEADERS`` env
        var to confirm whether a caller (e.g. Gemini Enterprise) activates the
        A2UI extension.
        """
        if "/a2a/" in request.url.path:
            redacted = {
                k: v
                for k, v in request.headers.items()
                if k.lower() not in ("authorization", "cookie", "proxy-authorization")
            }
            # NOTE: do NOT read request.body() here. Consuming the body inside
            # a Starlette BaseHTTPMiddleware and replaying it via
            # `request._receive` raises
            # `RuntimeError: Unexpected message received: http.request`
            # and kills every streaming turn. The body probe that did this
            # returned empty results anyway: Gemini Enterprise does not put a
            # user token in the A2A request body. Token delivery, if it
            # happens at all, is via ADK session state (`temp:<AUTH_ID>`),
            # which is probed in app.agent instead.
            logger.log_struct(
                {
                    "event": "a2a_request_headers",
                    "path": request.url.path,
                    "method": request.method,
                    "x_a2a_extensions": request.headers.get("x-a2a-extensions"),
                    "auth_header_prefix": (
                        request.headers.get("authorization", "")[:24] or None
                    ),
                    "headers": redacted,
                },
                severity="WARNING",
            )
        return await call_next(request)


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log feedback.

    Args:
        feedback: The feedback data to log

    Returns:
        Success message
    """
    logger.log_struct(feedback.model_dump(), severity="INFO")
    return {"status": "success"}


# Main execution
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
