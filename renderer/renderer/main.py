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

"""Renderer service: HTML slides -> slidify -> PPTX uploaded to GCS."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import textwrap
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("renderer")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="pixelpitch-slidegen-renderer")

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# Full profile measures fidelity without allowing oracle corrections to replace
# editable text with pictures. Slidify's low-memory mode retains the source PNG
# for SSIM/OCR but discards the plans used for destructive raster corrections.
# "full" keeps the tier-3 LLM adjudicator ON. Without it every ambiguous
# visual unit falls back to a raster, which drives native_area_ratio to 0 —
# i.e. the .pptx is a stack of pictures with no editable text. Tier 3 needs an
# LLM backend (SLIDIFY_LLM_BACKEND); the runtime service account already has
# Vertex access. "balanced" trades the SSIM/OCR oracle for speed but still
# emits native shapes. "fast" is the last-resort fallback.
_PROFILES = {
    "full": ["--low-memory"],
    "balanced": ["--no-oracle"],
    "fast": ["--no-oracle", "--no-tier3"],
}


class Slide(BaseModel):
    title: str = ""
    html: str = Field(min_length=1)


_DATA_URI = re.compile(r"data:[^\"')\s]+")

# Two budgets, because a slide carries two kinds of bytes. Markup is authored
# and a deck of it should be small; anything near this ceiling is a runaway
# generator, which is what the cap is for. Assets are inlined rather than
# authored, and they have to be inlined, because a slide crosses this boundary
# as a string and a relative `src` in it resolves against nothing. Twenty
# full-bleed plates are several megabytes and entirely legitimate. Measuring
# both against one number rejects a correct deck and calls it oversized HTML.
_MARKUP_LIMIT = 400_000
_PAYLOAD_LIMIT = 24_000_000


def _cap_slide_bytes(slides: list["Slide"]) -> list["Slide"]:
    total = sum(len(s.html) for s in slides)
    markup = sum(len(_DATA_URI.sub("", s.html)) for s in slides)
    if markup > _MARKUP_LIMIT:
        raise ValueError(f"total HTML markup is {markup} chars, over {_MARKUP_LIMIT}")
    if total > _PAYLOAD_LIMIT:
        raise ValueError(
            f"total payload is {total} chars, over {_PAYLOAD_LIMIT}. Shrink the "
            "inlined images."
        )
    return slides


class RenderRequest(BaseModel):
    deck_title: str = Field(min_length=1, max_length=200)
    brand_id: str = ""
    slides: list[Slide] = Field(min_length=1, max_length=20)
    preview_pages: int = Field(default=6, ge=0, le=20)

    @field_validator("slides")
    @classmethod
    def _cap_html_size(cls, v: list[Slide]) -> list[Slide]:
        return _cap_slide_bytes(v)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "deck"


# Lines slidify's own failure paths produce. Everything else on stderr is
# progress narration from the converter and its dependencies.
_SLIDIFY_ERROR_LINE = re.compile(
    r"^(?:slidify: |Traceback|\S*(?:Error|Exception): |.*\[\s*(?:error|critical)\s*\])",
    re.IGNORECASE,
)


def _slidify_failure_reason(stderr: str | None, stdout: str | None) -> str:
    """One human-readable sentence for why a slidify attempt failed.

    The tail of stderr is not that sentence. Font embedding narrates a few
    thousand glyph names on its way out, so a fixed-size tail is usually pure
    glyph noise with the real error scrolled off the top, and this string is
    caller-facing: it reaches the agent, and the agent shows it to a person.
    Prefer the lines slidify writes when it is actually failing, and say
    nothing rather than say glyph names.
    """
    for stream in (stderr, stdout):
        matches = [
            line.strip()
            for line in (stream or "").splitlines()
            if _SLIDIFY_ERROR_LINE.match(line.strip())
        ]
        if matches:
            return " / ".join(matches[-3:])[:400]
    return ""


def _run_slidify(
    slide_dir: pathlib.Path, out: pathlib.Path, report: pathlib.Path
) -> tuple[bool, str]:
    profile = os.getenv("SLIDIFY_PROFILE", "full").lower()
    order = ["full", "balanced", "fast"]
    start = order.index(profile) if profile in order else 0
    # Without LibreOffice the oracle cannot measure fidelity. Native-content
    # and editability admission still apply to the balanced profile.
    if start == 0 and shutil.which("soffice") is None:
        logger.warning("libreoffice absent: skipping the fidelity oracle")
        start = 1
    # Degrade gracefully: keep native output as long as possible, only drop to
    # the raster-heavy profile if richer ones fail outright.
    attempts = [_PROFILES[name] for name in order[start:]]

    timeout = float(os.getenv("SLIDIFY_TIMEOUT_SECONDS", "600"))
    executable = shutil.which("slidify") or "slidify"
    last_error = ""
    for extra_args in attempts:
        cmd = [
            executable,
            "convert",
            str(slide_dir),
            str(out),
            *extra_args,
            "--report-json",
            str(report),
            "--progress",
            "off",
        ]
        logger.info("running: %s", " ".join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last_error = f"slidify timed out after {timeout:.0f}s"
            continue
        # Exit 3 is slidify's editability-drift code: the deck converted, and a
        # quality gate on it did not pass. Retrying that at a poorer profile
        # cannot fix drift, it only trades native shapes for pictures, and the
        # cascade ends by reporting failure and throwing away a deck whose
        # native_area_ratio was 1.0. The drift already reaches the caller as
        # `editability_passed` in the quality report, which is where a warning
        # belongs. Only a missing artifact is a failure worth degrading for.
        if proc.returncode in (0, 3) and out.is_file():
            if proc.returncode == 3:
                logger.warning("slidify reports editability drift; see the report")
            return True, ""
        last_error = _slidify_failure_reason(proc.stderr, proc.stdout)
        logger.warning(
            "slidify attempt failed (%s): %s\n%s",
            extra_args,
            last_error,
            (proc.stderr or proc.stdout or "")[-4000:],
        )
    return False, last_error or "slidify produced no output"


def _native_area_ratio(report: pathlib.Path) -> float | None:
    try:
        with report.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("native_area_ratio", "nativeAreaRatio"):
        value = data.get(key)
        if isinstance(value, (int, float)):
            return round(float(value), 4)
    return None


# Caps so a pathological deck cannot return an unbounded defect list.
_MAX_DEFECTS = 24


def _pptx_readability_report(pptx_path: pathlib.Path) -> list[dict]:
    """Report text that became too small in the emitted PowerPoint."""
    try:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        presentation = Presentation(str(pptx_path))
    except Exception:
        logger.warning("could not inspect PPTX typography", exc_info=True)
        return []

    minimum_pt = float(os.getenv("SLIDEGEN_MIN_FONT_PT", "13.5"))
    failures: list[dict] = []

    def walk(shapes):
        for shape in shapes:
            if getattr(shape, "shape_type", None) == MSO_SHAPE_TYPE.GROUP:
                yield from walk(shape.shapes)
            else:
                yield shape

    for slide_index, slide in enumerate(presentation.slides):
        smallest: tuple[float, str] | None = None
        for shape in walk(slide.shapes):
            if not getattr(shape, "has_text_frame", False):
                continue
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    text = (run.text or "").strip()
                    size = getattr(run.font, "size", None)
                    if not text or size is None:
                        continue
                    size_pt = float(size.pt)
                    if smallest is None or size_pt < smallest[0]:
                        smallest = (size_pt, text[:80])
        if smallest is not None and smallest[0] < minimum_pt:
            failures.append(
                {
                    "slide_index": slide_index,
                    "font_size_pt": round(smallest[0], 1),
                    "minimum_pt": minimum_pt,
                    "sample_text": smallest[1],
                }
            )
    return failures


def _quality_report(
    report: pathlib.Path, pptx_path: pathlib.Path | None = None
) -> dict:
    """Extract actionable defects from slidify's ConversionResult.

    These are deterministic compile-time facts (the DOM walker captured the
    resolved layout), not render guesses, so they can drive an automatic
    revision pass instead of a human eyeballing PNGs.
    """
    try:
        with report.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    def _get(*names, default=None):
        for n in names:
            if n in data:
                return data[n]
        return default

    overflow = []
    for item in (_get("overflow_elements", "overflowElements", default=[]) or [])[
        :_MAX_DEFECTS
    ]:
        if not isinstance(item, dict):
            continue
        overflow.append(
            {
                "slide_index": item.get("slide_index", item.get("slideIndex")),
                "axis": item.get("axis"),
                "overflow_px": round(float(item.get("overflow_px", 0) or 0), 1),
                "tag": item.get("tag", ""),
                "sample_text": (item.get("sample_text") or "")[:80],
                "hint": (item.get("hint") or "")[:160],
            }
        )

    gaps = []
    for item in (_get("coverage_gaps", "coverageGaps", default=[]) or [])[
        :_MAX_DEFECTS
    ]:
        if not isinstance(item, dict):
            continue
        gaps.append(
            {
                "slide_index": item.get("slide_index", item.get("slideIndex")),
                "sample_text": (item.get("sample_text") or "")[:80],
                "overlap_ratio": item.get("overlap_ratio", item.get("overlapRatio")),
            }
        )

    fidelity = []
    for item in (_get("fidelity_reports", "fidelityReports", default=[]) or [])[:20]:
        if not isinstance(item, dict) or item.get("passed", True):
            continue
        fidelity.append(
            {
                "slide_index": item.get("slide_index", item.get("slideIndex")),
                "ssim": item.get("ssim"),
                "ocr_recall": item.get("ocr_recall", item.get("ocrRecall")),
                "note": (item.get("note") or "")[:120],
            }
        )

    escape = _get("escape_rate", "escapeRate", default={}) or {}
    return {
        "native_area_ratio": _native_area_ratio(report),
        # Diagnostics: when native_area_ratio is low these explain where the
        # area went (which tier decided, escape-hatch rasters, unmatched
        # signatures the pattern matcher could not classify).
        "diagnostics": {
            "n_slides": _get("n_slides", "nSlides"),
            "llm_calls": _get("llm_calls", "llmCalls"),
            "decisions_by_tier": _get(
                "decisions_by_tier", "decisionsByTier", default={}
            ),
            "pattern_coverage": _get("pattern_coverage", "patternCoverage"),
            "escape_rate": escape if isinstance(escape, dict) else {},
            "unmatched_signatures": len(
                _get("unmatched_signatures", "unmatchedSignatures", default=[]) or []
            ),
            "exclusivity_violations": len(
                _get("exclusivity_violations", "exclusivityViolations", default=[])
                or []
            ),
        },
        "editability_passed": bool(
            _get("editability_passed", "editabilityPassed", default=True)
        ),
        "editability_failing_slides": _get(
            "editability_failing_slides", "editabilityFailingSlides", default=[]
        )
        or [],
        "overflow": overflow,
        "coverage_gaps": gaps,
        "fidelity_failures": fidelity,
        "small_text": (
            _pptx_readability_report(pptx_path) if pptx_path is not None else []
        ),
    }


def _upload_to_gcs(local_path: pathlib.Path, deck_title: str) -> tuple[str, str | None]:
    from google.cloud import storage

    bucket_name = os.environ["SLIDEGEN_GCS_BUCKET"]
    object_key = (
        f"decks/{dt.datetime.now(dt.UTC):%Y/%m/%d}/"
        f"{uuid.uuid4().hex}-{_slug(deck_title)}.pptx"
    )
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_key)
    blob.upload_from_filename(str(local_path), content_type=PPTX_MIME)
    gs_uri = f"gs://{bucket_name}/{object_key}"

    https_url = _signed_url(blob, deck_title)
    return gs_uri, https_url


def _signed_url(blob, deck_title: str) -> str | None:
    """V4-sign a blob URL using IAM SignBlob.

    On Cloud Run the ambient credentials are
    ``compute_engine.credentials.Credentials``, which hold only a token and no
    private key, so ``generate_signed_url`` cannot sign locally. Passing
    ``service_account_email`` plus ``access_token`` makes the client sign via
    the IAM ``signBlob`` API instead, which requires the runtime identity to
    hold ``roles/iam.serviceAccountTokenCreator`` on itself.
    """
    import google.auth
    import google.auth.transport.requests

    common = {
        "version": "v4",
        "expiration": dt.timedelta(days=7),
        "response_disposition": f'attachment; filename="{_slug(deck_title)}.pptx"',
    }

    try:
        return blob.generate_signed_url(**common)
    except Exception as exc:  # no local private key; fall back to IAM signing
        logger.info("local signing unavailable (%s); trying IAM signBlob", exc)

    try:
        credentials, _ = google.auth.default()
        credentials.refresh(google.auth.transport.requests.Request())
        service_account_email = getattr(credentials, "service_account_email", None)
        if not service_account_email:
            service_account_email = os.getenv("SLIDEGEN_SIGNER_SA") or None
        if not service_account_email:
            logger.warning("no service account email available for IAM signing")
            return None
        return blob.generate_signed_url(
            service_account_email=service_account_email,
            access_token=credentials.token,
            **common,
        )
    except Exception as exc:
        logger.warning("signed URL unavailable (%s); returning gs_uri only", exc)
        return None


class ShotRequest(BaseModel):
    slides: list[Slide] = Field(min_length=1, max_length=12)
    width: int = 1280
    height: int = 720
    scale: float = 1.0


def _screenshot_slides(
    slides: list[Slide], width: int, height: int, scale: float
) -> list[str]:
    """Render each slide to a base64 PNG at the authored viewport size.

    The design model cannot judge its own work from source alone; this is what
    lets a vision pass see the composition it actually produced.
    """
    import base64

    from playwright.sync_api import sync_playwright

    shots: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=max(0.5, min(scale, 2.0)),
            )
            for slide in slides:
                page.set_content(slide.html, wait_until="networkidle")
                page.wait_for_timeout(150)
                shots.append(
                    base64.b64encode(page.screenshot(type="png")).decode("ascii")
                )
        finally:
            browser.close()
    return shots


@app.post("/screenshot")
def screenshot(request: ShotRequest, http_request: Request) -> JSONResponse:
    required_key = os.getenv("RENDERER_API_KEY", "")
    if required_key and http_request.headers.get("x-api-key") != required_key:
        return JSONResponse(
            status_code=401, content={"ok": False, "error": "unauthorized"}
        )
    try:
        shots = _screenshot_slides(
            request.slides, request.width, request.height, request.scale
        )
    except Exception as exc:
        logger.exception("screenshot failed")
        return JSONResponse(
            status_code=200, content={"ok": False, "error": f"screenshot failed: {exc}"}
        )
    return JSONResponse(
        status_code=200, content={"ok": True, "count": len(shots), "images_b64": shots}
    )


# ---------------------------------------------------------------------------
# Reference-deck extraction
# ---------------------------------------------------------------------------

_SLIDES_URL = re.compile(
    r"docs\.google\.com/presentation/d/(?:e/)?([a-zA-Z0-9_-]+)", re.I
)
_MAX_REF_PAGES = 8
_MAX_REF_BYTES = 60 * 1024 * 1024


class ExtractRequest(BaseModel):
    source_url: str = ""
    file_b64: str = ""
    filename: str = ""
    max_pages: int = _MAX_REF_PAGES


def _resolve_source_url(url: str) -> str:
    """Google Slides links are turned into their PDF export endpoint.

    Only link-shared decks resolve without credentials; private decks need
    3-legged OAuth, which the caller must handle by uploading a file instead.
    """
    m = _SLIDES_URL.search(url)
    if m:
        return f"https://docs.google.com/presentation/d/{m.group(1)}/export/pdf"
    return url


def _sniff(data: bytes) -> str:
    if data[:4] == b"%PDF":
        return "pdf"
    # PPTX is a zip; check for the presentation part rather than trusting name.
    if data[:2] == b"PK" and b"ppt/" in data[:8192]:
        return "pptx"
    if data[:2] == b"PK":
        return "pptx"
    return "unknown"


def _pptx_theme(path: pathlib.Path) -> dict:
    """Theme colours, fonts and layout names straight from the OOXML."""
    try:
        from pptx import Presentation
    except ImportError:
        return {}
    try:
        prs = Presentation(str(path))
    except Exception:
        logger.warning("could not open pptx for theme extraction")
        return {}

    fonts: list[str] = []
    texts: list[str] = []
    for slide in list(prs.slides)[:_MAX_REF_PAGES]:
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    name = getattr(run.font, "name", None)
                    if name and name not in fonts:
                        fonts.append(name)
                    if run.text and run.text.strip():
                        texts.append(run.text.strip())
    return {
        "slide_width_emu": getattr(prs, "slide_width", None),
        "slide_height_emu": getattr(prs, "slide_height", None),
        "n_slides": len(prs.slides._sldIdLst),
        "fonts": fonts[:8],
        "sample_text": texts[:40],
    }


def _run_tool(cmd: list[str], timeout: int) -> tuple[int, str]:
    """A missing binary is a failed conversion, not a failed request.

    Everything below is an optional post-step. Rendering the PPTX is the
    deliverable; the previews are a convenience, and a machine without
    LibreOffice or poppler installed should still get its deck.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{cmd[0]} is not installed"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]} timed out after {timeout}s"
    return proc.returncode, proc.stderr or ""


