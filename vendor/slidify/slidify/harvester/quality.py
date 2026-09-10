from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from slidify.harvester.types import DeckTelemetry, QualityIssue
from slidify.models import ConversionResult


def deck_telemetry_from_result(ref: str, result: ConversionResult) -> DeckTelemetry:
    """Condense a `ConversionResult` into stable per-deck harvest telemetry."""
    return DeckTelemetry(
        path=ref,
        source_group=_source_group(ref),
        n_slides=int(result.n_slides),
        unmatched_count=sum(int(s.n_occurrences) for s in result.unmatched_signatures),
        native_area_ratio=round(float(result.native_area_ratio or 0.0), 4),
        pattern_coverage=round(float(result.pattern_coverage or 0.0), 4),
        overflow_count=len(result.overflow_elements),
        coverage_gap_count=len(result.coverage_gaps),
        exclusivity_violation_count=len(result.exclusivity_violations),
        editability_passed=bool(result.editability_passed),
        editability_intended_total=int(result.editability_intended_total),
        editability_actual_total=int(result.editability_actual_total),
        editability_failing_slides=list(result.editability_failing_slides),
        decisions_by_tier=dict(result.decisions_by_tier),
    )


def quality_issues_from_result(ref: str, result: ConversionResult) -> list[QualityIssue]:
    """Build non-pattern issue records from conversion telemetry for one deck."""
    issues: list[QualityIssue] = []
    for overflow in result.overflow_elements:
        px = float(overflow.overflow_px)
        axis = str(overflow.axis or "unknown")
        hint = str(overflow.hint or overflow.data_atom or overflow.tag or "element")
        title = f"{axis}-edge overflow in {hint}"
        actions = ["add-layout-overflow-regression", "improve-autofit-or-bleed-policy"]
        if overflow.hint:
            actions.append("promote-authoring-hint")
        issues.append(
            QualityIssue(
                id=_issue_id("layout.overflow", f"{axis}:{hint}"),
                kind="layout.overflow",
                title=title,
                severity="high" if px >= 64 else "medium",
                instances=1,
                source_files={ref},
                source_groups={_source_group(ref)},
                examples=[_issue_ref(ref, overflow.slide_index, overflow.stable_selector)],
                actions=actions,
                metrics={"worst_overflow_px": round(px, 2)},
            )
        )

    for gap in result.coverage_gaps:
        tag = str(gap.tag or "element")
        cls = _first_token(gap.cls) or tag
        issues.append(
            QualityIssue(
                id=_issue_id("content.coverage_gap", f"{tag}:{cls}:{gap.reason}"),
                kind="content.coverage_gap",
                title=f"text coverage gap in {cls}",
                severity="critical" if float(gap.overlap_ratio) < 0.2 else "high",
                instances=1,
                source_files={ref},
                source_groups={_source_group(ref)},
                examples=[_issue_ref(ref, gap.slide_index, gap.stable_selector)],
                actions=[
                    "fix-dom-to-unit-coverage",
                    "add-content-coverage-regression",
                    "compare-source-vs-pptx-text-map",
                ],
                metrics={"min_overlap_ratio": round(float(gap.overlap_ratio), 4)},
            )
        )

    for violation in result.exclusivity_violations:
        key = f"{violation.parent_kind}:{violation.descendant_kind}:{violation.reason}"
        issues.append(
            QualityIssue(
                id=_issue_id("emit.exclusivity_violation", key),
                kind="emit.exclusivity_violation",
                title=f"duplicate emit path: {violation.parent_kind} over {violation.descendant_kind}",
                severity="high",
                instances=1,
                source_files={ref},
                source_groups={_source_group(ref)},
                examples=[_issue_ref(ref, violation.slide_index, violation.descendant_unit_id)],
                actions=[
                    "fix-absorbing-parent-emission",
                    "add-emit-exclusivity-regression",
                ],
                metrics={"max_overlap_ratio": round(float(violation.overlap_ratio), 4)},
            )
        )

    if not result.editability_passed:
        drift = max(
            int(result.editability_intended_total) - int(result.editability_actual_total),
            0,
        )
        issues.append(
            QualityIssue(
                id=_issue_id("editability.drift", "roundtrip"),
                kind="editability.drift",
                title="round-trip editability drift",
                severity="high" if drift else "medium",
                instances=max(len(result.editability_failing_slides), 1),
                source_files={ref},
                source_groups={_source_group(ref)},
                examples=[
                    _issue_ref(ref, slide, "editability-roundtrip")
                    for slide in result.editability_failing_slides[:5]
                ] or [f"{ref}#editability-roundtrip"],
                actions=[
                    "inspect-pptx-roundtrip-diff",
                    "add-editability-regression-case",
                    "tighten-native-emit-accounting",
                ],
                metrics={"missing_native_ops": float(drift)},
            )
        )
    return issues


