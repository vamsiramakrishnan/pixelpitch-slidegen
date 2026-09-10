from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from slidify.harvester.candidates import cluster_signatures, propose_atom_candidate
from slidify.harvester.collection import _harvest_result, _walk_corpus
from slidify.harvester.signals import (
    _deck_summary_to_dict,
    _quality_issue_to_dict,
    cluster_pipeline_signals,
    deck_telemetry_from_result,
    merge_quality_issues,
    quality_issues_from_result,
    run_pipeline_signals,
)
from slidify.harvester.types import DeckTelemetry, HarvestReport, QualityIssue
from slidify.models import UnmatchedSignature
from slidify.progress import ProgressCallback, emit_progress

log = structlog.get_logger(__name__)


def aggregate_corpus(
    corpus_dir: Path,
    *,
    min_occurrences: int = 1,
    on_progress=None,
    progress: ProgressCallback | None = None,
) -> HarvestReport:
    """Walk `corpus_dir`, run slidify on each HTML deck, cluster the misses.

    `on_progress(path, sigs_count)` is called after each deck finishes so a
    CLI caller can stream a per-deck status line.
    """
    corpus_dir = Path(corpus_dir).resolve()
    ref_base = corpus_dir.parent if corpus_dir.is_file() else corpus_dir
    t_start = time.perf_counter()
    paths = _walk_corpus(corpus_dir)
    emit_progress(
        progress,
        event="harvest.discover.done",
        stage="discover",
        message=f"found {len(paths)} HTML input(s)",
        total=len(paths),
        path=str(corpus_dir),
    )

    observations: list[tuple[str, UnmatchedSignature]] = []
    deck_summaries: list[DeckTelemetry] = []
    quality_issues: list[QualityIssue] = []
    decks_processed = 0
    errors: list[dict[str, str]] = []

    for index, path in enumerate(paths, start=1):
        try:
            ref_root = path.relative_to(ref_base).as_posix()
        except ValueError:
            ref_root = path.name
        emit_progress(
            progress,
            event="harvest.deck.start",
            stage="render",
            message=f"rendering {ref_root}",
            current=index,
            total=len(paths),
            path=str(path),
        )
        try:
            result = _harvest_result(path)
        except Exception as e:
            log.warning("harvester.deck_failed", path=str(path), error=str(e))
            errors.append({"path": str(path), "error": f"{type(e).__name__}: {e}"})
            emit_progress(
                progress,
                event="harvest.deck.error",
                stage="render",
                status="error",
                message=f"failed {ref_root}: {type(e).__name__}",
                current=index,
                total=len(paths),
                path=str(path),
                elapsed_seconds=round(time.perf_counter() - t_start, 3),
                metrics={"error": str(e)},
            )
            continue
        decks_processed += 1
        sigs = list(result.unmatched_signatures)
        deck_summaries.append(deck_telemetry_from_result(ref_root, result))
        quality_issues.extend(quality_issues_from_result(ref_root, result))
        for idx, sig in enumerate(sigs):
            deck_ref = f"{ref_root}#node-{idx}"
            observations.append((deck_ref, sig))
        if on_progress is not None:
            on_progress(path, len(sigs))
        emit_progress(
            progress,
            event="harvest.deck.done",
            stage="render",
            message=f"{ref_root}: {len(sigs)} unmatched",
            current=index,
            total=len(paths),
            path=str(path),
            elapsed_seconds=round(time.perf_counter() - t_start, 3),
            metrics={
                "unmatched": len(sigs),
                "slides": result.n_slides,
                "native_area_ratio": round(result.native_area_ratio, 4),
                "pattern_coverage": round(result.pattern_coverage, 4),
            },
        )

    emit_progress(
        progress,
        event="harvest.cluster.start",
        stage="cluster",
        message=f"clustering {len(observations)} unmatched observation(s)",
        current=decks_processed,
        total=len(paths),
        elapsed_seconds=round(time.perf_counter() - t_start, 3),
    )
    clusters = cluster_signatures(observations, min_occurrences=min_occurrences)
    candidates = {c.id: propose_atom_candidate(c) for c in clusters}
    emit_progress(
        progress,
        event="harvest.cluster.done",
        stage="cluster",
        message=f"{len(clusters)} recurring signature cluster(s)",
        current=decks_processed,
        total=len(paths),
        elapsed_seconds=round(time.perf_counter() - t_start, 3),
        metrics={
            "observations": len(observations),
            "clusters": len(clusters),
            "min_occurrences": min_occurrences,
        },
    )

    report = HarvestReport(
        timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
        corpus_dir=str(corpus_dir),
        decks_processed=decks_processed,
        total_unmatched=sum(int(s.n_occurrences) for _, s in observations),
        unique_signatures=len(clusters),
        clusters=clusters,
        candidates=candidates,
        deck_summaries=deck_summaries,
        quality_issues=merge_quality_issues(quality_issues),
        errors=errors,
    )
    emit_progress(
        progress,
        event="harvest.done",
        stage="summary",
        message=(
            f"processed {report.decks_processed} deck(s), "
            f"{report.total_unmatched} unmatched unit(s)"
        ),
        current=report.decks_processed,
        total=len(paths),
        elapsed_seconds=round(time.perf_counter() - t_start, 3),
        metrics={
            "total_unmatched": report.total_unmatched,
            "unique_signatures": report.unique_signatures,
            "errors": len(report.errors),
        },
    )
    return report


def report_to_dict(report: HarvestReport, *, top_n: int | None = None) -> dict[str, Any]:
    """Serialise a `HarvestReport` to the CONTRACT-v2 §G.3 JSON shape."""
    clusters = report.clusters
    if top_n is not None:
        clusters = clusters[:top_n]
    out_clusters = []
    for c in clusters:
        cand = report.candidates.get(c.id)
        signals = cluster_pipeline_signals(c, cand)
        out_clusters.append(
            {
                "id": c.id,
                "sig_hash": c.sig_hash,
                "signature": c.signature,
                "instances": c.instances,
                "exemplars": [ex.slide_ref for ex in c.exemplars],
                "source_files": c.source_files,
                "source_groups": c.source_groups,
                "sample_classes": c.sample_classes,
                "sample_text": c.sample_text,
                "bbox_typical": c.bbox_typical,
                "pipeline_signals": signals,
                "candidate_atom_id": cand.candidate_atom_id if cand else "",
                "candidate_axis": cand.candidate_axis if cand else "",
                "candidate_props": cand.candidate_props if cand else {},
                "candidate_confidence": cand.confidence if cand else 0.0,
                "candidate_reason": cand.reason if cand else "",
            }
        )
    payload = {
        "harvest_run": {
            "timestamp": report.timestamp,
            "corpus_dir": report.corpus_dir,
            "decks_processed": report.decks_processed,
            "total_unmatched": report.total_unmatched,
            "unique_signatures": report.unique_signatures,
        },
        "run_signals": run_pipeline_signals(report),
        "clusters": out_clusters,
        "quality_issues": [_quality_issue_to_dict(i) for i in report.quality_issues],
        "deck_summaries": [_deck_summary_to_dict(d) for d in report.deck_summaries],
        "errors": report.errors,
    }
    return payload


def write_report(report: HarvestReport, out_path: Path, *, top_n: int | None = None) -> None:
    """Write `report` as `clusters.json` per CONTRACT-v2 §G.3."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = report_to_dict(report, top_n=top_n)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
