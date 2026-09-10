from __future__ import annotations

from typing import Any

from slidify.harvester.candidates import _props_from_signature
from slidify.harvester.types import AtomCandidate, Cluster

_SLIDE_AREA = 1280 * 720


def cluster_pipeline_signals(
    cluster: Cluster,
    candidate: AtomCandidate | None = None,
) -> dict[str, Any]:
    """Summarise how this miss should influence the renderer pipeline.

    The raw signature is useful for dedupe, but curation needs more explicit
    signals: how broadly the miss appears, whether it carries expensive visual
    effects, and whether it should become a native atom, a hybrid recipe, or an
    intentionally preserved raster layer.
    """
    sig = cluster.signature or ""
    props = _props_from_signature(sig)
    w = float(cluster.bbox_typical.get("w_avg", 0) or 0)
    h = float(cluster.bbox_typical.get("h_avg", 0) or 0)
    area_share = (w * h / _SLIDE_AREA) if w and h else 0.0
    features = _visual_features(props, sig)
    risk = _fidelity_risk(props, sig, area_share)
    strategy = _render_strategy(props, sig, area_share, candidate)
    score = _promotion_score(cluster, area_share, risk, candidate)
    return {
        "source_file_count": len(cluster.source_files),
        "source_group_count": len(cluster.source_groups),
        "source_groups": cluster.source_groups,
        "area_share_avg": round(area_share, 4),
        "aspect": _aspect_bucket(w, h),
        "size": _size_bucket(area_share),
        "visual_features": features,
        "fidelity_risk": risk,
        "editability_goal": _editability_goal(strategy),
        "raster_fidelity_goal": _raster_fidelity_goal(strategy, risk, features),
        "render_strategy": strategy,
        "promotion_priority": _promotion_priority(score),
        "promotion_score": round(score, 2),
        "pipeline_actions": _pipeline_actions(strategy, risk, features, candidate),
    }


def _visual_features(props: dict[str, Any], sig: str) -> list[str]:
    features = [k for k, v in props.items() if v]
    if "{" in sig:
        features.append("nested")
    if "<" in sig and ">" in sig:
        features.append("compound")
    return sorted(set(features))


def _aspect_bucket(w: float, h: float) -> str:
    if not w or not h:
        return "unknown"
    ratio = w / h
    if ratio >= 3.0:
        return "banner"
    if ratio >= 1.35:
        return "wide"
    if ratio <= 0.55:
        return "tall"
    if 0.85 <= ratio <= 1.15:
        return "square"
    return "balanced"


def _size_bucket(area_share: float) -> str:
    if area_share >= 0.35:
        return "hero"
    if area_share >= 0.12:
        return "large"
    if area_share >= 0.035:
        return "medium"
    return "small"


def _fidelity_risk(props: dict[str, Any], sig: str, area_share: float) -> str:
    high_bits = {"canvas", "video", "filter"}
    if high_bits.intersection(props) or "shdw=x" in sig:
        return "high"
    if props.get("image") and area_share >= 0.035:
        return "high"
    medium_bits = {"gradient", "svg", "transform", "image"}
    if medium_bits.intersection(props) or "{" in sig:
        return "medium" if area_share < 0.35 else "high"
    return "low"


def _render_strategy(
    props: dict[str, Any],
    sig: str,
    area_share: float,
    candidate: AtomCandidate | None,
) -> str:
    if props.get("canvas") or props.get("video") or props.get("filter"):
        return "preserve-raster"
    if props.get("image") and area_share >= 0.035:
        return "image-aware-hybrid"
    if "shdw=x" in sig:
        return "effect-aware-hybrid"
    if candidate and candidate.confidence >= 0.65:
        return "native-atom"
    if props.get("gradient") or props.get("svg") or "{" in sig:
        return "hybrid-recipe"
    return "native-pattern"


def _promotion_score(
    cluster: Cluster,
    area_share: float,
    risk: str,
    candidate: AtomCandidate | None,
) -> float:
    risk_weight = {"low": 0.0, "medium": 2.0, "high": 4.0}[risk]
    confidence = candidate.confidence if candidate else 0.0
    return (
        cluster.instances
        + len(cluster.source_files) * 1.5
        + len(cluster.source_groups) * 2.0
        + min(area_share * 40.0, 10.0)
        + risk_weight
        + confidence * 3.0
    )


def _promotion_priority(score: float) -> str:
    if score >= 24:
        return "critical"
    if score >= 14:
        return "high"
    if score >= 7:
        return "medium"
    return "low"


def _pipeline_actions(
    strategy: str,
    risk: str,
    features: list[str],
    candidate: AtomCandidate | None,
) -> list[str]:
    actions: list[str] = []
    if strategy in {"native-atom", "native-pattern"}:
        actions.append("promote-to-native-pattern")
    if strategy.endswith("hybrid") or strategy == "hybrid-recipe":
        actions.append("add-hybrid-recipe")
    if strategy == "preserve-raster":
        actions.append("preserve-raster-layer")
        actions.append("optimize-raster-crop-and-resolution")
    if risk == "high":
        actions.append("add-fidelity-regression-case")
        actions.append("compare-source-vs-pptx-pixels")
    if "gradient" in features or "filter" in features:
        actions.append("capture-effect-fixture")
    if candidate and candidate.confidence < 0.4:
        actions.append("needs-designer-label")
    return actions


def _editability_goal(strategy: str) -> str:
    if strategy in {"native-atom", "native-pattern"}:
        return "maximize-native-editability"
    if strategy in {"hybrid-recipe", "effect-aware-hybrid", "image-aware-hybrid"}:
        return "native-structure-with-raster-effects"
    return "editable-wrapper-with-fidelity-raster"


def _raster_fidelity_goal(strategy: str, risk: str, features: list[str]) -> str:
    if strategy in {"native-atom", "native-pattern"} and risk == "low":
        return "avoid-raster"
    if "filter" in features or strategy == "preserve-raster":
        return "pixel-exact-effects"
    if strategy.endswith("hybrid") or strategy == "hybrid-recipe":
        return "surgical-effect-raster"
    return "fallback-raster-quality"
