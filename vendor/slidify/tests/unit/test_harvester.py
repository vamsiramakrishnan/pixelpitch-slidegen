"""Unit tests for the corpus harvester aggregator.

Covers `cluster_signatures` (dedupe + ranking + bbox stats) and
`propose_atom_candidate` (axis routing from class names + signature props).
The tests use synthetic `UnmatchedSignature` lists — the heavy slidify
conversion path is covered separately via `test_cli_harvest.py`.
"""

from __future__ import annotations

from slidify.harvester import (
    Cluster,
    DeckTelemetry,
    HarvestReport,
    QualityIssue,
    cluster_pipeline_signals,
    cluster_signatures,
    mechanisms_to_dict,
    merge_quality_issues,
    propose_atom_candidate,
    quality_issues_from_result,
    report_to_dict,
    run_pipeline_signals,
    top_mechanisms,
    write_report,
)
from slidify.harvester.collection import _walk_corpus
from slidify.models import ConversionResult, OverflowElement, UnmatchedSignature


def _sig(
    *,
    sig: str = "div(bg)[][256x256]",
    sig_hash: str = "deadbeefdeadbeef",
    bbox_w: int = 256,
    bbox_h: int = 256,
    sample_classes: str = "",
    sample_text: str = "",
    n_occurrences: int = 1,
) -> UnmatchedSignature:
    return UnmatchedSignature(
        sig=sig, sig_hash=sig_hash,
        bbox_w=bbox_w, bbox_h=bbox_h,
        sample_classes=sample_classes, sample_text=sample_text,
        n_occurrences=n_occurrences,
    )


# ---------------------------------------------------------------------------
# cluster_signatures
# ---------------------------------------------------------------------------


def test_cluster_signatures_dedupes_by_hash():
    obs = [
        ("deck-a.html#node-0", _sig(sig_hash="aaaa", sample_classes="card",
                                     n_occurrences=2)),
        ("deck-a.html#node-1", _sig(sig_hash="aaaa", sample_classes="card",
                                     n_occurrences=1)),
        ("deck-b.html#node-0", _sig(sig_hash="aaaa", sample_classes="card-large",
                                     n_occurrences=3)),
        ("deck-c.html#node-0", _sig(sig_hash="bbbb", sample_classes="icon")),
    ]
    clusters = cluster_signatures(obs)
    assert len(clusters) == 2
    # Sort by instance count: aaaa = 6, bbbb = 1.
    assert clusters[0].sig_hash == "aaaa"
    assert clusters[0].instances == 6
    assert clusters[0].id == "auto-cluster-001"
    assert clusters[1].sig_hash == "bbbb"
    assert clusters[1].instances == 1


def test_cluster_signatures_collects_exemplars_and_classes():
    obs = [
        ("a.html#node-0", _sig(sig_hash="x", sample_classes="card", sample_text="Hi")),
        ("b.html#node-0", _sig(sig_hash="x", sample_classes="card", sample_text="Hi")),
        ("c.html#node-7", _sig(sig_hash="x", sample_classes="tile", sample_text="Yo")),
    ]
    [c] = cluster_signatures(obs)
    refs = [ex.slide_ref for ex in c.exemplars]
    assert refs == ["a.html#node-0", "b.html#node-0", "c.html#node-7"]
    # Classes/text dedupe within the cluster.
    assert set(c.sample_classes) == {"card", "tile"}
    assert set(c.sample_text) == {"Hi", "Yo"}
    assert c.source_files == ["a.html", "b.html", "c.html"]
    assert c.source_groups == ["."]


def test_cluster_signatures_filters_by_min_occurrences():
    obs = [
        ("a.html#node-0", _sig(sig_hash="big", n_occurrences=10)),
        ("b.html#node-0", _sig(sig_hash="med", n_occurrences=3)),
        ("c.html#node-0", _sig(sig_hash="lo",  n_occurrences=1)),
    ]
    clusters = cluster_signatures(obs, min_occurrences=3)
    assert [c.sig_hash for c in clusters] == ["big", "med"]


def test_cluster_signatures_bbox_typical_aggregates():
    obs = [
        ("a.html#node-0", _sig(sig_hash="z", bbox_w=200, bbox_h=100)),
        ("b.html#node-0", _sig(sig_hash="z", bbox_w=300, bbox_h=200)),
        ("c.html#node-0", _sig(sig_hash="z", bbox_w=400, bbox_h=300)),
    ]
    [c] = cluster_signatures(obs)
    assert c.bbox_typical["w_avg"] == 300.0
    assert c.bbox_typical["h_avg"] == 200.0
    assert c.bbox_typical["w_min"] == 200
    assert c.bbox_typical["w_max"] == 400


