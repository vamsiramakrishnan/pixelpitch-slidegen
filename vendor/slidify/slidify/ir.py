"""Slide IR — Python mirror of @slidify/components/src/ir/schema.ts.

The IR is the wire format between the JS component library and the Python
compiler. JSX components emit IR JSON via their `*toIR` helpers; this module
parses the JSON into Pydantic models and `slidify.compile_ir` lowers them to
PPTX with no rendering and no classification.

Keep these models in lockstep with the TS schema; if you change a field on
one side, change it on the other. (A future move: make TS the source of
truth and codegen the Python.)

Wave-2 additions (mirror CONTRACT §1):
  * IRPathCommand + IRPathShapeNode (custGeom paths) — §1.1
  * IRArrowhead — §1.1
  * Multi-shadow `shadows` slot on text + shape (with deprecated `shadow`) — §1.2
  * IRPatternFill (added to Fill union) — §1.3
  * IRClipPath (rounded-rect | path) on every node base — §1.4
  * `onPath` slot on IRTextNode — §1.5
  * `mask` (linear/radial gradient alpha) on IRPictureNode — §1.6
  * Expanded shape enum + callout-bubble pointer fields on IRShapeNode — §1.7
  * Deck.version: Literal[1, 2] = 2 (lockstep upgrade) — §9.6

Field names match the TS schema EXACTLY (camelCase preserved).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---- Primitive value types ---------------------------------------------------


class ColorWithAlpha(BaseModel):
    model_config = ConfigDict(frozen=True)
    hex: str  # "#rrggbb"
    alpha: float = 1.0


# A Color is either a "#rrggbb"/"#rrggbbaa" hex string or a ColorWithAlpha object.
Color = str | ColorWithAlpha


class GradientStop(BaseModel):
    color: Color
    position: float


class FillSolid(BaseModel):
    kind: Literal["solid"]
    color: Color


class FillLinearGradient(BaseModel):
    kind: Literal["linear-gradient"]
    angleDeg: float = 180.0
    stops: list[GradientStop]


class FillRadialGradient(BaseModel):
    kind: Literal["radial-gradient"]
    shape: Literal["circle", "ellipse"] = "ellipse"
    cx: float = 0.5
    cy: float = 0.5
    stops: list[GradientStop]


class FillNone(BaseModel):
    kind: Literal["none"]


class IRPatternFill(BaseModel):
    """Native dot/line grid fill — §1.3.

    For prst-pattern variants the compiler emits `<a:pattFill prst="…">`
    with `fgClr=fgColor`, `bgClr=bgColor` (or transparent). For `dots`,
    the compiler emits a tiled group of small ovals (PPTX has no
    customizable dot-grid preset).
    """

    kind: Literal["pattern"]
    pattern: Literal[
        "dots",
        "lines-h",
        "lines-v",
        "lines-grid",
        "diagonal",
        "crosshatch",
    ]
    fgColor: Color
    bgColor: Color | None = None
    tileWidthPx: float = 16.0
    tileHeightPx: float = 16.0
    featureSizePx: float = 1.0
    angleDeg: float = 0.0


Fill = (
    FillSolid
    | FillLinearGradient
    | FillRadialGradient
    | IRPatternFill
    | FillNone
)


class IRBoxShadow(BaseModel):
    offsetX: float = 0.0
    offsetY: float = 0.0
    blur: float = 0.0
    spread: float = 0.0
    color: Color
    inset: bool = False


class IRBorder(BaseModel):
    width: float = 1.0
    color: Color
    style: Literal["solid", "dashed", "dotted"] = "solid"


class IRBbox(BaseModel):
    model_config = ConfigDict(frozen=True)
    x: float
    y: float
    w: float
    h: float


class IRTextRun(BaseModel):
    text: str
    fontFamily: str | None = None
    fontSizePx: float | None = None
    fontWeight: int | None = None
    color: Color | None = None
    italic: bool = False
    underline: bool = False


class IRParagraph(BaseModel):
    runs: list[IRTextRun]
    align: Literal["left", "center", "right", "justify"] = "left"


# ---- Path commands (§1.1) ----------------------------------------------------


class IRPathCommand(BaseModel):
    """Discriminated SVG path command. Coordinates are slide-pixel (NOT
    normalized; matches IRBbox).

    The discriminator is `op`. Fields outside the discriminating op are
    ignored at validation time but persisted on round-trip — this matches
    TypeScript's discriminated-union semantics where each branch has its
    own field set. We keep all fields optional and let the compiler read
    the ones it needs based on `op`.
    """

    model_config = ConfigDict(extra="allow")

    op: Literal["M", "L", "C", "Q", "A", "Z"]
    # M, L, C, Q, A all carry an endpoint (x, y); Z does not.
    x: float | None = None
    y: float | None = None
    # C: control points 1 + 2.
    x1: float | None = None
    y1: float | None = None
    x2: float | None = None
    y2: float | None = None
    # A: ellipse radii + flags.
    rx: float | None = None
    ry: float | None = None
    xAxisRotationDeg: float = 0.0
    largeArc: bool = False
    sweep: bool = True


class IRArrowhead(BaseModel):
    """Endpoint marker for a PathShape — §1.1.

    sm / md / lg map to the OOXML `<a:headEnd w/len>` enum {sm, med, lg}.
    """

    kind: Literal["none", "arrow", "dot", "diamond", "bar"] = "none"
    size: Literal["sm", "md", "lg"] = "md"


# ---- ClipPath (§1.4) ---------------------------------------------------------


class IRClipPathRoundedRect(BaseModel):
    kind: Literal["rounded-rect"]
    radiusPx: float = 0.0
    insetPx: float = 0.0


class IRClipPathPath(BaseModel):
    kind: Literal["path"]
    commands: list[IRPathCommand]
    fillRule: Literal["nonzero", "evenodd"] = "nonzero"


IRClipPath = IRClipPathRoundedRect | IRClipPathPath


# ---- Image masks (§1.6) ------------------------------------------------------


class IRMaskStop(BaseModel):
    alpha: float
    position: float


class IRMaskLinearGradient(BaseModel):
    kind: Literal["linear-gradient"]
    angleDeg: float = 180.0
    stops: list[IRMaskStop]


class IRMaskRadialGradient(BaseModel):
    kind: Literal["radial-gradient"]
    cx: float = 0.5
    cy: float = 0.5
    stops: list[IRMaskStop]


IRMask = IRMaskLinearGradient | IRMaskRadialGradient


# ---- Text-on-path (§1.5) -----------------------------------------------------


class IRTextOnPath(BaseModel):
    commands: list[IRPathCommand]
    align: Literal["start", "middle", "end"] = "start"
    preserveOrientation: bool = False


# ---- Node types --------------------------------------------------------------


class _NodeBase(BaseModel):
    recipeId: str
    bbox: IRBbox | None = None
    zOrder: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Wave-2: optional clip applied to this node's render — §1.4
    clipPath: IRClipPath | None = None


class IRTextNode(_NodeBase):
    kind: Literal["text"]
    paragraphs: list[IRParagraph]
    fill: Fill | None = None
    # DEPRECATED — kept for one minor cycle (CONTRACT §1.2). New code uses `shadows`.
    shadow: IRBoxShadow | None = None
    shadows: list[IRBoxShadow] | None = None
    # §1.5 text-on-path
    onPath: IRTextOnPath | None = None


# Expanded shape enum — §1.7
IRShapeKind = Literal[
    # Existing
    "rect",
    "rounded-rect",
    "oval",
    "line",
    # NEW (Wave-2)
    "triangle",
    "right-triangle",
    "pentagon",
    "hexagon",
    "octagon",
    "parallelogram",
    "trapezoid",
    "chevron",
    "chevron-left",
    "callout-bubble",
    "brace-left",
    "brace-right",
    "brace-top",
    "brace-bottom",
    "plus",
    "star-5",
    "star-6",
    "arrow-right",
    "arrow-left",
    "arrow-up",
    "arrow-down",
]


class IRShapeNode(_NodeBase):
    kind: Literal["shape"]
    shape: IRShapeKind = "rounded-rect"
    borderRadiusPx: float = 0.0
    fill: Fill
    border: IRBorder | None = None
    # DEPRECATED single-shadow slot (CONTRACT §1.2). New code uses `shadows`.
    shadow: IRBoxShadow | None = None
    shadows: list[IRBoxShadow] | None = None
    # §1.7 callout-bubble pointer fields (only meaningful for shape='callout-bubble')
    calloutPointerSide: Literal["top", "right", "bottom", "left"] | None = None
    calloutPointerOffset: float | None = None  # 0..1 along the side
    calloutPointerLengthPx: float | None = None


class IRPictureNode(_NodeBase):
    kind: Literal["picture"]
    src: str
    alt: str = ""
    # §1.6 alpha mask (linear/radial gradient).
    mask: IRMask | None = None


class IRRasterNode(_NodeBase):
    kind: Literal["raster"]
    pngBase64: str


class IRPathShapeNode(_NodeBase):
    """Native custGeom path — §1.1.

    Path coordinates are in slide-pixel space (top-left origin), the same
    coordinate space as ShapeNode.bbox. If `bbox` is omitted, the compiler
    computes it from `commands`.
    """

    kind: Literal["path"]
    commands: list[IRPathCommand]
    fill: Fill | None = None  # omitted = no fill (stroke only)
    fillRule: Literal["nonzero", "evenodd"] = "nonzero"
    strokeWidthPx: float = 0.0
    strokeColor: Color | None = None
    strokeDasharray: list[float] | None = None
    strokeLinecap: Literal["butt", "round", "square"] = "butt"
    strokeLinejoin: Literal["miter", "round", "bevel"] = "miter"
    markerStart: IRArrowhead | None = None
    markerEnd: IRArrowhead | None = None
    shadows: list[IRBoxShadow] | None = None


class IRGroupNode(_NodeBase):
    kind: Literal["group"]
    children: list[IRNode]


IRNode = (
    IRTextNode
    | IRShapeNode
    | IRPictureNode
    | IRRasterNode
    | IRPathShapeNode
    | IRGroupNode
)
IRGroupNode.model_rebuild()


# ---- Slide / Deck ------------------------------------------------------------


class IRTheme(BaseModel):
    name: str = "default"
    bgColor: Color = "#ffffff"
    fgColor: Color = "#000000"
    accent: Color = "#6366f1"
    fontFamily: str = "Inter, sans-serif"


class IRSlide(BaseModel):
    index: int
    bbox: IRBbox = IRBbox(x=0, y=0, w=1280, h=720)
    background: Fill = FillSolid(kind="solid", color="#ffffff")
    nodes: list[IRNode]
    notes: str = ""


class IRDeck(BaseModel):
    """Top-level IR document. Parse with `IRDeck.model_validate(json)`.

    `version` accepts 1 (pre-Wave-2 schema) or 2 (full §1 schema). Lockstep
    upgrade per §9.6 — no version negotiation.
    """

    version: Literal[1, 2] = 2
    theme: IRTheme = IRTheme()
    slides: list[IRSlide]


# ---- Helpers ----------------------------------------------------------------


def _color_to_hex_alpha(c: Color) -> tuple[str, float]:
    """Normalize an IR color to (hex, alpha)."""
    if isinstance(c, str):
        s = c.strip()
        if not s.startswith("#"):
            return ("#000000", 1.0)
        if len(s) == 9:
            try:
                a = int(s[7:9], 16) / 255.0
                return (s[:7], a)
            except ValueError:
                return (s[:7], 1.0)
        return (s[:7], 1.0)
    return (c.hex, c.alpha)


def normalize_shadows(node: Any) -> list[IRBoxShadow]:
    """Resolve the legacy `shadow` + new `shadows` fields per §1.2.

    Migration rule: if both are present, `shadows` wins; if only `shadow`
    is present, treat as `[shadow]`; otherwise empty list. Returns at
    most 4 entries (the §1.2 hard cap).
    """
    shadows = getattr(node, "shadows", None)
    if shadows:
        return list(shadows)[:4]
    legacy = getattr(node, "shadow", None)
    if legacy is not None:
        return [legacy]
    return []


__all__ = [
    "Color",
    "ColorWithAlpha",
    "Fill",
    "FillLinearGradient",
    "FillNone",
    "FillRadialGradient",
    "FillSolid",
    "GradientStop",
    "IRArrowhead",
    "IRBbox",
    "IRBorder",
    "IRBoxShadow",
    "IRClipPath",
    "IRClipPathPath",
    "IRClipPathRoundedRect",
    "IRDeck",
    "IRGroupNode",
    "IRMask",
    "IRMaskLinearGradient",
    "IRMaskRadialGradient",
    "IRMaskStop",
    "IRNode",
    "IRParagraph",
    "IRPathCommand",
    "IRPathShapeNode",
    "IRPatternFill",
    "IRPictureNode",
    "IRRasterNode",
    "IRShapeKind",
    "IRShapeNode",
    "IRSlide",
    "IRTextNode",
    "IRTextOnPath",
    "IRTextRun",
    "IRTheme",
    "_color_to_hex_alpha",
    "normalize_shadows",
]