def _pdf_to_pngs(
    pdf: pathlib.Path, workdir: pathlib.Path, max_pages: int
) -> list[bytes]:
    prefix = workdir / "page"
    code, error = _run_tool(
        [
            "pdftoppm",
            "-png",
            "-r",
            "90",
            "-f",
            "1",
            "-l",
            str(max(1, min(max_pages, _MAX_REF_PAGES))),
            str(pdf),
            str(prefix),
        ],
        180,
    )
    if code != 0:
        logger.warning("pdftoppm failed: %s", error[-400:])
    return [p.read_bytes() for p in sorted(workdir.glob("page*.png"))]


def _pdf_to_jpegs(
    pdf: pathlib.Path, workdir: pathlib.Path, max_pages: int
) -> list[bytes]:
    """Create compact PPTX-faithful previews suitable for A2UI srcdoc."""
    prefix = workdir / "preview"
    code, error = _run_tool(
        [
            "pdftoppm",
            "-jpeg",
            "-r",
            "72",
            "-jpegopt",
            "quality=78,optimize=y,progressive=y",
            "-f",
            "1",
            "-l",
            str(max(1, min(max_pages, 6))),
            str(pdf),
            str(prefix),
        ],
        180,
    )
    if code != 0:
        logger.warning("preview rasterisation failed: %s", error[-400:])
    return [p.read_bytes() for p in sorted(workdir.glob("preview*.jpg"))]


