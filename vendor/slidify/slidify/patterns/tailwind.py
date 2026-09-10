"""Tailwind utility class catalog and class-string parser.

Loads the JSON catalog at import time and exposes lookup helpers. Class names
are split on whitespace, stripped of variant prefixes (`md:`, `hover:`,
`dark:`, etc.) — slides are static, so variant classes are noise here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

# Variant prefixes we strip (responsive, state, dark mode, etc.). Slides are
# static — we never hit a hover state, never resize, so variant classes are
# inert decoration in our context.
_VARIANT_RE = re.compile(
    r"^(?:sm|md|lg|xl|2xl|hover|focus|active|disabled|dark|light|group-hover|peer-hover|first|last|odd|even|print|motion-safe|motion-reduce):"
)


def _strip_variants(token: str) -> str:
    while True:
        m = _VARIANT_RE.match(token)
        if not m:
            break
        token = token[m.end():]
    return token


def _split_classes(class_string: str | None) -> list[str]:
    if not class_string:
        return []
    return [_strip_variants(t) for t in class_string.split() if t]


@dataclass
class ResolvedClass:
    raw: str
    family: str  # 'color-bg' | 'color-text' | 'radius' | 'shadow' | 'font-size' | ...
    value: Any  # depends on family


@dataclass
class TailwindCatalog:
    """Dictionaries indexed by Tailwind utility-class form."""

    colors: dict[str, str] = field(default_factory=dict)
    border_radius: dict[str, str] = field(default_factory=dict)
    shadow: dict[str, str] = field(default_factory=dict)
    font_size: dict[str, dict[str, float]] = field(default_factory=dict)
    font_weight: dict[str, int] = field(default_factory=dict)
    tracking: dict[str, float] = field(default_factory=dict)
    leading: dict[str, float] = field(default_factory=dict)
    spacing_px: dict[str, int] = field(default_factory=dict)
    gradient_direction: dict[str, int] = field(default_factory=dict)
    opacity: dict[str, float] = field(default_factory=dict)
    rasterize_only: set[str] = field(default_factory=set)

    # ---- Color lookups ----------------------------------------------------

    def lookup_color(self, name: str) -> str | None:
        """Resolve a token like 'indigo-500' or 'white' to a hex value."""
        return self.colors.get(name)

    # ---- Token classification ---------------------------------------------

    def classify_token(self, token: str) -> ResolvedClass | None:
        """Map a single (variant-stripped) Tailwind token to a ResolvedClass.

        Returns None for tokens we don't recognize (bias toward silence so the
        rest of the pipeline still runs).
        """
        if token in self.rasterize_only:
            return ResolvedClass(raw=token, family="rasterize_only", value=True)
        if token in self.shadow:
            return ResolvedClass(raw=token, family="shadow", value=self.shadow[token])
        if token in self.border_radius:
            return ResolvedClass(raw=token, family="radius", value=self.border_radius[token])
        if token in self.font_size:
            return ResolvedClass(raw=token, family="font-size", value=self.font_size[token])
        if token in self.font_weight:
            return ResolvedClass(raw=token, family="font-weight", value=self.font_weight[token])
        if token in self.tracking:
            return ResolvedClass(raw=token, family="tracking", value=self.tracking[token])
        if token in self.leading:
            return ResolvedClass(raw=token, family="leading", value=self.leading[token])
        if token in self.gradient_direction:
            return ResolvedClass(raw=token, family="gradient-direction", value=self.gradient_direction[token])
        if token in self.opacity:
            return ResolvedClass(raw=token, family="opacity", value=self.opacity[token])

        # Color families: bg-*, text-*, border-*, from-*, to-*, via-*
        for prefix, family in (
            ("bg-", "color-bg"),
            ("text-", "color-text"),
            ("border-", "color-border"),
            ("from-", "color-from"),
            ("via-", "color-via"),
            ("to-", "color-to"),
            ("ring-", "color-ring"),
            ("decoration-", "color-decoration"),
        ):
            if token.startswith(prefix):
                rest = token[len(prefix):]
                col = self.lookup_color(rest)
                if col is not None:
                    return ResolvedClass(raw=token, family=family, value=col)
                # /opacity suffix handling: bg-white/10
                if "/" in rest:
                    base, _, op = rest.partition("/")
                    base_col = self.lookup_color(base)
                    try:
                        op_pct = float(op) / 100.0 if op else 1.0
                    except ValueError:
                        op_pct = 1.0
                    if base_col is not None:
                        return ResolvedClass(
                            raw=token,
                            family=family,
                            value={"hex": base_col, "alpha": op_pct},
                        )
                # text-* may also be a font-size token covered above
        # Spacing utilities: p-{n}, m-{n}, gap-{n}, w-{n}, h-{n}, etc.
        for prefix, family in (
            ("p-", "padding"), ("px-", "padding-x"), ("py-", "padding-y"),
            ("pt-", "padding-t"), ("pb-", "padding-b"),
            ("pl-", "padding-l"), ("pr-", "padding-r"),
            ("m-", "margin"), ("mx-", "margin-x"), ("my-", "margin-y"),
            ("mt-", "margin-t"), ("mb-", "margin-b"),
            ("ml-", "margin-l"), ("mr-", "margin-r"),
            ("gap-", "gap"), ("space-x-", "space-x"), ("space-y-", "space-y"),
            ("w-", "width"), ("h-", "height"),
        ):
            if token.startswith(prefix):
                rest = token[len(prefix):]
                if rest in self.spacing_px:
                    return ResolvedClass(
                        raw=token, family=family, value=self.spacing_px[rest]
                    )

        return None

    def classify_string(self, class_string: str | None) -> list[ResolvedClass]:
        """Classify every recognized token; drop unrecognized."""
        return [
            r
            for r in (self.classify_token(t) for t in _split_classes(class_string))
            if r is not None
        ]

    def has_token(self, class_string: str | None, token: str) -> bool:
        return token in _split_classes(class_string)

    def has_any(self, class_string: str | None, tokens: list[str]) -> bool:
        seen = _split_classes(class_string)
        return any(t in seen for t in tokens)

    def has_rasterize_only(self, class_string: str | None) -> bool:
        return any(
            t in self.rasterize_only for t in _split_classes(class_string)
        )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _load_json() -> dict:
    pkg = resources.files("slidify.patterns.data")
    return json.loads((pkg / "tailwind.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def get_default_catalog() -> TailwindCatalog:
    raw = _load_json()
    return TailwindCatalog(
        colors={k: v for k, v in raw.get("colors", {}).items()},
        border_radius=raw.get("border_radius", {}),
        shadow=raw.get("shadow", {}),
        font_size=raw.get("font_size", {}),
        font_weight=raw.get("font_weight", {}),
        tracking=raw.get("tracking", {}),
        leading=raw.get("leading", {}),
        spacing_px=raw.get("spacing_px", {}),
        gradient_direction=raw.get("gradient_direction", {}),
        opacity=raw.get("opacity", {}),
        rasterize_only=set(raw.get("rasterize_only", [])),
    )
