"""Opt-in, loopback-only MCP Apps server. A2A remains unchanged."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from app.mcp_auth import current_owner
from app.mcp_jobs import Brief, JobStore
from app.mcp_store import DeckStore
from app.tools import MAX_SLIDES

logger = logging.getLogger(__name__)
UI_URI = "ui://pixelpitch/deck-workspace.html"
UI_MIME = "text/html;profile=mcp-app"
APP_ROOT = Path(__file__).resolve().parent.parent
AUTH = {
    "workspace": {"type": "string", "maxLength": 64},
    "capability": {"type": "string", "maxLength": 128},
}


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _tool(
    name: str,
    description: str,
    properties: dict,
    required: list[str],
    *,
    readonly=False,
) -> types.Tool:
    return types.Tool(
        name=name,
        description=description,
        inputSchema=_schema(properties, required),
        annotations=types.ToolAnnotations(
            readOnlyHint=readonly,
            destructiveHint=name == "cancel_deck",
            openWorldHint=False,
        ),
        _meta={"ui": {"resourceUri": UI_URI, "visibility": ["app"]}},
    )


def create_server(store: DeckStore, html_path: Path) -> Server:
    server = Server(
        "Pixelpitch",
        version="0.1.0",
        instructions="Open the Pixelpitch workspace to create a deck. The user reviews the brief and starts generation inside the app; opening it does not start a build.",
    )
    tools = [
        types.Tool(
            name="open_pixelpitch",
            title="Open Pixelpitch",
            description="Open an interactive presentation brief, live draft previews, and PowerPoint download. Returns immediately without starting generation.",
            inputSchema=_schema({"topic": {"type": "string", "maxLength": 4000}}, []),
            annotations=types.ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, openWorldHint=False
            ),
            _meta={"ui": {"resourceUri": UI_URI}},
        ),
        _tool(
            "deck_choices",
            "Load available brands, styles, or reference templates.",
            {**AUTH, "kind": {"enum": ["brands", "styles", "templates"]}},
            [*AUTH, "kind"],
            readonly=True,
        ),
        _tool(
            "start_deck",
            "Queue one deck. Reuse request_id when retrying the same submission.",
            {
                **AUTH,
                "request_id": {"type": "string", "minLength": 1, "maxLength": 100},
                "brief": Brief.model_json_schema(),
            },
            [*AUTH, "request_id", "brief"],
        ),
        _tool(
            "deck_status",
            "Reconnect to the most recent deck, or poll a specific deck.",
            {**AUTH, "job_id": {"type": "string", "maxLength": 64}},
            [*AUTH],
            readonly=True,
        ),
        _tool(
            "deck_slide",
            "Read an authored draft; it has not necessarily passed export checks.",
            {
                **AUTH,
                "job_id": {"type": "string", "maxLength": 64},
                "index": {"type": "integer", "minimum": 0, "maximum": 99},
            },
            [*AUTH, "job_id", "index"],
            readonly=True,
        ),
        _tool(
            "cancel_deck",
            "Request cancellation of this workspace's deck.",
            {**AUTH, "job_id": {"type": "string", "maxLength": 64}},
            [*AUTH, "job_id"],
        ),
    ]

    @server.list_tools()
    async def list_tools():
        return tools

    @server.list_resources()
    async def list_resources():
        return [
            types.Resource(uri=UI_URI, name="Pixelpitch workspace", mimeType=UI_MIME)
        ]

    @server.read_resource()
    async def read_resource(uri):
        if str(uri) != UI_URI:
            raise ValueError("Unknown resource")
        return [
            ReadResourceContents(
                content=html_path.read_text(),
                mime_type=UI_MIME,
                meta={
                    "ui": {
                        "prefersBorder": False,
                        "csp": {
                            "connectDomains": [],
                            "resourceDomains": [],
                            "frameDomains": [],
                        },
                    }
                },
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            if name == "open_pixelpitch":
                # Only persist ownership here; never wait for catalogs or generation.
                connection = await asyncio.to_thread(
                    store.open_workspace, current_owner.get()
                )
                return types.CallToolResult(
                    content=[
                        types.TextContent(
                            type="text",
                            text="Pixelpitch is open. Review the brief and select Generate deck when ready.",
                        )
                    ],
                    structuredContent={
                        "topic": arguments.get("topic", ""),
                        "max_slides": MAX_SLIDES,
                    },
                    _meta={"pixelpitch": connection},
                )
            workspace = arguments["workspace"]
            await asyncio.to_thread(
                store.authorize, workspace, arguments["capability"], current_owner.get()
            )
            if name == "deck_choices":
                from app.tools import list_brands, list_styles, list_templates

                kind = arguments["kind"]
                if kind == "brands":
                    result = await asyncio.to_thread(list_brands)
                elif kind == "styles":
                    result = await asyncio.to_thread(list_styles, page=1, page_size=12)
                else:
                    result = await asyncio.to_thread(list_templates, query="")
                # Specimen HTML is unnecessary here and expensive to transport.
                fields = {
                    "brands": ("id", "name"),
                    "styles": ("id", "name"),
                    "templates": ("revision", "name"),
                }[kind]
                result = {
                    "kind": kind,
                    "choices": [
                        {key: item.get(key, "") for key in fields}
                        for item in result.get(kind, [])
                    ],
                    "available": result.get("status") == "ok",
                }
            elif name == "start_deck":
                result = {
                    "job": await asyncio.to_thread(
                        store.submit,
                        workspace,
                        arguments["request_id"],
                        Brief.model_validate(arguments["brief"]),
                    )
                }
            elif name == "deck_status":
                result = {
                    "job": await asyncio.to_thread(
                        store.snapshot, workspace, arguments.get("job_id")
                    )
                }
            elif name == "deck_slide":
                result = await asyncio.to_thread(
                    store.slide, workspace, arguments["job_id"], arguments["index"]
                )
            elif name == "cancel_deck":
                result = {
                    "job": await asyncio.to_thread(
                        store.cancel, workspace, arguments["job_id"]
                    )
                }
            else:
                raise ValueError("Unknown tool")
            return types.CallToolResult(content=[], structuredContent=result)
        except (ValueError, PermissionError) as error:
            return types.CallToolResult(
                isError=True, content=[types.TextContent(type="text", text=str(error))]
            )
        except Exception:
            logger.exception("MCP tool failed: %s", name)
            return types.CallToolResult(
                isError=True,
                content=[
                    types.TextContent(
                        type="text",
                        text="This request could not be completed. Reconnect and try again.",
                    )
                ],
            )

    return server


def create_app(
    path: Path | None = None,
    port: int = 18091,
    *,
    html_path: Path | None = None,
    store: DeckStore | None = None,
    public_url: str | None = None,
    verifier=None,
    preview_mode: str = "live",
    local_preview_origin: str | None = None,
) -> Starlette:
    from urllib.parse import urlsplit

    from app.mcp_auth import UserAuthMiddleware

    html_path = html_path or APP_ROOT / "mcp-app" / "dist" / "index.html"
    if local_preview_origin:
        origin = urlsplit(local_preview_origin)
        if (public_url or origin.scheme != "https" or not origin.hostname
                or origin.username or origin.password or origin.path
                or origin.query or origin.fragment or "*" in origin.netloc):
            raise ValueError("Local preview origin must be one exact HTTPS origin, without a path")
    local_hosts = [f"127.0.0.1:{port}", f"localhost:{port}"]
    local_origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    if local_preview_origin:
        local_hosts.append(urlsplit(local_preview_origin).netloc)
        local_origins.append(local_preview_origin)
    if public_url and (
        store is None or verifier is None or urlsplit(public_url).scheme != "https"
    ):
        raise ValueError(
            "Cloud MCP requires HTTPS, shared storage and user authentication"
        )
    if store is None:
        if path is None:
            raise ValueError("A local database path is required")
        store = JobStore(path)
    server = create_server(store, html_path)
    manager = StreamableHTTPSessionManager(
        server,
        stateless=True,
        json_response=True,
        security_settings=TransportSecuritySettings(
            allowed_hosts=[urlsplit(public_url).netloc]
            if public_url
            else local_hosts,
            allowed_origins=[public_url]
            if public_url
            else local_origins,
        ),
    )

    @asynccontextmanager
    async def lifespan(_app):
        async with manager.run():
            yield

    class McpEndpoint:
        async def __call__(self, scope, receive, send):
            await manager.handle_request(scope, receive, send)

    async def index(_request: Request):
        host = APP_ROOT / "mcp-app" / "dist" / "host" / "host.html"
        return HTMLResponse(
            host.read_text(),
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'",
            },
        )

    async def health(_request: Request):
        return JSONResponse(
            {
                "status": "ok",
                "mode": "cloud" if public_url else "local-only",
                "ui_built": html_path.exists(),
                "preview_mode": preview_mode,
            }
        )

    return Starlette(
        middleware=[
            Middleware(GZipMiddleware, minimum_size=1024),
            *([Middleware(UserAuthMiddleware, verifier=verifier)] if verifier else []),
        ],
        routes=[
            *([] if public_url else [Route("/", index)]),
            Route("/health", health),
            Route("/mcp", McpEndpoint(), methods=["GET", "POST", "DELETE"]),
        ],
        lifespan=lifespan,
    )
