"""Public API: convert(source, pptx_path, ...) → ConversionResult.

Slide sources accepted:
    * `str`  — full HTML, optionally containing `<!DOCTYPE html>` separators
              for multi-slide files (Genspark convention).
    * `Path` — single .html file (split on DOCTYPEs) OR a directory whose
              top-level *.html files are each treated as a single slide
              (sorted lexicographically).
    * `Iterable[str | Path]`        — each item is one slide's HTML or a path
                                      to a file containing one slide.
    * `AsyncIterable[str | Path]`   — same, but pulled lazily for true
                                      streaming sources (DB, HTTP, etc.).

The pipeline streams: each slide is rendered → classified → emitted → its
ground-truth PNG is dropped (when oracle is off) before the next batch starts,
so peak memory is bounded by `render_concurrency`, not by deck size.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import structlog

from slidify.api.config import ConversionConfig
from slidify.api.oracle import (
    force_full_raster as _force_full_raster,
)
from slidify.api.oracle import (
    force_raster_overlapping as _force_raster_overlapping,
)
from slidify.api.oracle import (
    oracle_with_correction as _oracle_with_correction,
)
from slidify.api.sources import SlideInput, SlideSource, _inline_local_images, _normalize_source
from slidify.api.state import SlidePlan as _SlidePlan
from slidify.api.state import SlideSummary as _SlideSummary
from slidify.atom_inference import infer_atom_id
from slidify.cache import MemoryCache, StructuralCache
from slidify.classifier.llm import LLMProvider, auto_select_backend, build_provider
from slidify.classifier.tier1 import classify_tier1
from slidify.classifier.tier2 import classify_tier2
from slidify.classifier.tier3 import Tier3Stats, classify_tier3
from slidify.emitter import Emitter, native_area_ratio
from slidify.models import (
    ConversionResult,
    Decision,
    DecisionKind,
    FidelityReport,
    RenderedSlide,
    UnmatchedSignature,
    VisualUnit,
)
from slidify.patterns import PatternStats, classify_tier0, get_default_catalog
from slidify.patterns.signatures import signature, signature_hash
from slidify.progress import emit_progress
from slidify.promotion import promote, to_emit_ops
from slidify.renderer import Renderer
from slidify.units import cluster, flatten

log = structlog.get_logger(__name__)

__all__ = [
    "ConversionConfig",
    "SlideSource",
    "_force_full_raster",
    "_force_raster_overlapping",
    "_inline_local_images",
    "convert",
    "convert_sync",
]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _decisions_by_tier_from_summaries(
    summaries: list[_SlideSummary],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in summaries:
        for tier, n in s.decisions_by_tier.items():
            counts[tier] = counts.get(tier, 0) + n
    return counts


def _per_slide_decisions_count(plan: _SlidePlan) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in plan.decisions.values():
        counts[d.source_tier] = counts.get(d.source_tier, 0) + 1
    return counts


def _extract_notes(rendered: RenderedSlide) -> str:
    parts = [e.pptx_notes for e in rendered.elements if e.pptx_notes]
    return "\n".join(parts).strip()


def _classify_unit_tier12(
    unit: VisualUnit,
    cache: StructuralCache,
    prior: dict[str, Decision],
    pattern_stats: PatternStats,
    unmatched: dict[str, UnmatchedSignature],
) -> Decision | None:
    cached = cache.get(unit)
    if cached is not None:
        return Decision(
            kind=cached.kind,
            confidence=cached.confidence,
            reason=f"cache_hit ({cached.reason})",
            metadata=cached.metadata,
            source_tier=f"cache:{cached.source_tier}",
        )
    # Implicit-atom inference: when the unit's signature is recognised by
    # the priming table and the author hasn't already tagged the markup,
    # synthesise a `data-atom` hint so the existing tier-0 atom recipes
    # fire on first run (no `data-atom` author hint required). Wrapped in
    # a defensive try/except so a malformed atom_signatures.json never
    # regresses the pipeline.
    inferred_atom_id: str | None = None
    try:
        if unit.elements:
            anchor = unit.elements[0]
            if not (anchor.data_atom or "").strip():
                inferred_atom_id = infer_atom_id(unit)
                if inferred_atom_id:
                    anchor.data_atom = inferred_atom_id
    except Exception as e:  # pragma: no cover — defensive
        log.debug("api.atom_inference_failed", error=str(e))
        inferred_atom_id = None
    # Tier 0: pattern DB recipes (Tailwind / shadcn / common compositions).
    d = classify_tier0(unit, get_default_catalog(), stats=pattern_stats)
    if d is not None and inferred_atom_id:
        # Tag the metadata so callers can distinguish recipe-from-inference
        # vs recipe-from-author-hint without re-deriving the signature.
        meta = dict(d.metadata or {})
        meta["atom_inference"] = "implicit"
        meta["inferred_atom_id"] = inferred_atom_id
        d = d.model_copy(update={"metadata": meta})
    if d is not None:
        cache.put(unit, d)
        return d
    # Pattern miss → record signature for the harvester.
    sig = signature(unit)
    sig_h = signature_hash(unit)
    if sig_h in unmatched:
        unmatched[sig_h].n_occurrences += 1
    else:
        anchor = unit.elements[0] if unit.elements else None
        sample_text = ""
        for e in unit.all_elements():
            if e.text and e.text.strip():
                sample_text = e.text.strip()[:60]
                break
        unmatched[sig_h] = UnmatchedSignature(
            sig=sig,
            sig_hash=sig_h,
            bbox_w=int(unit.bbox.w),
            bbox_h=int(unit.bbox.h),
            sample_classes=(anchor.cls or "") if anchor else "",
            sample_text=sample_text,
        )
    d = classify_tier1(unit)
    if d is not None:
        cache.put(unit, d)
        return d
    d = classify_tier2(unit, prior)
    if d is not None:
        cache.put(unit, d)
        return d
    return None  # defer to tier 3


async def _build_provider(config: ConversionConfig) -> LLMProvider | None:
    if not config.run_tier3:
        return None
    backend = config.llm_backend or auto_select_backend()
    if backend is None:
        return None
    try:
        return build_provider(
            backend,
            model=config.llm_model,
            project_id=config.google_project,
            location=config.google_location,
        )
    except Exception as e:
        log.warning("api.provider_build_failed", backend=backend, error=str(e))
        return None


async def _classify_slide(
    plan: _SlidePlan,
    cache: StructuralCache,
    provider: LLMProvider | None,
    pattern_stats: PatternStats,
    unmatched: dict[str, UnmatchedSignature],
) -> Tier3Stats:
    decisions: dict[str, Decision] = {}
    deferred: list[VisualUnit] = []

    for u in reversed(plan.units_flat):
        d = _classify_unit_tier12(u, cache, decisions, pattern_stats, unmatched)
        if d is None:
            deferred.append(u)
        else:
            decisions[u.id] = d

    stats = Tier3Stats()
    if deferred and provider is not None:
        decisions_t3, stats = await classify_tier3(
            deferred, plan.rendered.ground_truth_png, provider=provider
        )
        for uid, d in decisions_t3.items():
            decisions[uid] = d
            cache.put(plan.units_by_id[uid], d)
    elif deferred:
        for u in deferred:
            decisions[u.id] = Decision(
                kind=DecisionKind.Raster,
                confidence=0.5,
                reason="no_llm_provider",
                source_tier="tier3",
            )

    plan.decisions = decisions
    return stats


async def _render_one(renderer: Renderer, inp: SlideInput) -> list[RenderedSlide]:
    if inp.source_path is not None:
        return await renderer.render_file(inp.source_path)
    return [await renderer.render(inp.html)]


async def _render_batch(
    renderer: Renderer, slides: list[SlideInput]
) -> list[RenderedSlide]:
    """Render a batch of slides in parallel."""
    nested = await asyncio.gather(*(_render_one(renderer, s) for s in slides))
    return [r for batch in nested for r in batch]


async def _drain_in_batches(
    slide_iter: AsyncIterator[SlideInput], size: int
) -> AsyncIterator[list[SlideInput]]:
    """Pull `size` items at a time from an async source, yielding batches."""
    batch: list[SlideInput] = []
    async for item in slide_iter:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def convert_sync(
    source: SlideSource,
    pptx_path: str | Path,
    config: ConversionConfig | None = None,
) -> ConversionResult:
    """Synchronous wrapper around :func:`convert`.

    Convenient for scripts and notebook cells that don't already run an
    asyncio event loop. Will raise ``RuntimeError`` if called from inside
    one — use :func:`convert` directly there.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(convert(source, pptx_path, config))
    raise RuntimeError(
        "convert_sync() cannot be called from a running event loop; "
        "use `await convert(...)` instead."
    )


