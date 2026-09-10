"""DOM walker: runs in-page to extract a flat list of element snapshots."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

from slidify.models import BoundingBox, DomElement, TextRun

# Maximum SVG primitive count that the walker captures geometry for and that
# the tier-1 classifier accepts on the native path. Beyond this, the SVG is
# treated as a complex illustration and rasterized cleanly. The two sites
# (this constant and `classifier.tier1._has_complex_svg`) MUST stay aligned —
# a mismatch creates a "dead band" where the walker drops geometry but the
# classifier still routes the unit to the native emitter, which then emits
# an empty shell. Real-world charts (50+ data points + axis ticks + gridlines)
# and architecture diagrams (40-80 connectors) routinely exceed the previous
# cap of 30; the bumped budget keeps them on the native path.
SVG_NATIVE_PATH_BUDGET = 200

# Walks the DOM in-page and produces an array of element records.
# Skips invisible elements and <head>. Captures bbox, computed style,
# pseudo-element presence, and slidify hints.
_WALKER_JS_TEMPLATE = r"""
() => {
    const out = [];
    let nextId = 0;

    function buildStableSelector(el) {
        if (el.id) {
            try { return '#' + CSS.escape(el.id); } catch (_) {}
        }
        const path = [];
        let node = el;
        while (node && node.nodeType === 1 && node !== document.body && node.parentElement) {
            const idx = Array.from(node.parentElement.children).indexOf(node) + 1;
            path.unshift(node.tagName.toLowerCase() + ':nth-child(' + idx + ')');
            node = node.parentElement;
        }
        if (node === document.body) path.unshift('body');
        return path.join(' > ');
    }

    function isInteresting(content) {
        return !!content && content !== 'none' && content !== '""' && content !== "''" && content !== 'normal';
    }

    const INLINE_FORMATTING_TAGS = new Set([
        'SPAN', 'EM', 'STRONG', 'B', 'I', 'U', 'A', 'CODE', 'SMALL',
        'MARK', 'SUP', 'SUB', 'INS', 'DEL', 'KBD', 'ABBR', 'CITE',
    ]);

    function pseudoSnapshot(cs) {
        return {
            background_color: cs.backgroundColor || 'rgba(0, 0, 0, 0)',
            background_image: cs.backgroundImage || 'none',
            color: cs.color || 'rgb(0,0,0)',
            font_family: cs.fontFamily || '',
            font_size: cs.fontSize || '16px',
            font_weight: cs.fontWeight || '400',
            position: cs.position || 'static',
            border_radius: cs.borderRadius || '0px',
        };
    }

    function isVisuallyInline(el) {
        // Decide whether an inline-tag child should fold into the parent's
        // text frame or break out as its own visual unit. Tag-name alone
        // is too permissive: a `<span class="btn"
        // style="display:inline-block; background:#fff; border-radius:8px">`
        // is a button, not a kerning helper, and folding it loses the
        // visual block. Conversely, a plain `<span style="color:red">` is
        // a styled run and should fold.
        const cs = getComputedStyle(el);
        const d = cs.display || 'inline';
        if (d === 'block' || d === 'flex' || d === 'grid' ||
            d === 'inline-flex' || d === 'inline-grid' || d === 'list-item' ||
            d === 'table' || d === 'inline-table') return false;
        if (d === 'inline-block') {
            const bg = cs.backgroundColor || '';
            if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') return false;
            const bgi = cs.backgroundImage || 'none';
            if (bgi !== 'none') return false;
            if (parseFloat(cs.borderTopWidth || '0') > 0 ||
                parseFloat(cs.borderBottomWidth || '0') > 0 ||
                parseFloat(cs.borderLeftWidth || '0') > 0 ||
                parseFloat(cs.borderRightWidth || '0') > 0) return false;
            const sh = cs.boxShadow || '';
            if (sh && sh !== 'none') return false;
            if (parseFloat(cs.borderRadius || '0') > 0) return false;
            return true;
        }
        return true;  // 'inline', 'contents', 'ruby'
    }

    function isTextContainer(el) {
        // Functionally a text container when (a) every element child is
        // an inline-formatting tag (or BR), (b) every such child is
        // visually inline (computed display is `inline`/`contents`, or
        // `inline-block` with no decoration of its own), AND (c) the
        // rendered content carries some textual payload. The looser
        // hasText probe catches `<div><span>k</span><span>v</span></div>`
        // (no whitespace text node between siblings); the visual-inline
        // probe rejects `<div class="ctas"><span class="btn">…</span>
        // <span class="btn">…</span></div>` so each button keeps its
        // own unit instead of all collapsing into one text frame.
        let hasText = false;
        for (const node of el.childNodes) {
            if (node.nodeType === 3) {
                if ((node.textContent || '').trim()) hasText = true;
            } else if (node.nodeType === 1) {
                const t = node.tagName;
                if (t === 'BR') continue;
                if (!INLINE_FORMATTING_TAGS.has(t)) return false;
                if (!isVisuallyInline(node)) return false;
                if (!hasText && (node.textContent || '').trim()) hasText = true;
            }
        }
        return hasText;
    }

    // Slack around the slide frame. Anything whose bbox lies entirely
    // outside this expanded rect — e.g. screen-reader-only spans pinned
    // at left:-9999px, sr-only width:1px height:1px tricks — is captured
    // by the walker but flagged so the clusterer can drop it. The slack
    // accounts for legitimate decorative bleed (aurora blobs that hang
    // a few hundred px past the edge by design).
    const OFFCANVAS_SLACK = 800;
    const VIEWPORT_W = 1280;
    const VIEWPORT_H = 720;
    function isOffcanvasBbox(r) {
        if (r.width <= 1 && r.height <= 1) return true;
        if (r.x + r.width <= -OFFCANVAS_SLACK) return true;
        if (r.y + r.height <= -OFFCANVAS_SLACK) return true;
        if (r.x >= VIEWPORT_W + OFFCANVAS_SLACK) return true;
        if (r.y >= VIEWPORT_H + OFFCANVAS_SLACK) return true;
        return false;
    }

    function collectMixedContentText(el) {
        // Direct text-node children only, joined. Used when an element has
        // BOTH a direct text leaf AND block-level descendants — neither the
        // text-container path nor the leaf-text path covers this case, so
        // the parent unit needs to know its own text fragment to emit
        // alongside the descendants. Returns null when there's no
        // non-whitespace direct text.
        const parts = [];
        for (const node of el.childNodes) {
            if (node.nodeType === 3) {
                const t = (node.textContent || '').trim();
                if (t) parts.push(t);
            }
        }
        return parts.length > 0 ? parts.join(' ') : null;
    }

    function collectRuns(el) {
        const out = [];
        const cs = getComputedStyle(el);
        // Capture this element's bg-image so the Python emitter can substitute
        // a solid color when the run uses gradient-clipped text
        // (background-clip: text + color: transparent).
        const bgImage = cs.backgroundImage || 'none';
        const bgColor = cs.backgroundColor || 'rgba(0, 0, 0, 0)';
        const textTransform = cs.textTransform || 'none';
        const letterSpacing = cs.letterSpacing || 'normal';
        function elementLineBoxes(node) {
            const boxes = [];
            try {
                for (const rect of node.getClientRects()) {
                    if (rect.width > 0 && rect.height > 0) {
                        boxes.push({
                            x: rect.x, y: rect.y,
                            w: rect.width, h: rect.height,
                        });
                    }
                }
            } catch (_) {}
            return boxes;
        }
        for (const node of el.childNodes) {
            if (node.nodeType === 3) {
                const txt = node.textContent || '';
                if (!txt) continue;
                // Capture browser-derived line boxes for this text node via
                // Range.getClientRects(). Each rect is one visual line at
                // its exact rendered position. The Python emitter uses
                // these as ground truth so a wrapped headline lands at the
                // pixel-precise lines the source rendered, instead of
                // being squeezed into the unit's bbox and re-flowed.
                let lineBoxes = [];
                try {
                    const r = document.createRange();
                    r.selectNodeContents(node);
                    const rects = r.getClientRects();
                    for (const rect of rects) {
                        if (rect.width > 0 && rect.height > 0) {
                            lineBoxes.push({
                                x: rect.x, y: rect.y,
                                w: rect.width, h: rect.height,
                            });
                        }
                    }
                } catch (_) {}
                out.push({
                    text: txt,
                    font_family: cs.fontFamily,
                    font_size: cs.fontSize,
                    font_weight: cs.fontWeight,
                    color: cs.color,
                    background_color: bgColor,
                    background_image: bgImage,
                    letter_spacing: letterSpacing,
                    text_transform: textTransform,
                    italic: cs.fontStyle === 'italic',
                    underline: cs.textDecorationLine && cs.textDecorationLine.includes('underline'),
                    is_break: false,
                    line_boxes: lineBoxes,
                    background_boxes: [],
                });
            } else if (node.nodeType === 1) {
                if (node.tagName === 'BR') {
                    out.push({ text: '\n', is_break: true });
                    continue;
                }
                // Recurse with the child's own computed style.
                const sub = collectRuns(node);
                if (sub.length > 0) {
                    // CSS `margin-left` / `margin-right` on an inline child
                    // creates a visible gap that the source HTML doesn't
                    // express as whitespace (`<span>Q1</span>Foundation` →
                    // CSS margin makes "Q1 Foundation"). Insert ASCII
                    // spaces so the gap survives into the PPTX runs.
                    const ccs = getComputedStyle(node);
                    const mlPx = parseFloat(ccs.marginLeft || '0') || 0;
                    const mrPx = parseFloat(ccs.marginRight || '0') || 0;
                    if (mlPx >= 3) sub[0].text = ' ' + sub[0].text;
                    if (mrPx >= 3) sub[sub.length - 1].text = sub[sub.length - 1].text + ' ';
                    const cBg = ccs.backgroundColor || '';
                    const cBgImage = ccs.backgroundImage || 'none';
                    const hasBg = (
                        (cBg && cBg !== 'rgba(0, 0, 0, 0)' && cBg !== 'transparent')
                        || (cBgImage && cBgImage !== 'none')
                    );
                    if (hasBg) {
                        const bgBoxes = elementLineBoxes(node);
                        for (const r of sub) {
                            if (!r.is_break && (!r.background_boxes || r.background_boxes.length === 0)) {
                                r.background_boxes = bgBoxes;
                            }
                        }
                    }
                }
                for (const r of sub) out.push(r);
            }
        }
        return out;
    }

    // ---- HTML <table> capture ------------------------------------------
    //
    // Returns a {rows, header_rows} dict if `el` is a translatable HTML
    // table — every cell contains only text (text nodes + inline
    // formatting). Returns null for tables that contain nested tables,
    // images, SVG, canvas, or block-level layout inside cells; those
    // rasterize via the existing pipeline so we never half-emit a table.
    function isInlineOnly(node) {
        for (const ch of node.childNodes) {
            if (ch.nodeType === 1) {
                const t = ch.tagName;
                if (t === 'BR') continue;
                if (!INLINE_FORMATTING_TAGS.has(t)) return false;
                if (!isInlineOnly(ch)) return false;
            }
        }
        return true;
    }
    function captureTable(el) {
        // Only consider a flat (non-nested) <table>. We accept colspan /
        // rowspan but record them so the emitter can merge cells.
        if (el.querySelector('table, img, svg, canvas, video, iframe, ul, ol')) {
            return null;
        }
        const rows = [];
        // Header rows: any <tr> whose parent is <thead>, OR rows whose cells
        // are entirely <th>.
        let headerRows = 0;
        const trs = Array.from(el.querySelectorAll('tr'));
        if (trs.length === 0) return null;
        for (const tr of trs) {
            const inThead = tr.parentElement && tr.parentElement.tagName === 'THEAD';
            const cellsRaw = Array.from(tr.children).filter(
                (c) => c.tagName === 'TD' || c.tagName === 'TH'
            );
            if (cellsRaw.length === 0) continue;
            const allTh = cellsRaw.every((c) => c.tagName === 'TH');
            if (inThead || (rows.length === 0 && allTh)) headerRows++;
            const cells = [];
            for (const c of cellsRaw) {
                if (!isInlineOnly(c)) return null;
                const ccs = getComputedStyle(c);
                const colspan = parseInt(c.getAttribute('colspan') || '1', 10) || 1;
                const rowspan = parseInt(c.getAttribute('rowspan') || '1', 10) || 1;
                cells.push({
                    text: (c.textContent || '').trim(),
                    is_header: c.tagName === 'TH',
                    colspan: colspan,
                    rowspan: rowspan,
                    color: ccs.color || 'rgb(0,0,0)',
                    background_color: ccs.backgroundColor || 'rgba(0,0,0,0)',
                    font_family: ccs.fontFamily || '',
                    font_size: ccs.fontSize || '14px',
                    font_weight: ccs.fontWeight || '400',
                    text_align: ccs.textAlign || 'start',
                    italic: ccs.fontStyle === 'italic',
                    underline: ccs.textDecorationLine && ccs.textDecorationLine.includes('underline'),
                });
            }
            rows.push(cells);
        }
        // Reject ragged tables — equal column counts required (sums of
        // colspan within a row). Tables with rowspan/colspan that don't
        // form a clean grid are too ambiguous; rasterize them.
        const widths = rows.map((r) => r.reduce((a, c) => a + c.colspan, 0));
        const w0 = widths[0];
        if (!widths.every((w) => w === w0)) return null;
        return { rows: rows, header_rows: headerRows, n_cols: w0 };
    }

    function snapshot(el, parentId, depth) {
        // Skip non-element nodes and <head>/<script>/<style>
        if (!el || el.nodeType !== 1) return null;
        const tag = el.tagName;
        if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'META' || tag === 'LINK' || tag === 'NOSCRIPT') return null;

        let r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);

        const isHidden = cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0;
        const visibleBorderWidth = (style, width, color) => {
            if (!style || style === 'none' || style === 'hidden') return 0;
            const w = parseFloat(width || '0') || 0;
            if (w <= 0) return 0;
            const c = String(color || '').replace(/\s+/g, '').toLowerCase();
            if (c === 'transparent' || c === 'rgba(0,0,0,0)') return 0;
            return w;
        };
        const topBorderWidth = visibleBorderWidth(cs.borderTopStyle, cs.borderTopWidth, cs.borderTopColor);
        const rightBorderWidth = visibleBorderWidth(cs.borderRightStyle, cs.borderRightWidth, cs.borderRightColor);
        const bottomBorderWidth = visibleBorderWidth(cs.borderBottomStyle, cs.borderBottomWidth, cs.borderBottomColor);
        const leftBorderWidth = visibleBorderWidth(cs.borderLeftStyle, cs.borderLeftWidth, cs.borderLeftColor);
        const hasLineDecoration = topBorderWidth > 0 || rightBorderWidth > 0 || bottomBorderWidth > 0 || leftBorderWidth > 0;

        // Preserve zero-thickness line elements such as <hr>,
        // height:0;border-top, and width:0;border-left. Browser geometry may
        // report them as 0px tall/wide even though the border visibly paints.
        // Give downstream matching/emission a concrete bbox sized to the
        // border stroke and available parent span.
        if (isHidden) return null;
        if ((r.width < 1 || r.height < 1) && hasLineDecoration) {
            const parentRect = el.parentElement ? el.parentElement.getBoundingClientRect() : null;
            const cssWidth = parseFloat(cs.width || '0') || 0;
            const cssHeight = parseFloat(cs.height || '0') || 0;
            const horizontalStroke = Math.max(topBorderWidth, bottomBorderWidth);
            const verticalStroke = Math.max(leftBorderWidth, rightBorderWidth);
            const fallbackW = horizontalStroke > 0
                ? Math.max(r.width, cssWidth, parentRect ? parentRect.width : 0, 1)
                : Math.max(r.width, cssWidth, verticalStroke, 1);
            const fallbackH = verticalStroke > 0 && horizontalStroke <= 0
                ? Math.max(r.height, cssHeight, parentRect ? parentRect.height : 0, 1)
                : Math.max(r.height, cssHeight, horizontalStroke, 1);
            r = { x: r.x, y: r.y, width: fallbackW, height: fallbackH };
        }
        if ((r.width < 1 || r.height < 1)) {
            // Some parents have 0 size but children render; recurse without recording.
            for (const child of el.children) snapshot(child, parentId, depth + 1);
            return null;
        }

        const id = nextId++;

        let before = null, after = null, hasBefore = false, hasAfter = false;
        let pseudoBeforeStyle = null, pseudoAfterStyle = null;
        try {
            const csb = getComputedStyle(el, '::before');
            const csa = getComputedStyle(el, '::after');
            if (isInteresting(csb.content)) {
                hasBefore = true; before = csb.content;
                pseudoBeforeStyle = pseudoSnapshot(csb);
            }
            if (isInteresting(csa.content)) {
                hasAfter = true; after = csa.content;
                pseudoAfterStyle = pseudoSnapshot(csa);
            }
        } catch (_) {}

        // Leaf-text detection: only text node children, non-empty
        let isLeafText = el.children.length === 0 && (el.textContent || '').trim().length > 0;
        // Text container: text + only inline formatting children. Emitter
        // renders as a single text frame with multiple styled runs.
        const textContainer = !isLeafText && el.children.length > 0 && isTextContainer(el);
        const runs = textContainer ? collectRuns(el) : null;
        // Mixed content: direct text + block children. The
        // `<div>Helmsworth Industries...<div class="meta-sub">Steering committee</div></div>`
        // pattern. Capture the parent's own text so the unit clusterer can
        // emit a Hybrid unit (parent's text leaf + child unit stack)
        // instead of dropping one path or the other.
        const mixedContentText = (!isLeafText && !textContainer)
            ? collectMixedContentText(el)
            : null;
        // Off-canvas: bbox sitting fully outside the slide frame plus a
        // generous slack zone. Catches sr-only spans, screen-reader hidden
        // duplicates positioned at left:-9999px, and the
        // `width:1px height:1px overflow:hidden` a11y dance. The walker
        // still records them so the unit clusterer can decide what to do
        // (today: drop), but downstream consumers can re-include via
        // `data-pptx-allow-overflow`.
        const offCanvas = isOffcanvasBbox(r);

        // SVG path count (for tier 1 complexity rule)
        let svgPathCount = 0;
        let svgShapes = null;
        if (tag === 'SVG' || tag === 'svg') {
            try {
                // Skip primitives inside <defs> — those are definitions
                // (markers / gradients-as-defs / symbols / patterns) consumed
                // by other elements, not visible drawing primitives. The
                // walker counts and emits the *visible* drawing primitives only.
                const inDefs = (n) => {
                    let p = n.parentNode;
                    while (p && p !== el) {
                        if (p.tagName && p.tagName.toLowerCase() === 'defs') return true;
                        p = p.parentNode;
                    }
                    return false;
                };
                const allShapes = Array.from(
                    el.querySelectorAll('path, polygon, polyline, circle, rect, ellipse, line')
                ).filter(s => !inDefs(s));
                // Capture <text> children too — slidify emits these as
                // textboxes alongside the freeform shapes. Without this,
                // brutalist schematics / chart axis labels / annotated
                // callouts render their boxes/lines but lose every label.
                const allTexts = Array.from(
                    el.querySelectorAll('text')
                ).filter(t => !inDefs(t));
                svgPathCount = allShapes.length;

                // Capture <linearGradient> / <radialGradient> defs so the
                // emitter can resolve `fill="url(#id)"` references into
                // native <a:gradFill> stops.  Round-2 visual-QA finding:
                // without this, the walker stored "url(#duo-bg)" verbatim,
                // the emitter's parse_color() returned None, and the
                // gradient layer rendered with no fill — silently dropping
                // the entire duotone composition (slides 22-duo-photo,
                // atlas 04-duotone-plate, atlas 08-cinemagraph).
                //
                // Output shape (one entry per gradient def):
                //   { kind: 'linear'|'radial', id, stops: [{offset, color, opacity}],
                //     x1, y1, x2, y2,             // linear (objectBB units)
                //     cx, cy, r, fx, fy }         // radial (objectBB units)
                const svgGradients = {};
                const gradientNodes = el.querySelectorAll('linearGradient, radialGradient');
                for (const g of gradientNodes) {
                    const gid = g.getAttribute('id') || '';
                    if (!gid) continue;
                    const isLinear = g.tagName === 'linearGradient';
                    const stops = [];
                    for (const st of g.querySelectorAll('stop')) {
                        const stCs = getComputedStyle(st);
                        // <stop offset> may be "0", "50%", "0.5"; normalize
                        // to 0..1.
                        let off = st.getAttribute('offset') || '0';
                        if (off.endsWith('%')) {
                            off = parseFloat(off) / 100;
                        } else {
                            off = parseFloat(off);
                        }
                        if (!Number.isFinite(off)) off = 0;
                        // <stop stop-color> as attribute or style.
                        const color = st.getAttribute('stop-color') || stCs.stopColor || '#000000';
                        // <stop stop-opacity> may be on attribute or style.
                        let op = st.getAttribute('stop-opacity');
                        if (op === null || op === '') op = stCs.stopOpacity || '1';
                        op = parseFloat(op);
                        if (!Number.isFinite(op)) op = 1;
                        stops.push({ offset: Math.max(0, Math.min(1, off)),
                                      color: color, opacity: Math.max(0, Math.min(1, op)) });
                    }
                    // Geometric attributes — default to objectBoundingBox 0..1
                    // ranges per SVG spec when not specified.
                    const def = { kind: isLinear ? 'linear' : 'radial', id: gid, stops };
                    if (isLinear) {
                        def.x1 = parseFloat(g.getAttribute('x1') || '0') || 0;
                        def.y1 = parseFloat(g.getAttribute('y1') || '0') || 0;
                        def.x2 = parseFloat(g.getAttribute('x2') || '1') || 0;
                        def.y2 = parseFloat(g.getAttribute('y2') || '0') || 0;
                    } else {
                        def.cx = parseFloat(g.getAttribute('cx') || '0.5') || 0.5;
                        def.cy = parseFloat(g.getAttribute('cy') || '0.5') || 0.5;
                        def.r  = parseFloat(g.getAttribute('r')  || '0.5') || 0.5;
                        def.fx = parseFloat(g.getAttribute('fx') || def.cx) || def.cx;
                        def.fy = parseFloat(g.getAttribute('fy') || def.cy) || def.cy;
                    }
                    svgGradients[gid] = def;
                }
                // Capture geometry for SVGs up to __SVG_NATIVE_PATH_BUDGET__
                // primitives (kept in sync with classifier.tier1). Real-world
                // charts (line/bar with 50+ data points + ticks + gridlines)
                // and architecture diagrams (40-80 connectors) routinely
                // exceed the older cap of 30. The classifier's simple/complex
                // SVG rules scope to a unit's direct elements only, so a
                // large overlay SVG can't absorb sibling content even when
                // its bbox spans most of the slide.
                if (svgPathCount > 0 && svgPathCount <= __SVG_NATIVE_PATH_BUDGET__) {
                    const svgRect = el.getBoundingClientRect();
                    svgShapes = [];
                    // `resolveFill` turns `url(#id)` references into the
                    // structured gradient def captured above; bare colour
                    // strings pass through untouched.  This lets the
                    // Python emitter make a single dispatch — `dict` →
                    // <a:gradFill>, `str` → <a:solidFill> — instead of
                    // re-parsing `url()` per shape.
                    const resolveFill = (raw) => {
                        if (!raw) return raw;
                        const m = String(raw).match(/^\s*url\(\s*#([^)\s]+)\s*\)\s*$/);
                        if (!m) return raw;
                        const def = svgGradients[m[1]];
                        if (!def) return raw;  // unresolved — pass through
                        // Return a fresh shallow copy so each shape carries
                        // its own object (the Python side keeps a per-shape
                        // reference, not a shared singleton).
                        return Object.assign({}, def);
                    };
                    for (const s of allShapes) {
                        const sCs = getComputedStyle(s);
                        const ss = { tag: s.tagName.toLowerCase(),
                                     fill: resolveFill(s.getAttribute('fill') || sCs.fill || 'none'),
                                     stroke: resolveFill(s.getAttribute('stroke') || sCs.stroke || 'none'),
                                     stroke_width: parseFloat(s.getAttribute('stroke-width') || sCs.strokeWidth || '0') || 0,
                                     fill_opacity: parseFloat(s.getAttribute('fill-opacity') || sCs.fillOpacity || '1'),
                                     stroke_opacity: parseFloat(s.getAttribute('stroke-opacity') || sCs.strokeOpacity || '1') };
                        if (s.tagName === 'rect') {
                            ss.x = parseFloat(s.getAttribute('x') || '0');
                            ss.y = parseFloat(s.getAttribute('y') || '0');
                            ss.w = parseFloat(s.getAttribute('width') || '0');
                            ss.h = parseFloat(s.getAttribute('height') || '0');
                            ss.rx = parseFloat(s.getAttribute('rx') || '0');
                        } else if (s.tagName === 'circle') {
                            ss.cx = parseFloat(s.getAttribute('cx') || '0');
                            ss.cy = parseFloat(s.getAttribute('cy') || '0');
                            ss.r = parseFloat(s.getAttribute('r') || '0');
                        } else if (s.tagName === 'ellipse') {
                            ss.cx = parseFloat(s.getAttribute('cx') || '0');
                            ss.cy = parseFloat(s.getAttribute('cy') || '0');
                            ss.rx = parseFloat(s.getAttribute('rx') || '0');
                            ss.ry = parseFloat(s.getAttribute('ry') || '0');
                        } else if (s.tagName === 'line') {
                            ss.x1 = parseFloat(s.getAttribute('x1') || '0');
                            ss.y1 = parseFloat(s.getAttribute('y1') || '0');
                            ss.x2 = parseFloat(s.getAttribute('x2') || '0');
                            ss.y2 = parseFloat(s.getAttribute('y2') || '0');
                        } else if (s.tagName === 'polygon' || s.tagName === 'polyline') {
                            ss.points = s.getAttribute('points') || '';
                        } else {
                            // path — skip detailed parsing for v1
                            ss.d = s.getAttribute('d') || '';
                        }
                        svgShapes.push(ss);
                    }
                    // Add <text> children alongside the shapes. Each
                    // captured with attribute coords + computed style
                    // (font / size / fill / anchor) so the emitter can
                    // place a PPTX textbox at the right pixel.
                    for (const t of allTexts) {
                        const tcs = getComputedStyle(t);
                        svgShapes.push({
                            tag: 'text',
                            text: t.textContent || '',
                            x: parseFloat(t.getAttribute('x') || '0'),
                            y: parseFloat(t.getAttribute('y') || '0'),
                            font_size: parseFloat(t.getAttribute('font-size') || tcs.fontSize || '14') || 14,
                            font_weight: t.getAttribute('font-weight') || tcs.fontWeight || '400',
                            font_family: tcs.fontFamily || 'Inter',
                            font_style: tcs.fontStyle || 'normal',
                            fill: t.getAttribute('fill') || tcs.fill || '#000',
                            text_anchor: t.getAttribute('text-anchor') || 'start',
                        });
                    }
                    // Embed _svgRect on every shape dict — JS array
                    // properties don't survive JSON serialization, but
                    // dict items do. Capture viewBox + preserveAspectRatio
                    // so the Python side can map SVG userspace units (which
                    // may be very different from viewport pixels) into the
                    // slide. Without this, an SVG with viewBox="0 0 100 100"
                    // rendered into a 1280×720 box has every coordinate off
                    // by a factor of ~12.
                    let viewBox = null;
                    try {
                        const vb = el.viewBox && el.viewBox.baseVal;
                        if (vb && (vb.width > 0 || vb.height > 0)) {
                            viewBox = [vb.x, vb.y, vb.width, vb.height];
                        } else if (el.getAttribute('viewBox')) {
                            const parts = el.getAttribute('viewBox').trim().split(/[\s,]+/).map(parseFloat);
                            if (parts.length === 4 && parts.every(n => Number.isFinite(n))) {
                                viewBox = parts;
                            }
                        }
                    } catch (_) {}
                    const preserveAspectRatio = el.getAttribute('preserveAspectRatio') || null;
                    const rect = {
                        x: svgRect.x,
                        y: svgRect.y,
                        w: svgRect.width,
                        h: svgRect.height,
                        viewBox: viewBox,
                        preserveAspectRatio: preserveAspectRatio,
                    };
                    for (const sh of svgShapes) sh._svgRect = rect;
                }
            } catch (_) {}
        }

        // <table> capture (only the <table> root; cell elements are not
        // recursed into independently).
        let tableData = null;
        if (tag === 'TABLE') {
            try { tableData = captureTable(el); } catch (_) { tableData = null; }
        }

        const ds = el.dataset || {};

        out.push({
            id,
            parent_id: parentId,
            depth,
            tag,
            cls: typeof el.className === 'string' ? el.className : (el.className && el.className.baseVal) || '',
            bbox: { x: r.x, y: r.y, w: r.width, h: r.height },
            z_index: cs.zIndex === 'auto' ? 0 : (parseInt(cs.zIndex, 10) || 0),
            transform: cs.transform || 'none',
            opacity: parseFloat(cs.opacity || '1'),
            overflow: cs.overflow || 'visible',
            background_color: cs.backgroundColor || 'rgba(0, 0, 0, 0)',
            background_image: cs.backgroundImage || 'none',
            border: cs.border || 'none',
            border_top: cs.borderTop || 'none',
            border_right: cs.borderRight || 'none',
            border_bottom: cs.borderBottom || 'none',
            border_left: cs.borderLeft || 'none',
            border_radius: cs.borderRadius || '0px',
            box_shadow: cs.boxShadow || 'none',
            filter: cs.filter || 'none',
            clip_path: cs.clipPath || 'none',
            mix_blend_mode: cs.mixBlendMode || 'normal',
            backdrop_filter: cs.backdropFilter || 'none',
            background_clip: cs.backgroundClip || 'border-box',
            mask_image: cs.maskImage || cs.webkitMaskImage || 'none',
            background_blend_mode: cs.backgroundBlendMode || 'normal',
            text: isLeafText ? el.textContent : (textContainer ? el.textContent : null),
            is_text_container: textContainer,
            mixed_content_text: mixedContentText,
            is_offcanvas: offCanvas,
            display: cs.display || 'inline',
            padding_top: cs.paddingTop || '0px',
            padding_right: cs.paddingRight || '0px',
            padding_bottom: cs.paddingBottom || '0px',
            padding_left: cs.paddingLeft || '0px',
            runs: runs,
            font_family: cs.fontFamily || '',
            font_size: cs.fontSize || '16px',
            font_weight: cs.fontWeight || '400',
            color: cs.color || 'rgb(0, 0, 0)',
            text_align: cs.textAlign || 'start',
            line_height: cs.lineHeight || 'normal',
            letter_spacing: cs.letterSpacing || 'normal',
            text_transform: cs.textTransform || 'none',
            text_shadow: cs.textShadow || 'none',
            writing_mode: cs.writingMode || 'horizontal-tb',
            aspect_ratio: cs.aspectRatio || 'auto',
            grid_template_columns: cs.gridTemplateColumns || 'none',
            gap: cs.gap || 'normal',
            object_fit: cs.objectFit || 'fill',
            object_position: cs.objectPosition || '50% 50%',
            has_before: hasBefore,
            has_after: hasAfter,
            before_content: before,
            after_content: after,
            pseudo_before_style: pseudoBeforeStyle,
            pseudo_after_style: pseudoAfterStyle,
            is_canvas: tag === 'CANVAS',
            is_svg: tag === 'SVG' || tag === 'svg',
            is_img: tag === 'IMG',
            is_video: tag === 'VIDEO',
            img_src: tag === 'IMG' ? (el.currentSrc || el.src || null) : null,
            animation_name: cs.animationName || 'none',
            animation_duration: cs.animationDuration || '0s',
            transition_duration: cs.transitionDuration || '0s',
            svg_path_count: svgPathCount,
            svg_shapes: svgShapes,
            is_table: tableData !== null,
            table_data: tableData,
            pptx_role: ds.pptxRole || null,
            pptx_rasterize: ds.pptxRasterize === 'true',
            pptx_skip: ds.pptxSkip === 'true',
            pptx_text: ds.pptxText || null,
            pptx_notes: ds.pptxNotes || null,
            pptx_record: ds.pptxRecord || null,
            aria_label: el.getAttribute('aria-label'),
            stable_selector: buildStableSelector(el),
            decorate_hint: ds.slidifyDecorate || '',
            data_atom: ds.atom || '',
            allow_overflow: ds.pptxAllowOverflow === 'true',
        });

        // If we captured this element as a text container, don't recurse —
        // the runs already include all inline children's text and styling.
        // Same for tables we captured natively: their cells are owned by the
        // NativeTable emit op, not by the visual-unit clusterer.
        if (!textContainer && !(tag === 'TABLE' && tableData !== null)) {
            for (const child of el.children) snapshot(child, id, depth + 1);
        }
        return id;
    }

    snapshot(document.body, null, 0);
    return out;
}
"""


WALKER_JS = _WALKER_JS_TEMPLATE.replace(
    "__SVG_NATIVE_PATH_BUDGET__", str(SVG_NATIVE_PATH_BUDGET)
)

# Variant that accepts a CSS selector for the walk root via Playwright's
# page.evaluate argument passing: `(selector) => { ... }`.
_WALKER_SCOPED_JS = (
    _WALKER_JS_TEMPLATE
    .replace("() => {", "(selector) => {", 1)
    .replace(
        "snapshot(document.body, null, 0);",
        "snapshot(selector ? (document.querySelector(selector) || document.body) : document.body, null, 0);",
    )
    .replace("__SVG_NATIVE_PATH_BUDGET__", str(SVG_NATIVE_PATH_BUDGET))
)


async def walk(page: Page, root_selector: str | None = None) -> list[DomElement]:
    """Run the in-page walker and parse results into DomElement models."""
    if root_selector:
        raw: list[dict[str, Any]] = await page.evaluate(_WALKER_SCOPED_JS, root_selector)
    else:
        raw = await page.evaluate(WALKER_JS)
    elements: list[DomElement] = []
    for entry in raw:
        bb = entry["bbox"]
        elements.append(
            DomElement(
                id=entry["id"],
                parent_id=entry["parent_id"],
                depth=entry["depth"],
                tag=entry["tag"],
                cls=entry["cls"] or "",
                bbox=BoundingBox(x=bb["x"], y=bb["y"], w=bb["w"], h=bb["h"]),
                z_index=entry["z_index"],
                transform=entry["transform"],
                opacity=entry["opacity"],
                overflow=entry["overflow"],
                background_color=entry["background_color"],
                background_image=entry["background_image"],
                border=entry["border"],
                border_top=entry["border_top"],
                border_right=entry.get("border_right", "none"),
                border_bottom=entry.get("border_bottom", "none"),
                border_left=entry.get("border_left", "none"),
                border_radius=entry["border_radius"],
                box_shadow=entry["box_shadow"],
                filter=entry["filter"],
                clip_path=entry["clip_path"],
                mix_blend_mode=entry.get("mix_blend_mode", "normal"),
                backdrop_filter=entry.get("backdrop_filter", "none"),
                background_clip=entry.get("background_clip", "border-box"),
                mask_image=entry.get("mask_image", "none"),
                background_blend_mode=entry.get("background_blend_mode", "normal"),
                text=entry["text"],
                is_text_container=entry.get("is_text_container", False),
                mixed_content_text=entry.get("mixed_content_text"),
                is_offcanvas=entry.get("is_offcanvas", False),
                display=entry.get("display", "inline"),
                padding_top=entry.get("padding_top", "0px"),
                padding_right=entry.get("padding_right", "0px"),
                padding_bottom=entry.get("padding_bottom", "0px"),
                padding_left=entry.get("padding_left", "0px"),
                runs=[
                    TextRun(
                        text=r.get("text", ""),
                        font_family=r.get("font_family", ""),
                        font_size=r.get("font_size", "16px"),
                        font_weight=r.get("font_weight", "400"),
                        color=r.get("color", "rgb(0, 0, 0)"),
                        background_color=r.get("background_color", "rgba(0, 0, 0, 0)"),
                        background_image=r.get("background_image", "none"),
                        letter_spacing=r.get("letter_spacing", "normal"),
                        text_transform=r.get("text_transform", "none"),
                        italic=r.get("italic", False),
                        underline=r.get("underline", False),
                        is_break=r.get("is_break", False),
                        line_boxes=[
                            BoundingBox(
                                x=lb["x"], y=lb["y"], w=lb["w"], h=lb["h"]
                            )
                            for lb in (r.get("line_boxes") or [])
                        ],
                        background_boxes=[
                            BoundingBox(
                                x=lb["x"], y=lb["y"], w=lb["w"], h=lb["h"]
                            )
                            for lb in (r.get("background_boxes") or [])
                        ],
                    )
                    for r in (entry.get("runs") or [])
                ] if entry.get("runs") else None,
                font_family=entry["font_family"],
                font_size=entry["font_size"],
                font_weight=entry["font_weight"],
                color=entry["color"],
                text_align=entry["text_align"],
                line_height=entry["line_height"],
                letter_spacing=entry.get("letter_spacing", "normal"),
                text_transform=entry.get("text_transform", "none"),
                text_shadow=entry.get("text_shadow", "none"),
                writing_mode=entry.get("writing_mode", "horizontal-tb"),
                aspect_ratio=entry.get("aspect_ratio", "auto"),
                grid_template_columns=entry.get("grid_template_columns", "none"),
                gap=entry.get("gap", "normal"),
                object_fit=entry.get("object_fit", "fill"),
                object_position=entry.get("object_position", "50% 50%"),
                has_before=entry["has_before"],
                has_after=entry["has_after"],
                before_content=entry["before_content"],
                after_content=entry["after_content"],
                pseudo_before_style=entry.get("pseudo_before_style"),
                pseudo_after_style=entry.get("pseudo_after_style"),
                is_canvas=entry["is_canvas"],
                is_svg=entry["is_svg"],
                is_img=entry["is_img"],
                is_video=entry["is_video"],
                img_src=entry["img_src"],
                animation_name=entry.get("animation_name", "none"),
                animation_duration=entry.get("animation_duration", "0s"),
                transition_duration=entry.get("transition_duration", "0s"),
                svg_path_count=entry["svg_path_count"],
                svg_shapes=entry.get("svg_shapes"),
                is_table=entry.get("is_table", False),
                table_data=entry.get("table_data"),
                pptx_role=entry["pptx_role"],
                pptx_rasterize=entry["pptx_rasterize"],
                pptx_skip=entry["pptx_skip"],
                pptx_text=entry["pptx_text"],
                pptx_notes=entry["pptx_notes"],
                pptx_record=entry.get("pptx_record"),
                aria_label=entry["aria_label"],
                stable_selector=entry["stable_selector"],
                decorate_hint=entry.get("decorate_hint", ""),
                data_atom=entry.get("data_atom", ""),
                allow_overflow=entry.get("allow_overflow", False),
            )
        )
    return elements