def _pptx_to_pdf(pptx: pathlib.Path, workdir: pathlib.Path) -> pathlib.Path | None:
    _, error = _run_tool(
        [
            "soffice",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(workdir),
            str(pptx),
        ],
        300,
    )
    out = workdir / (pptx.stem + ".pdf")
    if out.is_file():
        return out
    logger.warning("libreoffice conversion failed: %s", error[-400:])
    return None


@app.post("/extract")
def extract(request: ExtractRequest, http_request: Request) -> JSONResponse:
    """Turn a reference deck (link or uploaded file) into page images + theme."""
    import base64

    required_key = os.getenv("RENDERER_API_KEY", "")
    if required_key and http_request.headers.get("x-api-key") != required_key:
        return JSONResponse(
            status_code=401, content={"ok": False, "error": "unauthorized"}
        )

    data = b""
    if request.file_b64:
        try:
            data = base64.b64decode(request.file_b64)
        except Exception:
            return JSONResponse(
                status_code=200,
                content={"ok": False, "error": "file_b64 is not valid base64"},
            )
    elif request.source_url.strip().startswith("gs://"):
        # Templates uploaded to our own bucket need no user OAuth: the runtime
        # service account already has read access.
        uri = request.source_url.strip()
        try:
            from google.cloud import storage

            bucket_name, _, object_key = uri[5:].partition("/")
            if not object_key:
                return JSONResponse(
                    status_code=200,
                    content={"ok": False, "error": f"{uri} is a bucket, not an object"},
                )
            blob = storage.Client().bucket(bucket_name).blob(object_key)
            if not blob.exists():
                return JSONResponse(
                    status_code=200,
                    content={"ok": False, "error": f"No such object: {uri}"},
                )
            data = blob.download_as_bytes()
        except Exception as exc:
            return JSONResponse(
                status_code=200,
                content={"ok": False, "error": f"Could not read {uri}: {exc}"},
            )
    elif request.source_url:
        url = _resolve_source_url(request.source_url.strip())
        try:
            with httpx.Client(follow_redirects=True, timeout=120) as client:
                resp = client.get(url)
            if resp.status_code != 200:
                return JSONResponse(
                    status_code=200,
                    content={
                        "ok": False,
                        "error": (
                            f"Could not fetch the deck (HTTP {resp.status_code}). "
                            "If this is a Google Slides link, set sharing to "
                            "'Anyone with the link', or upload the file instead."
                        ),
                    },
                )
            data = resp.content
        except Exception as exc:
            return JSONResponse(
                status_code=200, content={"ok": False, "error": f"fetch failed: {exc}"}
            )
    else:
        return JSONResponse(
            status_code=200,
            content={"ok": False, "error": "provide source_url or file_b64"},
        )

    if not data:
        return JSONResponse(
            status_code=200, content={"ok": False, "error": "empty download"}
        )
    if len(data) > _MAX_REF_BYTES:
        return JSONResponse(
            status_code=200,
            content={"ok": False, "error": "reference deck is too large"},
        )

    kind = _sniff(data)
    if kind == "unknown":
        return JSONResponse(
            status_code=200,
            content={
                "ok": False,
                "error": (
                    "That did not look like a PDF or PPTX. A Google Slides link "
                    "must be shared as 'Anyone with the link'; a private link "
                    "returns a sign-in page instead of the deck."
                ),
            },
        )

    with tempfile.TemporaryDirectory(prefix="slidegen-ref-") as tmp:
        work = pathlib.Path(tmp)
        theme: dict = {}
        if kind == "pptx":
            src = work / "deck.pptx"
            src.write_bytes(data)
            theme = _pptx_theme(src)
            pdf = _pptx_to_pdf(src, work)
            if pdf is None:
                return JSONResponse(
                    status_code=200,
                    content={"ok": False, "error": "could not convert PPTX to PDF"},
                )
        else:
            pdf = work / "deck.pdf"
            pdf.write_bytes(data)

        try:
            pages = _pdf_to_pngs(pdf, work, request.max_pages)
        except subprocess.TimeoutExpired:
            return JSONResponse(
                status_code=200,
                content={"ok": False, "error": "page rasterisation timed out"},
            )

    if not pages:
        return JSONResponse(
            status_code=200,
            content={"ok": False, "error": "no pages could be rendered"},
        )

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "kind": kind,
            "page_count": len(pages),
            "theme": theme,
            "images_b64": [base64.b64encode(p).decode("ascii") for p in pages],
        },
    )