async def convert(
    source: SlideSource,
    pptx_path: str | Path,
    config: ConversionConfig | None = None,
) -> ConversionResult:
    """Convert HTML slides to PPTX. See module docstring for source forms."""
    cfg = config or ConversionConfig()
    pptx_path = Path(pptx_path)
    t_start = time.perf_counter()
    cache = cfg.cache or StructuralCache(MemoryCache())

    summaries: list[_SlideSummary] = []
    plans_for_oracle: list[_SlidePlan] = []
    total_stats = Tier3Stats()
    pattern_stats = PatternStats()
    unmatched: dict[str, UnmatchedSignature] = {}
    # Streamed accumulator of deck-wide DOM colors, scanned at save-time
    # to derive theme accent1..accent6. Element refs are cheap (~few KB
    # per slide) and we drop them after the theme is patched.
    color_elements: list = []

    emit_progress(
        cfg.progress_callback,
        event="convert.start",
        stage="setup",
        message=f"converting source to {pptx_path}",
        path=str(pptx_path),
        metrics={
            "viewport": list(cfg.viewport),
            "render_concurrency": cfg.render_concurrency,
            "oracle": cfg.run_oracle,
            "tier3": cfg.run_tier3,
            "embed_fonts": cfg.embed_fonts,
        },
    )

    async with Renderer(
        viewport=cfg.viewport, differential=cfg.differential_render
    ) as renderer:
        emit_progress(
            cfg.progress_callback,
            event="convert.renderer.ready",
            stage="render",
            message="browser renderer is ready",
            elapsed_seconds=round(time.perf_counter() - t_start, 3),
        )
        provider = await _build_provider(cfg)
        emitter = Emitter()

        slide_iter = _normalize_source(source)
        batch_size = max(1, cfg.render_concurrency)
        slide_idx = 0
        try:
            async for batch in _drain_in_batches(slide_iter, batch_size):
                emit_progress(
                    cfg.progress_callback,
                    event="convert.batch.start",
                    stage="render",
                    message=f"rendering batch of {len(batch)} slide(s)",
                    current=slide_idx + 1,
                    elapsed_seconds=round(time.perf_counter() - t_start, 3),
                    metrics={"batch_size": len(batch)},
                )
                rendered_batch = await _render_batch(renderer, batch)
                for rendered in rendered_batch:
                    plan = await _process_one(
                        slide_idx,
                        rendered,
                        cache,
                        provider,
                        emitter,
                        renderer,
                        total_stats,
                        pattern_stats,
                        unmatched,
                    )
                    from slidify._overflow import detect_overflow
                    from slidify.unit_coverage import find_coverage_gaps

                    overflow = detect_overflow(
                        slide_index=plan.index,
                        elements=plan.rendered.elements,
                        viewport_w=plan.rendered.viewport_w,
                        viewport_h=plan.rendered.viewport_h,
                    )
                    if overflow:
                        log.warning(
                            "api.slide_overflow",
                            slide=plan.index,
                            n=len(overflow),
                            axes=sorted({o.axis for o in overflow}),
                            worst_px=max(o.overflow_px for o in overflow),
                        )
                    # Coverage oracle: report DOM elements with text whose
                    # region isn't owned by any produced VisualUnit. The
                    # auditing pass is defensive — wrapped in try/except
                    # so a bug in it can never break a real conversion.
                    try:
                        coverage_gaps = find_coverage_gaps(
                            plan.rendered.elements,
                            plan.units,
                            slide_index=plan.index,
                        )
                    except Exception as e:
                        log.warning(
                            "api.coverage_oracle_failed",
                            slide=plan.index,
                            error=str(e),
                        )
                        coverage_gaps = []
                    if coverage_gaps:
                        log.warning(
                            "api.slide_coverage_gaps",
                            slide=plan.index,
                            n=len(coverage_gaps),
                        )
                    # Emit-pathway exclusivity audit: structural assertion
                    # that an absorbing parent (NativeText/Bullet/Picture/
                    # Svg/Table) doesn't overlap a descendant unit that also
                    # emits — that pattern is the fingerprint of visual
                    # duplication in the produced PPTX. Defensive: any
                    # exception is logged and treated as zero violations,
                    # never propagates to the conversion pipeline.
                    try:
                        from slidify.promotion import audit_emit_exclusivity

                        excl_violations = audit_emit_exclusivity(
                            plan.units,
                            plan.decisions,
                            plan.ops,
                            slide_index=plan.index,
                        )
                    except Exception as e:
                        log.warning(
                            "api.exclusivity_audit_failed",
                            slide=plan.index,
                            error=str(e),
                        )
                        excl_violations = []
                    if excl_violations:
                        log.warning(
                            "api.slide_emit_duplicates",
                            slide=plan.index,
                            n=len(excl_violations),
                        )
                    summaries.append(
                        _SlideSummary(
                            index=plan.index,
                            ops=plan.ops,
                            decisions_by_tier=_per_slide_decisions_count(plan),
                            overflow=overflow,
                            coverage_gaps=coverage_gaps,
                            exclusivity_violations=excl_violations,
                        )
                    )
                    color_elements.extend(plan.rendered.elements)
                    # Refresh decoration palette as soon as we have enough
                    # color signal — gives later slides' decoration layers
                    # access to the deck's actual brand colors.
                    if len(color_elements) > 0 and slide_idx % 2 == 0:
                        try:
                            from slidify.theme import derive_accents_from_elements

                            running = derive_accents_from_elements(color_elements)
                            if running:
                                emitter.set_brand_palette(
                                    [a.lstrip("#") for a in running]
                                )
                        except Exception:
                            pass
                    if cfg.run_oracle and cfg.keep_plans_for_oracle:
                        plans_for_oracle.append(plan)
                    else:
                        # Keep only ground-truth PNG if we still need it for oracle.
                        if cfg.run_oracle:
                            plan.units = []
                            plan.units_flat = []
                            plan.units_by_id = {}
                            plan.decisions = {}
                        else:
                            # No oracle: drop the rendered PNG immediately too.
                            plan.rendered = RenderedSlide(
                                html="",
                                elements=[],
                                ground_truth_png=b"",
                                viewport_w=cfg.viewport[0],
                                viewport_h=cfg.viewport[1],
                            )
                        plans_for_oracle.append(plan)
                    slide_idx += 1
                    emit_progress(
                        cfg.progress_callback,
                        event="convert.slide.done",
                        stage="emit",
                        message=f"slide {plan.index + 1} emitted",
                        current=slide_idx,
                        elapsed_seconds=round(time.perf_counter() - t_start, 3),
                        metrics={
                            "units": len(plan.units_flat),
                            "ops": len(plan.ops),
                            "overflow": len(overflow),
                            "coverage_gaps": len(coverage_gaps),
                            "emit_duplicates": len(excl_violations),
                            "decisions_by_tier": _per_slide_decisions_count(plan),
                        },
                    )

            if slide_idx == 0:
                raise ValueError("no slides produced from source")

            # Patch the deck's theme color scheme with the most-used brand
            # colors. Downstream tools (corporate template imports, "change
            # theme" in PowerPoint) then recolor schemeClr-bound shapes to
            # match. This is purely additive — explicit srgbClr fills we
            # already emitted continue to render unchanged.
            try:
                from slidify.theme import (
                    derive_accents_from_elements,
                    set_theme_accents,
                )

                accents = derive_accents_from_elements(color_elements)
                if accents:
                    set_theme_accents(
                        emitter.prs,
                        primary=accents[0] if len(accents) >= 1 else None,
                        secondary=accents[1] if len(accents) >= 2 else None,
                        accents=accents[2:6] if len(accents) > 2 else None,
                    )
                    # Strip the leading '#' so decoration layers can hand the
                    # palette straight to MeshGlow's hex-only API.
                    emitter.set_brand_palette(
                        [a.lstrip("#") for a in accents]
                    )
            except Exception as e:
                log.warning("api.theme_patch_failed", error=str(e))
            # Defer color_elements.clear() until after font-embedding so
            # the resolver can scan requested families. Cleared at the
            # end of convert() via the local going out of scope.

            emit_progress(
                cfg.progress_callback,
                event="convert.write.start",
                stage="write",
                message=f"writing {pptx_path}",
                path=str(pptx_path),
                elapsed_seconds=round(time.perf_counter() - t_start, 3),
            )
            emitter.save(pptx_path)
            emit_progress(
                cfg.progress_callback,
                event="convert.write.done",
                stage="write",
                message=f"wrote {pptx_path}",
                path=str(pptx_path),
                elapsed_seconds=round(time.perf_counter() - t_start, 3),
            )
        finally:
            emitter.close()

        # Post-process: embed source fonts so renderers don't substitute.
        # Two passes:
        #   1. embed_default_fonts → embed Inter (the engine's standard).
        #   2. resolve_and_subset_for_deck → walk the rendered DOM for
        #      every CSS-specified family (Source Serif Pro / JetBrains
        #      Mono / etc.), resolve each via fontconfig, subset to the
        #      glyphs the deck actually uses, and embed those too.
        # Without (2), CSS asks for Source Serif Pro, fontconfig
        # silently substitutes DejaVu Sans (a sans-serif), and the
        # serif intent is lost in render. (2) keeps every requested
        # family available to the renderer by name.
        if cfg.embed_fonts:
            from slidify.font_embed import (
                audit_font_bindings,
                discover_inter,
                embed_fonts_in_pptx,
            )
            from slidify.font_resolver import resolve_and_subset_for_deck

            try:
                emit_progress(
                    cfg.progress_callback,
                    event="convert.fonts.start",
                    stage="fonts",
                    message="resolving and embedding deck fonts",
                    elapsed_seconds=round(time.perf_counter() - t_start, 3),
                )
                fonts_to_embed: list = []
                inter = discover_inter()
                if inter is not None:
                    fonts_to_embed.append(inter)
                deck_fonts = resolve_and_subset_for_deck(color_elements)
                fonts_to_embed.extend(deck_fonts)
                if fonts_to_embed:
                    embed_fonts_in_pptx(pptx_path, fonts_to_embed)
                    font_audit = audit_font_bindings(pptx_path)
                    if font_audit.missing_embeds:
                        log.warning(
                            "api.font_bindings_missing",
                            families=sorted(font_audit.missing_embeds),
                        )
                    log.info(
                        "api.fonts_embedded",
                        n=len(fonts_to_embed),
                        families=[f.typeface for f in fonts_to_embed],
                    )
                    emit_progress(
                        cfg.progress_callback,
                        event="convert.fonts.done",
                        stage="fonts",
                        message=f"embedded {len(fonts_to_embed)} font subset(s)",
                        elapsed_seconds=round(time.perf_counter() - t_start, 3),
                        metrics={
                            "families": [f.typeface for f in fonts_to_embed],
                            "missing_bindings": sorted(font_audit.missing_embeds),
                        },
                    )
            except Exception as e:
                log.warning("api.font_embed_failed", error=str(e))
                emit_progress(
                    cfg.progress_callback,
                    event="convert.fonts.error",
                    stage="fonts",
                    status="warning",
                    message=f"font embedding failed: {type(e).__name__}",
                    elapsed_seconds=round(time.perf_counter() - t_start, 3),
                    metrics={"error": str(e)},
                )

        reports: list[FidelityReport] = []
        if cfg.run_oracle:
            emit_progress(
                cfg.progress_callback,
                event="convert.oracle.start",
                stage="oracle",
                message="running fidelity oracle",
                elapsed_seconds=round(time.perf_counter() - t_start, 3),
            )
            reports = await _oracle_with_correction(
                pptx_path, plans_for_oracle, summaries, cfg, renderer
            )
            emit_progress(
                cfg.progress_callback,
                event="convert.oracle.done",
                stage="oracle",
                message="fidelity oracle finished",
                elapsed_seconds=round(time.perf_counter() - t_start, 3),
                metrics={
                    "reports": len(reports),
                    "failed": sum(1 for r in reports if not r.passed),
                },
            )

    n_slides = len(summaries)
    avg_native = (
        sum(native_area_ratio(s.ops) for s in summaries) / n_slides if n_slides else 0.0
    )
    elapsed = time.perf_counter() - t_start
    # Top-N unmatched signatures by occurrence — most-likely candidates for new patterns.
    unmatched_sorted = sorted(
        unmatched.values(), key=lambda u: u.n_occurrences, reverse=True
    )[:25]
    from slidify.compat import MATRIX_VERSION as _COMPAT_VERSION
    from slidify.compat import matrix_summary as _compat_summary

    # Editability round-trip: re-open the produced .pptx and verify that
    # the shapes the emitter was asked to produce actually survived to
    # disk. Defaults on (cheap; one extra pptx open per conversion).
    edit_passed = True
    edit_intended_total = 0
    edit_actual_total = 0
    edit_failing: list[int] = []
    if cfg.run_editability_check:
        try:
            from slidify.roundtrip import check_pptx_editability

            ops_per_slide = [s.ops for s in summaries]
            edit_report = check_pptx_editability(pptx_path, ops_per_slide)
            edit_passed = edit_report.passed
            edit_intended_total = sum(
                s.intended_editable for s in edit_report.per_slide
            )
            edit_actual_total = sum(
                s.actual_editable for s in edit_report.per_slide
            )
            edit_failing = [
                s.slide_index for s in edit_report.per_slide if not s.passed
            ]
            if not edit_passed:
                # The totals alone read as "45 intended, 45 actual, all six
                # slides failed", which tells an operator nothing. The notes
                # name the dimension and the slide that actually drifted.
                log.warning(
                    "api.editability_drift",
                    failing_slides=edit_failing,
                    intended_total=edit_intended_total,
                    actual_total=edit_actual_total,
                    reasons=[
                        f"slide {s.slide_index + 1}: {s.notes}"
                        for s in edit_report.per_slide
                        if not s.passed and s.notes
                    ],
                )
            emit_progress(
                cfg.progress_callback,
                event="convert.editability.done",
                stage="editability",
                message="editability round-trip checked",
                elapsed_seconds=round(time.perf_counter() - t_start, 3),
                metrics={
                    "passed": edit_passed,
                    "intended_total": edit_intended_total,
                    "actual_total": edit_actual_total,
                    "failing_slides": edit_failing,
                },
            )
        except Exception as e:
            log.warning("api.editability_check_failed", error=str(e))

    overflow_elements = [o for s in summaries for o in s.overflow]
    coverage_gaps = [g for s in summaries for g in s.coverage_gaps]
    exclusivity_violations = [
        v for s in summaries for v in s.exclusivity_violations
    ]
    # Soft cross-check: if the editability round-trip reported drift
    # (more shapes than intended) and the exclusivity audit flagged
    # absorbing-parent + descendant emits, link the two so users see
    # the connection. Doesn't change exit codes; pure observability.
    if (
        cfg.run_editability_check
        and not edit_passed
        and edit_actual_total > edit_intended_total
        and exclusivity_violations
    ):
        log.warning(
            "roundtrip.exclusivity_explains_drift",
            n_violations=len(exclusivity_violations),
            actual_total=edit_actual_total,
            intended_total=edit_intended_total,
        )

    elapsed = time.perf_counter() - t_start
    result = ConversionResult(
        pptx_path=str(pptx_path),
        n_slides=n_slides,
        fidelity_reports=reports,
        native_area_ratio=avg_native,
        llm_calls=total_stats.n_calls,
        total_cost_usd=total_stats.cost_usd,
        elapsed_seconds=elapsed,
        cache_hit_rate=cache.hit_rate,
        decisions_by_tier=_decisions_by_tier_from_summaries(summaries),
        pattern_hits=dict(pattern_stats.hits_by_id),
        pattern_coverage=pattern_stats.coverage,
        unmatched_signatures=unmatched_sorted,
        compat_matrix_version=_COMPAT_VERSION,
        compat_matrix_summary=_compat_summary(),
        editability_passed=edit_passed,
        editability_intended_total=edit_intended_total,
        editability_actual_total=edit_actual_total,
        editability_failing_slides=edit_failing,
        overflow_elements=overflow_elements,
        coverage_gaps=coverage_gaps,
        exclusivity_violations=exclusivity_violations,
    )
    emit_progress(
        cfg.progress_callback,
        event="convert.done",
        stage="summary",
        message=f"converted {n_slides} slide(s) in {elapsed:.2f}s",
        current=n_slides,
        total=n_slides,
        path=str(pptx_path),
        elapsed_seconds=round(elapsed, 3),
        metrics={
            "native_area_ratio": round(avg_native, 4),
            "pattern_coverage": round(pattern_stats.coverage, 4),
            "unmatched_signatures": len(unmatched_sorted),
            "overflow": len(overflow_elements),
            "coverage_gaps": len(coverage_gaps),
            "editability_passed": edit_passed,
        },
    )
    return result


