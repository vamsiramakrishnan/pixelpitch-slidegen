from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from slidify.harvester.cluster_signals import cluster_pipeline_signals
from slidify.harvester.quality import _quality_issue_to_dict
from slidify.harvester.types import HarvestReport


def run_pipeline_signals(report: HarvestReport) -> dict[str, Any]:
    """Summarize a harvest as a renderer-roadmap snapshot."""
    deck_count = max(report.decks_processed, 1)
    overflow_total = sum(d.overflow_count for d in report.deck_summaries)
    coverage_gap_total = sum(d.coverage_gap_count for d in report.deck_summaries)
    exclusivity_total = sum(d.exclusivity_violation_count for d in report.deck_summaries)
    editability_failures = sum(0 if d.editability_passed else 1 for d in report.deck_summaries)
    avg_native = _avg(d.native_area_ratio for d in report.deck_summaries)
    avg_pattern = _avg(d.pattern_coverage for d in report.deck_summaries)
    cluster_strategies: dict[str, int] = {}
    cluster_actions: dict[str, int] = {}
    cluster_priorities: dict[str, int] = {}
    for cluster in report.clusters:
        signals = cluster_pipeline_signals(cluster, report.candidates.get(cluster.id))
        _bump(cluster_strategies, signals["render_strategy"], cluster.instances)
        _bump(cluster_priorities, signals["promotion_priority"], cluster.instances)
        for action in signals["pipeline_actions"]:
            _bump(cluster_actions, action, cluster.instances)

    recommendations = _run_recommendations(
        report=report,
        overflow_total=overflow_total,
        coverage_gap_total=coverage_gap_total,
        exclusivity_total=exclusivity_total,
        editability_failures=editability_failures,
        avg_pattern=avg_pattern,
    )
    return {
        "health": {
            "decks_processed": report.decks_processed,
            "error_count": len(report.errors),
            "avg_native_area_ratio": round(avg_native, 4),
            "avg_pattern_coverage": round(avg_pattern, 4),
            "unmatched_per_deck": round(report.total_unmatched / deck_count, 2),
            "quality_issues_per_deck": round(
                (overflow_total + coverage_gap_total + exclusivity_total + editability_failures)
                / deck_count,
                2,
            ),
        },
        "telemetry_totals": {
            "overflow_elements": overflow_total,
            "coverage_gaps": coverage_gap_total,
            "exclusivity_violations": exclusivity_total,
            "editability_failed_decks": editability_failures,
        },
        "cluster_strategy_mix": cluster_strategies,
        "cluster_priority_mix": cluster_priorities,
        "cluster_action_mix": cluster_actions,
        "top_quality_issues": [_quality_issue_to_dict(i) for i in report.quality_issues[:10]],
        "recommendations": recommendations,
    }


def _run_recommendations(
    *,
    report: HarvestReport,
    overflow_total: int,
    coverage_gap_total: int,
    exclusivity_total: int,
    editability_failures: int,
    avg_pattern: float,
) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    if report.clusters:
        top = report.clusters[0]
        cand = report.candidates.get(top.id)
        signals = cluster_pipeline_signals(top, cand)
        recs.append(
            {
                "priority": signals["promotion_priority"],
                "area": "pattern-library",
                "title": f"Promote {cand.candidate_atom_id if cand else top.sig_hash} from the top unmatched cluster",
                "why": f"{top.instances} instances across {len(top.source_groups)} source groups",
                "actions": signals["pipeline_actions"],
            }
        )
    if overflow_total:
        recs.append(
            {
                "priority": "high",
                "area": "layout-engine",
                "title": "Turn overflow telemetry into autofit and intentional-bleed policy",
                "why": f"{overflow_total} overflow elements were detected after browser layout",
                "actions": ["improve-autofit-or-bleed-policy", "add-layout-overflow-regression"],
            }
        )
    if coverage_gap_total:
        recs.append(
            {
                "priority": "critical",
                "area": "clusterer",
                "title": "Fix text-bearing DOM coverage gaps before adding more visual sugar",
                "why": f"{coverage_gap_total} rendered text elements were not covered by units",
                "actions": ["fix-dom-to-unit-coverage", "add-content-coverage-regression"],
            }
        )
    if exclusivity_total:
        recs.append(
            {
                "priority": "high",
                "area": "emitter",
                "title": "Eliminate duplicate native emit paths",
                "why": f"{exclusivity_total} absorbing-parent overlaps were detected",
                "actions": ["fix-absorbing-parent-emission", "add-emit-exclusivity-regression"],
            }
        )
    if editability_failures:
        recs.append(
            {
                "priority": "high",
                "area": "roundtrip",
                "title": "Promote editability drift into a release gate for bench decks",
                "why": f"{editability_failures} decks failed native operation round-trip accounting",
                "actions": ["inspect-pptx-roundtrip-diff", "add-editability-regression-case"],
            }
        )
    if avg_pattern and avg_pattern < 0.92:
        recs.append(
            {
                "priority": "medium",
                "area": "tier0-coverage",
                "title": "Raise pattern coverage before relying on raster fallbacks",
                "why": f"Average pattern coverage is {avg_pattern:.1%}",
                "actions": ["promote-to-native-pattern", "expand-pattern-regression-corpus"],
            }
        )
    return recs[:10]
def _avg(values: Iterable[float]) -> float:
    vals = [float(v) for v in values]
    return sum(vals) / len(vals) if vals else 0.0


def _bump(counts: dict[str, int], key: str, by: int = 1) -> None:
    counts[key] = counts.get(key, 0) + int(by)
