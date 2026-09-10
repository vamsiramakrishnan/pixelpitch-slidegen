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

"""One deck turn: stage a workspace, run the harness, harvest the artifact.

Both lanes are the same shape. The template lane stages a hash-pinned prepared
bundle and harvests `patches.json`; the brand lane stages a guideline and
harvests `slides/`. Everything between those two points belongs to the
harness, which selects its own skills.

Nothing in this module states how a slide should look. Design knowledge lives
in the skills under `agy-worker/skills/`, where the harness discovers it; a
system prompt that restates it would just be a second, staler copy.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
import tempfile
import threading
from pathlib import Path

from app.agy_authoring import AuthoringCancelled
from app.agy_authoring import generate as agy_turn
from app.template_policy import (
    BaselineSpecimen,
    DeckProvenance,
    PreparedBundleManifest,
    SeamReplacement,
    SlidePatch,
    TemplatePolicyError,
    apply_seam_patch,
    validate_prepared_manifest,
)

logger = logging.getLogger(__name__)

DECK_MODEL = os.getenv("SLIDEGEN_AUTHORING_MODEL", "gemini-3.7-flash")

# The whole system prompt. It states where the work happens and which skill
# owns the method; the method itself is the skill's to disclose.
DECK_SYSTEM = """You are Pixelpitch's deck harness.

Use the pixelpitch-deck skill. It is the entry point and routes to everything
else you need: craft, template roundtrip, brand derivation. Read it before you
plan, and follow the playbook it selects.

Work only inside the workspace named in the message. Its layout and the shape
of what you hand back are in the deck skill's references/workspace.md.

