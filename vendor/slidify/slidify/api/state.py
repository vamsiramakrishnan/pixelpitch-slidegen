"""Internal state objects for conversion orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field

from slidify.models import Decision, EmitOp, RenderedSlide, VisualUnit


@dataclass
class SlidePlan:
    index: int
    rendered: RenderedSlide
    units: list[VisualUnit]
    units_flat: list[VisualUnit]
    units_by_id: dict[str, VisualUnit]
    decisions: dict[str, Decision] = field(default_factory=dict)
    ops: list[EmitOp] = field(default_factory=list)
    notes: str = ""


@dataclass
class SlideSummary:
    """Lightweight per-slide bookkeeping kept after ``SlidePlan`` is dropped."""

    index: int
    ops: list[EmitOp]
    decisions_by_tier: dict[str, int]
    overflow: list = field(default_factory=list)
    coverage_gaps: list = field(default_factory=list)
    exclusivity_violations: list = field(default_factory=list)

