"""DOM-shape signatures for VisualUnits.

A signature is a stable, content-addressable string that identifies a unit's
structural shape independent of its text content or exact pixel positions:

    sig := tag(@kind?)[classes_normalized][bbox_quantized]{ child_sig, ... }

Two units with the same signature should classify identically. We use this:
  * As a stronger structural-cache key
  * For telemetry: log every unmatched signature so the corpus harvester
    can suggest new patterns from the most-common ones
  * As a future basis for pattern matching by example
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from slidify.geom import parse_px
from slidify.gradients import LinearGradient, RadialGradient, parse_gradient
from slidify.models import DomElement, VisualUnit
from slidify.patterns.tailwind import _split_classes
from slidify.shadows import is_translatable_shadow

# Class-name "families" we keep in the signature — the value-bearing ones
# that change a unit's classification. Class names matching these prefixes
# survive the signature normalization step. Everything else (specific colors,
# specific paddings, etc.) is dropped because it doesn't change the structural
# shape.
_KEPT_CLASS_PREFIXES: tuple[str, ...] = (
    "rounded",
    "shadow",
    "bg-gradient",
    "from-",
    "to-",
    "via-",
    "tracking-",
    "uppercase",
    "italic",
    "underline",
    "border",
    "text-",   # text-xs/sm/base/lg/xl/2xl/3xl/...
    "font-",   # font-bold/semibold/...
    "leading-",
    "flex",
    "grid",
    "items-",
    "justify-",
    "gap-",
    # Phase 2b: high-signal typography / layout class families that
    # carry register intent (serif vs sans, tracking-tight headlines,
    # leading-loose body, aspect-* ratios) so the signature reflects them.
    "font-serif",
    "font-sans",
    "font-mono",
    "tracking-tight",
    "leading-tight",
    "leading-loose",
    "aspect-",
)

_DROPPED_CLASS_EXACT: frozenset[str] = frozenset({
    # Layout / positioning utilities that don't affect structural shape.
    "block", "inline", "inline-block", "hidden",
    "relative", "absolute", "fixed", "sticky", "static",
    "overflow-hidden", "overflow-visible",
    "antialiased", "subpixel-antialiased",
    # Default/zero variants.
    "rounded-none", "shadow-none", "border-0",
})


def _normalize_classes(cls: str | None) -> tuple[str, ...]:
    if not cls:
        return ()
    out: list[str] = []
    for token in _split_classes(cls):
        if token in _DROPPED_CLASS_EXACT:
            continue
        # bg-{color}-{shade} → bg-color (drop specific color/shade)
        # text-{color} stays as text-* (we keep the size text-xs/sm/etc.)
        if token.startswith("bg-") and not token.startswith("bg-gradient"):
            # If it's a color token like bg-indigo-500 or bg-white/10, normalize.
            rest = token[3:]
            if "-" in rest or rest in {"white", "black", "transparent"} or "/" in rest:
                out.append("bg-*color")
                continue
        if token.startswith("text-") and "-" in token[5:]:
            # text-{color}-{shade} also collapses, but text-xs / text-4xl pass.
            rest = token[5:]
            if rest.split("-")[0] in {"xs", "sm", "base", "lg", "xl",
                                       "2xl", "3xl", "4xl", "5xl", "6xl",
                                       "7xl", "8xl", "9xl"}:
                out.append(token)
                continue
            out.append("text-*color")
            continue
        # Drop spacing utility specifics (p-4, m-6, gap-4 are noise for shape).
        if any(token.startswith(p + "-") for p in
               ("p", "px", "py", "pt", "pb", "pl", "pr",
                "m", "mx", "my", "mt", "mb", "ml", "mr",
                "w", "h", "min-w", "min-h", "max-w", "max-h",
                "space-x", "space-y")):
            continue
        # Keep classes from kept families.
        if any(token.startswith(p) for p in _KEPT_CLASS_PREFIXES):
            out.append(token)
            continue
        # Otherwise drop (unknown / arbitrary classes don't help shape).
    return tuple(sorted(set(out)))


def _quantize(v: float, bucket: int = 32) -> int:
    """Bucket a px dimension to a coarse grid for shape stability."""
    return int(v // bucket) * bucket


def _gradient_tag(bg: str) -> str:
    """Return `grad/n<stops>/<dir>` for a parsed gradient, or `grad` on failure."""
    grad = parse_gradient(bg)
    if grad is None:
        return "grad"
    stops = min(len(grad.stops), 8)
    if isinstance(grad, LinearGradient):
        # Bucket angle to nearest 45° (0..315). CSS angles are normalized mod 360.
        ang = grad.angle_deg % 360.0
        bucket = int(round(ang / 45.0)) * 45
        bucket = bucket % 360
        direction = f"d{bucket}"
    elif isinstance(grad, RadialGradient):
        direction = "r"
    else:
        direction = "d0"
    return f"grad/n{stops}/{direction}"


def _shadow_layer_count(value: str) -> int:
    """Count the comma-separated layers of a CSS box-shadow (cap at 6)."""
    if not value:
        return 0
    depth = 0
    n = 1
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            n += 1
    return min(n, 6)


_RADIUS_BUCKETS = (0, 4, 8, 12, 16, 24, 32, 9999)


def _radius_bucket(value: str) -> int:
    """Return the largest bucket ≤ parsed px value (or 9999 for pill)."""
    px = parse_px(value)
    if px <= 0:
        return 0
    # Treat very large values (>=999px) as pill / fully rounded.
    if px >= 999:
        return 9999
    bucket = 0
    for b in _RADIUS_BUCKETS:
        if b == 9999:
            continue
        if px >= b:
            bucket = b
    return bucket


def _border_side_width(value: str) -> float:
    """Return the parsed-px width of a per-side border shorthand, 0 if none."""
    if not value or value == "none":
        return 0.0
    parts = value.split()
    if not parts:
        return 0.0
    return parse_px(parts[0])


def _border_side_signature(value: str) -> tuple[float, str, str]:
    """Tuple (width, style, color) used to detect (a)symmetry between sides."""
    if not value or value == "none":
        return (0.0, "", "")
    parts = value.split()
    width = parse_px(parts[0]) if parts else 0.0
    style = parts[1] if len(parts) > 1 else ""
    color = " ".join(parts[2:]) if len(parts) > 2 else ""
    return (width, style, color)


def _asymmetric_border_tag(anchor: DomElement) -> str | None:
    """Return `brd-asym<sides>` when sides differ, else None."""
    sides = (
        ("T", anchor.border_top),
        ("R", anchor.border_right),
        ("B", anchor.border_bottom),
        ("L", anchor.border_left),
    )
    widths = [(initial, _border_side_width(v), v) for initial, v in sides]
    present = [(initial, v) for initial, w, v in widths if w > 0]
    if not present:
        return None
    sigs = [_border_side_signature(v) for _, v in sides]
    # All four sides identical (and at least one present) → uniform, skip.
    if len(set(sigs)) == 1:
        return None
    return "brd-asym" + "".join(initial for initial, _ in present)


def _classify_face(family: str) -> str:
    """Mirror matcher._classify_font_family without importing it."""
    f = (family or "").lower()
    mono_tokens = ("mono", "courier", "menlo", "consolas", "monaco")
    if any(t in f for t in mono_tokens):
        return "mono"
    if "serif" in f and "sans-serif" not in f:
        stripped = f.replace("sans-serif", "")
        if "serif" in stripped:
            return "serif"
    display_tokens = (
        "display", "black", "bebas", "anton", "oswald",
        "archivo black", "druk", "editorial",
    )
    if any(t in f for t in display_tokens):
        return "display"
    return "sans"


def _letter_spacing_register(value: str) -> str | None:
    """Return tight/wide/widest, or None if value resolves to normal/0."""
    s = (value or "").strip().lower()
    if not s or s == "normal":
        return None
    px = parse_px(s)
    if px == 0.0:
        # Try a bare numeric value (em / unitless are not resolvable here).
        return None
    if px < -0.5:
        return "tight"
    if px <= 0.4:
        return None  # normal bucket — skip
    if px <= 1.6:
        return "wide"
    return "widest"


def _aspect_bucket(ratio: float) -> str:
    if ratio < 0.6:
        return "tall"
    if ratio < 0.85:
        return "portrait"
    if ratio < 1.18:
        return "square"
    if ratio < 1.5:
        return "landscape"
    if ratio < 2.2:
        return "wide"
    return "ultra"


def _grid_column_count(value: str) -> int:
    """Parse `grid-template-columns` into a column count (capped at 12)."""
    s = (value or "").strip()
    if not s or s == "none":
        return 0
    low = s.lower()
    # repeat(N, ...) → N
    if low.startswith("repeat"):
        # Find first arg of repeat(...)
        try:
            inner = s[s.index("(") + 1 : s.rindex(")")]
            first = inner.split(",", 1)[0].strip()
            n = int(float(first))
            return min(n, 12)
        except (ValueError, IndexError):
            pass
    # Otherwise count whitespace tokens, skipping `[name]` segments.
    n = 0
    for tok in s.split():
        if tok.startswith("[") or tok.endswith("]"):
            continue
        n += 1
    return min(n, 12)


def _anchor_kind(anchor: DomElement) -> str:
    """Compress anchor properties into a shape-relevant kind tag."""
    bits: list[str] = []
    bg = anchor.background_image or ""
    if bg and bg != "none":
        if "url(" in bg:
            bits.append("bgi=url")
        elif parse_gradient(bg):
            # Phase 2b axis 1: gradient stop count + direction bucket.
            bits.append("bgi=" + _gradient_tag(bg))
        else:
            bits.append("bgi=other")
    if anchor.background_color and anchor.background_color != "rgba(0, 0, 0, 0)":
        bits.append("bg")
    if anchor.box_shadow and anchor.box_shadow != "none":
        if is_translatable_shadow(anchor.box_shadow):
            # Phase 2b axis 2: shadow elevation (layer count).
            n_layers = _shadow_layer_count(anchor.box_shadow)
            bits.append(f"shdw=t/L{n_layers}")
        else:
            bits.append("shdw=x")
    if anchor.border and anchor.border != "none":
        # parse a non-zero width
        first = anchor.border.split()
        if first and first[0] not in ("0px", "0"):
            bits.append("brd")
    if anchor.transform and anchor.transform != "none":
        bits.append("xform")
    if anchor.filter and anchor.filter != "none":
        bits.append("filter")
    if anchor.has_before:
        bits.append("::before")
    if anchor.has_after:
        bits.append("::after")
    if anchor.is_canvas:
        bits.append("canvas")
    if anchor.is_svg:
        bits.append(f"svg/{min(anchor.svg_path_count, 12)}")
    if anchor.is_img:
        bits.append("img")
    if anchor.is_video:
        bits.append("video")
    if anchor.text and anchor.text.strip():
        bits.append("txt")
    if anchor.is_text_container:
        bits.append("txtc")

    # ------------------------------------------------------------------
    # Phase 2b — new high-signal axes. Order is significant: appended
    # after the existing tags so cache keys remain stable when an axis
    # is added or removed.
    # ------------------------------------------------------------------

    # Axis 3: border-radius bucket.
    rb = _radius_bucket(anchor.border_radius)
    if rb > 0:
        bits.append(f"r{rb}")

    # Axis 4: asymmetric per-side borders.
    asym = _asymmetric_border_tag(anchor)
    if asym is not None:
        bits.append(asym)

    # Axis 5: clip-path preset. Lazy import to avoid circular deps.
    cp = (anchor.clip_path or "").strip()
    if cp and cp.lower() != "none":
        from slidify.preset_shapes import clip_path_to_preset

        match = clip_path_to_preset(cp, anchor.bbox)
        if match is not None:
            bits.append(f"clip={int(match.preset)}")
        else:
            bits.append("clip=raw")

    # Axis 6: font family register (only when this anchor carries text).
    has_text = bool(anchor.text and anchor.text.strip()) or anchor.is_text_container
    if has_text:
        bits.append(f"face={_classify_face(anchor.font_family)}")

    # Axis 7: letter-spacing register.
    ls = _letter_spacing_register(anchor.letter_spacing)
    if ls is not None:
        bits.append(f"tr={ls}")

    # Axis 8: writing mode (any vertical/sideways collapses to v).
    wm = anchor.writing_mode or "horizontal-tb"
    if wm != "horizontal-tb":
        bits.append("wm=v")

    # Axis 9: explicit aspect-ratio bucket (skip when "auto" — bbox quantize
    # already encodes the rendered aspect; only EXPLICIT intent fires this).
    ar_value = (anchor.aspect_ratio or "auto").strip()
    if ar_value and ar_value.lower() != "auto":
        # Resolved aspect_ratio is typically "W / H" (e.g. "1 / 1", "16 / 9").
        try:
            if "/" in ar_value:
                w_str, h_str = ar_value.split("/", 1)
                w_num = float(w_str.strip())
                h_num = float(h_str.strip())
                ratio = w_num / h_num if h_num else 0.0
            else:
                ratio = float(ar_value)
            if ratio > 0:
                bits.append(f"ar={_aspect_bucket(ratio)}")
        except ValueError:
            pass

    # Axis 10: grid container.
    gc = _grid_column_count(anchor.grid_template_columns)
    if gc > 0:
        bits.append(f"grid={gc}")

    # Axis 11: text-shadow / mask-image / background-blend-mode presence.
    if anchor.text_shadow and anchor.text_shadow not in ("none", ""):
        bits.append("tshdw")
    if anchor.mask_image and anchor.mask_image not in ("none", ""):
        bits.append("mask")
    if (
        anchor.background_blend_mode
        and anchor.background_blend_mode not in ("normal", "")
    ):
        bits.append("bblend")

    return "+".join(bits)


def signature(unit: VisualUnit) -> str:
    """Return a deterministic structural signature for `unit`.

    Same shape → same string, regardless of text content or precise
    coordinates. Sub-units recurse.
    """
    if not unit.elements:
        return f"empty[{_quantize(unit.bbox.w, 64)}x{_quantize(unit.bbox.h, 32)}]"
    a = unit.elements[0]
    kind = _anchor_kind(a)
    classes = _normalize_classes(a.cls)
    cls_str = ",".join(classes)
    qbbox = f"{_quantize(unit.bbox.w, 64)}x{_quantize(unit.bbox.h, 32)}"
    children_sigs = ",".join(sorted(signature(c) for c in unit.children))
    children_str = f"{{{children_sigs}}}" if children_sigs else ""
    own_n = max(0, len(unit.elements) - 1)
    own_str = f"<{own_n}>" if own_n else ""
    return f"{a.tag.lower()}({kind})[{cls_str}]{own_str}[{qbbox}]{children_str}"


def signature_hash(unit: VisualUnit) -> str:
    """A short stable hash of `signature(unit)` for use as a cache key."""
    return hashlib.sha256(signature(unit).encode("utf-8")).hexdigest()[:16]


@dataclass
class SignatureMiss:
    """One unmatched unit recorded for telemetry / harvester."""

    sig: str
    sig_hash: str
    bbox_w: int
    bbox_h: int
    sample_classes: str
    sample_text: str
