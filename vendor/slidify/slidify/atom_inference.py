"""Implicit atom inference and synthetic priming.

Phase 3 of the coverage-expansion effort. Provides two complementary pieces:

  * :func:`infer_atom_id` — given a :class:`VisualUnit`, return the canonical
    atom id (e.g. ``"bg.mesh"``) when its structural signature is recognised
    by the priming table. Used by ``api._classify_unit_tier12`` to synthesise
    a ``data-atom`` hint when the author didn't tag the markup themselves.
    A miss returns ``None`` silently.
  * :func:`_canonical_unit_for_atom` — build a synthetic :class:`VisualUnit`
    shaped like the prototypical render of an atom. Drives the
    ``slidify prime-atom-cache`` CLI, which hashes one canonical unit per atom
    id from ``atoms.yaml`` and writes the result to
    ``slidify/patterns/data/atom_signatures.json``. The browser-rendered
    variant is intentionally deferred (see TODO inside the function).

The priming table is loaded lazily from a JSON file packaged inside
``slidify.patterns.data``. Format:

    {
      "version": 1,
      "entries": { "<sig_hash>": "<atom_id>", ... }
    }

A missing or malformed file is treated as an empty table — inference simply
returns ``None`` and the existing classifier path runs unchanged.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

import structlog

from slidify.models import BoundingBox, DomElement, VisualUnit
from slidify.patterns.signatures import signature_hash

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DATA_PACKAGE = "slidify.patterns.data"
_SIG_FILE = "atom_signatures.json"


@lru_cache(maxsize=1)
def _load_table() -> dict[str, str]:
    """Load the packaged ``atom_signatures.json`` priming table.

    Cached for the process lifetime so repeated lookups are O(1). Returns an
    empty dict if the file is missing or unparseable — never raises.
    """
    try:
        pkg = resources.files(_DATA_PACKAGE)
        path = pkg / _SIG_FILE
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, ModuleNotFoundError):
        return {}
    try:
        raw: Any = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries = raw.get("entries", {})
    if not isinstance(entries, dict):
        return {}
    # Coerce all keys/values to str defensively.
    return {str(k): str(v) for k, v in entries.items() if v}


def _reset_table_cache() -> None:
    """Drop the cached priming table. Used by tests after they overwrite the
    JSON file. Not part of the public API."""
    _load_table.cache_clear()


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def infer_atom_id(unit: VisualUnit) -> str | None:
    """Look up `unit`'s signature against the priming table.

    Returns the registered atom id (e.g. ``"bg.mesh"``) when the unit's
    ``signature_hash`` is recognised, otherwise ``None``. The miss path is
    silent so the classifier degrades gracefully when the JSON file is
    missing, empty, or malformed.
    """
    try:
        table = _load_table()
    except Exception:
        return None
    if not table:
        return None
    try:
        h = signature_hash(unit)
    except Exception:
        return None
    return table.get(h)


# ---------------------------------------------------------------------------
# Synthetic canonical units (for cache priming)
# ---------------------------------------------------------------------------


def _bbox(x: float, y: float, w: float, h: float) -> BoundingBox:
    return BoundingBox(x=x, y=y, w=w, h=h)


def _make_element(
    *,
    eid: int = 1,
    tag: str = "DIV",
    cls: str = "",
    bbox: BoundingBox | None = None,
    text: str | None = None,
    is_text_container: bool = False,
    bg_image: str = "none",
    bg_color: str = "rgba(0, 0, 0, 0)",
    border_radius: str = "0px",
    box_shadow: str = "none",
    clip_path: str = "none",
    font_family: str = "",
    letter_spacing: str = "normal",
    writing_mode: str = "horizontal-tb",
    aspect_ratio: str = "auto",
    grid_template_columns: str = "none",
    text_shadow: str = "none",
    mask_image: str = "none",
    is_svg: bool = False,
    svg_path_count: int = 0,
    is_img: bool = False,
    is_canvas: bool = False,
    data_atom: str = "",
) -> DomElement:
    return DomElement(
        id=eid,
        parent_id=None,
        depth=0,
        tag=tag,
        cls=cls,
        bbox=bbox or _bbox(0, 0, 200, 100),
        background_color=bg_color,
        background_image=bg_image,
        border_radius=border_radius,
        box_shadow=box_shadow,
        clip_path=clip_path,
        is_text_container=is_text_container,
        font_family=font_family,
        letter_spacing=letter_spacing,
        writing_mode=writing_mode,
        aspect_ratio=aspect_ratio,
        grid_template_columns=grid_template_columns,
        text_shadow=text_shadow,
        mask_image=mask_image,
        text=text,
        is_svg=is_svg,
        svg_path_count=svg_path_count,
        is_img=is_img,
        is_canvas=is_canvas,
        data_atom=data_atom,
        stable_selector=f"#syn-{eid}",
    )


# Per-namespace defaults used by `_canonical_unit_for_atom`. The bbox and
# anchor properties are chosen so that the SIGNATURE (not the pixel render)
# differs across namespaces — that's all we need to populate a unique entry
# per atom id in the priming table. Real-browser priming will produce more
# faithful signatures; see the TODO in `_canonical_unit_for_atom`.
_NAMESPACE_TEMPLATES: dict[str, dict[str, Any]] = {
    "bg": {
        "bbox": _bbox(0, 0, 1280, 720),
        "bg_image": (
            "linear-gradient(135deg, rgb(99, 102, 241) 0%, "
            "rgb(236, 72, 153) 100%)"
        ),
        "border_radius": "0px",
    },
    "surf": {
        "bbox": _bbox(0, 0, 480, 320),
        "bg_color": "rgb(255, 255, 255)",
        "box_shadow": "0 12px 24px rgba(0, 0, 0, 0.2)",
        "border_radius": "16px",
    },
    "type": {
        "tag": "H1",
        "bbox": _bbox(0, 0, 720, 180),
        "text": "Headline",
        "font_family": "Inter, sans-serif",
        "letter_spacing": "0.2px",
        "is_text_container": True,
    },
    "mask": {
        "bbox": _bbox(0, 0, 320, 320),
        "clip_path": "circle(50% at 50% 50%)",
        "border_radius": "9999px",
    },
    "dec": {
        "bbox": _bbox(0, 0, 240, 4),
        "bg_color": "rgb(0, 0, 0)",
    },
    "data": {
        "tag": "SVG",
        "bbox": _bbox(0, 0, 240, 240),
        "is_svg": True,
        "svg_path_count": 4,
    },
    "motion": {
        "bbox": _bbox(0, 0, 480, 240),
        "bg_image": (
            "repeating-linear-gradient(90deg, rgb(0, 0, 0) 0px, "
            "rgb(0, 0, 0) 2px, transparent 2px, transparent 8px)"
        ),
    },
    "ui": {
        "bbox": _bbox(0, 0, 640, 400),
        "bg_color": "rgb(244, 244, 245)",
        "border_radius": "8px",
        "box_shadow": "0 1px 2px rgba(0, 0, 0, 0.1)",
    },
    "anno": {
        "tag": "SVG",
        "bbox": _bbox(0, 0, 200, 80),
        "is_svg": True,
        "svg_path_count": 2,
    },
    "comp": {
        "bbox": _bbox(0, 0, 1200, 600),
        "grid_template_columns": "repeat(3, 1fr)",
    },
    "chrome": {
        "bbox": _bbox(0, 0, 800, 450),
        "bg_color": "rgb(15, 23, 42)",
        "border_radius": "18px",
        "box_shadow": "0 18px 48px rgba(0, 0, 0, 0.28)",
    },
}


def _atom_namespace(atom_id: str) -> str:
    return atom_id.split(".", 1)[0] if "." in atom_id else atom_id


def _canonical_unit_for_atom(
    atom_id: str, *, salt: int = 0
) -> VisualUnit | None:
    """Build a synthetic VisualUnit shaped like the prototypical atom render.

    Used by the ``slidify prime-atom-cache`` subcommand to populate
    ``atom_signatures.json`` deterministically without spinning up a browser.

    The unit's signature is *not* guaranteed to match what a real
    browser-rendered ``examples/landing/atoms.html`` would produce — that's
    fine for now: the goal of this Phase-3 deliverable is to lay the rail
    so inference fires on synthetic shapes, and let real-browser priming be
    bolted on later as a refinement.

    ``salt`` drives the bbox offset deterministically so callers can ask for
    a unique unit per atom id within a namespace. The signature's bbox is
    quantized at 64×32 px, so the salt is mapped to a (dw, dh) pair on a
    grid wide enough that two distinct salts always produce distinct
    quantized bboxes (and therefore distinct signatures within a namespace).

    TODO: replace this with a real renderer pass that walks
    ``examples/landing/atoms.html``, captures each cluster's anchor, and
    feeds the signature_hash into the table — at which point the synthetic
    fallback only matters when running ``prime-atom-cache`` in environments
    without Chromium.
    """
    if not atom_id:
        return None
    ns = _atom_namespace(atom_id)
    template = _NAMESPACE_TEMPLATES.get(ns)
    if template is None:
        return None
    # Map ``salt`` to a (dw, dh) pair on a 17×N grid. dw cycles every 17,
    # dh advances every 17, so two distinct salts produce distinct (dw, dh)
    # for any salt < 17 * 1000 — far beyond the registry size we'll ever
    # see. Quantization buckets are 64 / 32 px so multiples are essential.
    base_bbox: BoundingBox = template["bbox"]
    dw = 64 * (salt % 17)
    dh = 32 * (salt // 17)
    bbox = _bbox(base_bbox.x, base_bbox.y, base_bbox.w + dw, base_bbox.h + dh)

    kwargs = {k: v for k, v in template.items() if k != "bbox"}
    el = _make_element(bbox=bbox, **kwargs)
    return VisualUnit(
        id=f"syn_{atom_id}",
        bbox=bbox,
        elements=[el],
        children=[],
        anchor_element_id=el.id,
    )


# ---------------------------------------------------------------------------
# Build helpers (used by the CLI)
# ---------------------------------------------------------------------------


def _atom_ids_from_yaml() -> list[str]:
    """Return every atom id mentioned in ``atoms.yaml``'s ``data_atom_id``
    clauses (single strings or lists). Globs (``"*"``) and namespace-only
    clauses are skipped — they don't pin one canonical signature.
    """
    import yaml

    pkg = resources.files(_DATA_PACKAGE)
    text = (pkg / "atoms.yaml").read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    ids: list[str] = []
    for entry in raw.get("patterns", []):
        match = entry.get("match", {}) or {}
        v = match.get("anchor.data_atom_id")
        if v is None:
            continue
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str) and "*" not in item and "." in item:
                    ids.append(item)
        elif isinstance(v, str) and "*" not in v and "." in v:
            ids.append(v)
    # Dedupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for a in ids:
        if a not in seen:
            out.append(a)
            seen.add(a)
    return out


def build_synthetic_table() -> dict[str, str]:
    """Construct the {sig_hash: atom_id} table by hashing one synthetic unit
    per atom id declared in ``atoms.yaml``.

    Within a namespace the per-salt bbox offset (see
    :func:`_canonical_unit_for_atom`) is monotonic and the signature's bbox
    is quantized at 64×32 px, so distinct salts always produce distinct
    intra-namespace signatures. Cross-namespace collisions can still happen
    (different templates can land on identical computed kinds + bbox
    buckets); when one occurs we bump the salt and retry, guaranteeing that
    every atom id in ``atoms.yaml`` receives a unique entry. Truly
    pathological registries that exhaust the salt budget log a warning and
    skip the offending atom rather than silently overwriting a sibling.
    """
    table: dict[str, str] = {}
    salt_per_ns: dict[str, int] = {}
    dropped: list[tuple[str, str]] = []
    for atom_id in _atom_ids_from_yaml():
        ns = _atom_namespace(atom_id)
        salt = salt_per_ns.get(ns, 0)
        attempts = 0
        unit = None
        h: str | None = None
        while attempts < 200:
            unit = _canonical_unit_for_atom(atom_id, salt=salt)
            if unit is None:
                break
            try:
                candidate = signature_hash(unit)
            except Exception:
                candidate = None
            if candidate is None:
                salt += 1
                attempts += 1
                continue
            if candidate not in table:
                h = candidate
                break
            salt += 1
            attempts += 1
        salt_per_ns[ns] = salt + 1
        if h is None or unit is None:
            dropped.append((atom_id, "salt budget exhausted"))
            continue
        table[h] = atom_id
    if dropped:
        log.warning(
            "atom_inference.build_synthetic_table.dropped",
            count=len(dropped),
            atoms=dropped,
        )
    return table


# ---------------------------------------------------------------------------
# Real-browser priming
# ---------------------------------------------------------------------------


async def build_browser_table(
    html_path: "str | os.PathLike[str] | list[str | os.PathLike[str]]",
    *,
    viewport: tuple[int, int] | None = None,
) -> dict[str, str]:
    """Prime the table by rendering ``html_path`` through Chromium.

    For each slide in the source (DOCTYPE-separated), the renderer captures
    every element + computed style; we cluster into VisualUnits the same way
    the convert pipeline does, then hash every unit whose anchor carries a
    non-empty ``data_atom`` attribute.

    This is what production needs: the synthetic generator (above) produces
    signatures that don't match what real Chromium renders, so implicit
    inference never fired on actual decks. The browser walk produces real
    signatures so the priming table actually maps onto the units the
    classifier sees at convert time.

    Returns ``{sig_hash: atom_id}``. When the same atom id appears multiple
    times across slides (e.g. ``type.gfill-4`` on every chapter slide), the
    first signature wins — they're all valid renderings of the same atom
    so any of them serves as a priming key.

    Raises if Playwright / Chromium isn't available; callers can fall back
    to ``build_synthetic_table`` in CI environments without a browser.
    """
    import os
    from pathlib import Path

    from slidify.geom import SLIDE_H_PX, SLIDE_W_PX
    from slidify.renderer import Renderer
    from slidify.splitter import split_slides
    from slidify.units import cluster, flatten

    if isinstance(html_path, (str, os.PathLike)):
        sources = [html_path]
    else:
        sources = list(html_path)
    vp = viewport or (SLIDE_W_PX, SLIDE_H_PX)
    table: dict[str, str] = {}
    async with Renderer(viewport=vp) as renderer:
        for src in sources:
            p = Path(os.fspath(src))
            html = p.read_text(encoding="utf-8")
            slides = split_slides(html) or [html]
            for slide_html in slides:
                rendered = await renderer.render(slide_html)
                roots = cluster(rendered.elements)
                for unit in flatten(roots):
                    if not unit.elements:
                        continue
                    aid = (unit.elements[0].data_atom or "").strip()
                    if not aid:
                        continue
                    try:
                        h = signature_hash(unit)
                    except Exception:
                        continue
                    # First-write-wins so iteration order across slides
                    # stays deterministic; callers that want
                    # every-occurrence stats can re-walk and accumulate
                    # themselves.
                    table.setdefault(h, aid)
    return table


async def build_hybrid_table(
    html_paths: "list[str | os.PathLike[str]]",
    *,
    viewport: tuple[int, int] | None = None,
) -> dict[str, str]:
    """Browser-priming with a synthetic backstop.

    For each atom id declared in ``atoms.yaml``, prefer the signature
    captured from a real browser render (so implicit inference fires on
    actual decks). For atoms the source HTML doesn't demonstrate, fall back
    to a synthetic entry so the priming table never silently drops a
    declared atom.

    The browser-derived signatures are real — they're keyed off what the
    convert pipeline actually computes. The synthetic fallback keys won't
    match any real render, but they keep the table complete and let later
    HTML reference decks add real signatures incrementally.
    """
    browser_table = await build_browser_table(html_paths, viewport=viewport)
    covered = set(browser_table.values())
    declared = set(_atom_ids_from_yaml())
    missing = sorted(declared - covered)
    table = dict(browser_table)
    if missing:
        synthetic_full = build_synthetic_table()
        # Filter the synthetic table to entries whose atom_id is in `missing`
        # AND whose signature_hash isn't already present in the browser table.
        for h, aid in synthetic_full.items():
            if aid in missing and h not in table:
                table[h] = aid
        log.info(
            "atom_inference.build_hybrid_table.synthetic_fallback",
            n_browser=len(browser_table),
            n_synthetic_added=len(table) - len(browser_table),
            missing_atoms=missing[:8],
        )
    return table