The tools in each skill's scripts/ directory are the supported way to do the
mechanical steps. Prefer them over reinventing the step in prose.
"""


def slug_for(name: str) -> str:
    """Stable slug for a template's display/file name.

    Normalizes corporate-file noise ("Copy of ...", "... Presentation
    Template") so the slug derived at runtime matches the published bundle.
    """
    cleaned = (name or "").lower()
    cleaned = re.sub(r"^(a |an |the )?copy of ", "", cleaned)
    cleaned = re.sub(r"\b(presentation )?template\b", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    return slug or "default"


def slug_for_object(path: str) -> str:
    """Slug for a template's storage object: a gs:// URI or a bare object name.

    A prepared bundle lives under templates/<slug>/, and the slug comes from
    the file name with its suffix removed. Passing the raw object name to
    slug_for instead appends "-pptx", which is a prefix nothing publishes to,
    so the lookup misses and the template reports unprepared forever. This is
    the only rule for turning a stored object into that slug; the runtime lane
    and the gallery must not each derive their own.
    """
    basename = (path or "").rsplit("/", 1)[-1]
    return slug_for(basename.rsplit(".", 1)[0] if "." in basename else basename)


def display_name_for_object(path: str) -> str:
    """What to call a stored template on screen.

    Deliberately not derived from the slug. Slugs address published bundles
    and so can never be tightened without orphaning what is already in the
    bucket, whereas a label is free to drop filing noise the slug is stuck
    with. Casing is left alone rather than title-cased, because these names
    carry acronyms and a titlecased "AU" becomes "Au".
    """
    basename = (path or "").rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    cleaned = stem.replace("_", " ")
    cleaned = re.sub(r"^(a |an |the )?copy of\s+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^please make a copy\s+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(presentation\s+)?template\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")
    return cleaned or stem or "Template"


# --------------------------------------------------------------------------
# prepared bundles


def _load_prepared_bundle(
    slug: str, template_gs_uri: str = ""
) -> tuple[PreparedBundleManifest | None, dict[str, bytes], str | None]:
    """Download and validate a complete manifest-pinned prepared bundle."""
    bucket_name = os.getenv("SLIDEGEN_GCS_BUCKET", "")
    if not bucket_name:
        return None, {}, "SLIDEGEN_GCS_BUCKET is unset"
    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        prefix = f"templates/{slug}/prepared/"
        versioned = False
        generation = None
        etag = None
        if template_gs_uri:
            from app.template_versions import TemplateVersion, ready_prefix
            source_bucket, _, source_key = template_gs_uri[5:].partition("/")
            source_blob = client.bucket(source_bucket).blob(source_key)
            source_blob.reload()
            generation = str(source_blob.generation or "")
            etag = str(source_blob.etag or "")
            version = TemplateVersion(source_bucket, source_key, generation)
            committed = ready_prefix(bucket, version)
            versioned = committed is not None
            prefix = committed or prefix
        manifest_blob = bucket.blob(f"{prefix}manifest.json")
        if not manifest_blob.exists():
            return None, {}, "prepared manifest is missing"
        try:
            manifest_value = json.loads(manifest_blob.download_as_bytes())
            provisional = PreparedBundleManifest.from_dict(manifest_value)
        except (ValueError, TemplatePolicyError) as exc:
            return None, {}, f"prepared manifest is invalid: {exc}"

        artifacts: dict[str, bytes] = {}
        for artifact in provisional.artifacts:
            blob = bucket.blob(f"{prefix}{artifact.path}")
            if not blob.exists():
                return None, {}, f"prepared artifact is missing: {artifact.path}"
            artifacts[artifact.path] = blob.download_as_bytes()

        manifest = validate_prepared_manifest(
            manifest_value,
            artifacts,
            expected_source_uri=template_gs_uri or None,
            expected_generation=generation,
            # Metadata-only edits can change an ETag without changing bytes.
            # Generation is the content identity of a versioned bundle.
            expected_etag=None if versioned else etag,
        )
        return manifest, artifacts, None
    except Exception as exc:
        logger.exception("prepared bundle validation failed for %s", slug)
        return None, {}, f"prepared bundle could not be validated: {exc}"


def prepared_bundle_exists(slug: str, template_gs_uri: str = "") -> bool:
    """True only for a complete, hash-valid bundle with admitted baselines."""
    manifest, _, _ = _load_prepared_bundle(slug, template_gs_uri)
    return manifest is not None


def _local_artifact_path(workdir: Path, logical_path: str) -> Path:
    if logical_path.startswith(("spec/", "base/")):
        return workdir / "spec-out" / logical_path
    return workdir / logical_path


def _fetch_prepared_bundle(
    slug: str, template_gs_uri: str, workdir: Path
) -> tuple[PreparedBundleManifest | None, str | None]:
    """Stage only a hash-valid prepared bundle into ``workdir``."""
    manifest, artifacts, error = _load_prepared_bundle(slug, template_gs_uri)
    if manifest is None:
        return None, error
    for logical_path, value in artifacts.items():
        target = _local_artifact_path(workdir, logical_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    (workdir / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    return manifest, None


# --------------------------------------------------------------------------
# staging and running


def _write_brief(
    workdir: Path,
    *,
    topic: str,
    audience: str,
    goal: str,
    slide_count: int,
    expressions: list[str] | None,
) -> None:
    expression_note = ""
    if expressions:
        expression_note = (
            "**Chosen visual expressions.** Author the relevant slides in "
            "these patterns: " + ", ".join(expressions) + "."
        )
    (workdir / "brief.md").write_text(
        "# Deck brief\n\n"
        f"**Topic.** {topic}\n\n"
        f"**Audience.** {audience}\n\n"
        f"**Goal and tone.** {goal}\n\n"
        f"**Slides.** exactly {slide_count}\n\n"
        f"{expression_note}\n",
        encoding="utf-8",
    )


def _sample(paths: list[Path], limit: int = 8) -> list[str]:
    """An evenly spread sample, so the harness sees the range of specimens."""
    if not paths:
        return []
    step = max(1, len(paths) // limit)
    return [str(path) for path in paths[::step]][:limit]


def _template_attachments(manifest: PreparedBundleManifest, workdir: Path) -> list[str]:
    # Covers often occupy the first eight pages. Show a source specimen for
    # every admitted archetype before filling the remaining image budget.
    chosen: list[str] = []
    seen: set[str] = set()
    for slide_type in manifest.slide_types:
        if slide_type.source_slide and slide_type.archetype not in seen:
            chosen.append(str(workdir / "spec-out/base" / f"slide-{slide_type.source_slide:02d}.png"))
            seen.add(slide_type.archetype)
    for path in _sample(sorted((workdir / "spec-out/base").glob("slide-*.png"))):
        if path not in chosen:
            chosen.append(path)
    return chosen[:8]


def run_turn(
    *,
    workdir: Path,
    message: str,
    attachments: list[str] | None = None,
    triggers: list[dict] | None = None,
    on_event=None,
    cancellation_event: threading.Event | None = None,
) -> str:
    """One AGY turn scoped to ``workdir``. Returns its final text."""
    return agy_turn(
        contents=message,
        system=DECK_SYSTEM,
        model=DECK_MODEL,
        contents_files=attachments or [],
        triggers=triggers or [],
        workspaces=[str(workdir)],
        on_event=on_event,
        cancellation_event=cancellation_event,
    )


_WATCH_SECONDS = 1.0
_LEADING_NUMBER = re.compile(r"^(\d+)")


def _watch_slides(workdir: Path, on_slide, stop: threading.Event) -> threading.Thread:
    """Publish each slide the moment it lands, by watching the workspace.

    The harness owns its own concurrency now, so there is no fan-out to hang a
    per-slide callback on. Watching the directory recovers the same live
    feedback without the outer process dictating the order or the pace.

    A file is only published once its mtime has held steady for a full poll,
    which keeps a half-flushed write off the user's screen. A rewrite
    republishes; last write wins, same as the workspace contract says.
    """
    slides_dir = workdir / "slides"
    stamps: dict[str, float] = {}
    published: set[str] = set()
    arrivals: list[str] = []

    def run() -> None:
        while not stop.wait(_WATCH_SECONDS):
            for path in sorted(slides_dir.glob("*.html")):
                try:
                    stamp = path.stat().st_mtime
                except OSError:
                    continue
                if stamps.get(path.name) != stamp:
                    stamps[path.name] = stamp
                    published.discard(path.name)
                    continue
                if path.name in published:
                    continue
                published.add(path.name)
                if path.name not in arrivals:
                    arrivals.append(path.name)
                try:
                    html = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                match = _LEADING_NUMBER.match(path.name)
                index = (
                    max(0, int(match.group(1)) - 1)
                    if match
                    else arrivals.index(path.name)
                )
                try:
                    on_slide(
                        {
                            "index": index,
                            "title": _title_of(html, f"Slide {index + 1}"),
                            "html": html,
                        }
                    )
                except Exception:  # a preview must never break authoring
                    logger.exception("slide preview callback failed")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


# --------------------------------------------------------------------------
# harvesting


_IMG_SRC = re.compile(r"(<img\b[^>]*?\bsrc=)([\"'])([^\"']+)\2", re.IGNORECASE)


def _inline_local_images(html: str, *, base_dir: Path, workdir: Path) -> str:
    """Fold on-disk image references into the markup as data URIs.

    A slide leaves this module as a bare string and lands in a scratch
    directory on the renderer, so a relative `src` in it resolves against
    nothing. Nothing reports that. The browser loads no image, slidify emits no
    picture for an `<img>` the DOM still declares, and the editability
    round-trip fails the slide over a clean plate that was on disk the whole
    time. Bundles also disagree about the prefix, `../clean/x.png` relative to
    `baselines/` against `clean/x.png` relative to the bundle root, so both
    roots are tried and neither bundle has to be re-cut to be convertible.
    """
    root = workdir.resolve()

    def resolve(src: str) -> Path | None:
        for candidate in (base_dir / src, workdir / src):
            try:
                path = candidate.resolve()
            except OSError:
                continue
            if path.is_file() and path.is_relative_to(root):
                return path
        return None

    def replace(match: re.Match[str]) -> str:
        prefix, quote, src = match.group(1), match.group(2), match.group(3).strip()
        if not src or src.startswith(("data:", "http://", "https://", "//")):
            return match.group(0)
        path = resolve(src)
        if path is None:
            logger.warning("slide references an image outside the bundle: %s", src)
            return match.group(0)
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"{prefix}{quote}data:{mime};base64,{payload}{quote}"

    return _IMG_SRC.sub(replace, html)


_TITLE_ELEMENT = re.compile(
    r"<([a-zA-Z0-9]+)[^>]*data-pptx-role\s*=\s*[\"']title[\"'][^>]*>(.*?)</\1>",
    re.I | re.S,
)


def _title_of(html: str, fallback: str) -> str:
    match = _TITLE_ELEMENT.search(html)
    if match:
        text = re.sub(r"<[^>]+>", " ", match.group(2))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text[:200]
    return fallback


def harvest_slides(workdir: Path) -> tuple[list[dict], str | None]:
    """Read ``slides/`` back as ordered slide records."""
    slides_dir = workdir / "slides"
    if not slides_dir.is_dir():
        return [], "the turn wrote no slides/ directory"

    order: list[dict] = []
    index_path = slides_dir / "index.json"
    if index_path.is_file():
        try:
            document = json.loads(index_path.read_text(encoding="utf-8"))
            entries = document.get("slides") if isinstance(document, dict) else document
            if isinstance(entries, list):
                order = [entry for entry in entries if isinstance(entry, dict)]
        except ValueError:
            logger.warning("slides/index.json is not valid JSON; falling back to filenames")

    if not order:
        order = [
            {"file": path.name}
            for path in sorted(slides_dir.glob("*.html"))
        ]
    if not order:
        return [], "the turn wrote no slide HTML"

    slides: list[dict] = []
    for index, entry in enumerate(order):
        path = slides_dir / str(entry.get("file") or "")
        if not path.is_file():
            return [], f"slides/index.json names a missing file: {entry.get('file')!r}"
        html = _inline_local_images(
            path.read_text(encoding="utf-8"), base_dir=slides_dir, workdir=workdir
        )
        slides.append(
            {
                "index": index,
                "title": str(entry.get("title") or "").strip()
                or _title_of(html, f"Slide {index + 1}"),
                "role": str(entry.get("role") or "body"),
                "html": html,
            }
        )
    return slides, None


def harvest_patches(
    workdir: Path, manifest: PreparedBundleManifest, slide_count: int
) -> tuple[list[dict], str | None]:
    """Admit ``patches.json`` against the pinned baselines."""
    patches_path = workdir / "patches.json"
    if not patches_path.is_file():
        return [], "the turn wrote no patches.json"
    by_type = {slide_type.id: slide_type for slide_type in manifest.slide_types}
    try:
        document = json.loads(patches_path.read_text(encoding="utf-8"))
        items = document.get("slides") if isinstance(document, dict) else None
        if not isinstance(items, list) or len(items) != slide_count:
            raise TemplatePolicyError(
                f"patches.json must contain exactly {slide_count} slides"
            )
        slides: list[dict] = []
        seen: set[int] = set()
        for item in items:
            if not isinstance(item, dict):
                raise TemplatePolicyError("patch slide must be an object")
            raw_index = item.get("slide_index")
            if not isinstance(raw_index, int) or isinstance(raw_index, bool):
                raise TemplatePolicyError(f"slide_index must be an integer, got {raw_index!r}")
            index = raw_index
            if index in seen or not 0 <= index < slide_count:
                raise TemplatePolicyError(f"invalid or duplicate slide index {index}")
            seen.add(index)
            slide_type_id = str(item.get("slide_type_id") or "")
            slide_type = by_type.get(slide_type_id)
            if slide_type is None:
                raise TemplatePolicyError(f"unknown slide type {slide_type_id!r}")
            baseline_path = _local_artifact_path(workdir, slide_type.baseline_path)
            baseline = BaselineSpecimen.from_html(
                slide_type, baseline_path.read_text(encoding="utf-8")
            )
            html = apply_seam_patch(
                baseline,
                SlidePatch(
                    baseline_sha256=str(item.get("baseline_sha256") or ""),
                    replacements=tuple(
                        SeamReplacement(
                            seam_id=str(replacement.get("seam_id") or ""),
                            html_fragment=str(replacement.get("html_fragment") or ""),
                        )
                        for replacement in (item.get("replacements") or [])
                        if isinstance(replacement, dict)
                    ),
                ),
            )
            html = re.sub(r"(<!-- pp:slide-number -->)\d+", lambda match: match[1] + str(index + 1), html)
            slides.append(
                {
                    "index": index,
                    "title": str(item.get("title") or f"Slide {index + 1}"),
                    "role": str(item.get("role") or "body"),
                    "slide_type_id": slide_type_id,
                    "html": _inline_local_images(
                        html, base_dir=baseline_path.parent, workdir=workdir
                    ),
                }
            )
        if seen != set(range(slide_count)):
            raise TemplatePolicyError("patches.json slide indexes are not contiguous")
    except (OSError, ValueError, TypeError, TemplatePolicyError) as exc:
        return [], f"patches failed admission: {exc}"
    slides.sort(key=lambda slide: slide["index"])
    return slides, None


# --------------------------------------------------------------------------
# the two lanes


def _cancelled() -> dict:
    return {"status": "error", "code": "cancelled", "error": "Generation cancelled."}


def run_template_deck(
    *,
    topic: str,
    audience: str,
    goal: str,
    slide_count: int,
    template_name: str,
    template_gs_uri: str,
    expressions: list[str] | None = None,
    on_event=None,
    cancellation_event: threading.Event | None = None,
) -> dict:
    """Build on a customer template through admitted seam patches.

    Returns ``{"status": "ok", "workspace", "slides", "titles", "provenance",
    "final_line"}`` or ``{"status": "error", "error"}``. Never raises.
    """
    slug = slug_for(template_name)
    workdir = Path(tempfile.mkdtemp(prefix=f"deck-{slug}-"))
    manifest, bundle_error = _fetch_prepared_bundle(slug, template_gs_uri, workdir)
    if manifest is None:
        return {
            "status": "error",
            "error": (
                f"Template {template_name!r} is not admitted for creative "
                f"HTML authoring: {bundle_error or 'prepared bundle invalid'}. "
                "Extract and admit the template HTML specimens first."
            ),
        }

    _write_brief(
        workdir,
        topic=topic,
        audience=audience,
        goal=goal,
        slide_count=slide_count,
        expressions=expressions,
    )
    (workdir / "authoring-plan.json").write_text(
        json.dumps(
            {
                "template_bundle_id": manifest.bundle_id,
                "source": manifest.source.to_dict(),
                "slide_count": slide_count,
                "slide_types": [
                    slide_type.to_dict() for slide_type in manifest.slide_types
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    if cancellation_event is not None and cancellation_event.is_set():
        return _cancelled()
    try:
        final_line = run_turn(
            workdir=workdir,
            message=(
                f"Work only in {workdir}. Build this deck on the admitted "
                f"baselines and write patches.json there: exactly "
                f"{slide_count} slides. brief.md has the brief; "
                "authoring-plan.json has the admitted types."
            ),
            attachments=_template_attachments(manifest, workdir),
            on_event=on_event,
            cancellation_event=cancellation_event,
        )
    except AuthoringCancelled:
        return _cancelled()
    except Exception as exc:
        logger.exception("template deck turn failed")
        return {"status": "error", "error": f"Deck generation failed: {exc}"}

    if cancellation_event is not None and cancellation_event.is_set():
        return _cancelled()
    slides, error = harvest_patches(workdir, manifest, slide_count)
    if error:
        return {"status": "error", "error": f"{error}: {final_line[-300:]}"}
    return {
        "status": "ok",
        "workspace": workdir,
        "slides": slides,
        "titles": [str(slide["title"]) for slide in slides],
        "template_bundle_id": manifest.bundle_id,
        "provenance": DeckProvenance.HTML_RECONSTRUCTED_BY_SLIDIFY.value,
        "final_line": final_line[-200:],
    }


def run_designed_deck(
    *,
    topic: str,
    audience: str,
    goal: str,
    slide_count: int,
    brand_name: str,
    guideline: str,
    style: dict | None = None,
    expressions: list[str] | None = None,
    on_event=None,
    slide_ready=None,
    cancellation_event: threading.Event | None = None,
) -> dict:
    """Build a deck on a Pixelpitch brand and design system."""
    workdir = Path(tempfile.mkdtemp(prefix=f"deck-{slug_for(brand_name)}-"))
    (workdir / "slides").mkdir(parents=True, exist_ok=True)
    _write_brief(
        workdir,
        topic=topic,
        audience=audience,
        goal=goal,
        slide_count=slide_count,
        expressions=expressions,
    )
    (workdir / "brand-guideline.md").write_text(
        f"# {brand_name}\n\n{guideline}\n", encoding="utf-8"
    )
    if style:
        (workdir / "style.json").write_text(
            json.dumps(style, indent=2, sort_keys=True), encoding="utf-8"
        )

    if cancellation_event is not None and cancellation_event.is_set():
        return _cancelled()
    stop_watching = threading.Event()
    if slide_ready is not None:
        _watch_slides(workdir, slide_ready, stop_watching)
    try:
        final_line = run_turn(
            workdir=workdir,
            message=(
                f"Work only in {workdir}. Author this deck and write exactly "
                f"{slide_count} slides into slides/ with an index.json. "
                "brief.md has the brief; brand-guideline.md is the brand's own "
                "guideline and is authoritative over your defaults."
            ),
            on_event=on_event,
            cancellation_event=cancellation_event,
        )
    except AuthoringCancelled:
        return _cancelled()
    except Exception as exc:
        logger.exception("designed deck turn failed")
        return {"status": "error", "error": f"Deck generation failed: {exc}"}
    finally:
        stop_watching.set()

    if cancellation_event is not None and cancellation_event.is_set():
        return _cancelled()
    slides, error = harvest_slides(workdir)
    if error:
        return {"status": "error", "error": f"{error}: {final_line[-300:]}"}
    if len(slides) != slide_count:
        logger.warning(
            "asked for %d slides, harvested %d", slide_count, len(slides)
        )
    return {
        "status": "ok",
        "workspace": workdir,
        "slides": slides,
        "titles": [str(slide["title"]) for slide in slides],
        "provenance": DeckProvenance.HTML_RECONSTRUCTED_BY_SLIDIFY.value,
        "final_line": final_line[-200:],
    }