async def _process_one(
    slide_idx: int,
    rendered: RenderedSlide,
    cache: StructuralCache,
    provider: LLMProvider | None,
    emitter: Emitter,
    renderer: Renderer,
    total_stats: Tier3Stats,
    pattern_stats: PatternStats,
    unmatched: dict[str, UnmatchedSignature],
) -> _SlidePlan:
    roots = cluster(rendered.elements)
    flat = flatten(roots)
    by_id = {u.id: u for u in flat}
    plan = _SlidePlan(
        index=slide_idx,
        rendered=rendered,
        units=roots,
        units_flat=flat,
        units_by_id=by_id,
        notes=_extract_notes(rendered),
    )
    stats = await _classify_slide(plan, cache, provider, pattern_stats, unmatched)
    total_stats.n_calls += stats.n_calls
    total_stats.n_units += stats.n_units
    total_stats.cost_usd += stats.cost_usd
    for k, v in stats.by_backend.items():
        total_stats.by_backend[k] = total_stats.by_backend.get(k, 0) + v
    if stats.backend:
        total_stats.backend = stats.backend
        total_stats.model = stats.model

    plan.decisions = promote(plan.units, plan.decisions)
    plan.ops = to_emit_ops(plan.units, plan.decisions)
    await emitter.emit_slide(
        plan.index,
        plan.rendered,
        plan.units_by_id,
        plan.ops,
        renderer,
        notes=plan.notes,
    )
    return plan
