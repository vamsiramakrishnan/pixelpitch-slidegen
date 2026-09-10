from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClusterExemplar:
    """One concrete instance contributing to a cluster.

    `slide_ref` is `<file>#node-<index>` (file path relative to the corpus
    root). `node-<index>` is best-effort — without re-walking the DOM we use
    the per-deck rank order in which the signature first appeared.
    """

    slide_ref: str
    bbox_w: int
    bbox_h: int
    sample_text: str = ""
    sample_classes: str = ""


@dataclass
class Cluster:
    """A group of UnmatchedSignature observations sharing a structural shape."""

    id: str
    sig_hash: str
    signature: str
    instances: int
    exemplars: list[ClusterExemplar] = field(default_factory=list)
    sample_classes: list[str] = field(default_factory=list)
    sample_text: list[str] = field(default_factory=list)
    bbox_typical: dict[str, float] = field(default_factory=dict)
    source_files: list[str] = field(default_factory=list)
    source_groups: list[str] = field(default_factory=list)


@dataclass
class AtomCandidate:
    """Heuristic proposal for the cluster's eventual home atom."""

    candidate_atom_id: str
    candidate_axis: str
    candidate_props: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""


@dataclass
class HarvestReport:
    """Full harvester output. Maps directly to `clusters.json` schema."""

    timestamp: str
    corpus_dir: str
    decks_processed: int
    total_unmatched: int
    unique_signatures: int
    clusters: list[Cluster] = field(default_factory=list)
    candidates: dict[str, AtomCandidate] = field(default_factory=dict)
    deck_summaries: list[DeckTelemetry] = field(default_factory=list)
    quality_issues: list[QualityIssue] = field(default_factory=list)
    # Per-deck error log so a flaky corpus doesn't silently shrink the run.
    errors: list[dict[str, str]] = field(default_factory=list)


@dataclass
class DeckTelemetry:
    """Per-source conversion telemetry used to separate pattern misses from QA risk."""

    path: str
    source_group: str
    n_slides: int
    unmatched_count: int
    native_area_ratio: float
    pattern_coverage: float
    overflow_count: int = 0
    coverage_gap_count: int = 0
    exclusivity_violation_count: int = 0
    editability_passed: bool = True
    editability_intended_total: int = 0
    editability_actual_total: int = 0
    editability_failing_slides: list[int] = field(default_factory=list)
    decisions_by_tier: dict[str, int] = field(default_factory=dict)


@dataclass
class QualityIssue:
    """Aggregated non-pattern issue surfaced by conversion telemetry.

    Unmatched signatures tell us which shapes need native or hybrid recipes.
    Quality issues tell us where already-rendered decks still risk poor
    presentation output: overflow, dropped text coverage, duplicate emission,
    and editability drift.
    """

    id: str
    kind: str
    title: str
    severity: str
    instances: int = 0
    source_files: set[str] = field(default_factory=set)
    source_groups: set[str] = field(default_factory=set)
    examples: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