def test_cluster_signatures_caps_exemplars_at_ten():
    obs = [
        (f"deck-{i}.html#node-0", _sig(sig_hash="z"))
        for i in range(25)
    ]
    [c] = cluster_signatures(obs)
    assert c.instances == 25
    assert len(c.exemplars) == 10


# ---------------------------------------------------------------------------
# propose_atom_candidate
# ---------------------------------------------------------------------------


def _cluster(
    *,
    classes: str = "",
    sig: str = "div(bg)[][256x256]",
    bbox_w: int = 256,
    bbox_h: int = 256,
) -> Cluster:
    """Build a one-exemplar cluster shaped like what `cluster_signatures` emits."""
    obs = [("deck.html#node-0", _sig(sig=sig, sample_classes=classes,
                                      bbox_w=bbox_w, bbox_h=bbox_h))]
    return cluster_signatures(obs)[0]


def test_propose_card_class_lands_surf():
    c = _cluster(classes="card shadow-xl rounded-2xl",
                 sig="div(bg+brd+shdw=t)[][256x256]")
    cand = propose_atom_candidate(c)
    assert cand.candidate_axis == "surf"
    assert cand.candidate_atom_id.startswith("surf.card")
    assert cand.candidate_props.get("border") is True
    assert cand.candidate_props.get("shadow") == "translatable"
    assert cand.confidence >= 0.7


def test_propose_icon_class_lands_dec():
    c = _cluster(classes="icon icon-lg",
                 sig="svg(svg/3)[][32x32]",
                 bbox_w=32, bbox_h=32)
    cand = propose_atom_candidate(c)
    assert cand.candidate_axis == "dec"
    assert cand.candidate_atom_id.startswith("dec.icon")


def test_propose_stat_class_lands_data():
    c = _cluster(classes="stat-num", sig="div(txt)[][192x32]",
                 bbox_w=192, bbox_h=64)
    cand = propose_atom_candidate(c)
    assert cand.candidate_axis == "data"
    assert "stat" in cand.candidate_atom_id


def test_propose_svg_anchor_routes_to_dec_when_no_class_match():
    c = _cluster(classes="", sig="svg(svg/12)[][80x80]",
                 bbox_w=80, bbox_h=80)
    cand = propose_atom_candidate(c)
    assert cand.candidate_axis == "dec"
    # Small square svg → icon-vector heuristic.
    assert "icon" in cand.candidate_atom_id


def test_propose_large_svg_lands_illustration():
    c = _cluster(classes="", sig="svg(svg/12)[][512x512]",
                 bbox_w=512, bbox_h=512)
    cand = propose_atom_candidate(c)
    assert cand.candidate_axis == "dec"
    assert "illustration" in cand.candidate_atom_id


def test_propose_img_anchor_routes_to_media():
    c = _cluster(classes="", sig="img(img)[][640x360]",
                 bbox_w=640, bbox_h=360)
    cand = propose_atom_candidate(c)
    assert cand.candidate_axis == "media"
    assert cand.candidate_atom_id.startswith("media.image")


def test_propose_falls_back_to_unmatched_with_low_confidence():
    c = _cluster(classes="weirdthing", sig="span(txt)[][7x7]",
                 bbox_w=7, bbox_h=7)
    cand = propose_atom_candidate(c)
    # No keyword match, no svg/img signal, tiny bbox → unmatched fallback.
    assert cand.candidate_atom_id.startswith("surf.unmatched-") or \
           cand.candidate_axis in {"dec", "surf", "text"}
    assert cand.confidence <= 0.45


def test_propose_modifiers_sorted_alphabetically():
    c = _cluster(classes="card",
                 sig="div(bg+brd+shdw=t+xform+bgi=grad)[][256x256]")
    cand = propose_atom_candidate(c)
    # Modifiers in stem are sorted: bordered, gradient, rotated, shadowed.
    stem = cand.candidate_atom_id.split(".", 1)[1]
    # Strip the leading 'card' prefix to get just the modifier list.
    mods = stem.removeprefix("card-").split("-")
    assert mods == sorted(mods)


