"""CSS / HTML feature compatibility matrix.

A single source of truth for what slidify can translate to native PPTX,
what it can preserve via raster fallback, and what it actively drops.

The matrix is consumed by:
  * `slidify compat` CLI — prints a Markdown table for humans / docs.
  * `--report-json` — embeds the matrix version + level counts in
    conversion reports so users can grep for what they're getting.

Each row is grounded in one specific code path:
  * tier1 rules in `slidify.classifier.tier1`,
  * native parsers (`slidify.gradients`, `slidify.shadows`,
    `slidify.preset_shapes`, `slidify.svg_shapes`),
  * the emitter dispatch in `slidify.emitter._emit_op`.

If a row claims `Native` but the cited code path doesn't actually exist,
the unit test in `tests/unit/test_compat_matrix.py` will fail. That makes
the matrix self-validating documentation rather than commentary that
silently rots.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Bump this when rows change so consumers can pin / detect drift.
MATRIX_VERSION = "4"


class Support(str, Enum):
    """How well a feature survives HTML → PPTX conversion."""

    Native = "native"        # editable PPTX primitive (text frame, shape, table, …)
    Raster = "raster"        # preserved visually via a pixel crop; not editable
    Partial = "partial"      # natively translated for a subset of inputs
    Unsupported = "unsupported"  # silently dropped or visually wrong
    Planned = "planned"      # roadmap — not yet implemented; documents the target


@dataclass(frozen=True)
class CompatRow:
    """One row in the compatibility matrix.

    Attributes:
        category: human-grouped section ("Typography", "Backgrounds", …).
        feature:  the CSS property / HTML element this row describes.
        support:  level (Native / Raster / Partial / Unsupported / Planned).
        code_path: the dotted Python attribute that implements this row.
            Empty for ``Planned`` rows (no implementation yet); for all other
            levels the matrix self-test verifies the path resolves.
        note: short prose describing what survives and what doesn't.
        plan: for ``Planned`` rows, the intended implementation strategy
            (e.g., "record N frames via Playwright and embed as GIF").
            Empty for shipped rows.
    """

    category: str
    feature: str
    support: Support
    code_path: str
    note: str
    plan: str = ""


# ---------------------------------------------------------------------------
# The matrix. Each row cites the exact code path that handles it; the unit
# test imports each path at collection time, so deletions break the build.
# ---------------------------------------------------------------------------

def _row(*args, plan: str = "", **kwargs) -> CompatRow:  # tiny helper to keep rows readable
    return CompatRow(*args, plan=plan, **kwargs)


MATRIX: tuple[CompatRow, ...] = (
    # --- Typography / text -----------------------------------------------
    CompatRow(
        "Typography", "color (rgb / rgba / hex / named)", Support.Native,
        "slidify.colors.parse_color",
        "Solid colors with alpha; gradient-clipped text degrades to first stop.",
    ),
    CompatRow(
        "Typography", "color (oklch / oklab)", Support.Native,
        "slidify.oklch.oklch_to_srgb",
        "Converted to sRGB at emit time.",
    ),
    CompatRow(
        "Typography", "font-family / font-weight / font-size / italic / underline",
        Support.Native, "slidify.emitter.Emitter._emit_native_text",
        "Per-run styling preserved; font fallback resolution via fontconfig.",
    ),
    CompatRow(
        "Typography", "line-height / text-align", Support.Native,
        "slidify.emitter.Emitter._emit_native_text",
        "Browser-derived per-line getClientRects() carry exact line positions.",
    ),
    CompatRow(
        "Typography", "text-shadow", Support.Partial,
        "slidify.dom_walker.WALKER_JS",
        "Captured on every element so the matcher can reason about it; "
        "OOXML emit is per-shape outerShdw on the run, not yet wired in.",
    ),
    CompatRow(
        "Typography", "letter-spacing (numeric / em / px)", Support.Native,
        "slidify.dom_walker.WALKER_JS",
        "Captured as the resolved px value so the matcher can route to "
        "kicker / display registers; emitter applies via run.font.spacing.",
    ),
    CompatRow(
        "Typography", "writing-mode (vertical-rl / vertical-lr / sideways-*)",
        Support.Partial, "slidify.dom_walker.WALKER_JS",
        "Captured for matcher; vertical type rasterizes for now (PPTX text "
        "frames support vertical via bodyPr but the run pipeline doesn't yet).",
    ),
    CompatRow(
        "Typography", "background-clip: text + background-image: url()",
        Support.Raster, "slidify.classifier.tier1._has_text_image_mask",
        "Text-as-image-mask falls back to a pixel crop.",
    ),

    # --- Backgrounds / fills ---------------------------------------------
    CompatRow(
        "Backgrounds", "background-color (rgba)", Support.Native,
        "slidify.emitter.Emitter._apply_fill",
        "Alpha is preserved via OOXML <a:alpha>.",
    ),
    CompatRow(
        "Backgrounds", "linear-gradient / radial-gradient / conic-gradient",
        Support.Native, "slidify.gradients.parse_gradient",
        "Multi-stop gradients translated to <a:gradFill>.",
    ),
    CompatRow(
        "Backgrounds", "background-image: url(...)",
        Support.Raster, "slidify.classifier.tier1._has_url_background_image",
        "Bg-images aren't fetched + re-embedded; the unit rasterizes.",
    ),
    CompatRow(
        "Backgrounds", "Mesh gradients (multi-radial composition)",
        Support.Native, "slidify.mesh",
        "A small set of canonical mesh recipes emit as layered gradients.",
    ),

    # --- Borders / shadows / radius --------------------------------------
    CompatRow(
        "Borders", "border (width + style + color)", Support.Native,
        "slidify.emitter.Emitter._apply_border",
        "Solid + dashed; per-side variants degrade to uniform.",
    ),
    CompatRow(
        "Borders", "border-radius", Support.Native,
        "slidify.emitter._shape_kind_for_anchor",
        "Maps to ROUNDED_RECTANGLE / OVAL when radius is large enough.",
    ),
    CompatRow(
        "Borders", "box-shadow (single + multi-layer)", Support.Native,
        "slidify.shadows.parse_box_shadows",
        "Inset shadows degrade to outer; spread + blur honored.",
    ),
    CompatRow(
        "Borders", "Asymmetric per-side borders (border-top / -right / -bottom / -left)",
        Support.Partial, "slidify.dom_walker.WALKER_JS",
        "All four sides captured so the matcher can recognize accent-stripe, "
        "magazine-rule, and brutalist top-rule patterns. Emit currently uses "
        "the dominant side's color/width.",
    ),

    # --- Transforms / clipping / effects ---------------------------------
    CompatRow(
        "Effects", "transform: rotate / translate / scale", Support.Partial,
        "slidify.classifier.tier1.rule_3d_transform_raster",
        "2D transforms applied at emit; 3D / matrix3d / skew rasterize.",
    ),
    CompatRow(
        "Effects", "filter (blur / brightness / hue-rotate / …)",
        Support.Raster, "slidify.classifier.tier1.rule_filter_raster",
        "PPTX has no equivalent; entire unit rasterizes.",
    ),
    CompatRow(
        "Effects", "clip-path: inset() / circle() / common polygon()",
        Support.Native, "slidify.preset_shapes.clip_path_to_preset",
        "Translated to PPTX preset shapes; other clip-paths rasterize.",
    ),
    CompatRow(
        "Effects", "clip-path: path() / url(#mask)", Support.Raster,
        "slidify.classifier.tier1._has_untranslatable_clip_path",
        "Falls back to a region screenshot crop.",
    ),
    CompatRow(
        "Effects", "mix-blend-mode", Support.Raster,
        "slidify.classifier.tier1._has_mix_blend_mode",
        "OOXML <a:blendOptions> is too narrow to reproduce reliably.",
    ),
    CompatRow(
        "Effects", "backdrop-filter", Support.Raster,
        "slidify.classifier.tier1._has_backdrop_filter",
        "Frosted-glass needs a live blur; baked via raster crop.",
    ),
    CompatRow(
        "Effects", "mask-image / -webkit-mask-image", Support.Raster,
        "slidify.dom_walker.WALKER_JS",
        "Captured so units carrying a mask route to Raster instead of "
        "emitting an unmasked shape; native translation is roadmap.",
    ),
    CompatRow(
        "Effects", "background-blend-mode", Support.Raster,
        "slidify.dom_walker.WALKER_JS",
        "Multi-background Porter-Duff compositing; PPTX has no equivalent. "
        "Captured so the unit raster-crops cleanly.",
    ),
    CompatRow(
        "Effects", "opacity (< 1)", Support.Native,
        "slidify.emitter.Emitter._set_solid_fill_alpha",
        "Solid + image opacity baked into the shape / pixels at emit time.",
    ),

    # --- Layout primitives -----------------------------------------------
    CompatRow(
        "Layout", "Absolute / flow positioning", Support.Native,
        "slidify.dom_walker.WALKER_JS",
        "Walker captures getBoundingClientRect() — the resolved pixel layout.",
    ),
    CompatRow(
        "Layout", "z-index ordering", Support.Native,
        "slidify.promotion.to_emit_ops",
        "z-index promoted into z_order on every EmitOp.",
    ),
    CompatRow(
        "Layout", "overflow: hidden", Support.Partial,
        "slidify.classifier.tier1.RULES",
        "Children that overflow the viewport are clipped at slide bounds.",
    ),
    CompatRow(
        "Layout", "CSS Grid (template-columns / gap)", Support.Native,
        "slidify.dom_walker.WALKER_JS",
        "Grid template + gap captured so the matcher can recognize bento / "
        "dashboard / 12-col compositions; resolved geometry survives via bbox.",
    ),
    CompatRow(
        "Layout", "aspect-ratio", Support.Native,
        "slidify.dom_walker.WALKER_JS",
        "Captured so square-tile / cinema / golden compositions can be matched "
        "and routed to mask presets independent of class names.",
    ),
    CompatRow(
        "Layout", "object-fit / object-position (on <img>)", Support.Partial,
        "slidify.dom_walker.WALKER_JS",
        "Captured so the picture emit can apply <a:srcRect> crop; default "
        "fill behavior preserves the rendered look.",
    ),

    # --- HTML primitives -------------------------------------------------
    CompatRow(
        "HTML", "<h1>-<h6> / <p> / <span> / inline formatting",
        Support.Native, "slidify.emitter.Emitter._emit_native_text",
        "Multi-run text frames with per-span color / weight / italic.",
    ),
    CompatRow(
        "HTML", "<ul> / <ol> / <li>", Support.Native,
        "slidify.classifier.tier1.rule_list_item",
        "List items emit as native bulleted text frames.",
    ),
    CompatRow(
        "HTML", "<table> (with thead / colspan / rowspan)", Support.Native,
        "slidify.emitter.Emitter._emit_native_table",
        "HTML tables emit as PPTX table primitives with editable cells.",
    ),
    CompatRow(
        "HTML", "<img>", Support.Native,
        "slidify.emitter.Emitter._emit_native_picture",
        "Fetched + re-embedded; opacity baked against underlying bg.",
    ),
    CompatRow(
        "HTML", "<svg> (≤200 primitives)", Support.Native,
        "slidify.svg_shapes.emit_svg_shapes",
        "Translatable primitives emit natively; complex SVGs rasterize.",
    ),
    CompatRow(
        "HTML", "<canvas>", Support.Raster,
        "slidify.classifier.tier1.rule_canvas_always_raster",
        "Canvas pixels captured by the screenshot pass.",
    ),
    CompatRow(
        "HTML", "<video>", Support.Raster,
        "slidify.classifier.tier1.rule_video_always_raster",
        "First frame captured; playback dropped.",
    ),

    # --- Slidify hint protocol -------------------------------------------
    CompatRow(
        "Hints", "data-pptx-role / data-pptx-rasterize / data-pptx-skip",
        Support.Native, "slidify.classifier.tier1.RULES",
        "Author-controlled overrides bypass classification.",
    ),
    CompatRow(
        "Hints", "data-pptx-notes (speaker notes)", Support.Native,
        "slidify.emitter.Emitter._set_notes",
        "Plain-text notes attached to slide.notes_slide.",
    ),
    CompatRow(
        "Hints", "data-slidify-decorate (hero / glass / tactile / …)",
        Support.Native, "slidify.decorations.derive_decorations",
        "Layered decoration stacks emit as native shape stacks.",
    ),
    CompatRow(
        "Hints", "data-atom (atom catalog short-circuit)",
        Support.Native, "slidify.patterns.matcher._h_data_atom_id",
        "Tags a cluster as a known atom (bg.mesh, surf.glass, type.echo, …); "
        "matcher routes to the canonical recipe in atoms.yaml. "
        "Catalog: examples/landing/atoms.html.",
    ),
    CompatRow(
        "Hints", "Implicit atom inference (no data-atom needed)",
        Support.Native, "slidify.atom_inference.infer_atom_id",
        "When the unit's structural signature matches a primed atom hash, "
        "the matcher synthesises the data-atom hint at classify time so "
        "the canonical recipe fires without an author tag. Priming table "
        "lives in slidify/patterns/data/atom_signatures.json; rebuild with "
        "`slidify prime-atom-cache`.",
    ),

    # --- Notion-style block primitives -----------------------------------
    # Notion's blocks are "just HTML" once exported — toggles, callouts,
    # columns, databases all render as nested div/table/h2 trees. Slidify
    # captures the resolved layout, so the visual result survives. The
    # interactive bits (open/close toggles, hover) flatten to their
    # "as-rendered" state.
    CompatRow(
        "Block layouts", "Callout / quote / code blocks (Notion-style)",
        Support.Native, "slidify.classifier.tier1.rule_simple_leaf_text",
        "Card + icon + text emit as native shape + native text frames.",
    ),
    CompatRow(
        "Block layouts", "Multi-column page layouts (Notion columns / CSS Grid)",
        Support.Native, "slidify.dom_walker.WALKER_JS",
        "Resolved column geometry survives via getBoundingClientRect.",
    ),
    CompatRow(
        "Block layouts", "Notion-style toggle (closed state)", Support.Native,
        "slidify.dom_walker.WALKER_JS",
        "Captured in whatever open/closed state the source HTML left it.",
    ),
    CompatRow(
        "Block layouts", "Notion-style toggle (animated open/close)",
        Support.Planned, "",
        "Toggling state is interactive; can't survive into PPTX.",
        plan=(
            "Add data-pptx-toggle hint that emits both the closed and open "
            "frames as separate slides; bake the chosen state via author "
            "control."
        ),
    ),
    CompatRow(
        "Block layouts", "Notion database (gallery / board / table view)",
        Support.Partial, "slidify.emitter.Emitter._emit_native_table",
        "Table view → native PPTX table; gallery/board → cards (native shapes).",
    ),

    # --- Animation / motion (Framer / Framer-Motion / GSAP / CSS) ---------
    CompatRow(
        "Motion", "CSS @keyframes animations (final-frame capture)",
        Support.Partial, "slidify.renderer.Renderer",
        "Renderer captures the post-load steady state; mid-animation frames lost.",
    ),
    CompatRow(
        "Motion", "CSS @keyframes → PPTX motion paths",
        Support.Planned, "",
        "Today the rendered final frame is captured; animation lost.",
        plan=(
            "Parse @keyframes (transform/opacity), translate to PPTX "
            "<p:par><p:childTnLst> motion paths so PowerPoint replays them "
            "natively. Bounded to 2D translate / fade / scale."
        ),
    ),
    CompatRow(
        "Motion", "Framer / Framer-Motion (final state)", Support.Partial,
        "slidify.renderer.Renderer",
        "Default convert path captures the final state; use `slidify capture-gif` for animated output.",
    ),
    CompatRow(
        "Motion", "Framer / Framer-Motion (record-and-embed as animated GIF)",
        Support.Native, "slidify.anim_capture.capture_html_to_gif",
        "Render the slide without freeze, sample N frames at the declared fps, "
        "emit an animated GIF that PowerPoint plays in slideshow mode. "
        "Reference the GIF from a normal slide via <img src=\"...\"> and "
        "the static-emit pipeline embeds it as a NativePicture verbatim.",
    ),
    CompatRow(
        "Motion", "Lottie animations", Support.Planned, "",
        "Currently rasterized via canvas/SVG fallback (single frame). "
        "Same record-and-embed plan as Framer once lottie-web detection lands.",
        plan=(
            "Detect lottie-web players in DOM, drive lottie.goToAndStop on "
            "frame 0..N-1 and capture each via anim_capture, embed as GIF."
        ),
    ),
    CompatRow(
        "Motion", "GSAP / Anime.js timelines",
        Support.Native, "slidify.anim_capture.capture_html_to_gif",
        "Same path as Framer Motion: capture-gif samples the timeline at the "
        "declared fps and writes an animated GIF for embed.",
    ),
    CompatRow(
        "Motion", "Scroll-triggered animations (IntersectionObserver / GSAP ScrollTrigger)",
        Support.Planned, "",
        "Without scrolling the page, the in-view triggers never fire.",
        plan=(
            "Add an emit-time scroll pass: scroll to each scene, wait for "
            "anim to settle, capture, advance. Each scene becomes its own slide."
        ),
    ),

    # --- 3D / WebGL / Three.js / volumetric --------------------------------
    CompatRow(
        "3D / WebGL", "Three.js / WebGL canvas (single frame)",
        Support.Raster, "slidify.classifier.tier1.rule_canvas_always_raster",
        "Canvas pixels captured by the screenshot pass; 3D scene flattens.",
    ),
    CompatRow(
        "3D / WebGL", "Three.js / WebGL canvas → animated GIF embed",
        Support.Planned, "",
        "Today: single-frame raster. Target: orbital animation as embedded GIF.",
        plan=(
            "Add data-pptx-record='gif|mp4 duration=Ns fps=N' on canvas; "
            "renderer drives requestAnimationFrame N frames, encodes via "
            "Pillow (GIF) or ffmpeg (MP4), embeds via add_picture (GIF) or "
            "add_movie (MP4). PowerPoint plays both natively."
        ),
    ),
    CompatRow(
        "3D / WebGL", "react-three-fiber scenes", Support.Raster,
        "slidify.classifier.tier1.rule_canvas_always_raster",
        "RTF mounts to a canvas — same path as raw Three.js.",
    ),
    CompatRow(
        "3D / WebGL", "WebGL shader art / Shadertoy", Support.Raster,
        "slidify.classifier.tier1.rule_canvas_always_raster",
        "Single-frame today; same record-and-embed plan as Three.js.",
    ),
    CompatRow(
        "3D / WebGL", "model-viewer (<model-viewer>)", Support.Planned, "",
        "Treated as canvas today; orbit animation lost.",
        plan=(
            "Drive model-viewer's exposed cameraOrbit setter over a 360° arc, "
            "capture each frame, embed as GIF / MP4."
        ),
    ),
    CompatRow(
        "3D / WebGL", "CSS 3D transforms (matrix3d / preserve-3d)",
        Support.Raster, "slidify.classifier.tier1.rule_3d_transform_raster",
        "PPTX shapes are 2D; 3D-transformed cards rasterize.",
    ),

    # --- Video / audio / embeds -------------------------------------------
    CompatRow(
        "Embeds", "<video src='*.mp4' / *.webm>", Support.Planned, "",
        "Today: rasterized to first frame.",
        plan=(
            "Fetch source via httpx, embed via slide.shapes.add_movie() so "
            "PowerPoint plays the clip in-deck. Honor autoplay / loop attrs."
        ),
    ),
    CompatRow(
        "Embeds", "<audio>", Support.Planned, "",
        "Currently dropped (audio elements have no visual bbox).",
        plan="add_movie() with audio mime; surface as a speaker icon at the bbox.",
    ),
    CompatRow(
        "Embeds", "Animated GIF (<img src='*.gif'>)", Support.Native,
        "slidify.emitter.Emitter._emit_native_picture",
        "Embedded as a picture; PowerPoint plays the GIF on slide-show.",
    ),
    CompatRow(
        "Embeds", "<iframe> (YouTube / Vimeo / CodePen / Figma)",
        Support.Raster, "slidify.classifier.tier1.RULES",
        "Cross-origin iframes screenshot to a single frame; interactivity lost.",
    ),
    CompatRow(
        "Embeds", "YouTube / Vimeo as native PPTX online video", Support.Planned, "",
        "Recognize iframe[src*='youtube'] and embed via OOXML videoFile link.",
        plan=(
            "Detect provider from iframe src, write a <p:videoFile r:link='...'> "
            "into the slide XML so PowerPoint plays via online-video pane."
        ),
    ),
    CompatRow(
        "Embeds", "Twitter / X embed", Support.Raster,
        "slidify.classifier.tier1.RULES",
        "Embed renders to iframe → screenshot crop.",
    ),
    CompatRow(
        "Embeds", "Hyperlinks (<a href>)", Support.Planned, "",
        "Anchor text emits today, but the href is dropped.",
        plan=(
            "Set run.hyperlink.address on the PPTX run when the source <a> "
            "carries a non-fragment href."
        ),
    ),

    # --- Charts / visualization libraries ---------------------------------
    CompatRow(
        "Charts", "Chart.js (canvas)", Support.Raster,
        "slidify.classifier.tier1.rule_canvas_always_raster",
        "Canvas pixels captured; legends + axes survive visually but not as data.",
    ),
    CompatRow(
        "Charts", "D3 (SVG)", Support.Native,
        "slidify.svg_shapes.emit_svg_shapes",
        "≤200 primitives emit as native shapes; complex viz rasterizes.",
    ),
    CompatRow(
        "Charts", "Plotly (SVG mode)", Support.Native,
        "slidify.svg_shapes.emit_svg_shapes",
        "SVG-rendered Plotly charts emit native; canvas mode rasterizes.",
    ),
    CompatRow(
        "Charts", "ECharts / Highcharts (canvas)", Support.Raster,
        "slidify.classifier.tier1.rule_canvas_always_raster",
        "Canvas-only; same as Chart.js.",
    ),
    CompatRow(
        "Charts", "Chart.js / ECharts → native PPTX chart", Support.Planned, "",
        "Charts emit as raster pixels today; data semantics lost.",
        plan=(
            "Add data-pptx-chart='bar|line|pie' on a hidden <pre> emitting "
            "chart series JSON; emitter calls slide.shapes.add_chart() so the "
            "chart is editable in PowerPoint."
        ),
    ),

    # --- Maps -------------------------------------------------------------
    CompatRow(
        "Maps", "Leaflet / Mapbox / Google Maps", Support.Raster,
        "slidify.classifier.tier1.rule_canvas_always_raster",
        "Tile compositions captured as a single frame.",
    ),

    # --- Math / code ------------------------------------------------------
    CompatRow(
        "Math / Code", "KaTeX / MathJax (HTML mode)", Support.Native,
        "slidify.emitter.Emitter._emit_native_text",
        "Rendered as nested <span> trees; emits as native text.",
    ),
    CompatRow(
        "Math / Code", "MathML", Support.Raster,
        "slidify.classifier.tier1.RULES",
        "Browser-native MathML rasterizes; KaTeX/MathJax recommended.",
    ),
    CompatRow(
        "Math / Code", "Syntax-highlighted code (Prism / Shiki / highlight.js)",
        Support.Native, "slidify.emitter.Emitter._emit_native_text",
        "Per-token <span> colors survive as multi-run native text.",
    ),
    CompatRow(
        "Math / Code", "Emoji (Unicode + Twemoji <img>)", Support.Native,
        "slidify.emitter.Emitter._emit_native_text",
        "Unicode emoji as text; Twemoji as native pictures.",
    ),

    # --- Web fonts / icons ------------------------------------------------
    CompatRow(
        "Fonts / Icons", "@font-face web fonts (Google / self-hosted)",
        Support.Native, "slidify.font_embed.embed_fonts_in_pptx",
        "Fonts subset to used glyphs and embedded in the .pptx.",
    ),
    CompatRow(
        "Fonts / Icons", "Variable fonts (axis settings)", Support.Partial,
        "slidify.font_embed.embed_fonts_in_pptx",
        "Static instance shipped; PowerPoint can't address axes at runtime.",
    ),
    CompatRow(
        "Fonts / Icons", "Icon fonts (Font Awesome / Material Icons)",
        Support.Native, "slidify.emitter.Emitter._emit_native_text",
        "Rendered as ligatures/text — survive provided font is embedded.",
    ),
    CompatRow(
        "Fonts / Icons", "SVG icon sets (Heroicons / Lucide)", Support.Native,
        "slidify.svg_shapes.emit_svg_shapes",
        "Icons emit as native shape paths.",
    ),

    # --- Markdown / MDX rendering -----------------------------------------
    CompatRow(
        "Markdown", "Rendered Markdown / MDX (final HTML)", Support.Native,
        "slidify.dom_walker.WALKER_JS",
        "MDX is just HTML after build; same support as hand-authored HTML.",
    ),

    # --- Slide-level features --------------------------------------------
    CompatRow(
        "Slide structure", "<!DOCTYPE html> separator (multi-slide file)",
        Support.Native, "slidify.splitter.split_slides",
        "Genspark convention; one DOCTYPE = one slide.",
    ),
    CompatRow(
        "Slide structure", "reveal.js <section> stacks (vertical/horizontal)",
        Support.Planned, "",
        "Today reveal.js decks render only the first section per slide.",
        plan=(
            "Add a reveal-aware splitter that walks <section> stacks and "
            "emits one slide per leaf section, honoring data-fragment as "
            "stepwise reveal layers."
        ),
    ),
    CompatRow(
        "Slide structure", "Slide transitions (fade / push / morph)",
        Support.Planned, "",
        "PPTX supports per-slide transitions; not yet emitted.",
        plan=(
            "Add data-pptx-transition='fade|push|morph' hint; emit "
            "<p:transition> on the slide XML."
        ),
    ),
    CompatRow(
        "Slide structure", "Master / theme slides", Support.Partial,
        "slidify.theme",
        "Brand accents propagated; full master inheritance not yet wired.",
    ),

    # --- Forms / interactive --------------------------------------------
    CompatRow(
        "Forms", "<input> / <button> / <select> (visual state)",
        Support.Raster, "slidify.classifier.tier1.RULES",
        "Form controls captured as their rendered visual; interactivity lost.",
    ),
    CompatRow(
        "Forms", "Checkbox / radio (rendered state)", Support.Raster,
        "slidify.classifier.tier1.RULES",
        "Whatever checked state the source had at render time is captured.",
    ),

    # --- Accessibility ---------------------------------------------------
    CompatRow(
        "A11y", "aria-label / alt text", Support.Native,
        "slidify.dom_walker.WALKER_JS",
        "Captured on every element; emitted as picture descriptions.",
    ),
    CompatRow(
        "A11y", "Reading order via DOM order", Support.Native,
        "slidify.promotion.to_emit_ops",
        "Emit op z-order respects DOM source order.",
    ),

    # --- Diagnostics -----------------------------------------------------
    CompatRow(
        "Diagnostics", "Unit coverage oracle (DOM text → unit map)",
        Support.Native, "slidify.unit_coverage.find_coverage_gaps",
        "Reports DOM elements with text content whose region isn't covered by "
        "any produced VisualUnit. The dual of the harvester: harvester finds "
        "unrecognised shapes, this finds dropped content. Surface via "
        "ConversionResult.coverage_gaps and the convert summary.",
    ),
    CompatRow(
        "Diagnostics", "Emit-pathway exclusivity audit",
        Support.Native, "slidify.promotion.audit_emit_exclusivity",
        "Flags emit ops where an absorbing parent (NativeText / NativeBullet "
        "/ NativePicture / NativeSvg / NativeTable) overlaps a descendant unit "
        "that ALSO emits — the structural fingerprint of visual duplication. "
        "Allows the legitimate Phase-A hybrid case (parent has "
        "mixed_content_text). Surface via "
        "ConversionResult.exclusivity_violations.",
    ),
)


def matrix() -> tuple[CompatRow, ...]:
    """Public accessor — returns the immutable matrix tuple."""
    return MATRIX


def matrix_summary() -> dict[str, int]:
    """Return per-support-level counts. Embedded in `--report-json`."""
    out: dict[str, int] = {s.value: 0 for s in Support}
    for row in MATRIX:
        out[row.support.value] += 1
    return out


def to_markdown() -> str:
    """Render the matrix as a GitHub-flavored Markdown table grouped by category.

    Planned rows surface their ``plan`` text inline so the matrix doubles as
    a roadmap (and reviewers can object to specific plans).
    """
    lines = [
        f"# slidify CSS / HTML compatibility (matrix v{MATRIX_VERSION})",
        "",
        "Levels:",
        "",
        "- **native** — editable PPTX primitive (text frame, shape, table, picture).",
        "- **raster** — preserved visually as a pixel crop; not editable.",
        "- **partial** — natively translated for a subset of inputs; falls back otherwise.",
        "- **unsupported** — currently dropped or visually wrong.",
        "- **planned** — roadmap item, not yet shipped; the `plan` cell describes the target.",
        "",
    ]
    by_cat: dict[str, list[CompatRow]] = {}
    for row in MATRIX:
        by_cat.setdefault(row.category, []).append(row)
    for cat, rows in by_cat.items():
        lines.append(f"## {cat}")
        lines.append("")
        lines.append("| Feature | Support | Notes | Plan (if planned) |")
        lines.append("|---------|---------|-------|--------------------|")
        for r in rows:
            note = r.note.replace("|", "\\|")
            feature = r.feature.replace("|", "\\|")
            plan = (r.plan or "").replace("|", "\\|")
            lines.append(
                f"| `{feature}` | **{r.support.value}** | {note} | {plan} |"
            )
        lines.append("")
    return "\n".join(lines)


def code_path_exists(dotted: str) -> bool:
    """True iff ``dotted`` resolves to an importable Python attribute.

    Used by the matrix self-test to detect rotted citations. Empty input
    is treated as a valid no-op so ``Planned`` rows (which legitimately
    have no implementation yet) don't trip the check.
    """
    if not dotted:
        return True
    import importlib

    parts = dotted.split(".")
    for split in range(len(parts) - 1, 0, -1):
        mod_name = ".".join(parts[:split])
        attr_path = parts[split:]
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        cur = mod
        ok = True
        for a in attr_path:
            if not hasattr(cur, a):
                ok = False
                break
            cur = getattr(cur, a)
        if ok:
            return True
    # Fallback: maybe ``dotted`` IS a module path.
    try:
        importlib.import_module(dotted)
        return True
    except ImportError:
        return False
