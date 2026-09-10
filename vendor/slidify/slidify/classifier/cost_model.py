"""Tier-2 learned-cost-model scaffold.

This module is deliberately dependency-free: it is a calibrated linear prior
that can run everywhere slidify runs today. The shape is the important bit:
features -> expected value for {native, hybrid, raster} -> high-margin decision.

Once oracle-labelled unit rows exist, the constants below should be replaced by
weights exported from the eval loop. Until then, the prior encodes what the
latest corpus iterations already taught us:

* text-bearing units and translatable decorations are worth pushing native;
* unsupported CSS should raster early and cleanly;
* parents with native children plus untranslatable decoration are best emitted
  as surgical hybrids, not cascade-rastered.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from slidify.geom import SLIDE_H_PX, SLIDE_W_PX, parse_px
from slidify.gradients import parse_gradient
from slidify.models import Decision, DecisionKind, DomElement, VisualUnit
from slidify.shadows import is_translatable_shadow

MODEL_VERSION = "v0.calibrated-prior.2026-05-01"
DECISION_MARGIN_FLOOR = 0.18
HYBRID_MARGIN_FLOOR = 0.12


@dataclass(frozen=True)
class UnitCostFeatures:
    """Compact, JSON-serializable features for the cost model.

    These are intentionally renderer/runtime facts rather than raw CSS strings
    so the same schema can be logged, fitted offline, and loaded back as model
    weights later.
    """

    area_fraction: float
    n_elements: int
    n_children: int
    has_own_text: bool
    has_any_text: bool
    has_image: bool
    has_canvas_or_video: bool
    has_translatable_decoration: bool
    has_untranslatable_decoration: bool
    has_unsupported_css: bool
    has_opacity_with_children: bool
    native_child_ratio: float
    raster_child_ratio: float
    text_density: float
    style_diversity: float


@dataclass(frozen=True)
class CostPrediction:
    decision: DecisionKind
    confidence: float
    margin: float
    native_ev: float
    hybrid_ev: float
    raster_ev: float
    features: UnitCostFeatures
    reason: str

    def metadata(self) -> dict:
        return {
            "model_version": MODEL_VERSION,
            "margin": self.margin,
            "expected_value": {
                "native": self.native_ev,
                "hybrid": self.hybrid_ev,
                "raster": self.raster_ev,
            },
            "features": asdict(self.features),
        }


def classify_with_cost_model(
    unit: VisualUnit,
    prior: dict[str, Decision],
    *,
    pressure_metadata: dict | None = None,
) -> Decision | None:
    """Return a high-margin model decision, or None to defer to tier 3.

    Tier-2 still owns the hard deterministic thresholds. This model fills the
    ambiguous middle band with an expected-value decision only when the margin
    is strong enough to avoid silently lowering fidelity.
    """

    prediction = predict_costs(unit, prior)
    floor = HYBRID_MARGIN_FLOOR if prediction.decision == DecisionKind.Hybrid else DECISION_MARGIN_FLOOR
    if prediction.margin < floor:
        return None

    metadata = prediction.metadata()
    if pressure_metadata is not None:
        metadata["pressure"] = pressure_metadata

    return Decision(
        kind=prediction.decision,
        confidence=prediction.confidence,
        reason=prediction.reason,
        metadata=metadata,
        source_tier="tier2:model",
    )


def predict_costs(unit: VisualUnit, prior: dict[str, Decision]) -> CostPrediction:
    features = extract_features(unit, prior)

    # Native expected value: editability gain minus fidelity risk. Text and
    # clean decoration carry most of the upside; unsupported CSS and sparse
    # decorative regions carry most of the risk.
    native_ev = 0.05
    if features.has_own_text and not features.n_children:
        native_ev += 0.42
    if features.has_image and not features.has_any_text:
        native_ev += 0.34
    if features.has_translatable_decoration:
        native_ev += 0.30
    native_ev += 0.22 * features.native_child_ratio
    native_ev += 0.10 * min(1.0, features.text_density * 2.0)
    native_ev -= 0.85 if features.has_unsupported_css else 0.0
    native_ev -= 0.45 if features.has_untranslatable_decoration else 0.0
    native_ev -= 0.35 if features.has_opacity_with_children else 0.0
    native_ev -= 0.18 * features.style_diversity
    native_ev -= 0.10 * min(1.0, features.area_fraction * 2.0)

    # Surgical hybrid expected value: best for decorated parents with native
    # children. It preserves editability in descendants while buying fidelity
    # for the background/visual residue.
    hybrid_ev = -0.10
    hybrid_ev += 0.42 * features.native_child_ratio
    hybrid_ev += 0.35 if features.has_untranslatable_decoration else 0.0
    hybrid_ev += 0.18 if features.has_translatable_decoration and features.n_children else 0.0
    hybrid_ev += 0.12 if features.has_any_text and features.n_children else 0.0
    hybrid_ev -= 0.36 if not features.n_children else 0.0
    hybrid_ev -= 0.20 if features.has_canvas_or_video else 0.0
    hybrid_ev -= 0.12 * features.raster_child_ratio

    # Raster expected value: the safety net. It wins when CSS is unsupported,
    # children are already raster, or the region is mostly visual residue.
    raster_ev = 0.08
    raster_ev += 0.72 if features.has_unsupported_css else 0.0
    raster_ev += 0.42 if features.has_canvas_or_video else 0.0
    raster_ev += 0.22 if features.has_untranslatable_decoration else 0.0
    raster_ev += 0.20 * features.raster_child_ratio
    raster_ev += 0.12 if features.text_density < 0.05 and not features.has_own_text else 0.0
    raster_ev -= 0.22 * features.native_child_ratio
    raster_ev -= 0.18 if features.has_own_text and not features.has_unsupported_css else 0.0

    scores = {
        DecisionKind.NativeShape: native_ev,
        DecisionKind.Hybrid: hybrid_ev,
        DecisionKind.Raster: raster_ev,
    }
    if features.has_own_text and not features.n_children:
        scores.pop(DecisionKind.NativeShape, None)
        scores[DecisionKind.NativeText] = native_ev + 0.04
    if features.has_image and not features.has_any_text:
        scores.pop(DecisionKind.NativeShape, None)
        scores[DecisionKind.NativePicture] = native_ev + 0.04

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_kind, best_ev = ranked[0]
    second_ev = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = max(0.0, best_ev - second_ev)
    confidence = max(0.50, min(0.95, 0.55 + margin * 0.75))

    reason = f"cost_model:{best_kind.value} margin={margin:.2f}"
    return CostPrediction(
        decision=best_kind,
        confidence=confidence,
        margin=margin,
        native_ev=native_ev,
        hybrid_ev=hybrid_ev,
        raster_ev=raster_ev,
        features=features,
        reason=reason,
    )


def extract_features(unit: VisualUnit, prior: dict[str, Decision]) -> UnitCostFeatures:
    elems = unit.all_elements()
    own = list(unit.elements)
    anchor = own[0] if own else None
    slide_area = float(SLIDE_W_PX * SLIDE_H_PX)
    area_fraction = min(1.0, max(0.0, unit.bbox.area / slide_area)) if slide_area > 0 else 0.0

    has_own_text = _has_text(own)
    has_any_text = _has_text(elems)
    has_image = any(e.is_img for e in elems)
    has_canvas_or_video = any(e.is_canvas or e.is_video for e in elems)
    has_unsupported_css = _has_unsupported_css(unit)
    has_translatable_decoration = _has_translatable_decoration(anchor)
    has_untranslatable_decoration = _has_untranslatable_decoration(unit)
    has_opacity_with_children = bool(anchor and anchor.opacity < 0.99 and unit.children)

    child_decisions = [prior.get(c.id) for c in unit.children]
    decided = [d for d in child_decisions if d is not None]
    native_child_ratio = _ratio(decided, _NATIVE_KINDS)
    raster_child_ratio = _ratio(decided, {DecisionKind.Raster, DecisionKind.Hybrid})

    text_density = _text_density(unit, elems)
    style_diversity = _style_diversity(elems)

    return UnitCostFeatures(
        area_fraction=area_fraction,
        n_elements=len(elems),
        n_children=len(unit.children),
        has_own_text=has_own_text,
        has_any_text=has_any_text,
        has_image=has_image,
        has_canvas_or_video=has_canvas_or_video,
        has_translatable_decoration=has_translatable_decoration,
        has_untranslatable_decoration=has_untranslatable_decoration,
        has_unsupported_css=has_unsupported_css,
        has_opacity_with_children=has_opacity_with_children,
        native_child_ratio=native_child_ratio,
        raster_child_ratio=raster_child_ratio,
        text_density=text_density,
        style_diversity=style_diversity,
    )


_NATIVE_KINDS = {
    DecisionKind.NativeText,
    DecisionKind.NativeShape,
    DecisionKind.NativeBullet,
    DecisionKind.NativePicture,
    DecisionKind.NativeSvg,
}


def _has_text(elems: list[DomElement]) -> bool:
    for e in elems:
        if e.text and e.text.strip():
            return True
        if e.runs and any(r.text.strip() for r in e.runs if not r.is_break):
            return True
    return False


def _ratio(decisions: list[Decision], kinds: set[DecisionKind]) -> float:
    if not decisions:
        return 0.0
    return sum(1 for d in decisions if d.kind in kinds) / len(decisions)


def _has_translatable_decoration(anchor: DomElement | None) -> bool:
    if anchor is None:
        return False
    visible_fill = bool(
        anchor.background_color
        and anchor.background_color not in ("rgba(0, 0, 0, 0)", "transparent", "")
    )
    bg = anchor.background_image or "none"
    if bg != "none":
        if "url(" in bg:
            return False
        if parse_gradient(bg) is None:
            return False
        visible_fill = True
    if anchor.box_shadow and anchor.box_shadow != "none" and not is_translatable_shadow(anchor.box_shadow):
        return False
    border_visible = bool(anchor.border and anchor.border != "none" and parse_px(anchor.border.split()[0]) > 0)
    radius_visible = parse_px(anchor.border_radius) > 0
    return visible_fill or border_visible or radius_visible


def _has_untranslatable_decoration(unit: VisualUnit) -> bool:
    for e in unit.all_elements():
        bg = e.background_image or "none"
        if bg != "none" and ("url(" in bg or parse_gradient(bg) is None):
            return True
        if e.has_before or e.has_after:
            before = e.before_content or ""
            after = e.after_content or ""
            if "url(" in before or "url(" in after:
                return True
            # Pseudo boxes can alter pixels even without url content; treat
            # them as hybrid-friendly rather than native-safe.
            if e.pseudo_before_style or e.pseudo_after_style:
                return True
        if e.box_shadow and e.box_shadow != "none" and not is_translatable_shadow(e.box_shadow):
            return True
    return False


def _has_unsupported_css(unit: VisualUnit) -> bool:
    for e in unit.all_elements():
        if e.is_canvas or e.is_video:
            return True
        if e.filter and e.filter != "none":
            return True
        if e.clip_path and e.clip_path != "none":
            return True
        if getattr(e, "mix_blend_mode", "normal") not in ("normal", ""):
            return True
        if getattr(e, "backdrop_filter", "none") not in ("none", ""):
            return True
        if getattr(e, "background_clip", "border-box") == "text" and "url(" in (e.background_image or ""):
            return True
    return False


def _text_density(unit: VisualUnit, elems: list[DomElement]) -> float:
    if unit.bbox.area <= 0:
        return 0.0
    text_area = sum(e.bbox.area for e in elems if (e.text and e.text.strip()) or e.runs)
    return max(0.0, min(1.0, text_area / unit.bbox.area))


def _style_diversity(elems: list[DomElement]) -> float:
    text_elems = [e for e in elems if (e.text and e.text.strip()) or e.runs]
    if not text_elems:
        return 0.0
    distinct = {(e.font_family, e.font_weight, e.color, e.font_size) for e in text_elems}
    return min(1.0, max(0, len(distinct) - 1) / 4.0)
