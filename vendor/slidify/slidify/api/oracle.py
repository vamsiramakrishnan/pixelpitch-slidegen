"""Fidelity oracle correction loop for the conversion API."""

from __future__ import annotations

from pathlib import Path

import structlog

from slidify.api.config import ConversionConfig
from slidify.api.state import SlidePlan, SlideSummary
from slidify.emitter import Emitter
from slidify.models import Decision, DecisionKind, FidelityReport, VisualUnit
from slidify.oracle import FidelityOracle
from slidify.promotion import to_emit_ops
from slidify.renderer import Renderer

log = structlog.get_logger(__name__)


async def oracle_with_correction(
    pptx_path: Path,
    plans: list[SlidePlan],
    summaries: list[SlideSummary],
    cfg: ConversionConfig,
    renderer: Renderer,
) -> list[FidelityReport]:
    oracle = FidelityOracle()
    ground_truths = [p.rendered.ground_truth_png for p in plans]

    def _units_per_slide() -> list[tuple[dict[str, VisualUnit], dict[str, Decision]]]:
        return [(p.units_by_id, p.decisions) for p in plans]

    reports = await oracle.evaluate(
        pptx_path, ground_truths, units_per_slide=_units_per_slide(),
        text_elements_per_slide=[p.rendered.elements for p in plans],
    )

    if not cfg.keep_plans_for_oracle:
        return reports

    for _iter in range(cfg.max_oracle_iterations):
        failing = [r for r in reports if not r.passed]
        if not failing:
            break
        log.info("oracle.iter", failing=len(failing))
        any_changed = False
        for r in failing:
            plan = plans[r.slide_index]
            if not plan.units:
                continue
            if not r.failing_regions:
                force_full_raster(plan)
                any_changed = True
                continue
            for region in r.failing_regions:
                if force_raster_overlapping(plan, region):
                    any_changed = True
        if not any_changed:
            break

        emitter = Emitter()
        try:
            for plan in plans:
                if not plan.units:
                    plan.ops = summaries[plan.index].ops
                else:
                    plan.ops = to_emit_ops(plan.units, plan.decisions)
                await emitter.emit_slide(
                    plan.index,
                    plan.rendered,
                    plan.units_by_id,
                    plan.ops,
                    renderer,
                    notes=plan.notes,
                )
            emitter.save(pptx_path)
        finally:
            emitter.close()
        for plan in plans:
            summaries[plan.index].ops = plan.ops
        reports = await oracle.evaluate(
            pptx_path, ground_truths, units_per_slide=_units_per_slide(),
            text_elements_per_slide=[p.rendered.elements for p in plans],
        )

    return reports


def force_full_raster(plan: SlidePlan) -> None:
    new_decisions: dict[str, Decision] = {}
    for u in plan.units:
        new_decisions[u.id] = Decision(
            kind=DecisionKind.Raster,
            confidence=1.0,
            reason="oracle_full_raster",
            source_tier="oracle_fix",
        )
        stack = list(u.children)
        while stack:
            c = stack.pop()
            new_decisions[c.id] = Decision(
                kind=DecisionKind.Skip,
                confidence=1.0,
                reason="absorbed by oracle_full_raster",
                source_tier="oracle_fix",
            )
            stack.extend(c.children)
    plan.decisions = {**plan.decisions, **new_decisions}


def force_raster_overlapping(plan: SlidePlan, region) -> bool:
    """Mark the smallest unit overlapping ``region`` as Raster."""
    region_area = region.w * region.h
    if region_area <= 0:
        return False

    candidates: list[VisualUnit] = []
    for u in plan.units_flat:
        if u.bbox.area <= 0:
            continue
        contained = u.bbox.intersect_area(region) / region_area
        if contained < 0.5:
            continue
        if u.bbox.area > region_area * 10:
            continue
        candidates.append(u)

    if not candidates:
        return False

    target = min(candidates, key=lambda u: u.bbox.area)
    cur = plan.decisions.get(target.id)
    if cur is not None and cur.kind in (DecisionKind.Raster, DecisionKind.Skip):
        return False
    plan.decisions[target.id] = Decision(
        kind=DecisionKind.Raster,
        confidence=1.0,
        reason="oracle_region_fix",
        source_tier="oracle_fix",
    )
    skip_set: set[str] = set()
    stack: list[VisualUnit] = list(target.children)
    while stack:
        c = stack.pop()
        skip_set.add(c.id)
        stack.extend(c.children)
    target_bbox = target.bbox
    target_area = target_bbox.area
    if target_area > 0:
        for u in plan.units_flat:
            if u.id == target.id or u.id in skip_set:
                continue
            if u.bbox.area <= 0:
                continue
            if u.bbox.area > target_area:
                continue
            inter = u.bbox.intersect_area(target_bbox)
            if inter / u.bbox.area >= 0.85:
                skip_set.add(u.id)
    for uid in skip_set:
        plan.decisions[uid] = Decision(
            kind=DecisionKind.Skip,
            confidence=1.0,
            reason="absorbed by oracle_region_fix",
            source_tier="oracle_fix",
        )
    return True
