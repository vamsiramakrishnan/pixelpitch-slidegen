"""CLI smoke tests for the `slidify harvest` subcommand.

We bypass the heavy slidify conversion path by monkey-patching
`slidify.harvester.aggregate_corpus` to return a synthetic `HarvestReport`.
That keeps the test fast and hermetic — Playwright/Chromium are not required
to verify CLI plumbing (option parsing, JSON shape, exit codes).

A full end-to-end harvest is exercised separately by running the CLI against
`_bench/corpus/` from the verification command in the M4 brief.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from slidify import harvester
from slidify.cli import cli
from slidify.harvester import (
    AtomCandidate,
    Cluster,
    ClusterExemplar,
    HarvestReport,
)


def _fake_report(corpus_dir: Path) -> HarvestReport:
    cluster = Cluster(
        id="auto-cluster-001",
        sig_hash="cafebabec0ffee01",
        signature="div(bg+brd)[][256x256]",
        instances=12,
        exemplars=[
            ClusterExemplar(
                slide_ref="landing-atoms.html#node-0",
                bbox_w=273, bbox_h=260,
                sample_text="comp.bento", sample_classes="tile",
            ),
            ClusterExemplar(
                slide_ref="sophisticated.html#node-3",
                bbox_w=273, bbox_h=260,
                sample_text="card", sample_classes="tile shadow-xl",
            ),
        ],
        sample_classes=["tile", "tile shadow-xl"],
        sample_text=["comp.bento", "card"],
        bbox_typical={"w_avg": 273, "h_avg": 260,
                      "w_min": 273, "w_max": 273,
                      "h_min": 260, "h_max": 260},
    )
    candidates = {
        cluster.id: AtomCandidate(
            candidate_atom_id="surf.tile-bordered",
            candidate_axis="surf",
            candidate_props={"border": True},
            confidence=0.75,
            reason="class 'tile' matched /\\btile\\b/",
        )
    }
    return HarvestReport(
        timestamp="2026-05-02T00:00:00+00:00",
        corpus_dir=str(corpus_dir),
        decks_processed=2,
        total_unmatched=12,
        unique_signatures=1,
        clusters=[cluster],
        candidates=candidates,
    )


def _make_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    # Two minimal HTML files so the input path passes existence checks.
    (corpus / "a.html").write_text(
        "<!doctype html><html><body><div class='tile'>x</div></body></html>",
        encoding="utf-8",
    )
    (corpus / "b.html").write_text(
        "<!doctype html><html><body><div class='tile'>y</div></body></html>",
        encoding="utf-8",
    )
    return corpus


def test_cli_harvest_writes_clusters_json(tmp_path, monkeypatch):
    corpus = _make_corpus(tmp_path)
    out = tmp_path / "out" / "clusters.json"

    monkeypatch.setattr(
        harvester, "aggregate_corpus",
        lambda corpus_dir, **kwargs: _fake_report(corpus_dir),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["harvest", str(corpus), "--output", str(out), "--top-n", "10"],
    )
    assert result.exit_code == 0, result.output
    assert out.exists(), f"clusters.json not written: {result.output}"

    payload = json.loads(out.read_text())
    # Top-level shape per CONTRACT-v2 §G.3.
    assert "harvest_run" in payload
    assert "clusters" in payload
    run = payload["harvest_run"]
    for k in ("timestamp", "corpus_dir",
              "decks_processed", "total_unmatched", "unique_signatures"):
        assert k in run, f"missing harvest_run.{k}"
    assert run["decks_processed"] == 2
    assert run["unique_signatures"] == 1

    [c] = payload["clusters"]
    for k in ("id", "sig_hash", "signature", "instances",
              "exemplars", "sample_classes", "sample_text",
              "bbox_typical", "candidate_atom_id", "candidate_axis",
              "candidate_props"):
        assert k in c, f"missing clusters[].{k}"
    assert c["candidate_atom_id"] == "surf.tile-bordered"
    assert c["candidate_axis"] == "surf"
    assert c["instances"] == 12

    # Human summary echoes top cluster lines + the output path.
    assert "auto-cluster-001" in result.output
    assert "Wrote clusters.json" in result.output


def test_cli_harvest_min_occurrences_passed_through(tmp_path, monkeypatch):
    corpus = _make_corpus(tmp_path)
    out = tmp_path / "clusters.json"

    seen_kwargs: dict = {}

    def _spy(corpus_dir, **kwargs):
        seen_kwargs.update(kwargs)
        return _fake_report(corpus_dir)

    monkeypatch.setattr(harvester, "aggregate_corpus", _spy)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["harvest", str(corpus), "--output", str(out),
         "--min-occurrences", "5"],
    )
    assert result.exit_code == 0, result.output
    assert seen_kwargs.get("min_occurrences") == 5


def test_cli_harvest_zero_decks_exits_nonzero(tmp_path, monkeypatch):
    corpus = _make_corpus(tmp_path)

    def _empty(corpus_dir, **kwargs):
        return HarvestReport(
            timestamp="2026-05-02T00:00:00+00:00",
            corpus_dir=str(corpus_dir),
            decks_processed=0,
            total_unmatched=0,
            unique_signatures=0,
        )

    monkeypatch.setattr(harvester, "aggregate_corpus", _empty)

    runner = CliRunner()
    result = runner.invoke(cli, ["harvest", str(corpus)])
    assert result.exit_code == 1


def test_cli_harvest_back_compat_out_json_alias(tmp_path, monkeypatch):
    corpus = _make_corpus(tmp_path)
    out = tmp_path / "old-style.json"

    monkeypatch.setattr(
        harvester, "aggregate_corpus",
        lambda corpus_dir, **kwargs: _fake_report(corpus_dir),
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["harvest", str(corpus), "--out-json", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "deprecated" in result.output.lower() or "deprecated" in result.stderr.lower()


def test_cli_harvest_promote_yaml_flag_does_not_NameError(tmp_path, monkeypatch):
    """Regression: post-merge, the harvest body called
    `promote_unmatched_to_yaml(ranked, ...)` where `ranked` had been
    renamed in the cluster-aggregated refactor. Every invocation of
    `slidify harvest --promote-yaml ...` raised NameError before the
    fix. Test asserts the flag now runs through and produces a YAML
    file (whether or not it has stubs is independent — min_count=1
    forces at least one).
    """
    corpus = _make_corpus(tmp_path)
    out_yaml = tmp_path / "promoted.yaml"
    monkeypatch.setattr(harvester, "aggregate_corpus", lambda *a, **kw: _fake_report(corpus))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "harvest",
            str(corpus),
            "--promote-yaml", str(out_yaml),
            "--min-count", "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Promoted" in result.output
    assert out_yaml.exists(), "expected the promoted YAML file to be written"
    body = out_yaml.read_text(encoding="utf-8")
    # The fake cluster has 12 instances >= min_count=1 so it should be promoted.
    assert "harvested-cafebabe" in body, (
        "expected the synthesized stub id derived from the cluster's "
        "sig_hash to appear in the promoted YAML"
    )
