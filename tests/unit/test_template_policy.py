# Copyright 2026 Google LLC

"""Proof for immutable prepared bundles and guarded HTML seams."""

import json

import pytest

from app.template_policy import (
    AdmittedSlideType,
    BaselineSpecimen,
    DeckProvenance,
    SeamReplacement,
    SeamSpec,
    SlidePatch,
    SourceIdentity,
    TemplatePolicyError,
    apply_seam_patch,
    build_prepared_manifest,
    sha256_bytes,
    validate_prepared_manifest,
)

BASELINE = """<!doctype html><html><body>
<section class="slide" style="width:1280px;height:720px">
  <h1><!-- pp:seam:title:start -->Sample<!-- pp:seam:title:end --></h1>
  <div class="aid"><!-- pp:seam:aid:start --><span>10%</span><!-- pp:seam:aid:end --></div>
  <footer>Protected furniture</footer>
</section></body></html>"""


def _slide_type() -> AdmittedSlideType:
    return AdmittedSlideType(
        id="statement",
        archetype="hero/section",
        baseline_path="baselines/statement.html",
        preview_path="specimens/statement.png",
        seams=(
            SeamSpec("title", "content", frozenset({"em"}), frozenset()),
            SeamSpec(
                "aid",
                "visual_aid",
                frozenset({"div", "span", "svg", "path"}),
                frozenset({"color", "font-size", "display"}),
            ),
        ),
    )


def _artifacts() -> dict[str, bytes]:
    from app.template_baselines import compile_source_baselines

    template = b"PK template bytes"
    record = {"schema_version": 2, "slide": 1, "layout": "Title and body", "canvas": [1280, 720],
              "shapes": [{"shape_id": 1, "placeholder": "TITLE", "x": 48.5, "y": 56, "w": 1183.5, "h": 80.8,
                          "text": [{"runs": [{"text": "Sample", "font": "Roboto Medium", "size_px": 40, "color": "#0E0D26"}]}]}]}
    result = {
        "template.pptx": template,
        "brand-contract.json": b"{}",
        "spec/slide-01.json": json.dumps(record).encode(),
        "base/slide-01.png": b"base",
        "clean/slide-01.png": b"clean",
    }
    result["source-extraction.json"] = json.dumps({"schema_version": 2, "slide_count": 1,
        "source_sha256": sha256_bytes(template), "plate_method": "ooxml-remove-slide-text",
        "artifacts": {name: sha256_bytes(value) for name, value in result.items() if name.startswith(("spec/", "base/", "clean/"))}}).encode()
    result.update(compile_source_baselines(result))
    return result


def _source(artifacts: dict[str, bytes]) -> SourceIdentity:
    return SourceIdentity(
        gs_uri="gs://bucket/templates/acme.pptx",
        generation="42",
        etag="etag-42",
        sha256=sha256_bytes(artifacts["template.pptx"]),
    )


def test_manifest_identity_is_deterministic_and_hash_validated():
    artifacts = _artifacts()
    first = build_prepared_manifest(source=_source(artifacts), artifacts=artifacts)
    second = build_prepared_manifest(source=_source(artifacts), artifacts=artifacts)

    assert first.bundle_id == second.bundle_id
    admitted = validate_prepared_manifest(
        first.to_dict(),
        artifacts,
        expected_source_uri="gs://bucket/templates/acme.pptx",
        expected_generation="42",
        expected_etag="etag-42",
    )
    assert admitted.slide_types[0].id == "source-01"

    changed = {**artifacts, "clean/slide-01.png": b"mutated"}
    with pytest.raises(TemplatePolicyError, match="hash mismatch"):
        validate_prepared_manifest(first.to_dict(), changed)


def test_manifest_requires_real_admitted_baseline_specimens():
    artifacts = _artifacts()
    artifacts.pop("baselines/source-01.html")

    with pytest.raises(TemplatePolicyError, match=r"missing baselines/source-01\.html"):
        build_prepared_manifest(source=_source(artifacts), artifacts=artifacts)


def test_legacy_preparation_cannot_be_relabelled_as_template_fidelity():
    artifacts = _artifacts()
    artifacts.pop("source-extraction.json")
    with pytest.raises(TemplatePolicyError, match="source-extraction"):
        build_prepared_manifest(source=_source(artifacts), artifacts=artifacts)


def test_every_source_page_requires_a_real_render_and_clean_plate():
    artifacts = _artifacts()
    extraction = json.loads(artifacts["source-extraction.json"])
    extraction["slide_count"] = 28
    artifacts["source-extraction.json"] = json.dumps(extraction).encode()
    with pytest.raises(TemplatePolicyError, match="slide-02"):
        build_prepared_manifest(source=_source(artifacts), artifacts=artifacts)


def test_reconstructed_geometry_cannot_replace_the_source_geometry():
    artifacts = _artifacts()
    artifacts["baselines/source-01.html"] = artifacts["baselines/source-01.html"].replace(b"top:56px", b"top:36px")
    with pytest.raises(TemplatePolicyError, match="source geometry"):
        build_prepared_manifest(source=_source(artifacts), artifacts=artifacts)


def test_seam_patch_changes_only_declared_content():
    baseline = BaselineSpecimen.from_html(_slide_type(), BASELINE)
    authored = apply_seam_patch(
        baseline,
        SlidePatch(
            baseline_sha256=baseline.sha256,
            replacements=(
                SeamReplacement("title", "A sharper <em>assertion</em>"),
                SeamReplacement(
                    "aid", '<div style="display:flex;color:#111"><span>42%</span></div>'
                ),
            ),
        ),
    )

    assert "A sharper <em>assertion</em>" in authored
    assert "42%" in authored
    assert "Protected furniture" in authored


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        (
            SlidePatch("stale", (SeamReplacement("title", "x"), SeamReplacement("aid", ""))),
            "stale baseline",
        ),
        (
            None,
            "duplicates a seam",
        ),
        (
            SlidePatch("", (SeamReplacement("title", "x"),)),
            "missing seams",
        ),
    ],
)
def test_seam_patch_rejects_stale_duplicate_and_missing(patch, message):
    baseline = BaselineSpecimen.from_html(_slide_type(), BASELINE)
    if patch is None:
        patch = SlidePatch(
            baseline.sha256,
            (
                SeamReplacement("title", "x"),
                SeamReplacement("title", "y"),
                SeamReplacement("aid", ""),
            ),
        )
    elif patch.baseline_sha256 == "":
        patch = SlidePatch(baseline.sha256, patch.replacements)
    with pytest.raises(TemplatePolicyError, match=message):
        apply_seam_patch(baseline, patch)


def test_seam_patch_rejects_unknown_tags_and_styles():
    baseline = BaselineSpecimen.from_html(_slide_type(), BASELINE)
    for fragment, message in (
        ("<script>alert(1)</script>", "does not allow <script>"),
        ('<div style="position:fixed">x</div>', "does not allow style"),
    ):
        with pytest.raises(TemplatePolicyError, match=message):
            apply_seam_patch(
                baseline,
                SlidePatch(
                    baseline.sha256,
                    (
                        SeamReplacement("title", "Safe"),
                        SeamReplacement("aid", fragment),
                    ),
                ),
            )


def test_provenance_is_a_closed_set_of_one():
    # There is one lane out of this system: reconstructed HTML converted by
    # Slidify. A second member would mean a second, unaudited way to ship a
    # deck, so the count is the assertion.
    assert [member.value for member in DeckProvenance] == [
        "HTML_RECONSTRUCTED_BY_SLIDIFY"
    ]
