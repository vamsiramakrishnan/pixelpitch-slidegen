from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_SEVERITY = {"low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass
class Mechanism:
    """One pipeline mechanism derived from harvest telemetry."""

    id: str
    title: str
    area: str
    priority: str
    score: float
    why: str
    evidence: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    gates: list[str] = field(default_factory=list)


def top_mechanisms(payloads: list[dict[str, Any]], *, limit: int = 10) -> list[Mechanism]:
    """Compose one or more harvest payloads into a ranked improvement plan."""
    mechanisms = _fixed_mechanisms()
    _apply_payload_signals(mechanisms, payloads)
    ranked = sorted(
        mechanisms.values(),
        key=lambda m: (_SEVERITY.get(m.priority, 0), m.score, len(m.evidence)),
        reverse=True,
    )
    return ranked[:limit]


def mechanisms_to_dict(mechanisms: list[Mechanism]) -> dict[str, Any]:
    return {"mechanisms": [_mechanism_to_dict(m) for m in mechanisms]}


def _fixed_mechanisms() -> dict[str, Mechanism]:
    return {
        "coverage-text-map": Mechanism(
            id="coverage-text-map",
            title="DOM-to-unit text coverage gate",
            area="clusterer",
            priority="medium",
            score=0.0,
            why="Text-bearing DOM that is visible in the browser must map to a visual unit before export.",
            actions=[
                "fix-dom-to-unit-coverage",
                "add-content-coverage-regression",
                "compare-source-vs-pptx-text-map",
            ],
            gates=["fail on coverage_gaps > 0 for deck suites"],
        ),
        "overflow-policy": Mechanism(
            id="overflow-policy",
            title="Autofit and intentional-bleed policy",
            area="layout-engine",
            priority="medium",
            score=0.0,
            why="Browser layout overflows need a deterministic choice: shrink, wrap, split, clip, or mark as intentional bleed.",
            actions=[
                "improve-autofit-or-bleed-policy",
                "add-layout-overflow-regression",
                "promote-authoring-hint",
            ],
            gates=["warn on medium overflow", "fail on high overflow unless allow-bleed is set"],
        ),
        "bench-input-hygiene": Mechanism(
            id="bench-input-hygiene",
            title="Harvest only presentation fixtures",
            area="bench",
            priority="high",
            score=12.0,
            why="Generated catalogue pages and long-form indexes should not dilute slide conversion signals.",
            actions=[
                "skip-generated-index-html",
                "separate-fixtures-from-generated-artifacts",
                "keep-harvest-manifest-explicit",
            ],
            gates=["exclude root index.html when index.json marks a generated catalogue"],
        ),
        "rotated-glyph-native": Mechanism(
            id="rotated-glyph-native",
            title="Native rotated glyph primitive",
            area="pattern-library",
            priority="medium",
            score=0.0,
            why="Small transformed decorative glyphs should stay editable instead of becoming raster residue.",
            actions=[
                "promote-to-native-pattern",
                "preserve-rotation-transform",
                "add-rotated-glyph-regression",
            ],
            gates=["no repeated dec.glyph-rotated cluster above min occurrence threshold"],
        ),
        "banner-footer-patterns": Mechanism(
            id="banner-footer-patterns",
            title="Banner and footer chrome atoms",
            area="pattern-library",
            priority="low",
            score=0.0,
            why="Repeated low-risk chrome should become native atoms to reduce unmatched noise.",
            actions=[
                "promote-to-native-pattern",
                "add-pattern-regression-case",
            ],
            gates=["no recurring text.banner or chrome.footer-bordered clusters"],
        ),
        "font-fidelity-telemetry": Mechanism(
            id="font-fidelity-telemetry",
            title="Structured font fallback telemetry",
            area="font-system",
            priority="high",
            score=18.0,
            why="The run logs show many font substitutions, but the harvest JSON does not yet expose fallback families as ranked data.",
            actions=[
                "surface-font-fallback-events-in-conversion-result",
                "add-font-pack-bootstrap-report",
                "gate-brand-font-substitution",
            ],
            gates=["report fallback family, resolved file, and source deck"],
        ),
        "animation-capture-states": Mechanism(
            id="animation-capture-states",
            title="Deterministic animation state harvesting",
            area="renderer",
            priority="medium",
            score=10.0,
            why="Animated fixtures need stable snapshot moments plus editable static overlays.",
            actions=[
                "capture-keyframe-state-fixtures",
                "separate-editable-overlay-from-motion-raster",
                "add-animation-frame-regression",
            ],
            gates=["animated deck reports include captured frame metadata"],
        ),
        "hybrid-raster-recipes": Mechanism(
            id="hybrid-raster-recipes",
            title="Surgical raster plus editable wrappers",
            area="renderer",
            priority="medium",
            score=10.0,
            why="Image-led, filtered, and masked visuals need brilliant raster fidelity while preserving editable text and geometry.",
            actions=[
                "preserve-raster-layer",
                "optimize-raster-crop-and-resolution",
                "compare-source-vs-pptx-pixels",
            ],
            gates=["raster layers must be cropped, high-DPI, and behind editable text"],
        ),
        "promotion-queue": Mechanism(
            id="promotion-queue",
            title="Pattern promotion queue with thresholds",
            area="harvester",
            priority="medium",
            score=0.0,
            why="High-frequency misses should automatically become reviewable native-pattern work.",
            actions=[
                "promote-to-native-pattern",
                "expand-pattern-regression-corpus",
                "needs-designer-label",
            ],
            gates=["critical clusters block release until promoted or waived"],
        ),
        "editability-roundtrip-gate": Mechanism(
            id="editability-roundtrip-gate",
            title="Round-trip editability drift gate",
            area="roundtrip",
            priority="medium",
            score=0.0,
            why="Designer-grade decks need PowerPoint-native edit operations to survive round trip accounting.",
            actions=[
                "inspect-pptx-roundtrip-diff",
                "add-editability-regression-case",
                "tighten-native-emit-accounting",
            ],
            gates=["fail on editability_failed_decks > 0"],
        ),
    }


def _apply_payload_signals(
    mechanisms: dict[str, Mechanism],
    payloads: list[dict[str, Any]],
) -> None:
    for payload in payloads:
        run = payload.get("harvest_run", {})
        label = _payload_label(run)
        telemetry = payload.get("run_signals", {}).get("telemetry_totals", {})
        coverage_gaps = int(telemetry.get("coverage_gaps", 0) or 0)
        overflow = int(telemetry.get("overflow_elements", 0) or 0)
        editability_failures = int(telemetry.get("editability_failed_decks", 0) or 0)
        if coverage_gaps:
            _raise(
                mechanisms["coverage-text-map"],
                "critical",
                coverage_gaps * 9,
                f"{label}: {coverage_gaps} coverage gaps",
            )
        if overflow:
            _raise(
                mechanisms["overflow-policy"],
                "high",
                min(overflow, 200) * 0.8,
                f"{label}: {overflow} overflow elements",
            )
        if editability_failures:
            _raise(
                mechanisms["editability-roundtrip-gate"],
                "high",
                editability_failures * 8,
                f"{label}: {editability_failures} editability failures",
            )

        for issue in payload.get("quality_issues", []):
            kind = str(issue.get("kind", ""))
            instances = int(issue.get("instances", 0) or 0)
            severity = str(issue.get("severity", "medium"))
            title = str(issue.get("title", kind))
            if kind == "layout.overflow":
                _raise(
                    mechanisms["overflow-policy"],
                    severity,
                    instances * _SEVERITY.get(severity, 1),
                    f"{label}: {instances}x {title}",
                )
            elif kind == "content.coverage_gap":
                _raise(
                    mechanisms["coverage-text-map"],
                    severity,
                    instances * 8,
                    f"{label}: {instances}x {title}",
                )
            elif kind == "editability.drift":
                _raise(
                    mechanisms["editability-roundtrip-gate"],
                    severity,
                    instances * 8,
                    f"{label}: {instances}x {title}",
                )

        for cluster in payload.get("clusters", []):
            atom = str(cluster.get("candidate_atom_id", ""))
            instances = int(cluster.get("instances", 0) or 0)
            signals = cluster.get("pipeline_signals", {})
            priority = str(signals.get("promotion_priority", "medium"))
            score = float(signals.get("promotion_score", 0.0) or 0.0)
            evidence = f"{label}: {instances}x {atom}"
            if atom == "dec.glyph-rotated":
                _raise(mechanisms["rotated-glyph-native"], priority, score, evidence)
            elif atom in {"text.banner", "chrome.footer-bordered"}:
                _raise(mechanisms["banner-footer-patterns"], priority, score, evidence)
            if instances:
                _raise(
                    mechanisms["promotion-queue"],
                    priority,
                    score * 0.7,
                    evidence,
                )
            if str(signals.get("render_strategy", "")).endswith("hybrid"):
                _raise(
                    mechanisms["hybrid-raster-recipes"],
                    priority,
                    score,
                    evidence,
                )


def _payload_label(run: dict[str, Any]) -> str:
    corpus = str(run.get("corpus_dir", "")).rstrip("/")
    return corpus.rsplit("/", 1)[-1] if corpus else "harvest"


def _raise(mechanism: Mechanism, priority: str, score: float, evidence: str) -> None:
    if _SEVERITY.get(priority, 0) > _SEVERITY.get(mechanism.priority, 0):
        mechanism.priority = priority
    mechanism.score += float(score)
    if evidence and evidence not in mechanism.evidence and len(mechanism.evidence) < 12:
        mechanism.evidence.append(evidence)


def _mechanism_to_dict(mechanism: Mechanism) -> dict[str, Any]:
    return {
        "id": mechanism.id,
        "title": mechanism.title,
        "area": mechanism.area,
        "priority": mechanism.priority,
        "score": round(mechanism.score, 2),
        "why": mechanism.why,
        "evidence": mechanism.evidence,
        "actions": mechanism.actions,
        "gates": mechanism.gates,
    }
