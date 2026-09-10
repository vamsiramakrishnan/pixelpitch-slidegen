"""Tests for slidify.text_metrics — font measurement + autofit pre-compute."""

from __future__ import annotations

import platform
import time
from pathlib import Path

import pytest

from slidify.models import BoundingBox
from slidify.text_metrics import (
    compute_font_scale_for_textbox,
    get_fallback_font_path,
    get_inter_font_path,
    measure_text,
    measure_text_width_px,
    shrink_pill_bbox_to_fit_text,
)


def _require_inter() -> Path:
    p = get_inter_font_path()
    if p is None:
        pytest.skip("Inter not installed on this system")
    return p


def _require_fallback() -> Path:
    p = get_fallback_font_path()
    if p is None:
        pytest.skip("No Liberation/DejaVu fallback font available")
    return p


def test_measure_text_returns_positive_width():
    inter = _require_inter()
    m = measure_text("hello", inter, 18.0)
    assert m.width_px > 0
    assert m.ascent_px > 0
    assert m.line_height_px >= m.ascent_px + m.descent_px


def test_variable_width_assertion():
    inter = _require_inter()
    w_wide = measure_text_width_px("WWWW", inter, 18.0)
    w_narrow = measure_text_width_px("iiii", inter, 18.0)
    assert w_wide > w_narrow


def test_inter_hello_18pt_known_width():
    inter = _require_inter()
    width = measure_text_width_px("Hello", inter, 18.0)
    # Inter "Hello" @ 18pt is ~52-58px depending on font version. Allow ±8px
    # to absorb metric drift between Inter shipping versions.
    assert 44.0 <= width <= 64.0, f"unexpected Inter Hello width: {width}"


def test_get_inter_font_path():
    p = get_inter_font_path()
    if p is None:
        pytest.skip("Inter not installed")
    assert p.is_file()
    assert p.suffix.lower() in (".otf", ".ttf")


def test_get_fallback_font_path():
    if platform.system() != "Linux":
        pytest.skip("fallback paths target Linux")
    p = get_fallback_font_path()
    if p is None:
        pytest.skip("no fallback font on this system")
    assert p.is_file()
    assert p.suffix.lower() == ".ttf"


def test_compute_font_scale_no_autofit_when_self_compared():
    inter = _require_inter()
    # Use Inter as both primary AND fallback — ratio ≈ 1.0, no shrink.
    res = compute_font_scale_for_textbox(
        [("Hello world", 18.0)],
        bbox_w_px=400.0,
        primary_font=inter,
        fallback_font=inter,
    )
    assert res.needs_autofit is False
    assert res.font_scale_pct == 100_000


def test_compute_font_scale_triggers_when_fallback_overflows():
    inter = _require_inter()
    # DejaVu Sans is meaningfully wider than Inter — emulates the
    # Liberation/Calibri-substitution scenario.
    dejavu = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not dejavu.is_file():
        pytest.skip("DejaVu Sans not available")
    text = "The quick brown fox jumps over the lazy dog"
    inter_w = measure_text_width_px(text, inter, 24.0)
    # Bbox just barely fits Inter, but DejaVu (wider) overflows.
    res = compute_font_scale_for_textbox(
        [(text, 24.0)],
        bbox_w_px=inter_w * 1.0,
        primary_font=inter,
        fallback_font=dejavu,
    )
    assert res.needs_autofit is True
    assert res.font_scale_pct < 100_000
    assert res.font_scale_pct >= 50_000  # clamped floor


def test_compute_font_scale_clamps_at_50pct():
    inter = _require_inter()
    dejavu = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not dejavu.is_file():
        pytest.skip("DejaVu Sans not available")
    # Absurdly tiny bbox forces ratio way above 2 → would scale below 50%
    # if not clamped.
    res = compute_font_scale_for_textbox(
        [("X" * 200, 36.0)],
        bbox_w_px=20.0,
        primary_font=inter,
        fallback_font=dejavu,
    )
    assert res.font_scale_pct == 50_000


def test_shrink_pill_bbox_narrows_short_text():
    fallback = _require_fallback()
    bbox = BoundingBox(x=10, y=20, w=200, h=24)
    new_bbox = shrink_pill_bbox_to_fit_text(
        "OK", bbox, font_size_pt=12.0, fallback_font=fallback
    )
    assert new_bbox.x == bbox.x
    assert new_bbox.y == bbox.y
    assert new_bbox.h == bbox.h
    assert new_bbox.w < bbox.w / 2


def test_shrink_pill_bbox_idempotent_on_tight_box():
    fallback = _require_fallback()
    text = "Available now"
    pt = 14.0
    text_w = measure_text_width_px(text, fallback, pt)
    pad = 12.0
    tight = BoundingBox(x=5, y=5, w=text_w + 2 * pad, h=24)
    out = shrink_pill_bbox_to_fit_text(
        text, tight, font_size_pt=pt, horizontal_padding_px=pad,
        fallback_font=fallback,
    )
    assert abs(out.w - tight.w) <= 1.0


def test_measure_text_caching_is_fast():
    inter = _require_inter()
    # Warm cache.
    measure_text_width_px("warmup", inter, 18.0)
    t0 = time.perf_counter()
    for _ in range(100):
        measure_text_width_px("Hello, world!", inter, 18.0)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.1, f"100 measurements took {elapsed:.3f}s — cache likely cold"


def test_empty_text_zero_width():
    inter = _require_inter()
    m = measure_text("", inter, 18.0)
    assert m.width_px == 0
    assert measure_text_width_px("", inter, 18.0) == 0
