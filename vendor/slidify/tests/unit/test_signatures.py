"""Tests for `slidify.patterns.signatures` Phase 2b axes.

These tests pin down the structural signature alphabet so the cache key
(``signature_hash``) discriminates designer-grade intent. Each test covers
one of the new tags appended to ``_anchor_kind`` (gradient stops, shadow
elevation, radius bucket, asymmetric border, clip-path preset, font face
register, letter-spacing register, writing mode, explicit aspect ratio,
grid container, text-shadow / mask / blend-mode presence) and confirms
that two units that previously collided now key apart.
"""

from __future__ import annotations

from slidify.models import BoundingBox, DomElement, VisualUnit
from slidify.patterns.signatures import (
    _anchor_kind,
    _normalize_classes,
    signature,
    signature_hash,
)


# ---- helpers ---------------------------------------------------------------


def _el(
    eid: int,
    tag: str = "DIV",
    cls: str = "",
    text: str | None = None,
    *,
    bbox: tuple[float, float, float, float] = (0, 0, 200, 100),
    bg: str = "rgba(0, 0, 0, 0)",
    bg_image: str = "none",
    radius: str = "0px",
    shadow: str = "none",
    border: str = "none",
    border_top: str = "none",
    border_right: str = "none",
    border_bottom: str = "none",
    border_left: str = "none",
    clip_path: str = "none",
    is_text_container: bool = False,
    font_family: str = "",
    letter_spacing: str = "normal",
    writing_mode: str = "horizontal-tb",
    aspect_ratio: str = "auto",
    grid_template_columns: str = "none",
    text_shadow: str = "none",
    mask_image: str = "none",
    background_blend_mode: str = "normal",
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
        border=border,
        border_top=border_top,
        border_right=border_right,
        border_bottom=border_bottom,
        border_left=border_left,
        border_radius=radius,
        box_shadow=shadow,
        clip_path=clip_path,
        is_text_container=is_text_container,
        font_family=font_family,
        letter_spacing=letter_spacing,
        writing_mode=writing_mode,
        aspect_ratio=aspect_ratio,
        grid_template_columns=grid_template_columns,
        text_shadow=text_shadow,
        mask_image=mask_image,
        background_blend_mode=background_blend_mode,
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


# ---- baseline --------------------------------------------------------------


def test_vanilla_div_has_no_new_axes():
    """A vanilla <div> emits no Phase 2b tags — all defaults skip."""
    el = _el(1, "DIV")
    kind = _anchor_kind(el)
    # No axes 1..11 should appear.
    assert kind == ""
    sig = signature(_unit([el]))
    # Signature is well-formed and contains the expected scaffolding.
    assert sig.startswith("div(")
    assert "[" in sig and "]" in sig


# ---- axis 1: gradient stop count + direction bucket ------------------------


def test_axis_gradient_stop_and_direction():
    el = _el(
        1,
        bg_image=(
            "linear-gradient(135deg, rgb(99, 102, 241) 0%, "
            "rgb(236, 72, 153) 50%, rgb(255, 0, 0) 100%)"
        ),
    )
    kind = _anchor_kind(el)
    # Exact format: bgi=grad/n<stops>/d<bucket>
    assert "bgi=grad/n3/d135" in kind


def test_axis_gradient_radial_bucket():
    el = _el(
        1,
        bg_image=(
            "radial-gradient(circle at 30% 40%, "
            "rgba(255, 255, 255, 0.4) 0%, rgba(0, 0, 0, 0) 60%)"
        ),
    )
    kind = _anchor_kind(el)
    # Direction bucket is `r` (radial). Stop count is parser-dependent;
    # only the format and direction matter here.
    assert "bgi=grad/n" in kind
    assert "/r" in kind


# ---- axis 2: shadow elevation ---------------------------------------------


def test_axis_shadow_layer_count():
    el = _el(
        1,
        shadow=(
            "0 1px 2px rgba(0, 0, 0, 0.1), "
            "0 2px 4px rgba(0, 0, 0, 0.1), "
            "0 4px 8px rgba(0, 0, 0, 0.1)"
        ),
    )
    kind = _anchor_kind(el)
    assert "shdw=t/L3" in kind


# ---- axis 3: border-radius bucket -----------------------------------------


def test_axis_radius_bucket():
    el = _el(1, radius="18px")
    kind = _anchor_kind(el)
    # 18 → bucket 16 (the highest in {0,4,8,12,16,24,32,...} ≤ 18).
    assert "r16" in kind
    # And 9999px is treated as pill.
    el2 = _el(2, radius="9999px")
    assert "r9999" in _anchor_kind(el2)


# ---- axis 4: asymmetric border --------------------------------------------


def test_axis_asymmetric_border():
    el = _el(
        1,
        border_top="3px solid rgb(0, 0, 0)",
        border_left="1px solid rgb(0, 0, 0)",
    )
    kind = _anchor_kind(el)
    assert "brd-asymTL" in kind


def test_symmetric_border_does_not_emit_asym():
    el = _el(
        1,
        border_top="2px solid rgb(0, 0, 0)",
        border_right="2px solid rgb(0, 0, 0)",
        border_bottom="2px solid rgb(0, 0, 0)",
        border_left="2px solid rgb(0, 0, 0)",
    )
    kind = _anchor_kind(el)
    assert "brd-asym" not in kind


# ---- axis 5: clip-path preset ---------------------------------------------


def test_axis_clip_path_preset():
    el = _el(1, clip_path="circle(50% at 50% 50%)")
    kind = _anchor_kind(el)
    # OVAL preset id should be encoded as a positive int.
    assert "clip=" in kind
    tag = next(t for t in kind.split("+") if t.startswith("clip="))
    assert tag != "clip=raw"
    # Value should parse as int.
    int(tag.split("=", 1)[1])


def test_axis_clip_path_raw_when_no_preset():
    # `path(...)` is not handled by clip_path_to_preset → raw fallback.
    el = _el(1, clip_path='path("M 10 10 L 90 10 L 90 90 Z")')
    kind = _anchor_kind(el)
    assert "clip=raw" in kind


# ---- axis 6: font family register -----------------------------------------


def test_axis_font_face_register_serif():
    el = _el(1, text="Headline", font_family='"Playfair Display", Georgia, serif')
    kind = _anchor_kind(el)
    # "serif" beats "display" because the heuristic checks mono → serif first.
    assert "face=serif" in kind


def test_axis_font_face_register_display():
    el = _el(1, text="HUGE", font_family='"Bebas Neue", sans-serif')
    kind = _anchor_kind(el)
    assert "face=display" in kind


def test_axis_font_face_skipped_without_text():
    el = _el(1, font_family='"Inter", sans-serif')  # no text, no container
    kind = _anchor_kind(el)
    assert "face=" not in kind


# ---- axis 7: letter-spacing register --------------------------------------


def test_axis_letter_spacing_widest():
    el = _el(1, text="HELLO", font_family="Inter", letter_spacing="2.5px")
    kind = _anchor_kind(el)
    assert "tr=widest" in kind


def test_axis_letter_spacing_normal_skipped():
    el = _el(1, text="hi", font_family="Inter", letter_spacing="0.2px")
    kind = _anchor_kind(el)
    assert "tr=" not in kind


# ---- axis 8: writing mode -------------------------------------------------


def test_axis_writing_mode_vertical():
    el = _el(1, writing_mode="vertical-rl")
    kind = _anchor_kind(el)
    assert "wm=v" in kind


def test_axis_writing_mode_horizontal_skipped():
    el = _el(1, writing_mode="horizontal-tb")
    kind = _anchor_kind(el)
    assert "wm=" not in kind


# ---- axis 9: explicit aspect-ratio ----------------------------------------


def test_axis_aspect_ratio_explicit_square():
    el = _el(1, aspect_ratio="1 / 1")
    kind = _anchor_kind(el)
    assert "ar=square" in kind


def test_axis_aspect_ratio_auto_skipped():
    el = _el(1, bbox=(0, 0, 400, 100), aspect_ratio="auto")
    kind = _anchor_kind(el)
    # No EXPLICIT aspect-ratio set → no `ar=` token even though bbox is wide.
    assert "ar=" not in kind


# ---- axis 10: grid container ----------------------------------------------


def test_axis_grid_repeat_columns():
    el = _el(1, grid_template_columns="repeat(3, 1fr)")
    kind = _anchor_kind(el)
    assert "grid=3" in kind


def test_axis_grid_token_columns():
    el = _el(1, grid_template_columns="1fr 2fr 1fr 1fr")
    kind = _anchor_kind(el)
    assert "grid=4" in kind


# ---- axis 11: text-shadow / mask / blend mode -----------------------------


def test_axis_text_shadow_mask_blend_present():
    el = _el(
        1,
        text_shadow="0 1px 2px rgba(0, 0, 0, 0.5)",
        mask_image="url(#m)",
        background_blend_mode="multiply",
    )
    kind = _anchor_kind(el)
    assert "tshdw" in kind
    assert "mask" in kind
    assert "bblend" in kind


def test_axis_text_shadow_mask_blend_default_skipped():
    el = _el(1)
    kind = _anchor_kind(el)
    assert "tshdw" not in kind
    assert "mask" not in kind
    assert "bblend" not in kind


# ---- normalize classes: new kept prefixes ---------------------------------


def test_normalize_classes_keeps_new_typography_families():
    out = _normalize_classes(
        "font-serif font-mono tracking-tight leading-loose aspect-square text-4xl"
    )
    # New families flow through.
    assert "font-serif" in out
    assert "font-mono" in out
    assert "tracking-tight" in out
    assert "leading-loose" in out
    assert "aspect-square" in out
    # And the existing text size token still survives.
    assert "text-4xl" in out


# ---- collision tests -------------------------------------------------------


def test_collision_hero_mesh_vs_body_gradient():
    """A hero-mesh card (3-stop radial + shadow + heavy radius) used to
    collide with a plain 2-stop linear body card. Phase 2b axes break the tie."""
    hero = _el(
        1,
        cls="rounded-3xl",
        bbox=(0, 0, 800, 600),
        bg_image=(
            "radial-gradient(circle at 30% 40%, "
            "rgba(99, 102, 241, 0.6) 0%, "
            "rgba(236, 72, 153, 0.4) 40%, "
            "rgba(0, 0, 0, 0) 100%)"
        ),
        radius="32px",
        shadow="0 12px 24px rgba(0, 0, 0, 0.2), 0 24px 48px rgba(0, 0, 0, 0.1)",
    )
    body = _el(
        2,
        cls="rounded-xl",
        bbox=(0, 0, 800, 600),
        bg_image=(
            "linear-gradient(180deg, rgb(255, 255, 255) 0%, rgb(244, 244, 245) 100%)"
        ),
        radius="12px",
        shadow="0 1px 2px rgba(0, 0, 0, 0.05)",
    )
    assert signature_hash(_unit([hero])) != signature_hash(_unit([body]))


def test_collision_asym_kicker_vs_symmetric_badge():
    """A left-rule kicker (asymmetric border, tracked uppercase) used to
    collide with a uniformly-bordered badge. Now they key apart."""
    kicker = _el(
        1,
        cls="uppercase tracking-widest",
        text="QUARTERLY UPDATE",
        bbox=(0, 0, 220, 24),
        border_left="4px solid rgb(0, 0, 0)",
        font_family="Inter, sans-serif",
        letter_spacing="2.5px",
    )
    badge = _el(
        2,
        cls="uppercase",
        text="QUARTERLY UPDATE",
        bbox=(0, 0, 220, 24),
        border="2px solid rgb(0, 0, 0)",
        border_top="2px solid rgb(0, 0, 0)",
        border_right="2px solid rgb(0, 0, 0)",
        border_bottom="2px solid rgb(0, 0, 0)",
        border_left="2px solid rgb(0, 0, 0)",
        font_family="Inter, sans-serif",
        letter_spacing="0.2px",
    )
    assert signature_hash(_unit([kicker])) != signature_hash(_unit([badge]))


# ---- stability ------------------------------------------------------------


def test_signature_hash_is_stable_for_same_input():
    def mk() -> VisualUnit:
        return _unit([
            _el(
                1,
                cls="rounded-2xl shadow-xl",
                text="Same shape",
                bbox=(0, 0, 320, 200),
                bg_image=(
                    "linear-gradient(135deg, rgb(99, 102, 241) 0%, "
                    "rgb(236, 72, 153) 100%)"
                ),
                radius="16px",
                shadow="0 12px 24px rgba(0, 0, 0, 0.2)",
                font_family="Inter, sans-serif",
                letter_spacing="0.8px",
                aspect_ratio="16 / 9",
            )
        ])

    a = signature_hash(mk())
    b = signature_hash(mk())
    assert a == b
    # And the underlying signature string is a non-empty stable string.
    assert signature(mk()) == signature(mk())