TEMPLATE_PREFIX = os.getenv("SLIDEGEN_TEMPLATE_PREFIX", "templates/")


@app.get("/templates")
def templates(http_request: Request) -> JSONResponse:
    """List reference templates uploaded to the deck bucket."""
    required_key = os.getenv("RENDERER_API_KEY", "")
    if required_key and http_request.headers.get("x-api-key") != required_key:
        return JSONResponse(
            status_code=401, content={"ok": False, "error": "unauthorized"}
        )
    bucket_name = os.getenv("SLIDEGEN_GCS_BUCKET", "")
    if not bucket_name:
        return JSONResponse(
            status_code=200, content={"ok": False, "error": "bucket not configured"}
        )
    try:
        from google.cloud import storage

        client = storage.Client()
        items = []
        for blob in client.list_blobs(bucket_name, prefix=TEMPLATE_PREFIX):
            if blob.name.endswith("/"):
                continue
            if not blob.name.lower().endswith((".pptx", ".ppt", ".pdf")):
                continue
            # Only real uploads: templates live directly under the prefix.
            # Prepared bundles (contract/spec/plate artifacts under
            # <slug>/prepared/) are internal and must never be offered as
            # user templates.
            if "/" in blob.name[len(TEMPLATE_PREFIX) :]:
                continue
            items.append(
                {
                    "name": blob.name[len(TEMPLATE_PREFIX) :],
                    "gs_uri": f"gs://{bucket_name}/{blob.name}",
                    "size_bytes": blob.size,
                    "updated": blob.updated.isoformat() if blob.updated else None,
                    "generation": str(blob.generation or ""),
                    "etag": str(blob.etag or ""),
                }
            )
    except Exception as exc:
        return JSONResponse(
            status_code=200, content={"ok": False, "error": f"list failed: {exc}"}
        )
    return JSONResponse(
        status_code=200, content={"ok": True, "count": len(items), "templates": items}
    )


