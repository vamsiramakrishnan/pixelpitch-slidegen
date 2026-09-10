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

"""Slidegen agent tools: brand guidelines and PPTX rendering via slidify."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html as html_lib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from google.adk.tools import ToolContext

from app.progress import report_progress

logger = logging.getLogger(__name__)

BRANDS_DIR = Path(__file__).resolve().parent / "assets" / "brands"
MAX_GUIDELINE_CHARS = 14_000
# Env-overridable deck size ceiling; the renderer enforces its own cap. Every
# surface that bounds a slide count reads these two, so a brief form can never
# refuse a size the pipeline would have accepted.
MAX_SLIDES = int(os.getenv("SLIDEGEN_MAX_SLIDES", "20"))
DEFAULT_SLIDES = 6
MAX_TOTAL_HTML_CHARS = 400_000
# Inlined assets are not authored markup, so they get their own budget. This
# tracks the renderer's payload cap, which is what actually has to accept it.
MAX_TOTAL_PAYLOAD_CHARS = 24_000_000
_DATA_URI = re.compile(r"data:[^\"')\s]+")

# Placeholder the model puts in `IFrameSrcdoc.htmlContent`. The real preview
# document is substituted during A2A conversion (see app_utils.a2a), so the
# model never has to reproduce tens of KB of slide HTML inside its JSON output.
SLIDE_PREVIEW_TOKEN = "__SLIDE_PREVIEW_HTML__"
SLIDE_PREVIEW_IMAGE_TOKEN_PREFIX = "__SLIDE_PREVIEW_IMAGE_"
BRAND_SPECIMEN_TOKEN = "__BRAND_SPECIMENS_HTML__"
STYLE_SPECIMEN_TOKEN = "__STYLE_SPECIMENS_HTML__"
SLIDE_PREVIEW_STATE_KEY = "slide_preview_html"

# Guard against emitting an unreasonably large data part.
MAX_PREVIEW_CHARS = 2_000_000

PIPELINE_WORKERS = max(1, min(int(os.getenv("SLIDEGEN_PIPELINE_WORKERS", "4")), 8))
_PIPELINE_POOL = ThreadPoolExecutor(
    max_workers=PIPELINE_WORKERS,
    thread_name_prefix="slidegen-pipeline",
)


async def _run_in_pipeline_pool(func, **kwargs):
    """Run a blocking agent tool without monopolising the A2A event loop."""
    cancellation_event = threading.Event()
    future = _PIPELINE_POOL.submit(
        func, cancellation_event=cancellation_event, **kwargs
    )
    try:
        while not future.done():
            await asyncio.sleep(0.02)
        return future.result()
    except asyncio.CancelledError:
        cancellation_event.set()
        future.cancel()
        raise


def _cancelled(cancellation_event: threading.Event | None) -> bool:
    return cancellation_event is not None and cancellation_event.is_set()


def _cancelled_result() -> dict:
    return {
        "status": "error",
        "code": "cancelled",
        "error": "Deck generation was cancelled before delivery.",
    }


# In-process handoff of the preview document from the tool to the A2A event
# converter.
#
# Session state is not usable here: a tool's `state_delta` is not committed to
# `invocation_context.session.state` until after the event is appended, so the
# converter still sees the pre-tool snapshot. The tool call and the conversion
# of the model's reply happen in the same process and the same invocation, so a
# bounded in-memory map keyed by invocation id is both sufficient and exact.
_PREVIEW_CACHE: OrderedDict[str, dict] = OrderedDict()
_PREVIEW_CACHE_MAX = 32


def _preview_key(ctx) -> str | None:
    """Stable key shared by the tool call and the A2A event conversion.

    Session id is used rather than invocation id: ADK hands the tool a
    ``Context`` whose ``invocation_id`` differs from the ``InvocationContext``
    seen by the event converter, so keying on invocation id never matches. The
    session is the same object for both, and is per-user, so a session-scoped
    key cannot leak one user's deck preview into another user's surface.
    """
    session = getattr(ctx, "session", None)
    session_id = getattr(session, "id", None)
    if session_id:
        return f"session:{session_id}"
    for attr in ("invocation_id", "_invocation_id"):
        value = getattr(ctx, attr, None)
        if value:
            return f"invocation:{value}"
    return None


def cache_fragment(key: str, token: str, html: str) -> None:
    """Stash one substitutable HTML fragment for a session."""
    bucket = _PREVIEW_CACHE.setdefault(key, {})
    bucket[token] = html
    _PREVIEW_CACHE.move_to_end(key)
    while len(_PREVIEW_CACHE) > _PREVIEW_CACHE_MAX:
        _PREVIEW_CACHE.popitem(last=False)


def get_cached_fragments(key: str | None) -> dict:
    """All substitutable fragments for a session, keyed by token."""
    if not key:
        return {}
    return _PREVIEW_CACHE.get(key) or {}


def clear_cached_fragments(key: str) -> None:
    """Release a worker session's fragments after its durable handoff."""
    _PREVIEW_CACHE.pop(key, None)


def _preview_image_token(index: int) -> str:
    return f"{SLIDE_PREVIEW_IMAGE_TOKEN_PREFIX}{index + 1:02d}__"


def _cache_preview_image_tokens(
    key: str, images_b64: list[str], mime_type: str = "image/jpeg"
) -> list[str]:
    tokens: list[str] = []
    for index, image in enumerate(images_b64):
        token = _preview_image_token(index)
        cache_fragment(key, token, f"data:{mime_type};base64,{image}")
        tokens.append(token)
    return tokens


def _cache_binary_preview(
    key: str,
    *,
    namespace: str,
    item_id: str,
    value: bytes,
    mime_type: str = "image/png",
) -> str:
    """Cache one flat image behind a collision-safe session-scoped token."""
    digest = hashlib.sha256(
        namespace.encode() + b"\0" + item_id.encode() + b"\0" + value
    ).hexdigest()[:20]
    safe_namespace = re.sub(r"[^A-Z0-9]+", "_", namespace.upper()).strip("_")
    token = f"__{safe_namespace}_IMAGE_{digest}__"
    data_url = f"data:{mime_type};base64,{base64.b64encode(value).decode('ascii')}"
    cache_fragment(key, token, data_url)
    return token


def _build_preview_html(
    slides: list[dict], images_b64: list[str], mime_type: str = "image/jpeg"
) -> str:
    """Build a flat gallery from images of the actual converted PPTX.

    The previous preview nested one ``srcdoc`` iframe per authored HTML slide
    inside A2UI's sandboxed iframe. Gemini Enterprise can block that nested
    browsing context. Data-URL images need no scripts, network, or nested
    frames and show the PowerPoint output rather than the source HTML.
    """
    blocks: list[str] = []
    for i, image in enumerate(images_b64):
        slide = slides[i] if i < len(slides) else {}
        title = html_lib.escape(str(slide.get("title") or f"Slide {i + 1}"))
        blocks.append(
            f'<figure class="slide">'
            f'<img loading="lazy" src="data:{mime_type};base64,{image}" '
            f'alt="{title}">'
            f"<figcaption>{i + 1}. {title}</figcaption>"
            f"</figure>"
        )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>"
        "*{box-sizing:border-box}"
        "body{margin:0;padding:12px;background:#f1f3f4;"
        "font:13px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#3c4043}"
        ".slide{margin:0 0 16px}"
        "img{display:block;width:100%;height:auto;border-radius:8px;"
        "background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.2)}"
        "figcaption{margin-top:6px;color:#5f6368}"
        "</style></head><body>" + "".join(blocks) + "</body></html>"
    )


RENDER_TIMEOUT_SECONDS = 900.0

_ID_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_ID_TOKEN_LIFETIME_SECONDS = 45 * 60


def _renderer_auth_headers(renderer_url: str) -> dict[str, str]:
    """Auth headers for the renderer service.

    Preference order:
    1. ``X-API-Key`` when ``SLIDEGEN_RENDERER_API_KEY`` is set (local dev).
    2. A short-lived Google ID token minted via the IAM Credentials API for
       ``SLIDEGEN_RENDERER_INVOKER_SA`` (production: Cloud Run verifies the
       token against its own URL through roles/run.invoker).
    3. An ID token from Application Default Credentials. This lets a Cloud
       Workstation or Cloud Run service call the renderer with its own
       workload identity when it already has ``roles/run.invoker``.
    """
    api_key = os.getenv("SLIDEGEN_RENDERER_API_KEY", "")
    headers = {"X-API-Key": api_key} if api_key else {}

    invoker_sa = os.getenv("SLIDEGEN_RENDERER_INVOKER_SA", "")
    cache_key = f"{invoker_sa or 'adc'}:{renderer_url}"
    cached = _ID_TOKEN_CACHE.get(cache_key)
    if cached and cached[1] > time.time():
        return {**headers, "Authorization": f"Bearer {cached[0]}"}

    if invoker_sa:
        from google.cloud.iam_credentials import IAMCredentialsClient
        from google.iam.v1 import iam_policy_pb2  # noqa: F401 (ensures proto deps)

        client = IAMCredentialsClient()
        token = client.generate_id_token(
            name=f"projects/-/serviceAccounts/{invoker_sa}",
            audience=renderer_url,
            include_email=True,
        ).token
    else:
        from google.auth import exceptions as auth_exceptions
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        try:
            token = id_token.fetch_id_token(Request(), renderer_url)
        except (auth_exceptions.GoogleAuthError, ValueError) as error:
            logger.warning("renderer workload identity unavailable: %s", error)
            return headers

    _ID_TOKEN_CACHE[cache_key] = (
        token,
        time.time() + _ID_TOKEN_LIFETIME_SECONDS,
    )
    return {**headers, "Authorization": f"Bearer {token}"}


