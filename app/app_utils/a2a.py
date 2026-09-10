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

"""Attach A2A (Agent2Agent) endpoints to the FastAPI app.

func:`attach_a2a_routes` registers the dynamic
agent-card endpoint and the JSON-RPC endpoint so the same app serves A2A
alongside the adk_api routes, reachable by A2A clients and Gemini Enterprise A2A
registration.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

from a2a.server.agent_execution import AgentExecutor as BaseA2aAgentExecutor
from a2a.server.agent_execution import RequestContext
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.apps.jsonrpc import DefaultCallContextBuilder
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import TaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    DataPart,
    Part,
    TextPart,
)
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    EXTENDED_AGENT_CARD_PATH,
)
from a2ui.a2a.extension import (
    get_a2ui_agent_extension,
    try_activate_a2ui_extension,
)
from a2ui.a2a.parts import create_a2ui_part  # noqa: F401 (re-exported convenience)
from a2ui.adk.a2a.part_converter import A2uiPartConverter
from a2ui.schema.constants import VERSION_0_9
from google.adk.a2a.converters.event_converter import convert_event_to_a2a_events
from google.adk.a2a.executor.a2a_agent_executor import (
    A2aAgentExecutor,
    A2aAgentExecutorConfig,
)
from google.adk.a2a.utils.agent_card_builder import AgentCardBuilder

from app.app_utils.a2ui_format import get_a2ui_format, get_supported_catalog_ids
from app.tools import _preview_key, get_cached_fragments
from app.turns import encode_action_turn

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext
    from a2a.server.events import Event as A2aEvent
    from fastapi import FastAPI
    from google.adk.a2a.converters.part_converter import (
        GenAIPartToA2APartConverter,
    )
    from google.adk.agents import BaseAgent
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.events.event import Event as AdkEvent
    from google.adk.runners import Runner

logger = logging.getLogger(__name__)

# URI advertised on the agent card describing the executor extension shipped
# by ADK. Kept as a module-level constant so callers can override or extend
# the capabilities list when needed.
_ADK_AGENT_EXECUTOR_EXTENSION_URI = (
    "https://google.github.io/adk-docs/a2a/a2a-extension/"
)
_A2UI_EXTENSION_URI = "https://a2ui.org/a2a-extension/a2ui/v0.9"


def _default_capabilities() -> AgentCapabilities:
    """Returns the default A2A capabilities used by scaffolded projects."""
    return AgentCapabilities(
        streaming=True,
        extensions=[
            AgentExtension(
                uri=_ADK_AGENT_EXECUTOR_EXTENSION_URI,
                description=("Ability to use the new agent executor implementation"),
            ),
            get_a2ui_agent_extension(
                version=VERSION_0_9,
                accepts_inline_catalogs=False,
                # Must be the Gemini Enterprise composite catalog, otherwise GE
                # cannot match the surface and renders text only.
                supported_catalog_ids=get_supported_catalog_ids(),
            ),
        ],
    )


class _A2uiActivatingContextBuilder(DefaultCallContextBuilder):
    """Confirm the A2UI extension back to the caller, not just on the card.

    An A2A extension is negotiated. The card only says the agent can speak
    A2UI; the caller then asks for it per request with ``X-A2A-Extensions``,
    and the server has to answer with the same header naming what it turned
    on. Until that answer arrives the extension is off, and a conformant
    client can only treat an A2UI ``DataPart`` as an opaque blob. Gemini
    Enterprise draws it as an attachment chip captioned with the part's mime
    type, which is where ``application/json+a2ui: Unsupported attachment``
    came from. The surface was well formed. Nobody was told to read it.

    The SDK emits that response header from
    ``ServerCallContext.activated_extensions`` and nothing was filling it in.
    ADK activates only its own executor extension, and only on the code path
    this agent deliberately does not take (see ``use_legacy`` below), so the
    set was empty on every turn and the header never appeared at all.

    Activation belongs here rather than in the executor because of when the
    header is written. A streaming turn hands the SDK an async generator that
    has not started, and the SDK reads the context to build the SSE response
    headers at that moment. An executor that activates on its first line still
    runs after the headers are on the wire, which is the whole traffic Gemini
    Enterprise uses.
    """

    def __init__(self, agent_card: AgentCard) -> None:
        super().__init__()
        self._agent_card = agent_card

    def build(self, request) -> ServerCallContext:  # type: ignore[no-untyped-def]
        context = super().build(request)
        try:
            # RequestContext is a thin view here purely to reach
            # add_activated_extension, which writes through to `context`.
            # Version selection stays in a2ui rather than being re-derived.
            version = try_activate_a2ui_extension(
                RequestContext(call_context=context), self._agent_card
            )
        except Exception:  # pragma: no cover - never fail a turn over a header
            logger.exception("could not negotiate the A2UI extension")
            return context
        if version:
            logger.info("activated A2UI extension v%s", version)
        elif context.requested_extensions:
            logger.warning(
                "no A2UI extension matched; requested %s",
                sorted(context.requested_extensions),
            )
        return context


def _resolve_app_url(app_url: str | None) -> str:
    """Resolve the public base URL advertised inside the agent card.

    Falls back in order: explicit ``app_url``, the ``APP_URL`` env var, the
    Agent Runtime ``/api`` passthrough self-built from runtime env vars (valid
    on the first deploy, before the CLI knows the server-assigned engine ID),
    then a local default.
    """
    if app_url:
        return app_url
    if env_url := os.getenv("APP_URL"):
        return env_url

    agent_engine_id = os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    # Not GOOGLE_CLOUD_LOCATION: the agent pins it to "global", which would build
    # an invalid "global-aiplatform.googleapis.com" URL.
    location = os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION", "us-central1")
    if agent_engine_id and project and location:
        return (
            f"https://{location}-aiplatform.googleapis.com/reasoningEngines/v1"
            f"/projects/{project}/locations/{location}"
            f"/reasoningEngines/{agent_engine_id}/api"
        )

    return "http://0.0.0.0:8000"


def _expand_preview_token(value, fragments: dict):
    """Recursively replace HTML placeholders with their real documents.

    The model emits ``IFrameSrcdoc.htmlContent`` as a short token (slide
    preview, brand specimens, style specimens) so it never has to reproduce
    large markup. Matching is lenient because the model may wrap the token in
    surrounding text.
    """
    if isinstance(value, str):
        for token, html in fragments.items():
            if value == token:
                return html
            if token in value:
                value = value.replace(token, html)
        return value
    if isinstance(value, list):
        return [_expand_preview_token(v, fragments) for v in value]
    if isinstance(value, dict):
        return {k: _expand_preview_token(v, fragments) for k, v in value.items()}
    return value


def _substitute_preview_in_events(events, fragments: dict) -> None:
    """Expand placeholder tokens inside every A2A data part, in place."""
    for event in events:
        for message in (
            getattr(getattr(event, "status", None), "message", None),
            getattr(event, "artifact", None),
            event if getattr(event, "parts", None) else None,
        ):
            for part in getattr(message, "parts", None) or []:
                root = getattr(part, "root", None)
                if isinstance(root, DataPart) and root.data:
                    root.data = _expand_preview_token(root.data, fragments)


_DOWNLOAD_TEXT = re.compile(r"^\s*Download(?:\s+PPTX)?:\s*(https?://\S+)\s*$", re.I)


def _promote_download_links_in_events(events) -> None:
    """Turn model-emitted PPTX URL text into a native A2UI openUrl button."""
    for event in events:
        for message in (
            getattr(getattr(event, "status", None), "message", None),
            getattr(event, "artifact", None),
            event if getattr(event, "parts", None) else None,
        ):
            for part in getattr(message, "parts", None) or []:
                root = getattr(part, "root", None)
                if not isinstance(root, DataPart) or not isinstance(root.data, dict):
                    continue
                update = root.data.get("updateComponents")
                if not isinstance(update, dict):
                    continue
                components = update.get("components")
                if not isinstance(components, list):
                    continue
                additions = []
                for component in components:
                    if (
                        not isinstance(component, dict)
                        or component.get("component") not in ("Text", "MaterialText")
                        or not isinstance(component.get("text"), str)
                    ):
                        continue
                    match = _DOWNLOAD_TEXT.match(component["text"])
                    if not match:
                        continue
                    component_id = str(component.get("id") or "download_pptx")
                    label_id = f"{component_id}_label"
                    component.clear()
                    component.update(
                        {
                            "id": component_id,
                            "component": "Button",
                            "child": label_id,
                            "variant": "primary",
                            "action": {
                                "functionCall": {
                                    "call": "openUrl",
                                    "args": {"url": match.group(1)},
                                    "returnType": "void",
                                }
                            },
                        }
                    )
                    additions.append(
                        {
                            "id": label_id,
                            "component": "Text",
                            "text": "Download PPTX",
                            "variant": "body",
                        }
                    )
                components.extend(additions)


class _StaticCatalogA2uiEventConverter:
    """Converts ADK events to A2A events using a process-wide A2UI catalog.

    Unlike ``a2ui.adk.a2a.event_converter.A2uiEventConverter``, which looks
    the catalog up in (possibly non-serializable) session state, this variant
    pins one static catalog so it works identically on in-memory sessions and
    Agent Runtime's Vertex-managed sessions.
    """

    def __init__(self, catalog, fallback_text: str | None = None) -> None:
        self._part_converter = A2uiPartConverter(
            catalog,
            bypass_tool_check=False,
            fallback_text=fallback_text,
            version=VERSION_0_9,
        ).convert

    def __call__(
        self,
        event: AdkEvent,
        invocation_context: InvocationContext,
        task_id: str | None = None,
        context_id: str | None = None,
        part_converter_func: GenAIPartToA2APartConverter | None = None,
    ) -> list[A2aEvent]:
        events = convert_event_to_a2a_events(
            event,
            invocation_context,
            task_id,
            context_id,
            self._part_converter,
        )
        fragments = get_cached_fragments(_preview_key(invocation_context))
        if fragments:
            try:
                _substitute_preview_in_events(events, fragments)
            except Exception:  # pragma: no cover - never break the turn
                logger.exception("failed to substitute preview fragments")
        try:
            _promote_download_links_in_events(events)
        except Exception:  # pragma: no cover - never break the turn
            logger.exception("failed to promote PPTX download link")
        return events


# Mime types we accept as a reference deck upload.
REFERENCE_MIME_TYPES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
)


def _capture_reference_upload(message, session_key: str | None) -> str | None:
    """Stash an attached deck so `use_reference_deck` can pick it up.

    A2A delivers uploads as FileParts. They are held per session rather than
    passed through the model, because base64 of a whole deck must never enter
    the prompt.
    """
    if message is None or not session_key or not getattr(message, "parts", None):
        return None
    for part in message.parts:
        root = getattr(part, "root", None)
        file_obj = getattr(root, "file", None)
        if file_obj is None:
            continue
        mime = (getattr(file_obj, "mime_type", "") or "").lower()
        name = (getattr(file_obj, "name", "") or "").lower()
        looks_like_deck = mime in REFERENCE_MIME_TYPES or name.endswith(
            (".pdf", ".pptx", ".ppt")
        )
        if not looks_like_deck:
            continue
        payload = getattr(file_obj, "bytes", None)
        if not payload:
            logger.info("attachment %r has no inline bytes; skipping", name)
            continue
        from app.tools import cache_reference, get_reference

        existing = get_reference(session_key) or {}
        existing["_pending_upload"] = payload
        cache_reference(session_key, existing)
        logger.info("captured reference upload %r (%s)", name, mime)
        return name or mime
    return None


def _translate_incoming_a2ui_actions(message) -> bool:
    """Rewrite A2UI action DataParts as a canonical transport envelope.

    A2UI v0.9 clients submit forms as
    ``DataPart(data={"version": ..., "action": {"name": ..., "context": {...}}})``. ADK's default conversion
    The custom root agent parses this versioned envelope directly. No model is
    asked to reinterpret user-controlled prose as a tool invocation.
    """
    if message is None or not getattr(message, "parts", None):
        return False

    action_texts: list[str] = []
    remaining: list[Part] = []
    for part in message.parts:
        root = getattr(part, "root", None)
        if isinstance(root, DataPart):
            data = root.data or {}
            action = data.get("action") or data.get("userAction")
            if isinstance(action, dict):
                event = action.get("event")
                if isinstance(event, dict):
                    action = event
                name = action.get("name", "unknown")
                context_data = action.get("context") or {}
                if not isinstance(context_data, dict):
                    context_data = {}
                action_texts.append(encode_action_turn(str(name), context_data))
                continue
        remaining.append(part)

    if not action_texts:
        return False

    translated = [Part(root=TextPart(text="\n\n".join(action_texts)))]
    message.parts = translated + remaining
    return True


class _A2uiActionExecutor(BaseA2aAgentExecutor):
    """Translate A2UI actions and capture reference uploads at ingress."""

    def __init__(self, delegate: BaseA2aAgentExecutor) -> None:
        self._delegate = delegate

    async def execute(self, context, event_queue) -> None:  # type: ignore[override]
        message = getattr(context, "message", None)
        try:
            _translate_incoming_a2ui_actions(message)
        except Exception:  # pragma: no cover - never block execution
            logger.exception("Failed to translate incoming A2UI actions")
        try:
            key = getattr(context, "context_id", None)
            if key:
                _capture_reference_upload(message, f"upload:{key}")
        except Exception:  # pragma: no cover - never block execution
            logger.exception("Failed to capture reference upload")

        # Progress now originates as ordinary ADK Events inside PixelpitchAgent.
        # The same event stream feeds Dev UI and the installed A2A converter,
        # eliminating duplicate polling and the misleading intake-only status.
        await self._delegate.execute(context, event_queue)

    async def cancel(self, context, event_queue):  # type: ignore[override]
        return await self._delegate.cancel(context, event_queue)


class _SlidegenA2aExecutor(A2aAgentExecutor):
    """Binds a captured upload to the real session id.

    An upload is captured in `execute` under the A2A ``context_id``, which is
    all that exists at that point. The ADK session is created afterwards and,
    with Vertex-managed sessions, gets a server-assigned id. This is the one
    place where both identifiers are in scope, so the entry is re-keyed here.
    """

    async def _prepare_session(self, context, run_request, runner):  # type: ignore[override]
        session = await super()._prepare_session(context, run_request, runner)
        try:
            from app.tools import rebind_reference

            context_id = getattr(context, "context_id", None)
            if context_id and getattr(session, "id", None):
                if rebind_reference(f"upload:{context_id}", f"session:{session.id}"):
                    logger.info("bound reference upload to session %s", session.id)
        except Exception:  # pragma: no cover - never block the turn
            logger.exception("could not bind reference upload to session")
        return session


def _build_slidegen_executor(runner: Runner) -> BaseA2aAgentExecutor:
    """Builds the A2UI-aware executor used by this project."""
    # Same format instance the system prompt was generated from, so emitted
    # createSurface messages carry the catalog id advertised on the agent card.
    catalog = get_a2ui_format().get_selected_catalog()
    config = A2aAgentExecutorConfig(
        event_converter=_StaticCatalogA2uiEventConverter(
            catalog,
            fallback_text="Here is your deck update.",
        ),
    )
    # use_legacy=True is required for A2UI over streaming.
    #
    # The legacy _handle_request drives a TaskResultAggregator that retains the
    # latest working status message and, at the end of the turn, re-emits its
    # *converted* parts as TaskArtifactUpdateEvent(last_chunk=True) followed by
    # a completed status. So the final artifact carries the real A2UI DataParts.
    #
    # With the default (use_legacy=False) implementation the A2UI JSON arrives
    # as a raw <a2ui-json> text artifact and the final status update carries no
    # parts at all, so Gemini Enterprise finds nothing renderable and shows text
    # only.
    delegate = _SlidegenA2aExecutor(runner=runner, config=config, use_legacy=True)
    return _A2uiActionExecutor(delegate)


async def attach_a2a_routes(
    app: FastAPI,
    *,
    agent: BaseAgent,
    runner: Runner,
    task_store: TaskStore,
    rpc_path: str,
    capabilities: AgentCapabilities | None = None,
    agent_version: str | None = None,
    app_url: str | None = None,
) -> None:
    """Register A2A routes (JSON-RPC + agent-card endpoints) under ``rpc_path``.

    Builds a dynamic agent card from ``agent`` and mounts the routes on ``app``.
    The ``runner`` should share the session/artifact/memory services with the
    standard ADK path. ``capabilities``, ``agent_version``, and ``app_url``
    override their defaults (streaming + ADK extension, ``AGENT_VERSION``,
    ``APP_URL``). Call once per app — typically in a FastAPI ``lifespan``, since
    the card is built asynchronously; repeated calls register duplicate routes.
    """
    resolved_app_url = _resolve_app_url(app_url)
    resolved_agent_version = agent_version or os.getenv("AGENT_VERSION", "0.1.0")
    resolved_capabilities = capabilities or _default_capabilities()

    agent_card = await AgentCardBuilder(
        agent=agent,
        capabilities=resolved_capabilities,
        rpc_url=f"{resolved_app_url}{rpc_path}",
        agent_version=resolved_agent_version,
    ).build()

    # Advertise deck uploads so clients offer an attachment control. Output
    # stays text/plain: A2UI surfaces travel as data parts, not as a mode.
    agent_card.default_input_modes = list(
        dict.fromkeys([*(agent_card.default_input_modes or []), *REFERENCE_MIME_TYPES])
    )

    request_handler = DefaultRequestHandler(
        agent_executor=_build_slidegen_executor(runner),
        task_store=task_store,
    )

    a2a_app = A2AFastAPIApplication(
        agent_card=agent_card,
        http_handler=request_handler,
        context_builder=_A2uiActivatingContextBuilder(agent_card),
    )
    a2a_app.add_routes_to_app(
        app,
        agent_card_url=f"{rpc_path}{AGENT_CARD_WELL_KNOWN_PATH}",
        rpc_url=rpc_path,
        extended_agent_card_url=f"{rpc_path}{EXTENDED_AGENT_CARD_PATH}",
    )