# ---------------------------------------------------------------------------
# Template population
# ---------------------------------------------------------------------------
#
# Style transfer (rasterise -> infer a design system -> author fresh HTML ->
# slidify) re-creates a brand from a description. Population instead opens the
# customer's own .pptx and fills its real layouts, so masters, embedded fonts,
# logos and vector artwork are the genuine articles rather than reconstructions
# — and it skips HTML authoring and conversion entirely, which is most of the
# wall-clock time.


class TemplateSourceRequest(BaseModel):
    gs_uri: str = Field(pattern=r"^gs://[^/]+/.+\.pptx$")
    generation: str = Field(pattern=r"^[0-9]+$")


@app.post("/template-source")
def template_source(request: TemplateSourceRequest, http_request: Request):
    """Prepare every source slide, without publishing or inferring its design."""
    required_key = os.getenv("RENDERER_API_KEY", "")
    if required_key and http_request.headers.get("x-api-key") != required_key:
        return JSONResponse(status_code=401, content={"ok": False, "error": "unauthorized"})
    from google.cloud import storage
    from renderer.template_source import archive, prepare_source

    try:
        bucket, _, key = request.gs_uri[5:].partition("/")
        blob = storage.Client().bucket(bucket).blob(key, generation=int(request.generation))
        blob.reload()
        if not blob.size or blob.size > _MAX_REF_BYTES:
            raise ValueError("template is empty or exceeds the source size limit")
        with tempfile.TemporaryDirectory(prefix="template-source-") as tmp:
            workdir = pathlib.Path(tmp)
            source = workdir / "template.pptx"
            blob.download_to_filename(source, if_generation_match=int(request.generation))
            payload = archive(prepare_source(source, workdir))
        # Stream the archive; complete template imagery can exceed a normal
        # non-streaming Cloud Run response. No artifacts are uploaded here.
        return StreamingResponse((payload[i:i + 65536] for i in range(0, len(payload), 65536)), media_type="application/zip")
    except Exception as exc:
        logger.exception("source template preparation failed")
        return JSONResponse(status_code=200, content={"ok": False, "error": str(exc)})


class PopulateSlide(BaseModel):
    archetype: str = "title+body"
    title: str = ""
    subtitle: str = ""
    body: list[str] = Field(default_factory=list)


class PopulateRequest(BaseModel):
    template_gs_uri: str
    deck_title: str = "Deck"
    slides: list[PopulateSlide] = Field(min_length=1, max_length=20)
    preview_pages: int = 6


def _ph_kind(shape) -> str:
    raw = str(shape.placeholder_format.type).split(" ")[0].upper()
    return {
        "CENTER_TITLE": "TITLE",
        "VERTICAL_TITLE": "TITLE",
        "OBJECT": "BODY",
        "TEXT": "BODY",
        "VERTICAL_BODY": "BODY",
    }.get(raw, raw)


def classify_layout(layout) -> str:
    """Bucket a layout by its placeholder composition.

    Template layout NAMES are unreliable (this deck exports from Google Slides
    as "Text Styles_1_1_2"), but the placeholder mix is structural.
    """
    kinds = [_ph_kind(s) for s in layout.placeholders]
    title = kinds.count("TITLE")
    sub = kinds.count("SUBTITLE")
    body = kinds.count("BODY")
    pic = kinds.count("PICTURE")
    if title and pic and not body:
        return "hero/section"
    if title and body >= 2:
        return "multi-column body"
    if title and body:
        return "title+body"
    if title and sub:
        return "title+subtitle"
    if title and not (sub or body or pic):
        return "section divider"
    return "other"


def _layout_inventory(prs) -> list[dict]:
    out = []
    for i, layout in enumerate(
        [lay for m in prs.slide_masters for lay in m.slide_layouts]
    ):
        kinds = [_ph_kind(s) for s in layout.placeholders]
        out.append(
            {
                "index": i,
                "name": layout.name,
                "archetype": classify_layout(layout),
                "placeholders": kinds,
                "body_slots": kinds.count("BODY"),
                "has_picture": "PICTURE" in kinds,
            }
        )
    return out


def _slide_shape_counts(slide) -> dict[str, int]:
    """Count the visual and editable structure carried by a real slide.

    A PowerPoint layout only describes placeholders and master artwork. Many
    brand templates (including Example Retail 100Y) keep their photography, masks,
    curves and lockups on the specimen slides themselves. Those objects must be
    inventoried separately or a layout-only population silently drops them.
    """
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    kinds = [
        _ph_kind(shape)
        for shape in slide.placeholders
        if getattr(shape, "is_placeholder", False)
    ]
    return {
        "title_slots": kinds.count("TITLE"),
        "subtitle_slots": kinds.count("SUBTITLE"),
        "body_slots": kinds.count("BODY"),
        "picture_slots": kinds.count("PICTURE"),
        "pictures": sum(
            shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in slide.shapes
        ),
        "groups": sum(
            shape.shape_type == MSO_SHAPE_TYPE.GROUP for shape in slide.shapes
        ),
        "tables": sum(getattr(shape, "has_table", False) for shape in slide.shapes),
    }


def classify_source_slide(slide) -> str:
    """Classify a specimen slide using its live shapes, not only its layout."""
    counts = _slide_shape_counts(slide)
    title = counts["title_slots"]
    sub = counts["subtitle_slots"]
    body = counts["body_slots"]
    visual = counts["pictures"] + counts["groups"]
    if title and body >= 2:
        return "multi-column body"
    if title and body:
        return "title+body"
    if title and sub:
        # A lone lockup/logo is not a photographic hero. Picture placeholders,
        # multiple pictures, or grouped artwork indicate a genuine visual page.
        if visual and (
            counts["picture_slots"]
            or counts["pictures"] >= 2
            or counts["groups"]
        ):
            return "hero/section"
        return "title+subtitle"
    if title and not (sub or body):
        return "section divider"
    return "other"