def _load_brand_index() -> list[dict]:
    with (BRANDS_DIR / "index.json").open(encoding="utf-8") as f:
        return json.load(f)


def list_brands(tool_context: ToolContext = None) -> dict:  # type: ignore[assignment]
    """List the brand identities available for slide deck generation.

    Call this whenever the user has not picked a brand, or to confirm a
    requested brand exists. Never invent brand ids.

    Returns:
        Dict with a ``brands`` list (``id``, ``name``, ``category``,
        ``description``, ``palette``, ``fonts``) and a ``specimen_token`` to
        embed in an ``IFrameSrcdoc`` so the user can SEE each brand.
    """
    from app.specimens import brand_fonts, brand_palette, brand_specimens_html

    brands = _load_brand_index()
    enriched = [
        {
            **b,
            "palette": list(brand_palette(b["id"]))[:5],
            "fonts": list(brand_fonts(b["id"]))[:2],
        }
        for b in brands
    ]
    result = {"status": "ok", "brands": enriched}
    if tool_context is not None:
        key = _preview_key(tool_context)
        if key:
            cache_fragment(key, BRAND_SPECIMEN_TOKEN, brand_specimens_html(brands))
            result["specimen_token"] = BRAND_SPECIMEN_TOKEN
    return result


def list_styles(
    query: str = "",
    page: int = 1,
    page_size: int = 8,
    tool_context: ToolContext = None,  # type: ignore[assignment]
) -> dict:
    """Search and page through the available visual STYLE systems.

    Style is a separate axis from brand: brand fixes the palette and type
    personality, style fixes the editorial treatment (grid, layout language,
    decorative vocabulary). Call this when the user has not picked a style.
    Prefer a short query based on the requested mood or treatment. Results are
    always paginated; request ``next_page`` only when the user needs more.

    Args:
        query: Optional case-insensitive search over id, name, tagline, mood,
            and fonts. Empty returns the full catalog in catalog order.
        page: One-based result page.
        page_size: Results per page, from 1 to 12. Defaults to 8.

    Returns:
        Dict with the current ``styles`` page, ``total_style_count``,
        ``has_more``, and ``next_page``. When a preview context is available,
        ``specimen_token`` contains only the current page's specimens.
    """
    from app.specimens import load_styles, style_specimens_html

    if page < 1:
        return {"status": "error", "error": "page must be at least 1"}
    if not 1 <= page_size <= 12:
        return {"status": "error", "error": "page_size must be between 1 and 12"}

    styles = load_styles()
    normalized_query = query.strip().casefold()
    if normalized_query:
        styles = [
            style
            for style in styles
            if normalized_query
            in " ".join(
                [
                    str(style.get("id", "")),
                    str(style.get("name", "")),
                    str(style.get("tagline", "")),
                    *[str(value) for value in style.get("mood", [])],
                    *[str(value) for value in style.get("fonts", [])],
                ]
            ).casefold()
        ]

    total_style_count = len(styles)
    start = (page - 1) * page_size
    page_styles = styles[start : start + page_size]
    if total_style_count and not page_styles:
        last_page = (total_style_count + page_size - 1) // page_size
        return {
            "status": "error",
            "error": f"page {page} is out of range; last page is {last_page}",
            "total_style_count": total_style_count,
        }

    slim = [
        {
            "id": s["id"],
            "name": s["name"],
            "tagline": s["tagline"],
            "mood": s.get("mood", [])[:4],
            "fonts": s.get("fonts", [])[:2],
        }
        for s in page_styles
    ]
    has_more = start + len(page_styles) < total_style_count
    result = {
        "status": "ok",
        "query": query.strip(),
        "page": page,
        "page_size": page_size,
        "style_count": len(slim),
        "total_style_count": total_style_count,
        "has_more": has_more,
        "next_page": page + 1 if has_more else None,
        "styles": slim,
    }
    if tool_context is not None:
        key = _preview_key(tool_context)
        if key:
            cache_fragment(
                key,
                STYLE_SPECIMEN_TOKEN,
                style_specimens_html(page_styles),
            )
            result["specimen_token"] = STYLE_SPECIMEN_TOKEN
    return result


def _resolve_expression_catalog(slug: str, *, bundle_prefix: str = "") -> tuple[list[dict], list[tuple[str, bytes]]] | None:
    """Fetch one catalog by slug: local cache dir, then GCS.

    Returns ``(entries, images)`` where images are (id, png_bytes) for a
    browsable gallery, or None when no catalog exists for the template.
    """
    local_dir = os.getenv("SLIDEGEN_CATALOG_DIR", "")

    def _from_dir(directory) -> tuple[list[dict], list[tuple[str, bytes]]] | None:
        manifest = directory / "catalog.json"
        if not manifest.is_file():
            return None
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        if isinstance(entries, dict):
            entries = entries.get("entries") or entries.get("catalog") or []
        images = []
        for entry in entries[:12]:
            png = directory / f"{entry.get('id')}.png"
            if png.is_file():
                images.append((str(entry.get("id")), png.read_bytes()))
        return entries, images

    if local_dir and not bundle_prefix:
        found = _from_dir(Path(local_dir))
        if found:
            return found

    bucket = os.getenv("SLIDEGEN_GCS_BUCKET", "")
    if not bucket:
        return None
    try:
        from google.cloud import storage

        client = storage.Client()
        prefix = bundle_prefix or f"catalogs/{slug}/"
        blob = client.bucket(bucket).blob(f"{prefix}catalog.json")
        if not blob.exists():
            return None
        entries = json.loads(blob.download_as_bytes())
        if isinstance(entries, dict):
            entries = entries.get("entries") or entries.get("catalog") or []
        images = []
        for entry in entries[:12]:
            entry_id = str(entry.get("id") or "")
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", entry_id):
                continue
            image_prefix = prefix + "catalog/" if bundle_prefix else prefix
            image_blob = client.bucket(bucket).blob(f"{image_prefix}{entry_id}.png")
            if image_blob.exists():
                images.append((str(entry.get("id")), image_blob.download_as_bytes()))
        return entries, images
    except Exception:
        logger.exception("expression catalog lookup failed for %s", slug)
        return None

# Warm catalog cache: expression catalogs are immutable per template, and the
# first GCS hit costs ~2s. A short TTL keeps mentions instant.
_CATALOG_CACHE: dict[str, tuple[float, tuple[list[dict], list[tuple[str, bytes]]]]] = {}
_CATALOG_CACHE_TTL_SECONDS = 600.0

# Slugs with a precompute turn already running. A build is ten minutes of
# Vertex calls, and the message shown to the user when a template is not yet
# admitted tells them to retry, so retries land here mid-build by design.
# Without this they queue a second identical turn that competes with the first
# for the same model quota and makes both slower.
_CATALOG_BUILDS_IN_FLIGHT: set[str] = set()
# Why the last precompute turn for a slug ended without a bundle. A build
# fails for reasons a retry will not change, such as a turn that never wrote
# brand-contract.json, and the unadmitted-template reply promises the user a
# deck in about ten minutes. Without the reason here that promise repeats
# every time, ten minutes of Vertex spend at a time, and never comes true.
_CATALOG_BUILD_FAILURES: dict[str, str] = {}
_CATALOG_BUILD_LOCK = threading.Lock()


def _load_expression_catalog(template_name: str, *, template_gs_uri: str = "") -> tuple[list[dict], list[tuple[str, bytes]]] | None:
    """Resolve a precomputed expression catalog: cache, local dir, then GCS."""
    import re as _re
    import time as _time

    slug = _re.sub(r"[^a-z0-9]+", "-", (template_name or "").lower()).strip("-") or "default"
    bundle_prefix = ""
    cache_key = slug
    if template_gs_uri:
        from google.cloud import storage
        from app.template_versions import TemplateVersion, ready_prefix
        try:
            client = storage.Client()
            source_bucket, _, source_name = template_gs_uri[5:].partition("/")
            source = client.bucket(source_bucket).blob(source_name)
            source.reload()
            version = TemplateVersion(source_bucket, source_name, str(source.generation))
            bundle_prefix = ready_prefix(client.bucket(os.environ["SLIDEGEN_GCS_BUCKET"]), version) or ""
            cache_key = version.task_id
            if not bundle_prefix:
                return None
        except Exception:
            logger.exception("versioned expression catalog unavailable")
            return None
    now = _time.monotonic()
    cached = _CATALOG_CACHE.get(cache_key)
    if cached and now - cached[0] < _CATALOG_CACHE_TTL_SECONDS:
        return cached[1]

    resolved = _resolve_expression_catalog(slug, bundle_prefix=bundle_prefix) if bundle_prefix else _resolve_expression_catalog(slug)
    if resolved is not None:
        _CATALOG_CACHE[cache_key] = (now, resolved)
    return resolved


