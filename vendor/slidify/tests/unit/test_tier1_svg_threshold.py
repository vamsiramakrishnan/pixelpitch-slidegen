"""Regression tests for the SVG complexity threshold in classifier.tier1.

Two invariants:
  1. The threshold lives in `slidify.dom_walker.SVG_NATIVE_PATH_BUDGET` and
     `_has_complex_svg` reads from the same constant. A divergence creates
     a "dead band" where the walker drops geometry but the classifier still
     routes the unit to the native emitter (which then emits an empty shell).
  2. SVGs at the boundary go native; one shape past it goes raster.
"""

from __future__ import annotations

from slidify.classifier.tier1 import _has_complex_svg
from slidify.dom_walker import SVG_NATIVE_PATH_BUDGET, WALKER_JS
from slidify.models import BoundingBox, DomElement, UnitKind, VisualUnit


def _svg_unit(path_count: int) -> VisualUnit:
    bbox = BoundingBox(x=0, y=0, w=400, h=300)
    el = DomElement(
        id=1,
        parent_id=None,
        depth=0,
        tag="svg",
        bbox=bbox,
        is_svg=True,
        svg_path_count=path_count,
    )
    return VisualUnit(id="u_svg", kind=UnitKind.Generic, bbox=bbox, elements=[el])


def test_threshold_exceeded_marks_unit_complex():
    """One shape past the budget routes to the raster path."""
    assert _has_complex_svg(_svg_unit(SVG_NATIVE_PATH_BUDGET + 1)) is True


def test_threshold_at_budget_stays_native():
    """An SVG with exactly the budget's worth of shapes should still go
    native — `>` not `>=` is the boundary."""
    assert _has_complex_svg(_svg_unit(SVG_NATIVE_PATH_BUDGET)) is False


def test_well_below_threshold_is_not_complex():
    """A small icon (≤ 10 primitives) is unambiguously native-eligible."""
    assert _has_complex_svg(_svg_unit(10)) is False


def test_zero_path_svg_is_not_complex():
    """A degenerate SVG with zero captured primitives isn't 'complex' per
    this rule — a different rule decides whether to skip it entirely."""
    assert _has_complex_svg(_svg_unit(0)) is False


def test_walker_js_was_substituted_with_python_constant():
    """The JS template uses `__SVG_NATIVE_PATH_BUDGET__` as a sentinel;
    the runtime `WALKER_JS` must contain the resolved integer instead.
    Catches accidental edits to the template that drop the substitution."""
    assert "__SVG_NATIVE_PATH_BUDGET__" not in WALKER_JS
    assert f"<= {SVG_NATIVE_PATH_BUDGET}" in WALKER_JS


def test_threshold_bumped_above_legacy_30():
    """Lock in the bump from 30 → ≥200 so a future cleanup doesn't silently
    revert it. Real charts ship 50-150 primitives; the old cap rasterized
    every one of them."""
    assert SVG_NATIVE_PATH_BUDGET >= 200