def _source_slide_inventory(prs) -> list[dict]:
    inventory = []
    for index, slide in enumerate(prs.slides):
        counts = _slide_shape_counts(slide)
        inventory.append(
            {
                "index": index,
                "layout": slide.slide_layout.name,
                "archetype": classify_source_slide(slide),
                **counts,
            }
        )
    return inventory


def _source_slide_score(entry: dict, spec: PopulateSlide) -> float:
    """Rank an unused specimen slide for content fit and visual richness."""
    requested = spec.archetype.strip().lower()
    actual = entry["archetype"]
    score = 0.0
    if actual == requested:
        score += 100
    elif requested == "multi-column body" and actual == "title+body":
        score += 70
    elif requested in {"title+body", "multi-column body"} and actual in {
        "hero/section",
        "title+subtitle",
    }:
        score += 50
    elif requested == "hero/section" and actual == "title+subtitle":
        score += 65
    elif actual == "section divider":
        score += 20

    needed_body = len(spec.body)
    score += min(entry["body_slots"], needed_body) * 8
    if needed_body and entry["body_slots"] == 0:
        score -= 10

    # The main reason to reuse specimen slides is their real design material.
    score += entry["pictures"] * 12 + entry["groups"] * 5
    # Stable tie-break that favors the template author's earlier variants.
    score -= entry["index"] / 1000
    return score


def _editable_source_entries(prs) -> list[dict]:
    """Return safe, reusable specimen slides and their presentation IDs."""
    entries = []
    for index, (slide, slide_id) in enumerate(
        zip(prs.slides, list(prs.slides._sldIdLst), strict=True)
    ):
        counts = _slide_shape_counts(slide)
        if counts["title_slots"] == 0:
            # Tables and free-form text boxes require a richer content mapping
            # contract. Do not corrupt them by pretending they are placeholders.
            continue
        prototype_text = " ".join(
            shape.text.strip()
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False) and shape.text.strip()
        )
        fixed_copy = " ".join(
            shape.text.strip()
            for shape in slide.shapes
            if not getattr(shape, "is_placeholder", False)
            and getattr(shape, "has_text_frame", False)
            and shape.text.strip()
        )
        # Long fixed copy is usually attribution, legal, cultural, or another
        # semantically bound element. Exclude the whole specimen rather than
        # guessing from brand-specific words or silently leaving mismatched copy.
        if len(fixed_copy) > 80:
            continue
        entries.append(
            {
                "index": index,
                "slide": slide,
                "slide_id": slide_id,
                "layout": slide.slide_layout.name,
                "archetype": classify_source_slide(slide),
                "prototype_text": prototype_text,
                **counts,
            }
        )
    return entries


def _pick_source_slide(entries: list[dict], spec: PopulateSlide) -> dict | None:
    if not entries:
        return None
    return max(entries, key=lambda entry: _source_slide_score(entry, spec))


def _fit_placeholder_font_size(shape, value: str, role: str) -> float | None:
    """Choose readable type that fits the placeholder's actual geometry.

    python-pptx does not expose the fully cascaded theme font size, and Office's
    auto-fit flag is not honored consistently by LibreOffice. This bounded
    estimator uses the live box and margins, preserves theme colour/family, and
    never crosses the role's readability floor.
    """
    if not value:
        return None
    from pptx.util import Pt

    maximum, minimum = {
        "counter": (32, 24),
        "title": (60, 30),
        "subtitle": (32, 22),
        "body": (26, 18),
    }.get(role, (26, 18))
    frame = shape.text_frame
    width_pt = max(
        1.0,
        (shape.width - frame.margin_left - frame.margin_right) / 12700,
    )
    height_pt = max(
        1.0,
        (shape.height - frame.margin_top - frame.margin_bottom) / 12700,
    )
    selected = minimum
    for size in range(maximum, minimum - 1, -1):
        chars_per_line = max(1, int(width_pt / (size * 0.52)))
        lines = 0
        for paragraph in value.splitlines() or [""]:
            wrapped = textwrap.wrap(
                paragraph,
                width=chars_per_line,
                break_long_words=False,
                break_on_hyphens=False,
            )
            lines += max(1, len(wrapped))
        if lines * size * 1.18 <= height_pt:
            selected = size
            break
    for paragraph in frame.paragraphs:
        for run in paragraph.runs:
            run.font.size = Pt(selected)
    return float(selected)


def _set_placeholder_text(
    shape, value: str, role: str = "body"
) -> float | None:
    """Replace prototype copy while retaining theme colour and font family."""
    from pptx.enum.text import MSO_AUTO_SIZE

    shape.text = value
    try:
        text_frame = shape.text_frame
    except (AttributeError, ValueError):
        return None
    try:
        text_frame.word_wrap = True
        text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    except (AttributeError, ValueError):
        pass
    return _fit_placeholder_font_size(shape, value, role)


