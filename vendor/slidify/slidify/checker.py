"""Pre-flight checker for slide HTML.

`slidify check <slide.html>` is the LLM-in-the-loop counterpart to
`slidify convert`: it walks the same HTML *without* compiling to PPTX
and returns a structured report telling the caller what will and won't
convert cleanly. Two modes:

  * **fast** (default): static HTML scan only. No Chromium. Returns
    self-containment + external-asset inventory + risky-CSS inventory
    + declared atom hints. ~50 ms per slide.
  * **deep** (``--deep``): also runs the full convert pipeline with
    ``write=False`` so the caller can see the matcher's actual
    decisions, ``native_area_ratio``, ``unmatched_signatures`` and
    ``escape_rate`` predictions. ~1-3 s per slide.

The fast mode is what an LLM uses in its inner loop ("regenerate this
slide if it has any external assets") — Chromium round-trips at every
LLM message would be a non-starter latency-wise. Deep mode is what CI
runs against a corpus to track regressions.

Output shape (JSON):

  {
    "self_contained": true,
    "external_assets": [
      { "kind": "script-src", "url": "https://cdn.tailwindcss.com" }
    ],
    "risky_css": [
      { "property": "backdrop-filter", "value": "blur(20px)", "selector": "div.glass",
        "reason": "PPTX has no native backdrop-filter; routes to raster." }
    ],
    "atom_hints": ["bg.aurora-band", "comp.hero-investor"],
    "warnings": [...],
    "deep": {                     // present only with --deep
       "native_area_ratio": 0.94,
       "unmatched_signatures": [...],
       "escape_rate": {...},
       ...
    }
  }

LLMs emitting slide HTML are advised to surface any non-empty
``external_assets`` or any ``risky_css`` row without an accompanying
opt-in (`data-slidify-allow-raster="true"`) as a regeneration trigger.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# CSS properties that have no native PPTX representation. The matcher
# may still emit something for the unit, but anything declaring these
# almost certainly routes to raster fallback (or loses fidelity).
#
# Keep this list curated, not exhaustive — false positives erode trust.
_RISKY_CSS_PROPS: dict[str, str] = {
    "backdrop-filter": (
        "PPTX has no native backdrop-filter; routes to raster fallback."
    ),
    "-webkit-backdrop-filter": (
        "Vendor-prefixed backdrop-filter; same caveat as backdrop-filter."
    ),
    "mix-blend-mode": (
        "Blend modes beyond 'normal' aren't supported natively; raster fallback."
    ),
    "background-blend-mode": (
        "Multi-background Porter-Duff blends aren't expressible in PPTX."
    ),
    "filter": (
        "CSS `filter:` (blur/saturate/etc.) routes to raster unless it resolves to "
        "drop-shadow which we map to native shadow effects."
    ),
    "clip-path": (
        "Arbitrary clip-path requires custGeom + raster fallback for non-rect "
        "shapes; ensure the shape resolves to a rect or use data-atom='mask.*'."
    ),
    "mask-image": (
        "Native masks are not supported on text/shapes; pictures use a soft "
        "alpha overlay fallback."
    ),
    "-webkit-mask-image": "Vendor-prefixed mask-image; same caveat.",
}

# CSS properties that COULD be native but only with specific values.
# Format: { property: (allowed_value_regex, rejection_reason) }
_VALUE_GATED_CSS: dict[str, tuple[re.Pattern, str]] = {
    "transform": (
        re.compile(
            r"^(?:none|translate(?:X|Y|3d)?\([^)]*\)|"
            r"scale(?:X|Y|3d)?\([^)]*\)|"
            r"rotate\([^)]*\)|"
            r"matrix\([^)]*\))(?:\s+(?:translate|scale|rotate|matrix)\([^)]*\))*$"
        ),
        "transform: only translate/scale/rotate/matrix are native; "
        "skew/perspective force raster.",
    ),
}


@dataclass
class ExternalAsset:
    kind: str   # 'script-src', 'link-stylesheet', 'img-src', 'iframe-src', 'fetch-call'
    url: str
    location: str = ""  # tag + optional id / class for context


@dataclass
class RiskyCss:
    property: str
    value: str
    selector: str
    reason: str


@dataclass
class CheckReport:
    self_contained: bool = True
    external_assets: list[ExternalAsset] = field(default_factory=list)
    risky_css: list[RiskyCss] = field(default_factory=list)
    atom_hints: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    deep: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "self_contained": self.self_contained,
            "external_assets": [
                {"kind": a.kind, "url": a.url, "location": a.location}
                for a in self.external_assets
            ],
            "risky_css": [
                {
                    "property": r.property,
                    "value": r.value,
                    "selector": r.selector,
                    "reason": r.reason,
                }
                for r in self.risky_css
            ],
            "atom_hints": list(self.atom_hints),
            "warnings": list(self.warnings),
            **({"deep": self.deep} if self.deep is not None else {}),
        }


# ---------------------------------------------------------------------------
# Static scan (fast path — no browser)
# ---------------------------------------------------------------------------


_TAG_SRC_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # (kind, regex_with_url_capturing_group, attribute_name)
    ("script-src", re.compile(r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE), "src"),
    (
        "link-stylesheet",
        re.compile(
            r"<link\b[^>]*\brel=[\"']stylesheet[\"'][^>]*\bhref=[\"']([^\"']+)[\"']",
            re.IGNORECASE,
        ),
        "href",
    ),
    (
        "link-stylesheet",
        re.compile(
            r"<link\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*\brel=[\"']stylesheet[\"']",
            re.IGNORECASE,
        ),
        "href",
    ),
    ("img-src", re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE), "src"),
    ("iframe-src", re.compile(r"<iframe\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE), "src"),
]

# JS-emitted runtime fetches. Not a complete static analysis (we can't
# reliably parse JS), but catches the obvious LLM antipattern of
# `fetch('https://api.example.com/...')` inside a slide.
_FETCH_LIKE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bfetch\s*\(\s*[\"']([^\"']+)[\"']", re.IGNORECASE),
    re.compile(r"\bXMLHttpRequest\b", re.IGNORECASE),
    re.compile(
        r"\bnew\s+Image\s*\(\s*\)[^;]*\.src\s*=\s*[\"']([^\"']+)[\"']",
        re.IGNORECASE,
    ),
]

_DATA_ATOM_RE = re.compile(
    r"data-atom\s*=\s*"
    r"(?:\"([^\"]+)\"|'([^']+)'|([^\s\"'>=`]+))",
    re.IGNORECASE,
)


def _is_external_url(url: str) -> bool:
    """True for URLs that need a network/file-system fetch at convert time.

    `data:` URIs are inline. Relative paths are resolved against the
    slide's directory; we treat them as "self-contained" provided the
    file ships alongside the HTML.
    """
    if not url:
        return False
    u = url.strip()
    if u.startswith(("data:", "blob:", "#", "about:")):
        return False
    if u.startswith(("http://", "https://", "//", "ftp://", "ws://", "wss://")):
        return True
    return False


def _scan_external_assets(html: str) -> list[ExternalAsset]:
    out: list[ExternalAsset] = []
    seen: set[tuple[str, str]] = set()
    for kind, pat, _attr in _TAG_SRC_PATTERNS:
        for m in pat.finditer(html):
            url = m.group(1)
            if not _is_external_url(url):
                continue
            key = (kind, url)
            if key in seen:
                continue
            seen.add(key)
            tag_open = m.group(0)[: 80]
            out.append(ExternalAsset(kind=kind, url=url, location=tag_open))
    for pat in _FETCH_LIKE_PATTERNS:
        for m in pat.finditer(html):
            try:
                url = m.group(1)
            except IndexError:
                url = ""
            if url and not _is_external_url(url):
                continue
            key = ("fetch-call", url or "<XHR>")
            if key in seen:
                continue
            seen.add(key)
            out.append(
                ExternalAsset(kind="fetch-call", url=url or "<XHR>", location="<inline-js>")
            )
    return out


# Iterate every CSS declaration (inside `<style>` blocks AND `style="…"`
# inline attributes). For each declaration, check the property + value
# against the risky-CSS table and the value-gated table.

_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>([\s\S]*?)</style>", re.IGNORECASE)
_INLINE_STYLE_RE = re.compile(r"\bstyle=[\"']([^\"']+)[\"']", re.IGNORECASE)
_RULE_RE = re.compile(
    r"([^{}/]+?)\s*\{\s*([^{}]*?)\s*\}",
    re.MULTILINE | re.DOTALL,
)
_DECL_RE = re.compile(r"([A-Za-z_-][\w-]*)\s*:\s*([^;]+?)\s*(?:;|$)")


def _check_decls(decls: str, selector: str) -> list[RiskyCss]:
    out: list[RiskyCss] = []
    for m in _DECL_RE.finditer(decls):
        prop = m.group(1).lower().strip()
        val = m.group(2).strip()
        if prop in _RISKY_CSS_PROPS:
            out.append(RiskyCss(property=prop, value=val, selector=selector,
                                reason=_RISKY_CSS_PROPS[prop]))
            continue
        if prop in _VALUE_GATED_CSS:
            allowed_re, reason = _VALUE_GATED_CSS[prop]
            if not allowed_re.match(val):
                out.append(RiskyCss(property=prop, value=val, selector=selector, reason=reason))
    return out


def _scan_risky_css(html: str) -> list[RiskyCss]:
    out: list[RiskyCss] = []
    for m in _STYLE_BLOCK_RE.finditer(html):
        block = m.group(1)
        for rule_m in _RULE_RE.finditer(block):
            selector = rule_m.group(1).strip().split("\n")[0][:80]
            decls = rule_m.group(2)
            out.extend(_check_decls(decls, selector))
    for m in _INLINE_STYLE_RE.finditer(html):
        decls = m.group(1)
        # Try to grab the parent tag's `class=` for context.
        start = max(0, m.start() - 200)
        ctx = html[start: m.start()]
        cls_m = re.search(r'class=[\"\']([^\"\']*)[\"\']\s*$', ctx)
        selector = (
            f"[inline]{(' .' + cls_m.group(1)) if cls_m else ''}"
        ).strip()[:80]
        out.extend(_check_decls(decls, selector))
    return out


def _scan_atom_hints(html: str) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in _DATA_ATOM_RE.finditer(html):
        v = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if v and v not in seen_set:
            seen_set.add(v)
            seen.append(v)
    return seen


def check_html(html: str) -> CheckReport:
    """Static (no-Chromium) check. Fast; suitable for an LLM inner loop."""
    rep = CheckReport()
    rep.external_assets = _scan_external_assets(html)
    rep.self_contained = len(rep.external_assets) == 0
    rep.risky_css = _scan_risky_css(html)
    rep.atom_hints = _scan_atom_hints(html)

    # Lint warnings — not blocking but informative.
    if "<iframe" in html.lower():
        rep.warnings.append("contains <iframe> — slidify cannot embed iframes; route to raster.")
    if not re.search(r"<!doctype\s+html", html, re.IGNORECASE):
        rep.warnings.append("no <!doctype html> — slidify expects a full HTML document.")
    if rep.risky_css and not rep.atom_hints:
        rep.warnings.append(
            "risky CSS detected but no `data-atom='…'` hint; "
            "consider tagging the element so the matcher can route it natively."
        )
    return rep


# ---------------------------------------------------------------------------
# Deep scan — runs the full convert path with write=False
# ---------------------------------------------------------------------------


def check_html_deep(html: str) -> CheckReport:
    """Static check + a full convert pass that returns matcher-side
    metrics. Slower (Chromium round-trip); suitable for CI / corpus runs.
    """
    import asyncio
    import tempfile

    from slidify.api import ConversionConfig, convert

    rep = check_html(html)

    # Run convert into a temp PPTX so we get real ConversionResult metrics
    # (write=False isn't a flag today; tempfile + discard achieves the
    # same surface signal without bypassing slide-emit code paths).
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "discard.pptx"
        try:
            cfg = ConversionConfig.fast()
            result = asyncio.run(convert(html, out, cfg))
        except Exception as e:
            rep.warnings.append(f"deep-check convert failed: {e}")
            return rep

    rep.deep = {
        "native_area_ratio": result.native_area_ratio,
        "n_slides": result.n_slides,
        "unmatched_signatures": [
            {
                "sig_hash": s.sig_hash,
                "n_occurrences": s.n_occurrences,
                "sample_classes": s.sample_classes,
                "sample_text": s.sample_text[:80],
            }
            for s in result.unmatched_signatures[:20]
        ],
        "escape_rate": (
            result.escape_rate.model_dump(by_alias=True)
            if hasattr(result.escape_rate, "model_dump")
            else {}
        ),
        "coverage_gaps": [
            {
                "tag": g.tag,
                "cls": g.cls,
                "sample_text": g.sample_text[:80],
                "overlap_ratio": g.overlap_ratio,
            }
            for g in (getattr(result, "coverage_gaps", None) or [])[:20]
        ],
    }
    return rep


__all__ = [
    "CheckReport",
    "ExternalAsset",
    "RiskyCss",
    "check_html",
    "check_html_deep",
]
