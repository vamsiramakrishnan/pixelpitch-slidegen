"""Tier-2 scorer.

For units the deterministic rules deferred, compute raster pressure first. The
hard tails preserve the old behavior; the ambiguous middle band now runs a
small expected-value cost model that can later be replaced by oracle-trained
weights. Returns None to defer to tier 3 when neither mechanism has enough
margin.
"""

from __future__ import annotations

from dataclasses import dataclass

from slidify.classifier.cost_model import classify_with_cost_model
from slidify.gradients import parse_gradient
from slidify.models import Decision, DecisionKind, VisualUnit
from slidify.shadows import is_translatable_shadow


@dataclass
class RasterPressure:
    pseudo_complexity: float = 0.0
    layout_complexity: float = 0.0
    decoration_complexity: float = 0.0
    text_density: float = 0.0
    style_uniqueness: float = 0.0
    child_disagreement: float = 0.0

    weights: tuple[float, float, float, float, float, float] = (
        0.20, 0.20, 0.15, 0.15, 0.10, 0.20,
    )

    def score(self) -> float:
        wp, wl, wd, wt, ws, wc = self.weights
        return (
            self.pseudo_complexity * wp
            + self.layout_complexity * wl
            + self.decoration_complexity * wd
            + self.text_density * wt
            + self.style_uniqueness * ws
            + self.child_disagreement * wc
        )


def _pseudo_complexity(unit: VisualUnit) -> float:
    n = 0
    rich = 0
    for e in unit.all_elements():
        if e.has_before:
            n += 1
            if e.before_content and any(t in e.before_content for t in ("url(", "image", '"\\')):
                rich += 1
        if e.has_after:
            n += 1
            if e.after_content and any(t in e.after_content for t in ("url(", "image", '"\\')):
                rich += 1
    if n == 0:
        return 0.0
    if n == 1 and rich == 0:
        return 0.4
    if rich >= 1:
        return 1.0
    return 0.6


def _layout_complexity(unit: VisualUnit) -> float:
    """Approximate via DOM depth within the unit. We don't have display info
    forwarded individually, so use depth + child count as a proxy."""
    elems = unit.all_elements()
    if len(elems) <= 3:
        return 0.0
    max_depth = max(e.depth for e in elems) - min(e.depth for e in elems)
    # Each level of depth >2 adds 0.25.
    return min(1.0, max(0.0, (max_depth - 2) * 0.25))


def _decoration_complexity(unit: VisualUnit) -> float:
    score = 0.0
    for e in unit.all_elements():
        bg = e.background_image or ""
        if "gradient(" in bg:
            # If the gradient is translatable to native a:gradFill, no pressure.
            if parse_gradient(bg) is not None:
                continue
            inner = bg.split("gradient(", 1)[1] if "gradient(" in bg else ""
            n_stops = inner.count(",")
            score = max(score, 0.8 if n_stops >= 3 else 0.4)
        if "url(" in bg:
            score = max(score, 0.7)
        if e.box_shadow and e.box_shadow != "none":
            # Translatable single shadows are free; multi-shadow stacks are not.
            if is_translatable_shadow(e.box_shadow):
                # Detect multi-layer (more than one comma at top level).
                depth = 0
                n = 1
                for ch in e.box_shadow:
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                    elif ch == "," and depth == 0:
                        n += 1
                if n > 1:
                    score = max(score, 0.4)
                # else: free
            else:
                score = max(score, 0.6)
    return min(1.0, score)


def _text_density(unit: VisualUnit) -> float:
    """Inverse text density → high raster pressure when text is sparse."""
    elems = unit.all_elements()
    text_elems = [e for e in elems if e.text and e.text.strip()]
    if not text_elems:
        # No text: only raster-pressured if there's decorative content.
        return 0.6
    text_area = sum(e.bbox.area for e in text_elems)
    total = unit.bbox.area
    if total <= 0:
        return 0.0
    density = text_area / total
    # density → pressure: low density = decorative-dominant region
    if density >= 0.4:
        return 0.0
    if density >= 0.2:
        return 0.3
    if density >= 0.05:
        return 0.6
    return 0.9


def _style_uniqueness(unit: VisualUnit) -> float:
    leaves = [e for e in unit.all_elements() if e.text and e.text.strip()]
    distinct = {(e.font_family, e.font_weight, e.color) for e in leaves}
    n = len(distinct)
    if n <= 1:
        return 0.0
    if n == 2:
        return 0.4
    if n == 3:
        return 0.7
    return 1.0


def _child_disagreement(
    unit: VisualUnit, prior: dict[str, Decision]
) -> float:
    if not unit.children:
        return 0.0
    decided = [prior.get(c.id) for c in unit.children]
    decided_kinds = [d.kind for d in decided if d is not None]
    if not decided_kinds:
        return 0.0
    n_raster = sum(
        1
        for k in decided_kinds
        if k in (DecisionKind.Raster, DecisionKind.Hybrid)
    )
    return n_raster / len(decided_kinds)


def compute_pressure(unit: VisualUnit, prior: dict[str, Decision]) -> RasterPressure:
    return RasterPressure(
        pseudo_complexity=_pseudo_complexity(unit),
        layout_complexity=_layout_complexity(unit),
        decoration_complexity=_decoration_complexity(unit),
        text_density=_text_density(unit),
        style_uniqueness=_style_uniqueness(unit),
        child_disagreement=_child_disagreement(unit, prior),
    )


def classify_tier2(unit: VisualUnit, prior: dict[str, Decision]) -> Decision | None:
    pressure = compute_pressure(unit, prior)
    score = pressure.score()
    pressure_metadata = pressure.__dict__ | {"score": score}

    if score >= 0.75:
        return Decision(
            kind=DecisionKind.Raster,
            confidence=score,
            reason=f"heuristic_high_pressure (score={score:.2f})",
            metadata={"pressure": pressure_metadata},
            source_tier="tier2",
        )
    if score <= 0.30:
        # Pick best native representation. A unit with sub-units is structural
        # — its children classify their own text — so we only consider the
        # unit's *own* elements when deciding NativeText vs NativeShape.
        own_elems = unit.elements
        has_own_text = any(e.text and e.text.strip() for e in own_elems)
        anchor = own_elems[0] if own_elems else None
        anchor_has_bg = (
            anchor is not None
            and anchor.background_color
            and anchor.background_color != "rgba(0, 0, 0, 0)"
        )
        anchor_has_border = anchor is not None and anchor.border and anchor.border != "none"
        if has_own_text and not unit.children:
            kind = DecisionKind.NativeText
        elif anchor_has_bg or anchor_has_border:
            kind = DecisionKind.NativeShape
        else:
            kind = DecisionKind.Skip
        return Decision(
            kind=kind,
            confidence=1.0 - score,
            reason=f"heuristic_low_pressure (score={score:.2f})",
            metadata={"pressure": pressure_metadata},
            source_tier="tier2",
        )

    # Ambiguous middle band: use the expected-value prior only when its margin
    # is high enough. Otherwise keep deferring to tier 3.
    return classify_with_cost_model(
        unit,
        prior,
        pressure_metadata=pressure_metadata,
    )
