"""Tests for `slidify.cli.promote_unmatched_to_yaml` — the harvester's
auto-promote path that turns unmatched signatures into a YAML review queue.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from slidify.cli import _build_promotion_stubs, promote_unmatched_to_yaml
from slidify.models import UnmatchedSignature


def _mk_sig(sig_hash: str, sig: str, n: int) -> UnmatchedSignature:
    return UnmatchedSignature(
        sig=sig,
        sig_hash=sig_hash,
        bbox_w=400,
        bbox_h=200,
        sample_classes="rounded-2xl shadow-xl",
        sample_text="Hello world",
        n_occurrences=n,
    )


def test_promote_yaml_appends_stubs(tmp_path: Path):
    out = tmp_path / "harvested.yaml"
    sigs = [
        _mk_sig("aaaaaaaa11111111", "div(bgi=other)[rounded-2xl][320x192]", 5),
        _mk_sig("bbbbbbbb22222222", "section(txt)[font-serif][640x320]", 3),
        # Below min_count — must not produce a stub.
        _mk_sig("cccccccc33333333", "span()[][16x16]", 1),
    ]

    n_new = promote_unmatched_to_yaml(sigs, out, min_count=3)
    assert n_new == 2

    text = out.read_text(encoding="utf-8")
    payload = yaml.safe_load(text)
    assert isinstance(payload, dict)
    patterns = payload.get("patterns", [])
    ids = [p["id"] for p in patterns]
    assert "harvested-aaaaaaaa" in ids
    assert "harvested-bbbbbbbb" in ids
    # The under-threshold signature is not promoted.
    assert "harvested-cccccccc" not in ids

    # Each stub defaults to Raster + low confidence.
    by_id = {p["id"]: p for p in patterns}
    sb = by_id["harvested-aaaaaaaa"]
    assert sb["emit"]["kind"] == "Raster"
    assert sb["emit"]["confidence"] == 0.5
    # Tag is uppercased so the matcher's case-sensitive `anchor.tag_in`
    # predicate hits real DOM tagName values (always uppercase).
    assert sb["match"]["anchor.tag_in"] == ["DIV"]
    assert by_id["harvested-bbbbbbbb"]["match"]["anchor.tag_in"] == ["SECTION"]


def test_promote_yaml_is_idempotent(tmp_path: Path):
    """Re-running with the same inputs must not duplicate entries."""
    out = tmp_path / "harvested.yaml"
    sigs = [
        _mk_sig("dddddddd44444444", "div(txt)[][320x192]", 4),
    ]
    n1 = promote_unmatched_to_yaml(sigs, out, min_count=3)
    n2 = promote_unmatched_to_yaml(sigs, out, min_count=3)
    assert n1 == 1
    assert n2 == 0  # already present, nothing new

    payload = yaml.safe_load(out.read_text(encoding="utf-8"))
    ids = [p["id"] for p in payload["patterns"]]
    assert ids.count("harvested-dddddddd") == 1


def test_build_promotion_stubs_min_count_filter():
    """The pure builder honours --min-count without touching the filesystem."""
    sigs = [
        _mk_sig("aa", "div()[][0x0]", 1),
        _mk_sig("bb", "div()[][0x0]", 5),
        _mk_sig("cc", "div()[][0x0]", 10),
    ]
    stubs = _build_promotion_stubs(sigs, min_count=5)
    assert len(stubs) == 2
    assert {s["id"][-2:] for s in stubs} == {"bb", "cc"}