def _catalog_exists_in_gcs(slug: str) -> bool:
    bucket = os.getenv("SLIDEGEN_GCS_BUCKET", "")
    if not bucket:
        return False
    try:
        from google.cloud import storage

        return storage.Client().bucket(bucket).blob(
            f"catalogs/{slug}/catalog.json"
        ).exists()
    except Exception:
        return False


def _stage_template_source(workdir: Path, source_uri: str, generation: str) -> dict[str, bytes]:
    import io
    import zipfile

    from app.deck_run import _local_artifact_path
    from app.template_baselines import compile_source_baselines

    renderer_url = os.getenv("SLIDEGEN_RENDERER_URL", "").rstrip("/")
    if not renderer_url:
        raise RuntimeError("source template preparation requires the renderer")
    response = httpx.post(
        f"{renderer_url}/template-source",
        json={"gs_uri": source_uri, "generation": generation},
        headers=_renderer_auth_headers(renderer_url), timeout=900,
    )
    response.raise_for_status()
    if "application/zip" not in response.headers.get("content-type", ""):
        raise RuntimeError(f"source template preparation failed: {response.json().get('error', 'archive missing')}")
    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)) or sum(item.file_size for item in bundle.infolist()) > 256_000_000:
            raise ValueError("invalid or oversized source archive")
        artifacts = {}
        for name in names:
            if (Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name
                    or not (name == "source-extraction.json" or name.startswith(("spec/", "base/", "clean/")))):
                raise ValueError(f"invalid source artifact path: {name}")
            artifacts[name] = bundle.read(name)
    evidence = json.loads(artifacts["source-extraction.json"])
    if evidence["source_sha256"] != hashlib.sha256((workdir / "template.pptx").read_bytes()).hexdigest():
        raise ValueError("renderer extracted a different template revision")
    artifacts.update(compile_source_baselines(artifacts))
    for name, value in artifacts.items():
        target = _local_artifact_path(workdir, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    return artifacts


def _build_catalog(
    template_gs_uri: str, slug: str, display_name: str, *,
    generation: str = "", publisher: Callable | None = None,
) -> None:
    """Background: derive + precompute + upload an expression catalog.

    Runs in the pipeline pool after a template is ingested, so the NEXT
    mention of the template gets instant expressions. Failures are logged
    and never surface to the user — the catalog is an enhancement.
    """
    import tempfile
    from pathlib import Path

    from app.agy_authoring import generate as agy_turn
    from app.deck_run import DECK_SYSTEM

    workdir = Path(tempfile.mkdtemp(prefix=f"catalog-{slug}-"))
    try:
        from google.cloud import storage

        bucket_name, _, key = template_gs_uri[5:].partition("/")
        source_blob = storage.Client().bucket(bucket_name).blob(key)
        source_blob.reload()
        if generation and str(source_blob.generation) != generation:
            raise ValueError("template generation changed before preparation")
        template_path = workdir / "template.pptx"
        template_path.write_bytes(source_blob.download_as_bytes(if_generation_match=source_blob.generation))
        staged_source = _stage_template_source(workdir, template_gs_uri, str(source_blob.generation))

        agy_turn(
            contents=(
                f"Work only in {workdir}. Run the precompute playbook against "
                f"the staged template.pptx, spec-out/, clean/, and compiled baselines/ there. The template's "
                f"display name is {display_name!r}. Uploading is done for you; "
                "leave the catalog and the prepared bundle in the workspace. "
                "Work until gated."
            ),
            system=DECK_SYSTEM,
            model=os.getenv("SLIDEGEN_REFERENCE_MODEL", "gemini-3.7-flash"),
            workspaces=[str(workdir)],
        )

        manifest = workdir / "catalog.json"
        if not manifest.is_file():
            raise RuntimeError("catalog turn produced no catalog.json")
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        if isinstance(entries, dict):
            entries = entries.get("entries") or entries.get("catalog") or []
        # Admit source-compiled baselines and the author's catalog together.
        # The prepared manifest is uploaded last as the commit marker.
        from app.template_policy import (
            SourceIdentity,
            build_prepared_manifest,
            sha256_bytes,
        )

        # Collected, not asserted. build_prepared_manifest is the one place
        # that rules on admissibility and it names every missing deliverable
        # at once. Reading one straight into the dict instead throws ten
        # minutes of precompute away on a bare FileNotFoundError that names
        # only whichever file the literal happened to reach first.
        prepared_artifacts: dict[str, bytes] = {
            "template.pptx": template_path.read_bytes()
        }
        for name, source in (
            ("brand-contract.json", workdir / "brand-contract.json"),
            ("catalog.json", manifest),
            ("slide-types.json", workdir / "slide-types.json"),
            ("source-extraction.json", workdir / "source-extraction.json"),
        ):
            if source.is_file():
                prepared_artifacts[name] = source.read_bytes()
        for section, folder in (
            ("spec", workdir / "spec-out" / "spec"),
            ("base", workdir / "spec-out" / "base"),
            ("clean", workdir / "clean"),
            ("baselines", workdir / "baselines"),
            ("specimens", workdir / "specimens"),
        ):
            if not folder.is_dir():
                continue
            for artifact in folder.rglob("*"):
                if artifact.is_file():
                    prepared_artifacts[
                        section + "/" + artifact.relative_to(folder).as_posix()
                    ] = artifact.read_bytes()
        # Source bytes and compiled shells are not authoring output. Detect a
        # rewritten plate/spec before considering any generated catalog.
        for name, value in staged_source.items():
            if prepared_artifacts.get(name) != value:
                raise ValueError(f"precompute changed immutable source artifact: {name}")
        from app.template_baselines import compile_source_baselines

        if "brand-contract.json" in prepared_artifacts:
            contract = json.loads(prepared_artifacts["brand-contract.json"])
            protected = {int(n) for item in contract.get("protected", []) for n in item.get("slides", [])}
            prepared_artifacts.update(compile_source_baselines(staged_source, protected))
        if publisher is not None:
            for entry in entries:
                entry_id = str(entry.get("id") or "")
                if not re.fullmatch(r"[a-zA-Z0-9_-]+", entry_id):
                    raise ValueError("invalid catalog entry id")
                png = workdir / "catalog" / f"{entry_id}.png"
                if png.is_file():
                    prepared_artifacts[f"catalog/{entry_id}.png"] = png.read_bytes()
        prepared_manifest = build_prepared_manifest(
            source=SourceIdentity(
                gs_uri=template_gs_uri,
                generation=str(source_blob.generation or ""),
                etag=str(source_blob.etag or ""),
                sha256=sha256_bytes(prepared_artifacts["template.pptx"]),
            ),
            artifacts=prepared_artifacts,
        )
        if publisher is not None:
            publisher(prepared_manifest, prepared_artifacts)
            with _CATALOG_BUILD_LOCK:
                _CATALOG_BUILD_FAILURES.pop(slug, None)
            return
        client = storage.Client()
        bucket = client.bucket(os.getenv("SLIDEGEN_GCS_BUCKET", ""))
        count = 0
        for entry in entries:
            png = workdir / "catalog" / f"{entry.get('id')}.png"
            if png.is_file():
                bucket.blob(f"catalogs/{slug}/{entry.get('id')}.png").upload_from_filename(png)
                count += 1
        bucket.blob(f"catalogs/{slug}/catalog.json").upload_from_string(
            json.dumps(entries), content_type="application/json"
        )
        prefix = f"templates/{slug}/prepared/"
        for logical_path, value in prepared_artifacts.items():
            bucket.blob(prefix + logical_path).upload_from_string(value)
        bucket.blob(prefix + "manifest.json").upload_from_string(
            prepared_manifest.to_json(), content_type="application/json"
        )
        logger.info(
            "catalog %s uploaded (%d pngs) + prepared bundle %s",
            slug,
            count,
            prepared_manifest.bundle_id[:12],
        )
        with _CATALOG_BUILD_LOCK:
            _CATALOG_BUILD_FAILURES.pop(slug, None)
    except Exception as exc:
        logger.exception("background catalog build failed for %s", slug)
        with _CATALOG_BUILD_LOCK:
            _CATALOG_BUILD_FAILURES[slug] = str(exc) or exc.__class__.__name__
        if publisher is not None:
            raise
    finally:
        with _CATALOG_BUILD_LOCK:
            _CATALOG_BUILDS_IN_FLIGHT.discard(slug)


def maybe_build_catalog(template_gs_uri: str, display_name: str) -> bool:
    """Enqueue a background expression-catalog build. True if one is running.

    Skips when disabled (SLIDEGEN_AUTO_PRECOMPUTE=0), when a warm local entry
    exists, or when the catalog is already in GCS. Never raises.
    """
    try:
        if os.getenv("SLIDEGEN_AUTO_PRECOMPUTE", "1").strip().lower() in {
            "0",
            "false",
            "no",
        }:
            return False
        from app.deck_run import prepared_bundle_exists, slug_for
        from app.template_queue import TemplateQueue, enqueue
        from app.template_versions import TemplateVersion

        if TemplateQueue.from_env() is not None:
            if prepared_bundle_exists(slug_for(display_name), template_gs_uri):
                return False
            from google.cloud import storage
            source_bucket, _, source_name = template_gs_uri[5:].partition("/")
            source = storage.Client().bucket(source_bucket).blob(source_name)
            source.reload()
            enqueue(TemplateVersion(source_bucket, source_name, str(source.generation)))
            return True

        # slug_for, not a local slugify. The deck lane resolves a bundle with
        # slug_for, so a second rule here publishes to a prefix nothing reads:
        # a template whose display name the model rendered as "Everyday Rewards
        # Brand Identity" was built and uploaded under that, while the lane
        # looked under the file-derived slug and reported it unprepared
        # forever. slug_for is idempotent over its own output, so callers may
        # pass either a display name or an already-resolved slug.
        slug = slug_for(display_name)
        catalog_ready = slug in _CATALOG_CACHE or _catalog_exists_in_gcs(slug)
        if catalog_ready and prepared_bundle_exists(slug, template_gs_uri):
            return False
        with _CATALOG_BUILD_LOCK:
            if slug in _CATALOG_BUILDS_IN_FLIGHT:
                logger.info("catalog build already in flight for %s", slug)
                return True
            _CATALOG_BUILDS_IN_FLIGHT.add(slug)
        try:
            _PIPELINE_POOL.submit(_build_catalog, template_gs_uri, slug, display_name)
        except Exception:
            with _CATALOG_BUILD_LOCK:
                _CATALOG_BUILDS_IN_FLIGHT.discard(slug)
            raise
        logger.info("background catalog build enqueued for %s", slug)
        return True
    except Exception:
        logger.exception("catalog enqueue failed")
        return False


def catalog_build_failure(display_name: str) -> str | None:
    """Why this template's last precompute turn produced no bundle, if any."""
    try:
        from app.deck_run import slug_for

        with _CATALOG_BUILD_LOCK:
            return _CATALOG_BUILD_FAILURES.get(slug_for(display_name))
    except Exception:
        return None


def list_expressions(
    template_name: str = "", tool_context: ToolContext = None  # type: ignore[assignment]
) -> dict:
    """Browse the precomputed VISUAL EXPRESSION catalog for a template.

    Shows the end user, instantly, what visual aids and narrative patterns
    are available on their template (ledger timelines, growth ledgers, hero
    stats, proportion bars, era statements, comparisons - each rendered on
    the template's own artwork). Call this the moment a template is chosen,
    BEFORE generation, so the user picks directions while the deck builds.
    Chosen expression ids can be passed to generate_deck as preferences.

    Returns:
        Dict with ``expressions`` (id, world, archetype, treatment,
        description written for the user), plus ``preview_expression_ids``
        and aligned ``preview_image_tokens`` for standard Image components.
    """
    reference = get_reference(_preview_key(tool_context)) if tool_context is not None else None
    reference_uri = (reference or {}).get("template_gs_uri", "")
    resolved = _load_expression_catalog(template_name, template_gs_uri=reference_uri) if reference_uri else _load_expression_catalog(template_name)
    if not resolved:
        return {
            "status": "ok",
            "expression_count": 0,
            "expressions": [],
            "hint": (
                "No precomputed catalog for this template yet. Offer the "
                "style specimens instead, or proceed directly."
            ),
        }
    entries, images = resolved
    slim = [
        {
            "id": e.get("id"),
            "world": e.get("world"),
            "archetype": e.get("archetype"),
            "treatment": e.get("treatment"),
            "description": e.get("description"),
        }
        for e in entries
    ]
    result = {"status": "ok", "expression_count": len(slim), "expressions": slim}
    if tool_context is not None and images:
        key = _preview_key(tool_context)
        if key:
            preview_ids: list[str] = []
            preview_tokens: list[str] = []
            token_by_id: dict[str, str] = {}
            for expression_id, png in images:
                token = _cache_binary_preview(
                    key,
                    namespace="expression_preview",
                    item_id=expression_id,
                    value=png,
                )
                preview_ids.append(expression_id)
                preview_tokens.append(token)
                token_by_id[expression_id] = token
            for expression in slim:
                token = token_by_id.get(str(expression.get("id") or ""))
                if token:
                    expression["preview_image_token"] = token
            result["preview_expression_ids"] = preview_ids
            result["preview_image_tokens"] = preview_tokens
    return result


def load_brand_guideline(brand_id: str) -> dict:
    """Load the full design guideline (DESIGN.md) for one brand.

    Read this BEFORE authoring any slide HTML so colors, typography, spacing
    and visual language follow the brand exactly.

    Args:
        brand_id: Brand id exactly as returned by ``list_brands``.

    Returns:
        Dict with ``status``, ``brand_id`` and ``guideline`` (markdown text,
        possibly truncated). On unknown ids returns ``status: "error"`` plus
        the list of valid ids.
    """
    try:
        index = _load_brand_index()
    except OSError as e:
        return {"status": "error", "error": f"Brand catalog unreadable: {e}"}

    entry = next((b for b in index if b["id"] == brand_id), None)
    if entry is None:
        valid = ", ".join(b["id"] for b in index)
        return {
            "status": "error",
            "error": f"Unknown brand_id '{brand_id}'. Valid ids: {valid}.",
        }

    path = BRANDS_DIR / f"{brand_id}.md"
    if not path.is_file():
        return {"status": "error", "error": f"Guideline file missing for '{brand_id}'."}

    text = path.read_text(encoding="utf-8")
    truncated = False
    if len(text) > MAX_GUIDELINE_CHARS:
        text = text[:MAX_GUIDELINE_CHARS]
        truncated = True
    return {
        "status": "ok",
        "brand_id": brand_id,
        "name": entry["name"],
        "guideline": text,
        "truncated": truncated,
    }


def render_deck_to_pptx(
    slides: list[dict],
    deck_title: str,
    brand_id: str,
    tool_context: ToolContext = None,  # type: ignore[assignment]
) -> dict:
    """Convert authored HTML slides into a branded PPTX stored in Google Cloud Storage.

    Call this ONCE after every slide's HTML has been authored. Each slide dict
    must contain ``title`` (short slide title) and ``html`` (a complete,
    standalone HTML5 document on a fixed 1280x720 canvas).

    Args:
        slides: Ordered list of ``{"title": str, "html": str}`` dicts,
            1 to MAX_SLIDES (20 by default).
        deck_title: Human-readable deck title used for the filename.
        brand_id: Brand id the deck was authored against.

    Returns:
        Dict with ``status: "ok"``, ``gs_uri``, ``https_url`` (may be None),
        ``slide_count``, ``native_area_ratio`` when available,
        ``preview_image_tokens`` for GE Image components, and ``preview_token``
        for the legacy full-gallery ``IFrameSrcdoc``. On failure returns
        ``status: "error"`` with an ``error`` message.
    """
    if not slides or not isinstance(slides, list):
        return {"status": "error", "error": "`slides` must be a non-empty list."}
    if len(slides) > MAX_SLIDES:
        return {
            "status": "error",
            "error": f"At most {MAX_SLIDES} slides per deck; got {len(slides)}.",
        }
    # Normalise slide shapes. The declared schema is a list of
    # {"title", "html"} objects, but models routinely emit a bare list of HTML
    # strings instead. Both are accepted rather than failing the whole render.
    normalised: list[dict] = []
    for i, slide in enumerate(slides):
        fallback_title = f"Slide {i + 1}"
        if isinstance(slide, str):
            if not slide.strip():
                return {"status": "error", "error": f"slides[{i}] is empty."}
            normalised.append({"title": fallback_title, "html": slide})
            continue
        if isinstance(slide, dict):
            markup = slide.get("html") or slide.get("content") or slide.get("body")
            if isinstance(markup, str) and markup.strip():
                normalised.append(
                    {
                        "title": str(slide.get("title") or fallback_title),
                        "html": markup,
                    }
                )
                continue
        return {
            "status": "error",
            "error": (
                f"slides[{i}] must be an HTML string, or an object with a "
                "non-empty 'html' key."
            ),
        }
    slides = normalised

    # Measure authored markup, not inlined bytes. A template slide carries its
    # clean plate as a data URI because a relative src cannot survive the trip
    # to the renderer, and one plate is ~98KB. Counting those against a markup
    # budget rejects a six-slide deck that is entirely well formed. The
    # renderer draws the same distinction and caps the payload separately.
    total_markup = sum(len(_DATA_URI.sub("", s["html"])) for s in slides)
    if total_markup > MAX_TOTAL_HTML_CHARS:
        return {"status": "error", "error": "Deck HTML exceeds size limit."}
    total_payload = sum(len(s["html"]) for s in slides)
    if total_payload > MAX_TOTAL_PAYLOAD_CHARS:
        return {"status": "error", "error": "Deck payload exceeds size limit."}

    renderer_url = os.getenv("SLIDEGEN_RENDERER_URL", "").rstrip("/")
    if not renderer_url:
        return {
            "status": "error",
            "error": (
                "Renderer service is not configured (SLIDEGEN_RENDERER_URL is "
                "unset). The PPTX cannot be produced right now."
            ),
        }
    bucket = os.getenv("SLIDEGEN_GCS_BUCKET", "")
    if not bucket:
        return {
            "status": "error",
            "error": "SLIDEGEN_GCS_BUCKET is unset; nowhere to store the PPTX.",
        }

    payload = {
        "deck_title": deck_title,
        "brand_id": brand_id,
        "slides": slides,
        "preview_pages": min(len(slides), 6),
    }
    try:
        headers = _renderer_auth_headers(renderer_url)
        response = httpx.post(
            f"{renderer_url}/render",
            json=payload,
            headers=headers,
            timeout=RENDER_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("error", "")
        except Exception:
            pass
        return {
            "status": "error",
            "error": f"Renderer returned {e.response.status_code}: {detail or e}",
        }
    except (httpx.HTTPError, ValueError) as e:
        return {"status": "error", "error": f"Renderer unreachable: {e}"}

    if data.get("ok") is not True:
        return {
            "status": "error",
            "error": data.get("error", "Conversion failed for an unknown reason."),
        }
    # An uneditable PPTX is a picture of a deck. Delivering one would break the
    # only promise this product makes about its output, so it fails closed.
    quality = data.get("quality") or {}
    if quality.get("editability_passed") is False:
        failing = quality.get("editability_failing_slides") or []
        where = (
            "slides " + ", ".join(str(index + 1) for index in failing)
            if failing
            else "at least one slide"
        )
        return {
            "status": "error",
            "error": (
                f"Quality admission failed: the converter could not rebuild {where} "
                "as native editable shapes. The deck was not delivered."
            ),
        }
    result = {
        "status": "ok",
        "gs_uri": data["gs_uri"],
        "https_url": data.get("https_url"),
        "slide_count": data.get("slide_count", len(slides)),
        "native_area_ratio": data.get("native_area_ratio"),
        "quality": data.get("quality") or {},
    }

    # Stash preview data behind short tokens. The model-facing tool result gets
    # token strings only; app_utils.a2a expands them at the A2A boundary.
    if tool_context is not None:
        try:
            images_b64 = data.get("images_b64") or []
            mime_type = str(data.get("preview_mime") or "image/jpeg")
            key = _preview_key(tool_context)
            if key and images_b64:
                result["preview_image_tokens"] = _cache_preview_image_tokens(
                    key, images_b64, mime_type
                )
            preview = _build_preview_html(slides, images_b64, mime_type)
            try:
                tool_context.state[SLIDE_PREVIEW_STATE_KEY] = preview
            except Exception:
                logger.debug("could not mirror preview into session state")
            if key:
                cache_fragment(key, SLIDE_PREVIEW_TOKEN, preview)
            result["preview_token"] = SLIDE_PREVIEW_TOKEN
        except Exception:  # never fail the render because of the preview
            logger.exception("failed to build slide preview")

    return result


def _deck_reveal_token(tool_context, rendered: dict, titles: list[str]) -> str | None:
    """Cache the final reveal card and return its fragment token."""
    if tool_context is None:
        return None
    key = _preview_key(tool_context)
    images_b64 = rendered.get("images_b64") or []
    if not key or not images_b64:
        return None
    listing = "".join(f"<li>{html_lib.escape(title)}</li>" for title in titles)
    reveal_html = (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "body{margin:0;padding:14px;background:#faf9f7;"
        "font:13px system-ui,sans-serif;color:#202124}"
        "img{width:100%;border-radius:10px;border:1px solid #e3e0da;"
        "display:block}h3{margin:10px 0 6px}ol{margin:0;padding-left:20px}"
        "li{margin:4px 0}.muted{color:#5f6368;margin-top:8px}"
        "</style></head><body>"
        f'<img src="data:image/jpeg;base64,{images_b64[0]}" alt="cover">'
        "<h3>Your deck is ready</h3><ol>" + listing + "</ol>"
        '<div class="muted">Download the editable PPTX below.</div>'
        "</body></html>"
    )
    token = "__DECK_REVEAL_HTML__"
    cache_fragment(key, token, reveal_html)
    return token


def _generate_deck_sync(
    topic: str,
    audience: str,
    goal: str,
    slide_count: int,
    brand_id: str,
    style_id: str = "",
    expression_ids: str | list[str] | None = None,
    tool_context: ToolContext = None,  # type: ignore[assignment]
    cancellation_event: threading.Event | None = None,
) -> dict:
    """Author AND render a complete branded deck. Call this ONCE per deck.

    This does the whole pipeline server-side: the brief, brand, style, and
    expression choices are validated here, one harness turn builds the deck in
    a staged workspace, and the result is converted to an editable PPTX in
    Cloud Storage.

    Do NOT write slide HTML yourself — you only supply the brief.

    Args:
        topic: What the deck is about.
        audience: Who it is for.
        goal: Desired outcome and tone.
        slide_count: Number of slides, 1 to MAX_SLIDES (20 by default).
        brand_id: Brand id from ``list_brands``.
        style_id: Optional style id from ``list_styles``.
        expression_ids: Optional expression ids from ``list_expressions``;
            seeds authoring with the user's chosen visual directions.
    Returns:
        Dict with ``status: "ok"``, ``gs_uri``, ``https_url``, ``slide_count``,
        ``preview_token`` and ``slide_titles``. On failure ``status: "error"``
        with an ``error`` message.
    """
    from app import deck_run
    from app.companion import CompanionThrottle, companion_line, dino_fragment
    from app.specimens import load_styles
    from app.template_policy import DeckProvenance

    if _cancelled(cancellation_event):
        return _cancelled_result()

    try:
        count = max(1, min(int(slide_count or DEFAULT_SLIDES), MAX_SLIDES))
    except (TypeError, ValueError):
        count = DEFAULT_SLIDES

    report_progress(
        tool_context,
        stage="brief",
        title="Building your deck",
        detail="Validating the brief, brand, and visual system",
        stage_index=1,
    )

    # Template-fidelity mode: matching a user-supplied deck is a deliberate
    # brief, so the reference's own design habits are reproduced rather than
    # corrected. Mechanical checks still apply — those are physics, not taste.
    template_fidelity = brand_id == REFERENCE_BRAND_ID
    reference: dict = {}
    if template_fidelity:
        reference = get_reference(_preview_key(tool_context) if tool_context else None)
        if not reference:
            return {
                "status": "error",
                "error": (
                    "No reference deck has been ingested in this conversation. "
                    "Call use_reference_deck first."
                ),
            }
        guideline = {"status": "ok", "guideline": reference.get("guideline", "")}
        brand_name = reference.get("name", "Reference deck")
        if reference.get("template_revision") and reference.get("template_gs_uri"):
            renderer_url = os.getenv("SLIDEGEN_RENDERER_URL", "").rstrip("/")
            try:
                listed = httpx.get(
                    f"{renderer_url}/templates",
                    headers=_renderer_auth_headers(renderer_url),
                    timeout=60,
                ).json()
                current = next(
                    (
                        item
                        for item in (listed.get("templates") or [])
                        if item.get("gs_uri") == reference["template_gs_uri"]
                    ),
                    None,
                )
            except (httpx.HTTPError, ValueError):
                current = None
            if current is None or _template_revision(current) != reference["template_revision"]:
                return {
                    "status": "error",
                    "error": (
                        "The pinned template revision changed or is unavailable. "
                        "Refresh the template gallery and choose again."
                    ),
                }
    else:
        guideline = load_brand_guideline(brand_id)
        if guideline.get("status") != "ok":
            return guideline
        brand_name = guideline.get("name", brand_id)

    style = None
    if style_id:
        style = next((s for s in load_styles() if s["id"] == style_id), None)
        if style is None:
            return {
                "status": "error",
                "error": f"Unknown style_id {style_id!r}. Call list_styles first.",
            }

    expressions: list[str] = []
    # User-chosen visual expressions seed the authoring brief. Selection is a
    # pinned catalog boundary: unknown or stale ids are an error, never a hint
    # that can be silently dropped.
    if expression_ids:
        if isinstance(expression_ids, str):
            wanted = [e.strip() for e in expression_ids.split(",") if e.strip()]
        else:
            wanted = [str(e).strip() for e in expression_ids if str(e).strip()]
        wanted = list(dict.fromkeys(wanted))
        template_label = (reference.get("name") if template_fidelity else None) or brand_name
        reference_uri = reference.get("template_gs_uri", "")
        resolved_catalog = _load_expression_catalog(template_label, template_gs_uri=reference_uri) if reference_uri else _load_expression_catalog(template_label)
        if not resolved_catalog:
            return {
                "status": "error",
                "error": (
                    "The selected visual expressions cannot be validated because "
                    f"the catalog for {template_label!r} is unavailable. Refresh "
                    "the expression gallery and choose again."
                ),
            }
        by_id = {
            str(entry.get("id")): entry
            for entry in resolved_catalog[0]
            if str(entry.get("id") or "")
        }
        unknown = [expression_id for expression_id in wanted if expression_id not in by_id]
        if unknown:
            return {
                "status": "error",
                "error": (
                    "Unknown or stale visual expression ids: "
                    + ", ".join(unknown)
                    + ". Refresh the expression gallery and choose again."
                ),
            }
        expressions = [
            f"{by_id[expression_id].get('id')} "
            f"({by_id[expression_id].get('world')} / "
            f"{by_id[expression_id].get('archetype')})"
            for expression_id in wanted
        ]

    # Companion voice + dino game: engagement while the deck builds.
    throttle = CompanionThrottle(min_interval_seconds=2.5)
    dino_token = None
    if tool_context is not None:
        key = _preview_key(tool_context)
        if key:
            dino_token = "__DINO_HTML__"
            cache_fragment(
                key, dino_token, dino_fragment("Egg cracked. Your deck-pet hatched.")
            )

    # Distinct slide indexes, written from the watcher thread and read from the
    # authoring thread. A count of callbacks would be wrong rather than merely
    # imprecise: the harness rewrites a slide and the watcher re-emits it, so a
    # bare counter overruns `count`, and display_percent's monotonic clamp then
    # pins the bar at 70 while most of the deck is still unwritten.
    landed: set[int] = set()
    landed_lock = threading.Lock()

    # The template lane writes one patches.json at the end, so no slide ever
    # lands mid-run and there is nothing to count. Reporting 0-of-N there is a
    # worse lie than reporting nothing: it scores 20 and sits, where the
    # unknown-total branch scores an honest indeterminate 30.
    template_lane = bool(template_fidelity and reference.get("template_gs_uri"))

    def cache_draft(slide: dict, index: int) -> None:
        session = getattr(tool_context, "session", None)
        if getattr(session, "app_name", None) != "pixelpitch_mcp":
            return
        key = _preview_key(tool_context)
        draft = str(slide.get("html") or "")
        if key and draft and len(draft) <= MAX_PREVIEW_CHARS:
            cache_fragment(key, f"__SLIDE_DRAFT_{index}__", draft)
            cache_fragment(key, f"__SLIDE_DRAFT_TITLE_{index}__", str(slide.get("title") or f"Slide {index + 1}")[:500])

    def on_event(event: dict) -> None:
        kind = event.get("agy_evt")
        if kind not in {"tool", "thought"}:
            return
        tick = throttle.allow()
        if tick is None:
            return
        with landed_lock:
            current = len(landed)
        if kind == "tool":
            detail = companion_line("tool", tool=event.get("name") or "a tool", tick=tick)
        else:
            detail = companion_line("thinking", slide=current + 1, tick=tick)
        report_progress(
            tool_context,
            stage="authoring",
            title="Building your deck",
            detail=detail,
            stage_index=2,
            current=None if template_lane else current,
            total=None if template_lane else count,
            fragment_token=dino_token,
        )

    report_progress(
        tool_context,
        stage="authoring",
        title="Building your deck",
        detail=companion_line("start", tick=throttle.allow() or 0),
        stage_index=2,
        current=None if template_lane else 0,
        total=None if template_lane else count,
        fragment_token=dino_token,
    )

    # Never throttled. The throttle rate-limits the chatty thought and tool
    # stream; a landed slide is the highest-signal event in the run and is the
    # only thing that moves the bar.
    def slide_ready(slide: dict) -> None:
        index = int(slide.get("index") or 0)
        title = str(slide.get("title") or "")
        with landed_lock:
            landed.add(index)
            current = len(landed)
        # The MCP worker persists these drafts separately from chat progress.
        # Never put authored HTML into an interim A2A text event.
        cache_draft(slide, index)
        report_progress(
            tool_context,
            stage="authoring",
            title="Building your deck",
            detail=companion_line("slide_ready", slide=index + 1, tick=current),
            stage_index=2,
            current=current,
            total=count,
            slide_index=index,
            slide_title=title or f"Slide {index + 1}",
            fragment_token=dino_token,
        )

    if template_lane:
        template_label = reference.get("name") or brand_name
        # One identity for both the lane that reads the prepared bundle and
        # the build that publishes it. Passing the display name to one and
        # the slug to the other is what left templates permanently
        # unadmitted while a finished bundle sat under the other slug.
        template_key = reference.get("template_slug") or template_label
        result = deck_run.run_template_deck(
            topic=topic,
            audience=audience,
            goal=goal,
            slide_count=count,
            template_name=template_key,
            template_gs_uri=reference["template_gs_uri"],
            expressions=expressions,
            on_event=on_event,
            cancellation_event=cancellation_event,
        )
        if result.get("status") != "ok":
            if result.get("code") == "cancelled":
                return _cancelled_result()
            # A template with no admitted bundle is not a failed deck, it
            # is a template that has not been prepared yet.
            requested = maybe_build_catalog(
                reference["template_gs_uri"], template_key
            )
            if requested:
                # The internal sentence ("prepared manifest is missing,
                # extract and admit the specimens first") describes work
                # this service is already doing and names nothing the
                # reader can act on. What they need is how long, and that
                # it is once per template rather than every deck.
                previous = catalog_build_failure(template_key)
                message = (
                    f"{template_label} has not been used before, so its "
                    "layouts, brand rules and slide specimens are being "
                    "extracted now. That takes about ten minutes and "
                    "happens once per template. Ask for this deck again "
                    "when it finishes and it will build straight away."
                )
                if previous:
                    # Never repeat the ten-minute promise to someone it
                    # has already failed. They are owed the reason.
                    message = (
                        f"Preparing {template_label} failed last time: "
                        f"{previous}. It is being retried now, but if the "
                        "next attempt stops here too the template itself "
                        "needs looking at rather than another retry."
                    )
                return {
                    "status": "error",
                    "code": "template_preparation_required",
                    "error": message,
                }
            return {
                "status": "error",
                "code": "template_preparation_required",
                "error": result.get("error", "the template lane failed")
                + " The selected template is not ready for creative "
                "HTML authoring.",
            }
    else:
        result = deck_run.run_designed_deck(
            topic=topic,
            audience=audience,
            goal=goal,
            slide_count=count,
            brand_name=brand_name,
            guideline=guideline.get("guideline", ""),
            style=style,
            expressions=expressions,
            on_event=on_event,
            slide_ready=slide_ready,
            cancellation_event=cancellation_event,
        )
        if result.get("status") != "ok":
            if result.get("code") == "cancelled":
                return _cancelled_result()
            return {"status": "error", "error": result.get("error", "authoring failed")}

    if _cancelled(cancellation_event):
        return _cancelled_result()

    slides = result.get("slides") or []
    # The final harvest also covers fast writes missed by the watcher and the
    # template lane, whose patches only become slides after authoring ends.
    for index, slide in enumerate(slides):
        cache_draft(slide, index)
    report_progress(
        tool_context,
        stage="conversion",
        title="Building your deck",
        detail="Converting authored slides into an editable PowerPoint",
        stage_index=3,
    )
    rendered = render_deck_to_pptx(
        slides=slides,
        deck_title=topic.strip()[:80] or "Untitled Deck",
        brand_id=brand_id,
        tool_context=tool_context,
    )
    if rendered.get("status") != "ok":
        return rendered

    titles = result.get("titles") or []
    report_progress(
        tool_context,
        stage="delivery",
        title="Building your deck",
        detail=companion_line("done", n=count, tick=throttle.allow() or 0),
        stage_index=5,
    )

    # Summarise quality for the model; the raw block is diagnostic noise. The
    # harness already gated its own work against these same signals, so this
    # is evidence to report, not a loop to run.
    quality = rendered.pop("quality", None) or {}
    recalls = [
        failure["ocr_recall"]
        for failure in (quality.get("fidelity_failures") or [])
        if isinstance(failure.get("ocr_recall"), (int, float))
    ]
    if recalls:
        rendered["lowest_text_recall"] = round(min(recalls), 2)
    rendered.update(
        {
            "reveal_token": _deck_reveal_token(tool_context, rendered, titles),
            "slide_titles": titles,
            "brand": brand_name,
            "template_fidelity": bool(template_lane and result.get("template_bundle_id")),
            "editability_passed": quality.get("editability_passed", True),
            "quality_evidence": quality,
            "provenance": result.get(
                "provenance", DeckProvenance.HTML_RECONSTRUCTED_BY_SLIDIFY.value
            ),
        }
    )
    if style:
        rendered["style"] = style["name"]
    if template_fidelity:
        rendered["template_bundle_id"] = result.get("template_bundle_id")
        rendered["note"] = (
            "Reconstructed from guarded template HTML baselines and converted "
            "by Slidify. Original PowerPoint masters are not preserved on this "
            "lane."
        )
    return rendered

async def generate_deck(
    topic: str,
    audience: str,
    goal: str,
    slide_count: int,
    brand_id: str,
    style_id: str = "",
    expression_ids: str | list[str] | None = None,
    tool_context: ToolContext = None,  # type: ignore[assignment]
) -> dict:
    """Author AND render a complete branded deck. Call this ONCE per deck.

    This does the whole pipeline server-side: a dedicated design model writes
    the slide HTML against the brand guideline and chosen visual style, then
    the deck is converted to an editable PPTX in Cloud Storage. Work runs off
    the A2A event loop so real progress can stream while the deck builds.

    Do NOT write slide HTML yourself — you only supply the brief.

    Args:
        topic: What the deck is about.
        audience: Who it is for.
        goal: Desired outcome and tone.
        slide_count: Number of slides, 1 to MAX_SLIDES (20 by default).
        brand_id: Brand id from ``list_brands``.
        style_id: Optional style id from ``list_styles``.

    Returns:
        Dict with ``status: "ok"``, ``gs_uri``, ``https_url``, ``slide_count``,
        ``preview_token`` and ``slide_titles``. On failure ``status: "error"``
        with an ``error`` message.
    """
    # Keep the ADK/A2A event loop free to stream progress while the synchronous
    # authoring and conversion pipeline runs. Polling the concurrent future is
    # deliberate: it works in constrained runtimes where the event loop's
    # cross-thread wakeup pipe may be unavailable, while retaining a hard cap
    # on concurrent deck pipelines.
    return await _run_in_pipeline_pool(
        _generate_deck_sync,
        topic=topic,
        audience=audience,
        goal=goal,
        slide_count=slide_count,
        brand_id=brand_id,
        style_id=style_id,
        expression_ids=expression_ids,
        tool_context=tool_context,
    )


# ---------------------------------------------------------------------------
# Reference decks
# ---------------------------------------------------------------------------

REFERENCE_BRAND_ID = "reference"
REFERENCE_SPECIMEN_TOKEN = "__REFERENCE_SPECIMEN_HTML__"

# Derived systems are session-scoped: they belong to one user's conversation
# and must never leak into another's.
_REFERENCE_CACHE: OrderedDict[str, dict] = OrderedDict()
_REFERENCE_CACHE_MAX = 32


def cache_reference(key: str, reference: dict) -> None:
    _REFERENCE_CACHE[key] = reference
    _REFERENCE_CACHE.move_to_end(key)
    while len(_REFERENCE_CACHE) > _REFERENCE_CACHE_MAX:
        _REFERENCE_CACHE.popitem(last=False)


def get_reference(key: str | None) -> dict | None:
    return _REFERENCE_CACHE.get(key) if key else None


def rebind_reference(old_key: str, new_key: str) -> bool:
    """Move a cached entry once the real session id is known.

    Uploads are captured in the A2A executor, which only knows the A2A
    ``context_id``. The session id is assigned later (and with Vertex-managed
    sessions it is server-generated, so it differs from the context id). This
    re-keys the entry so tools, which see only the session, can find it.
    """
    if old_key == new_key or old_key not in _REFERENCE_CACHE:
        return False
    entry = _REFERENCE_CACHE.pop(old_key)
    existing = _REFERENCE_CACHE.get(new_key) or {}
    existing.update(entry)
    cache_reference(new_key, existing)
    return True


def _use_reference_deck_sync(
    source_url: str = "",
    template_revision: str = "",
    tool_context: ToolContext = None,  # type: ignore[assignment]
    cancellation_event: threading.Event | None = None,
) -> dict:
    """Ingest a reference deck and use its design system for this conversation.

    Accepts a Google Slides share link (must be 'Anyone with the link'), or a
    direct URL to a .pptx or .pdf. If the user attached a file to the message,
    call this with an empty source_url and the attachment is used.

    After this succeeds, call `generate_deck` with brand_id="reference".

    Args:
        source_url: Link to the reference deck. Leave empty to use an
            attached file.

    Returns:
        Dict with ``status``, the derived ``name``, ``palette``, ``fonts``,
        ``tells`` (the source deck's own design habits) and a
        ``specimen_token`` to show the user for confirmation.
    """
    from app.reference import ingest
    from app.specimens import reference_specimen_html

    if _cancelled(cancellation_event):
        return _cancelled_result()

    renderer_url = os.getenv("SLIDEGEN_RENDERER_URL", "").rstrip("/")
    if not renderer_url:
        return {"status": "error", "error": "Extraction service is not configured."}

    key = _preview_key(tool_context) if tool_context is not None else None
    file_b64 = ""
    if not source_url:
        pending = (get_reference(key) or {}).get("_pending_upload") if key else None
        if not pending:
            return {
                "status": "error",
                "error": (
                    "No reference deck provided. Paste a share link, or attach "
                    "a .pptx or .pdf to your message."
                ),
            }
        file_b64 = pending

    report_progress(
        tool_context,
        stage="reference",
        title="Preparing your deck brief",
        detail="Rendering and analysing the requested template",
        stage_index=1,
        stage_total=2,
    )

    selected_template: dict | None = None
    if template_revision:
        if not source_url.startswith("gs://"):
            return {
                "status": "error",
                "error": "template_revision is valid only for a listed gs:// template.",
            }
        try:
            listed = httpx.get(
                f"{renderer_url}/templates",
                headers=_renderer_auth_headers(renderer_url),
                timeout=60,
            ).json()
            selected_template = next(
                (
                    item
                    for item in (listed.get("templates") or [])
                    if item.get("gs_uri") == source_url
                ),
                None,
            )
        except (httpx.HTTPError, ValueError):
            selected_template = None
        if (
            selected_template is None
            or _template_revision(selected_template) != template_revision
        ):
            return {
                "status": "error",
                "error": (
                    "The selected template revision is stale or unavailable. "
                    "Refresh the template gallery and choose again."
                ),
            }

    reference, error = ingest(
        renderer_url=renderer_url,
        headers=_renderer_auth_headers(renderer_url),
        source_url=source_url,
        file_b64=file_b64,
    )
    if error:
        return {"status": "error", "error": error}
    if selected_template is not None:
        reference["template_revision"] = template_revision
        reference["template_source_identity"] = {
            "gs_uri": source_url,
            "generation": str(selected_template.get("generation") or ""),
            "etag": str(selected_template.get("etag") or ""),
        }

    report_progress(
        tool_context,
        stage="reference",
        title="Preparing your deck brief",
        detail="Template analysed; preparing the editable confirmation form",
        stage_index=2,
        stage_total=2,
    )

    # A named template must stay on the source-bound lane. An inventory failure
    # is not permission to quietly replace it with brand/style transfer.
    if source_url.startswith("gs://") and source_url.lower().endswith(
        (".pptx", ".ppt")
    ):
        try:
            resp = httpx.get(
                f"{renderer_url}/template-layouts",
                params={"gs_uri": source_url},
                headers=_renderer_auth_headers(renderer_url),
                timeout=180,
            )
            resp.raise_for_status()
            inv = resp.json()
            if inv.get("ok") and inv.get("source_slides"):
                reference["template_gs_uri"] = source_url
                # Deterministic slug from the FILE name (not the prettified
                # display name) so prepared-bundle lookups always match what
                # was published.
                from app.deck_run import slug_for_object

                reference["template_slug"] = slug_for_object(source_url)
                reference["layouts"] = inv.get("layouts") or []
                reference["source_slides"] = inv["source_slides"]
                reference["slide_size"] = inv.get("slide_size")
                reference["archetypes"] = sorted(
                    {lay["archetype"] for lay in reference["layouts"]}
                )
            else:
                raise ValueError(inv.get("error") or "source slide inventory is empty")
        except Exception as exc:
            logger.exception("source template inventory unavailable")
            return {"status": "error", "error": f"Could not parse the requested PowerPoint template: {exc}. No style-transfer fallback was used."}

    if key:
        cache_reference(key, reference)
        if reference.get("template_gs_uri"):
            # Ahead-of-time expressions for the next mention of this template.
            maybe_build_catalog(
                reference["template_gs_uri"],
                reference.get("template_slug") or reference.get("name", "template"),
            )
        try:
            cache_fragment(
                key, REFERENCE_SPECIMEN_TOKEN, reference_specimen_html(reference)
            )
        except Exception:
            logger.exception("could not build reference specimen")

    palette = reference.get("palette") or {}
    typo = reference.get("typography") or {}
    return {
        "status": "ok",
        "name": reference.get("name", "Reference deck"),
        "summary": reference.get("summary", ""),
        "confidence": reference.get("confidence", "unknown"),
        "pages_analysed": reference.get("page_count"),
        "palette": palette,
        "fonts": [v for v in (typo.get("display"), typo.get("body")) if v],
        "tells": reference.get("tells") or [],
        "specimen_token": REFERENCE_SPECIMEN_TOKEN,
        "template_revision": reference.get("template_revision"),
        "next_step": (
            "Show the specimen and the detected palette to the user for "
            "confirmation, then call generate_deck with brand_id='reference'."
        ),
    }


async def use_reference_deck(
    source_url: str = "",
    template_revision: str = "",
    tool_context: ToolContext = None,  # type: ignore[assignment]
) -> dict:
    """Ingest a reference deck without blocking streamed A2UI feedback.

    Accepts a Google Slides share link, a direct .pptx/.pdf URL, a ``gs://``
    template URI, or an attachment captured from the current A2A message.
    Returns the inferred design system and specimen token for confirmation.
    """
    return await _run_in_pipeline_pool(
        _use_reference_deck_sync,
        source_url=source_url,
        template_revision=template_revision,
        tool_context=tool_context,
    )


def _match_score(query: str, path: str) -> float:
    """Rank a template path against a natural-language request.

    Users say "the Example Retail 100Y template", not
    "example-retail/example-100y-fy26.pptx". Paths are tokenised on separators so
    folder names count as evidence, and each query token is scored by exact
    token hit, prefix hit, or substring hit.
    """

    def tokens(text: str) -> list[str]:
        cleaned = re.sub(r"[^a-z0-9]+", " ", text.lower())
        return [t for t in cleaned.split() if t]

    stop = {
        "template",
        "templates",
        "deck",
        "decks",
        "the",
        "our",
        "use",
        "please",
        "file",
        "slide",
        "slides",
        "a",
        "an",
        "for",
        "with",
    }
    q = [t for t in tokens(query) if t not in stop]
    if not q:
        return 0.0
    hay = tokens(path)
    if not hay:
        return 0.0
    joined = "".join(hay)

    score = 0.0
    for term in q:
        if term in hay:
            score += 1.0  # whole-token match
        elif any(h.startswith(term) for h in hay):
            score += 0.7  # prefix, e.g. "example" -> example-retail
        elif any(term in h for h in hay):
            score += 0.5  # inside a token
        elif term in joined:
            score += 0.3  # spans separators, e.g. "100y"
    return score / len(q)


def _template_revision(item: dict) -> str:
    """Opaque immutable revision derived only from source object identity."""
    identity = {
        "gs_uri": str(item.get("gs_uri") or ""),
        "generation": str(item.get("generation") or ""),
        "etag": str(item.get("etag") or ""),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return "tr1_" + hashlib.sha256(encoded).hexdigest()


def _template_gallery_metadata(item: dict, tool_context) -> dict:
    """Attach admitted types and flat preview tokens without UI protocol data."""
    from app.deck_run import display_name_for_object, slug_for_object
    from app.template_policy import PreparedBundleManifest, TemplatePolicyError

    object_name = str(item.get("name") or "")
    slug = slug_for_object(object_name)
    admitted: list[dict] = []
    preview_values: list[tuple[str, bytes]] = []
    bucket_name = os.getenv("SLIDEGEN_GCS_BUCKET", "")
    if bucket_name:
        try:
            from google.cloud import storage

            bucket = storage.Client().bucket(bucket_name)
            prefix = f"templates/{slug}/prepared/"
            from app.template_versions import TemplateVersion, ready_prefix
            version = TemplateVersion.from_uri(str(item.get("gs_uri") or ""), str(item.get("generation") or ""))
            committed = ready_prefix(bucket, version)
            prefix = committed or prefix
            manifest_blob = bucket.blob(prefix + "manifest.json")
            if manifest_blob.exists():
                manifest = PreparedBundleManifest.from_dict(
                    json.loads(manifest_blob.download_as_bytes())
                )
                source_matches = (
                    manifest.source.gs_uri == str(item.get("gs_uri") or "")
                    and manifest.source.generation == str(item.get("generation") or "")
                    and (committed or manifest.source.etag == str(item.get("etag") or ""))
                )
                if source_matches:
                    admitted = [
                        {"id": slide_type.id, "archetype": slide_type.archetype}
                        for slide_type in manifest.slide_types
                    ]
                    declared = {artifact.path for artifact in manifest.artifacts}
                    # A declared preview_path is the only path to a preview, and
                    # an empty list here is the correct answer rather than a gap
                    # to fall back out of. A specimen is rendered from a
                    # synthesised per-archetype baseline, so it cannot be a
                    # slide of the customer's deck. Every other image in the
                    # bundle can be: base/*.png is the raw page render, one per
                    # source slide, and the bundle's own brand-contract.json
                    # protects some of them by number (Acknowledgement of
                    # Country, Indigenous artwork, never reused or shown).
                    # Enumerating base/*.png returned slides 01-03 knowing
                    # nothing of that list, which is why it is gone instead of
                    # filtered.
                    preview_paths = [
                        slide_type.preview_path
                        for slide_type in manifest.slide_types
                        if slide_type.preview_path in declared
                    ]
                    for path in preview_paths[:3]:
                        if not path:
                            continue
                        blob = bucket.blob(prefix + path)
                        if blob.exists():
                            preview_values.append((path, blob.download_as_bytes()))
        except (ValueError, TemplatePolicyError):
            logger.warning("prepared gallery manifest invalid for %s", slug)
        except Exception:
            logger.exception("prepared gallery lookup failed for %s", slug)

    # Archetype names only, and they stay descriptive: a catalog that predates
    # the guarded-baseline admission contract is still useful for browsing, but
    # is never reported as an admitted slide type. The catalog's PNGs stay out
    # of the gallery because each is authored ON the template's own clean plate
    # and so carries that slide's real artwork, and no stage of the precompute
    # pipeline filters the contract's protected slides out of the expression
    # matrix. list_expressions may show them because the user named that
    # template; this gallery lists every template in the shared bucket to
    # someone who named none of them.
    catalog = _load_expression_catalog(str(item.get("name") or ""), template_gs_uri=str(item.get("gs_uri") or ""))
    catalog_archetypes: list[str] = []
    if catalog:
        catalog_archetypes = sorted(
            {
                str(entry.get("archetype"))
                for entry in catalog[0]
                if str(entry.get("archetype") or "")
            }
        )[:8]

    item["revision"] = _template_revision(item)
    # name stays the storage object: catalog lookup and the deck lane key off
    # it. display_name is the only form of it a user should ever read.
    item["display_name"] = display_name_for_object(object_name)
    item["admitted_slide_types"] = admitted
    # Whether this template can actually produce a deck. Only a prepared
    # bundle whose source identity still matches the live object can, and the
    # brief form has no other way to tell: offering an unprepared template as
    # a pinnable option spends the user a full request to reach a wall.
    item["admitted"] = bool(admitted)
    item["available_archetypes"] = sorted(
        {entry["archetype"] for entry in admitted} | set(catalog_archetypes)
    )[:8]
    if tool_context is not None and preview_values:
        key = _preview_key(tool_context)
        if key:
            item["preview_image_tokens"] = [
                _cache_binary_preview(
                    key,
                    namespace="template_preview",
                    item_id=f"{item['revision']}:{preview_id}",
                    value=value,
                )
                for preview_id, value in preview_values
            ]
    return item


def list_templates(
    query: str = "",
    tool_context: ToolContext = None,  # type: ignore[assignment]
) -> dict:
    """Find reference deck templates in the shared template bucket.

    Templates live in Cloud Storage and need no user sign-in, so this works
    for corporate decks that cannot be link-shared. Call this whenever the
    user mentions a template, a brand's own deck, or "our standard deck".

    Args:
        query: What the user asked for, in their words, e.g.
            "the Example Retail 100Y template". Leave empty to list everything.
            Matching covers folder names as well as file names.

    Returns:
        Dict with ranked ``templates`` (``name``, ``gs_uri``, ``score``),
        ``ambiguous`` (True when the top candidates are close), and
        ``upload_path`` for when nothing matches.
    """
    renderer_url = os.getenv("SLIDEGEN_RENDERER_URL", "").rstrip("/")
    bucket = os.getenv("SLIDEGEN_GCS_BUCKET", "")
    upload_path = f"gs://{bucket}/templates/" if bucket else ""
    if not renderer_url:
        return {"status": "error", "error": "Template service is not configured."}
    try:
        response = httpx.get(
            f"{renderer_url}/templates",
            headers=_renderer_auth_headers(renderer_url),
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        return {"status": "error", "error": f"Could not list templates: {e}"}
    if data.get("ok") is not True:
        return {"status": "error", "error": data.get("error", "listing failed")}

    raw_items = data.get("templates") or []
    items = [
        dict(item)
        for item in raw_items
        if isinstance(item, dict)
    ]
    if not items:
        return {
            "status": "ok",
            "count": 0,
            "templates": [],
            "upload_path": upload_path,
            "hint": (
                "No templates have been uploaded yet. Ask the user to upload a "
                f".pptx or .pdf to {upload_path}"
            ),
        }

    if query:
        for item in items:
            item["score"] = round(_match_score(query, item.get("name", "")), 3)
        items.sort(key=lambda i: -i["score"])
        strong = [i for i in items if i["score"] >= 0.5]
        # Near-ties must be disambiguated by the user, not guessed at.
        ambiguous = len(strong) > 1 and (strong[0]["score"] - strong[1]["score"]) < 0.2
        shortlist = strong[:5] if strong else items[:5]
    else:
        shortlist, ambiguous = items[:20], len(items) > 1

    shortlist = [
        _template_gallery_metadata(item, tool_context) for item in shortlist
    ]

    if query and not any(i.get("score", 0) >= 0.5 for i in shortlist):
        return {
            "status": "ok",
            "count": len(items),
            "templates": shortlist,
            "matched": False,
            "upload_path": upload_path,
            "hint": (
                f"Nothing clearly matches {query!r}. Show the user these "
                "options and ask which one, or offer the upload path."
            ),
        }

    return {
        "status": "ok",
        "count": len(items),
        "templates": shortlist,
        "matched": bool(query),
        "ambiguous": ambiguous,
        "upload_path": upload_path,
        "hint": (
            "Several templates match closely. Show the user the names and ask "
            "which one before generating."
            if ambiguous
            else "Pass the top template's gs_uri to use_reference_deck."
        ),
    }