def test_cluster_pipeline_signals_surface_actionable_render_guidance():
    obs = [
        ("corpus/slide-01.html#node-0",
         _sig(sig_hash="imgx", sig="div(bgi=url+filter)[][960x540]",
              bbox_w=960, bbox_h=540, sample_classes="photo-mask")),
        ("corpus/slide-02.html#node-0",
         _sig(sig_hash="imgx", sig="div(bgi=url+filter)[][960x540]",
              bbox_w=960, bbox_h=540, sample_classes="photo-mask")),
    ]
    [cluster] = cluster_signatures(obs)
    cand = propose_atom_candidate(cluster)
    signals = cluster_pipeline_signals(cluster, cand)
    assert signals["fidelity_risk"] == "high"
    assert signals["render_strategy"] == "preserve-raster"
    assert signals["editability_goal"] == "editable-wrapper-with-fidelity-raster"
    assert signals["raster_fidelity_goal"] == "pixel-exact-effects"
    assert "preserve-raster-layer" in signals["pipeline_actions"]
    assert "optimize-raster-crop-and-resolution" in signals["pipeline_actions"]
    assert "add-fidelity-regression-case" in signals["pipeline_actions"]
    assert "compare-source-vs-pptx-pixels" in signals["pipeline_actions"]
    assert signals["source_group_count"] == 1


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def test_report_to_dict_matches_contract_shape():
    obs = [
        ("a.html#node-0", _sig(sig_hash="x", sample_classes="card")),
        ("b.html#node-0", _sig(sig_hash="x", sample_classes="card")),
    ]
    clusters = cluster_signatures(obs)
    candidates = {c.id: propose_atom_candidate(c) for c in clusters}
    report = HarvestReport(
        timestamp="2026-05-02T00:00:00+00:00",
        corpus_dir="/tmp/corpus",
        decks_processed=2,
        total_unmatched=2,
        unique_signatures=1,
        clusters=clusters,
        candidates=candidates,
    )
    payload = report_to_dict(report)
    assert payload["harvest_run"]["decks_processed"] == 2
    assert payload["harvest_run"]["unique_signatures"] == 1
    assert payload["harvest_run"]["timestamp"] == "2026-05-02T00:00:00+00:00"
    [c] = payload["clusters"]
    # Required keys per the contract spec in the M4 brief.
    for key in (
        "id", "sig_hash", "signature", "instances", "exemplars",
        "source_files", "source_groups", "sample_classes", "sample_text",
        "bbox_typical", "pipeline_signals",
        "candidate_atom_id", "candidate_axis", "candidate_props",
    ):
        assert key in c, f"missing key: {key}"
    assert c["candidate_axis"] == "surf"
    assert c["candidate_atom_id"].startswith("surf.card")
    assert c["pipeline_signals"]["render_strategy"] == "native-atom"
    assert c["pipeline_signals"]["editability_goal"] == "maximize-native-editability"
    assert "run_signals" in payload
    assert "quality_issues" in payload
    assert "deck_summaries" in payload


def test_quality_issues_from_result_promotes_overflow_to_action_queue():
    result = ConversionResult(
        pptx_path="/tmp/out.pptx",
        n_slides=1,
        fidelity_reports=[],
        native_area_ratio=0.9,
        pattern_coverage=0.8,
        unmatched_signatures=[],
        overflow_elements=[
            OverflowElement(
                slide_index=0,
                axis="bottom",
                overflow_px=96,
                bbox_x=10,
                bbox_y=700,
                bbox_w=200,
                bbox_h=120,
                tag="div",
                data_atom="text.banner",
                stable_selector=".banner",
                sample_text="Too long",
                hint="text.banner",
            )
        ],
    )
    issues = quality_issues_from_result("deck/slide.html", result)
    assert len(issues) == 1
    assert issues[0].kind == "layout.overflow"
    assert issues[0].severity == "high"
    assert "improve-autofit-or-bleed-policy" in issues[0].actions
    assert issues[0].metrics["worst_overflow_px"] == 96


def test_merge_quality_issues_accumulates_sources_and_metrics():
    issues = [
        QualityIssue(
            id="layout.overflow:bottom",
            kind="layout.overflow",
            title="bottom overflow",
            severity="medium",
            instances=1,
            source_files={"a.html"},
            source_groups={"."},
            examples=["a.html#slide-0"],
            actions=["add-layout-overflow-regression"],
            metrics={"worst_overflow_px": 12.0},
        ),
        QualityIssue(
            id="layout.overflow:bottom",
            kind="layout.overflow",
            title="bottom overflow",
            severity="high",
            instances=2,
            source_files={"b.html"},
            source_groups={"."},
            examples=["b.html#slide-0"],
            actions=["improve-autofit-or-bleed-policy"],
            metrics={"worst_overflow_px": 40.0},
        ),
    ]
    [merged] = merge_quality_issues(issues)
    assert merged.instances == 3
    assert merged.severity == "high"
    assert merged.source_files == {"a.html", "b.html"}
    assert merged.metrics["worst_overflow_px"] == 40.0