def merge_quality_issues(issues: Iterable[QualityIssue]) -> list[QualityIssue]:
    """Merge single-deck quality issues into ranked queues."""
    buckets: dict[str, QualityIssue] = {}
    for issue in issues:
        bucket = buckets.get(issue.id)
        if bucket is None:
            buckets[issue.id] = QualityIssue(
                id=issue.id,
                kind=issue.kind,
                title=issue.title,
                severity=issue.severity,
                instances=issue.instances,
                source_files=set(issue.source_files),
                source_groups=set(issue.source_groups),
                examples=list(issue.examples[:10]),
                actions=list(dict.fromkeys(issue.actions)),
                metrics=dict(issue.metrics),
            )
            continue
        bucket.instances += issue.instances
        bucket.severity = _max_severity(bucket.severity, issue.severity)
        bucket.source_files.update(issue.source_files)
        bucket.source_groups.update(issue.source_groups)
        for example in issue.examples:
            if example and example not in bucket.examples and len(bucket.examples) < 10:
                bucket.examples.append(example)
        for action in issue.actions:
            if action not in bucket.actions:
                bucket.actions.append(action)
        _merge_issue_metrics(bucket.metrics, issue.metrics)

    return sorted(
        buckets.values(),
        key=lambda i: (
            _severity_weight(i.severity),
            i.instances,
            len(i.source_groups),
            len(i.source_files),
            i.kind,
        ),
        reverse=True,
    )
def _source_group(ref: str) -> str:
    source = ref.split("#", 1)[0]
    return source.split("/", 1)[0] if "/" in source else "."


def _issue_ref(ref: str, slide_index: int, selector: str) -> str:
    suffix = selector.strip() if selector else "telemetry"
    return f"{ref}#slide-{slide_index}:{suffix}"


def _issue_id(kind: str, key: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", key.strip().lower()).strip("-")
    safe = safe[:80] or "unknown"
    return f"{kind}:{safe}"


def _first_token(value: str) -> str:
    return (value or "").split()[0] if value else ""


def _severity_weight(severity: str) -> int:
    return {"low": 1, "medium": 2, "high": 3, "critical": 4}.get(severity, 0)


def _max_severity(a: str, b: str) -> str:
    return a if _severity_weight(a) >= _severity_weight(b) else b


def _merge_issue_metrics(target: dict[str, float], incoming: dict[str, float]) -> None:
    for key, value in incoming.items():
        current = target.get(key)
        if current is None:
            target[key] = value
        elif key.startswith(("worst_", "max_")):
            target[key] = max(current, value)
        elif key.startswith("min_"):
            target[key] = min(current, value)
        else:
            target[key] = max(current, value)


def _quality_issue_to_dict(issue: QualityIssue) -> dict[str, Any]:
    return {
        "id": issue.id,
        "kind": issue.kind,
        "title": issue.title,
        "severity": issue.severity,
        "instances": issue.instances,
        "source_files": sorted(issue.source_files)[:25],
        "source_groups": sorted(issue.source_groups),
        "examples": issue.examples[:10],
        "actions": issue.actions,
        "metrics": issue.metrics,
    }


def _deck_summary_to_dict(deck: DeckTelemetry) -> dict[str, Any]:
    return {
        "path": deck.path,
        "source_group": deck.source_group,
        "n_slides": deck.n_slides,
        "unmatched_count": deck.unmatched_count,
        "native_area_ratio": deck.native_area_ratio,
        "pattern_coverage": deck.pattern_coverage,
        "overflow_count": deck.overflow_count,
        "coverage_gap_count": deck.coverage_gap_count,
        "exclusivity_violation_count": deck.exclusivity_violation_count,
        "editability_passed": deck.editability_passed,
        "editability_intended_total": deck.editability_intended_total,
        "editability_actual_total": deck.editability_actual_total,
        "editability_failing_slides": deck.editability_failing_slides,
        "decisions_by_tier": deck.decisions_by_tier,
    }