def _fill_source_slide(
    slide, spec: PopulateSlide, slide_number: int, slide_height: int
) -> dict:
    """Rewrite a real specimen slide without deleting its visual objects."""
    placeholders = list(slide.placeholders)
    title_slots = sorted(
        [shape for shape in placeholders if _ph_kind(shape) == "TITLE"],
        key=lambda shape: (shape.top, shape.left),
    )
    subtitle_slots = sorted(
        [
            shape
            for shape in placeholders
            if _ph_kind(shape) == "SUBTITLE" and shape.top < slide_height * 0.82
        ],
        key=lambda shape: (shape.top, shape.left),
    )
    body_slots = sorted(
        [shape for shape in placeholders if _ph_kind(shape) == "BODY"],
        key=lambda shape: (shape.left, shape.top),
    )

    filled: list[str] = []
    font_sizes: dict[str, list[float]] = {}
    consumed: set[int] = set()
    main_title = None
    if title_slots:
        if len(title_slots) > 1:
            counter = next(
                (
                    shape
                    for shape in title_slots
                    if re.fullmatch(r"\d{1,3}", shape.text.strip())
                    or shape.height < 914400
                ),
                title_slots[0],
            )
            size = _set_placeholder_text(counter, f"{slide_number:02d}", "counter")
            if size is not None:
                font_sizes.setdefault("SECTION_NUMBER", []).append(size)
            consumed.add(id(counter))
            filled.append("SECTION_NUMBER")
            remaining_titles = [shape for shape in title_slots if shape is not counter]
            main_title = max(remaining_titles, key=lambda shape: shape.width * shape.height)
        else:
            main_title = title_slots[0]
        size = _set_placeholder_text(main_title, spec.title, "title")
        if size is not None:
            font_sizes.setdefault("TITLE", []).append(size)
        consumed.add(id(main_title))
        filled.append("TITLE")

    if subtitle_slots and spec.subtitle:
        size = _set_placeholder_text(subtitle_slots[0], spec.subtitle, "subtitle")
        if size is not None:
            font_sizes.setdefault("SUBTITLE", []).append(size)
        consumed.add(id(subtitle_slots[0]))
        filled.append("SUBTITLE")

    body_queue = list(spec.body)
    if body_slots and body_queue:
        for index, slot in enumerate(body_slots):
            if not body_queue:
                break
            if index == len(body_slots) - 1:
                chunk = "\n".join(body_queue)
                body_queue.clear()
            else:
                chunk = body_queue.pop(0)
            size = _set_placeholder_text(slot, chunk, "body")
            if size is not None:
                font_sizes.setdefault("BODY", []).append(size)
            consumed.add(id(slot))
            filled.append("BODY")

    # Clear unused editable prototype copy (including footnote prompts) while
    # keeping every picture, group, table and picture placeholder relationship.
    for shape in placeholders:
        kind = _ph_kind(shape)
        if kind in {"TITLE", "SUBTITLE", "BODY"} and id(shape) not in consumed:
            _set_placeholder_text(shape, "")

    return {
        "filled": filled,
        "font_sizes_pt": font_sizes,
        "omitted_body_blocks": len(body_queue),
    }


def _pick_diverse_layout(pool: list, needed_body: int, usage: dict[int, int]):
    """Use every suitable template layout before repeating one.

    Corporate templates commonly contain several visually distinct layouts
    with the same placeholder archetype. Repeatedly choosing the first match
    collapses that design language into one monotonous slide. Usage count is
    therefore the primary rank, followed by placeholder fit and stable name.
    """
    ranked = sorted(
        pool,
        key=lambda layout: (
            usage.get(id(layout), 0),
            abs(
                sum(1 for shape in layout.placeholders if _ph_kind(shape) == "BODY")
                - needed_body
            ),
            str(layout.name),
        ),
    )
    chosen = ranked[0]
    usage[id(chosen)] = usage.get(id(chosen), 0) + 1
    return chosen


def _fill_slide(slide, spec: PopulateSlide) -> list[str]:
    """Populate placeholders positionally, never blanket-by-type.

    A layout can carry several SUBTITLE or BODY slots; assigning by type alone
    writes the same string into all of them.
    """
    filled: list[str] = []
    subtitle_used = False
    body_queue = list(spec.body)
    drop: list = []
    for ph in slide.placeholders:
        kind = _ph_kind(ph)
        if kind == "TITLE" and spec.title:
            _set_placeholder_text(ph, spec.title)
            filled.append("TITLE")
        elif kind == "SUBTITLE" and not subtitle_used and spec.subtitle:
            _set_placeholder_text(ph, spec.subtitle)
            subtitle_used = True
            filled.append("SUBTITLE")
        elif kind == "BODY" and body_queue:
            # One bullet block per BODY slot, so multi-column layouts split.
            chunk = body_queue.pop(0)
            _set_placeholder_text(ph, chunk)
            filled.append("BODY")
        else:
            # Unused placeholders would render as "Click to add text" prompts.
            drop.append(ph)

    for ph in drop:
        ph._element.getparent().remove(ph._element)
    return filled


def _populate_presentation(prs, specs: list[PopulateSlide]) -> list[dict]:
    """Populate an open presentation while preserving specimen relationships."""
    layouts = [lay for master in prs.slide_masters for lay in master.slide_layouts]
    if not layouts:
        raise ValueError("template has no slide layouts")
    by_arch: dict[str, list] = {}
    for layout in layouts:
        by_arch.setdefault(classify_layout(layout), []).append(layout)
    layout_usage: dict[int, int] = {}

    def pick_layout(archetype: str, needed_body: int):
        pool = by_arch.get(archetype) or by_arch.get("title+body") or layouts
        return _pick_diverse_layout(pool, needed_body, layout_usage)

    unused_source_entries = _editable_source_entries(prs)
    output_slide_ids = []
    report = []
    for slide_number, spec in enumerate(specs, start=1):
        source = _pick_source_slide(unused_source_entries, spec)
        if source is not None:
            unused_source_entries.remove(source)
            fill = _fill_source_slide(
                source["slide"], spec, slide_number, prs.slide_height
            )
            output_slide_ids.append(source["slide_id"])
            report.append(
                {
                    "archetype": spec.archetype,
                    "mode": "source_slide",
                    "layout": source["layout"],
                    "source_slide_index": source["index"],
                    "pictures_preserved": source["pictures"],
                    "groups_preserved": source["groups"],
                    **fill,
                }
            )
            continue

        # Some minimal templates have fewer safe specimen slides than the
        # requested deck. Preserve the old layout population as a bounded
        # fallback rather than duplicating slide relationships unsafely.
        layout = pick_layout(spec.archetype, max(1, len(spec.body)))
        slide = prs.slides.add_slide(layout)
        filled = _fill_slide(slide, spec)
        output_slide_ids.append(prs.slides._sldIdLst[-1])
        report.append(
            {
                "archetype": spec.archetype,
                "mode": "layout_fallback",
                "layout": layout.name,
                "layout_index": layouts.index(layout),
                "pictures_preserved": 0,
                "groups_preserved": 0,
                "filled": filled,
                "omitted_body_blocks": 0,
            }
        )

    # Keep selected source slides in narrative order and hide unselected
    # specimens. Moving existing IDs preserves image/group relationships.
    slide_id_list = prs.slides._sldIdLst
    for slide_id in list(slide_id_list):
        slide_id_list.remove(slide_id)
    for slide_id in output_slide_ids:
        slide_id_list.append(slide_id)
    return report


