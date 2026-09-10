"""Tests for the Tailwind pattern database."""

from __future__ import annotations

from slidify.models import BoundingBox, DomElement, VisualUnit
from slidify.patterns import classify_tier0, get_default_catalog
from slidify.patterns.tailwind import _split_classes

# ---- Catalog -----------------------------------------------------------------


def test_catalog_loads_colors():
    cat = get_default_catalog()
    # Tailwind 4 palette values (re-tuned from v3 — v3 used #6366f1).
    indigo = cat.lookup_color("indigo-500")
    assert indigo is not None
    assert indigo.startswith("#") and len(indigo) == 7
    assert cat.lookup_color("white") == "#ffffff"
    assert cat.lookup_color("nonexistent") is None


def test_catalog_classify_color_tokens():
    cat = get_default_catalog()
    r = cat.classify_token("bg-indigo-500")
    assert r is not None
    assert r.family == "color-bg"
    assert isinstance(r.value, str) and r.value.startswith("#")


def test_catalog_classify_color_with_opacity():
    cat = get_default_catalog()
    r = cat.classify_token("bg-white/10")
    assert r is not None
    assert r.family == "color-bg"
    assert r.value["hex"] == "#ffffff"
    assert abs(r.value["alpha"] - 0.10) < 1e-9


def test_catalog_classify_radius_shadow_typography():
    cat = get_default_catalog()
    assert cat.classify_token("rounded-2xl").value == "16px"
    assert "0 20px 25px" in cat.classify_token("shadow-xl").value
    assert cat.classify_token("text-4xl").value["size_px"] == 36
    assert cat.classify_token("font-bold").value == 700
    assert cat.classify_token("tracking-widest").value == 0.1


def test_catalog_classify_spacing():
    cat = get_default_catalog()
    assert cat.classify_token("p-6").value == 24
    assert cat.classify_token("gap-4").value == 16
    assert cat.classify_token("w-96").value == 384


def test_catalog_classify_gradient_direction():
    cat = get_default_catalog()
    assert cat.classify_token("bg-gradient-to-br").value == 135


def test_strip_variant_prefixes():
    assert _split_classes("md:bg-indigo-500 hover:rounded-2xl dark:text-white") == [
        "bg-indigo-500",
        "rounded-2xl",
        "text-white",
    ]


def test_unknown_tokens_silent():
    cat = get_default_catalog()
    assert cat.classify_token("totally-fake-class") is None


# ---- Recipes -----------------------------------------------------------------


def _el(
    eid: int,
    tag: str,
    cls: str = "",
    text: str | None = None,
    *,
    bbox: tuple[float, float, float, float] = (0, 0, 200, 32),
    bg: str = "rgba(0, 0, 0, 0)",
    bg_image: str = "none",
    radius: str = "0px",
    shadow: str = "none",
    font_size: str = "16px",
    font_weight: str = "400",
    transform: str = "none",
) -> DomElement:
    x, y, w, h = bbox
    return DomElement(
        id=eid,
        parent_id=None,
        depth=0,
        tag=tag,
        cls=cls,
        bbox=BoundingBox(x=x, y=y, w=w, h=h),
        background_color=bg,
        background_image=bg_image,
        border_radius=radius,
        box_shadow=shadow,
        transform=transform,
        font_size=font_size,
        font_weight=font_weight,
        text=text,
        stable_selector=f"#e{eid}",
    )


def _unit(elements: list[DomElement], children: list[VisualUnit] | None = None) -> VisualUnit:
    bb = elements[0].bbox if elements else BoundingBox(x=0, y=0, w=10, h=10)
    return VisualUnit(
        id=f"u_{elements[0].id}" if elements else "u_x",
        bbox=bb,
        elements=elements,
        children=children or [],
        anchor_element_id=elements[0].id if elements else None,
    )


def test_recipe_kicker_fires_on_tracking_uppercase():
    cat = get_default_catalog()
    el = _el(
        1, "DIV",
        cls="text-xs uppercase tracking-widest",
        text="QUARTERLY UPDATE",
        bbox=(0, 0, 220, 18),
        font_size="12px",
    )
    d = classify_tier0(_unit([el]), cat)
    assert d is not None
    assert d.metadata.get("recipe") == "kicker"


def test_recipe_pill_fires_on_rounded_full():
    cat = get_default_catalog()
    el = _el(
        1, "DIV",
        cls="rounded-full bg-white/10 px-3 py-1",
        text="LIVE",
        bbox=(0, 0, 80, 28),
        bg="rgba(255, 255, 255, 0.1)",
        radius="9999px",
        font_size="12px",
    )
    d = classify_tier0(_unit([el]), cat)
    assert d is not None
    assert d.metadata.get("recipe") == "pill"


def test_recipe_rotated_dot_fires_as_native_shape():
    cat = get_default_catalog()
    el = _el(
        1,
        "DIV",
        cls="dot",
        bbox=(0, 0, 60, 60),
        bg="rgb(45, 91, 255)",
        radius="9999px",
        transform="matrix(1, 0, 0, 1, 980, 0)",
    )
    d = classify_tier0(_unit([el]), cat)
    assert d is not None
    assert d.kind.value == "native_shape"
    assert d.metadata.get("recipe") == "dec_glyph_rotated"


def test_recipe_blur_forces_raster():
    cat = get_default_catalog()
    el = _el(1, "DIV", cls="backdrop-blur-md bg-white/5 rounded-2xl", text="card body")
    d = classify_tier0(_unit([el]), cat)
    assert d is not None
    assert d.kind.value == "raster"
    assert d.metadata.get("recipe") == "rasterize_only_class"


def test_recipe_gradient_card():
    cat = get_default_catalog()
    el = _el(
        1, "DIV",
        cls="rounded-xl",
        bg_image="linear-gradient(135deg, rgb(99, 102, 241) 0%, rgb(236, 72, 153) 100%)",
        radius="12px",
    )
    d = classify_tier0(_unit([el]), cat)
    assert d is not None
    assert d.metadata.get("recipe") in ("gradient_card", "gradient_text_card")


def test_recipe_hairline_divider():
    cat = get_default_catalog()
    el = _el(
        1, "DIV",
        cls="bg-zinc-800",
        bbox=(0, 0, 200, 1),
        bg="rgb(63, 63, 70)",
    )
    d = classify_tier0(_unit([el]), cat)
    assert d is not None
    assert d.metadata.get("recipe") == "hairline"


def test_recipes_match_long_paragraph():
    """Long body-copy paragraphs should be classified directly by the
    body-paragraph / long-paragraph recipes — that was the largest unmatched
    cluster in the showcase deck before patterns were added for it."""
    cat = get_default_catalog()
    el = _el(
        1, "P",
        text="The quick brown fox jumps over the lazy dog, repeatedly and at length.",
        bbox=(0, 0, 600, 24),
        font_size="16px",
    )
    d = classify_tier0(_unit([el]), cat)
    assert d is not None
    assert d.metadata.get("recipe") in ("body_paragraph", "long_paragraph", "short_label")


def test_recipes_silent_on_too_short_text():
    """Tiny single-word fragments shouldn't fire a pattern (defer to tier 1)."""
    cat = get_default_catalog()
    el = _el(
        1, "P", text="x", bbox=(0, 0, 12, 12), font_size="9px", font_weight="100"
    )
    d = classify_tier0(_unit([el]), cat)
    # 9px font is below body-paragraph minimum and below short-label minimum.
    assert d is None