def test_run_pipeline_signals_adds_recommendations_from_quality_telemetry():
    report = HarvestReport(
        timestamp="2026-05-02T00:00:00+00:00",
        corpus_dir="/tmp",
        decks_processed=2,
        total_unmatched=0,
        unique_signatures=0,
        deck_summaries=[
            DeckTelemetry(
                path="a.html",
                source_group=".",
                n_slides=1,
                unmatched_count=0,
                native_area_ratio=0.8,
                pattern_coverage=0.7,
                overflow_count=2,
            )
        ],
        quality_issues=[
            QualityIssue(
                id="layout.overflow:bottom",
                kind="layout.overflow",
                title="bottom overflow",
                severity="high",
                instances=2,
                actions=["add-layout-overflow-regression"],
            )
        ],
    )
    signals = run_pipeline_signals(report)
    assert signals["telemetry_totals"]["overflow_elements"] == 2
    assert signals["health"]["avg_pattern_coverage"] == 0.7
    assert any(r["area"] == "layout-engine" for r in signals["recommendations"])


def test_write_report_creates_parent_dirs(tmp_path):
    obs = [("a.html#node-0", _sig(sig_hash="x", sample_classes="tile"))]
    clusters = cluster_signatures(obs)
    candidates = {c.id: propose_atom_candidate(c) for c in clusters}
    report = HarvestReport(
        timestamp="2026-05-02T00:00:00+00:00",
        corpus_dir=str(tmp_path),
        decks_processed=1,
        total_unmatched=1,
        unique_signatures=1,
        clusters=clusters,
        candidates=candidates,
    )
    out = tmp_path / "nested" / "subdir" / "clusters.json"
    write_report(report, out)
    assert out.exists()
    # Round-trips through json.load cleanly.
    import json
    payload = json.loads(out.read_text())
    assert payload["clusters"][0]["candidate_axis"] == "surf"


def test_top_n_caps_clusters_in_payload():
    obs = []
    for i in range(15):
        # Each unique sig_hash creates its own cluster.
        obs.append((f"deck-{i}.html#node-0",
                    _sig(sig_hash=f"hash{i:02d}", n_occurrences=15 - i)))
    clusters = cluster_signatures(obs)
    candidates = {c.id: propose_atom_candidate(c) for c in clusters}
    report = HarvestReport(
        timestamp="2026-05-02T00:00:00+00:00",
        corpus_dir="/tmp",
        decks_processed=15,
        total_unmatched=sum(range(1, 16)),
        unique_signatures=15,
        clusters=clusters,
        candidates=candidates,
    )
    payload = report_to_dict(report, top_n=5)
    assert len(payload["clusters"]) == 5
    # Ranking preserved (instances descending).
    insts = [c["instances"] for c in payload["clusters"]]
    assert insts == sorted(insts, reverse=True)


def test_walk_corpus_skips_generated_root_index(tmp_path):
    (tmp_path / "index.html").write_text("<html>catalog</html>")
    (tmp_path / "index.json").write_text("{}")
    (tmp_path / "slide-01.html").write_text("<html>slide</html>")
    nested = tmp_path / "deck"
    nested.mkdir()
    (nested / "index.html").write_text("<html>real deck index slide</html>")

    paths = [p.relative_to(tmp_path).as_posix() for p in _walk_corpus(tmp_path)]

    assert paths == ["deck/index.html", "slide-01.html"]


def test_top_mechanisms_compose_quality_and_cluster_signals():
    obs = [
        ("a/anim-01.html#node-0",
         _sig(sig_hash="glyph", sig="span(txt+xform)[][60x60]",
              bbox_w=60, bbox_h=60, sample_classes="dot", n_occurrences=10)),
    ]
    clusters = cluster_signatures(obs)
    candidates = {c.id: propose_atom_candidate(c) for c in clusters}
    report = HarvestReport(
        timestamp="2026-05-02T00:00:00+00:00",
        corpus_dir="/tmp/decks",
        decks_processed=1,
        total_unmatched=10,
        unique_signatures=1,
        clusters=clusters,
        candidates=candidates,
        deck_summaries=[
            DeckTelemetry(
                path="a.html",
                source_group="a",
                n_slides=1,
                unmatched_count=10,
                native_area_ratio=0.95,
                pattern_coverage=0.9,
                overflow_count=2,
                coverage_gap_count=1,
            )
        ],
        quality_issues=[
            QualityIssue(
                id="content.coverage_gap:span",
                kind="content.coverage_gap",
                title="text coverage gap in SPAN",
                severity="critical",
                instances=1,
                actions=["fix-dom-to-unit-coverage"],
            )
        ],
    )

    mechanisms = top_mechanisms([report_to_dict(report)], limit=4)
    payload = mechanisms_to_dict(mechanisms)
    ids = [m["id"] for m in payload["mechanisms"]]

    assert "coverage-text-map" in ids
    assert "rotated-glyph-native" in ids
    assert payload["mechanisms"][0]["priority"] == "critical"