@app.get("/template-layouts")
def template_layouts(gs_uri: str, http_request: Request) -> JSONResponse:
    """Layout inventory for a template, so callers can target archetypes."""
    required_key = os.getenv("RENDERER_API_KEY", "")
    if required_key and http_request.headers.get("x-api-key") != required_key:
        return JSONResponse(
            status_code=401, content={"ok": False, "error": "unauthorized"}
        )
    try:
        from google.cloud import storage
        from pptx import Presentation

        bucket_name, _, key = gs_uri[5:].partition("/")
        data = storage.Client().bucket(bucket_name).blob(key).download_as_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "t.pptx"
            path.write_bytes(data)
            prs = Presentation(str(path))
            inv = _layout_inventory(prs)
            source_inv = _source_slide_inventory(prs)
            size = {
                "width_in": round(prs.slide_width / 914400, 2),
                "height_in": round(prs.slide_height / 914400, 2),
            }
    except Exception as exc:
        logger.exception("layout inventory failed")
        return JSONResponse(status_code=200, content={"ok": False, "error": str(exc)})
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "slide_size": size,
            "layout_count": len(inv),
            "layouts": inv,
            "source_slide_count": len(source_inv),
            "source_slides": source_inv,
        },
    )


@app.post("/populate")
def populate(request: PopulateRequest, http_request: Request) -> JSONResponse:
    """Build a deck by filling the customer's own template layouts."""
    import base64

    required_key = os.getenv("RENDERER_API_KEY", "")
    if required_key and http_request.headers.get("x-api-key") != required_key:
        return JSONResponse(
            status_code=401, content={"ok": False, "error": "unauthorized"}
        )

    try:
        from google.cloud import storage
        from pptx import Presentation

        uri = request.template_gs_uri
        bucket_name, _, key = uri[5:].partition("/")
        data = storage.Client().bucket(bucket_name).blob(key).download_as_bytes()
    except Exception as exc:
        return JSONResponse(
            status_code=200,
            content={
                "ok": False,
                "error": f"could not read template {request.template_gs_uri}: {exc}",
            },
        )

    with tempfile.TemporaryDirectory(prefix="slidegen-pop-") as tmp:
        work = pathlib.Path(tmp)
        src = work / "template.pptx"
        src.write_bytes(data)
        try:
            prs = Presentation(str(src))
        except Exception as exc:
            return JSONResponse(
                status_code=200,
                content={"ok": False, "error": f"not a readable pptx: {exc}"},
            )

        try:
            report = _populate_presentation(prs, request.slides)
        except ValueError as exc:
            return JSONResponse(
                status_code=200, content={"ok": False, "error": str(exc)}
            )

        out = work / "deck.pptx"
        prs.save(str(out))

        try:
            gs_uri, https_url = _upload_to_gcs(out, request.deck_title)
        except Exception as exc:
            return JSONResponse(
                status_code=200,
                content={"ok": False, "error": f"GCS upload failed: {exc}"},
            )

        previews: list[str] = []
        if request.preview_pages > 0:
            pdf = _pptx_to_pdf(out, work)
            if pdf is not None:
                try:
                    previews = [
                        base64.b64encode(p).decode("ascii")
                        for p in _pdf_to_pngs(pdf, work, request.preview_pages)
                    ]
                except Exception:
                    logger.warning("preview rasterisation failed", exc_info=True)

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "gs_uri": gs_uri,
            "https_url": https_url,
            "slide_count": len(request.slides),
            "slides": report,
            "images_b64": previews,
        },
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/render")
def render(request: RenderRequest, http_request: Request) -> JSONResponse:
    required_key = os.getenv("RENDERER_API_KEY", "")
    if required_key and http_request.headers.get("x-api-key") != required_key:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "unauthorized"},
        )
    with tempfile.TemporaryDirectory(prefix="slidegen-") as tmp:
        tmp_path = pathlib.Path(tmp)
        slide_dir = tmp_path / "slides"
        slide_dir.mkdir()
        for i, slide in enumerate(request.slides, start=1):
            (slide_dir / f"{i:02d}.html").write_text(slide.html, encoding="utf-8")

        out = tmp_path / "out.pptx"
        report = tmp_path / "report.json"
        ok, error = _run_slidify(slide_dir, out, report)
        if not ok:
            return JSONResponse(status_code=200, content={"ok": False, "error": error})

        # Score before uploading. An uneditable deck is refused downstream
        # anyway, so putting it in the bucket first only leaves an artifact
        # nobody is allowed to hand over.
        quality = _quality_report(report, out)
        if quality.get("editability_passed") is False:
            failing = quality.get("editability_failing_slides") or []
            where = (
                "slides " + ", ".join(str(index + 1) for index in failing)
                if failing
                else "at least one slide"
            )
            logger.warning("quality admission failed on %s", where)
            return JSONResponse(
                status_code=200,
                content={
                    "ok": False,
                    "error": (
                        f"Quality admission failed: the converter could not rebuild "
                        f"{where} as native editable shapes."
                    ),
                },
            )

        from renderer.content_check import missing_content

        missing = missing_content([slide.html for slide in request.slides], out)
        if missing:
            logger.warning("source content missing from PPTX: %s", missing)
            return JSONResponse(status_code=200, content={"ok": False,
                "error": "Quality admission failed: authored content is missing from the editable PowerPoint.",
                "content_failures": missing})
        quality["content_preserved"] = True

        try:
            gs_uri, https_url = _upload_to_gcs(out, request.deck_title)
        except Exception as exc:
            logger.exception("GCS upload failed")
            return JSONResponse(
                status_code=200,
                content={"ok": False, "error": f"GCS upload failed: {exc}"},
            )

        previews: list[str] = []
        if request.preview_pages > 0:
            pdf = _pptx_to_pdf(out, tmp_path)
            if pdf is not None:
                try:
                    import base64

                    previews = [
                        base64.b64encode(image).decode("ascii")
                        for image in _pdf_to_jpegs(pdf, tmp_path, request.preview_pages)
                    ]
                except Exception:
                    logger.warning("preview rasterisation failed", exc_info=True)
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "gs_uri": gs_uri,
                "https_url": https_url,
                "slide_count": len(request.slides),
                "native_area_ratio": quality.get("native_area_ratio"),
                "quality": quality,
                "images_b64": previews,
                "preview_mime": "image/jpeg",
            },
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
