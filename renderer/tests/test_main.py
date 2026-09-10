# Copyright 2026 Google LLC

"""Deterministic renderer quality checks."""

import base64
import io
from pathlib import Path
from subprocess import CompletedProcess

from fastapi.testclient import TestClient
import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches, Pt
from renderer.main import (
    app,
    PopulateSlide,
    _fill_source_slide,
    _pick_diverse_layout,
    _pick_source_slide,
    _pptx_readability_report,
    _quality_report,
    _run_slidify,
    _slidify_failure_reason,
)


class _PlaceholderType:
    def __init__(self, name: str):
        self.name = name

    def __str__(self) -> str:
        return self.name


class _Placeholder:
    def __init__(self, kind: str):
        self.placeholder_format = type(
            "PlaceholderFormat", (), {"type": _PlaceholderType(kind)}
        )()


class _Layout:
    def __init__(self, name: str, body_slots: int):
        self.name = name
        self.placeholders = [_Placeholder("TITLE")] + [
            _Placeholder("BODY") for _ in range(body_slots)
        ]


def _add_text_slide(prs: Presentation, text: str, size_pt: float) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(1))
    run = box.text_frame.paragraphs[0].add_run()
    run.text = text
    run.font.size = Pt(size_pt)


def test_pptx_readability_report_flags_only_text_below_floor(tmp_path: Path):
    prs = Presentation()
    _add_text_slide(prs, "Too small", 10)
    _add_text_slide(prs, "Readable", 18)
    deck = tmp_path / "deck.pptx"
    prs.save(deck)

    report = _pptx_readability_report(deck)

    assert report == [
        {
            "slide_index": 0,
            "font_size_pt": 10.0,
            "minimum_pt": 13.5,
            "sample_text": "Too small",
        }
    ]


def test_layout_picker_uses_each_matching_layout_before_repeating():
    layouts = [_Layout("A", 1), _Layout("B", 1), _Layout("C", 2)]
    usage: dict[int, int] = {}

    picked = [_pick_diverse_layout(layouts, 1, usage).name for _ in range(4)]

    assert picked[:3] == ["A", "B", "C"]
    assert picked[3] == "A"


def test_source_picker_uses_content_layout_then_visual_specimen():
    spec = PopulateSlide(
        archetype="title+body",
        title="Enterprise AI",
        subtitle="A concise argument",
        body=["Evidence"],
    )
    body = {
        "index": 25,
        "archetype": "title+body",
        "body_slots": 1,
        "pictures": 0,
        "groups": 0,
    }
    visual = {
        "index": 2,
        "archetype": "hero/section",
        "body_slots": 0,
        "pictures": 3,
        "groups": 0,
    }

    assert _pick_source_slide([visual, body], spec) is body
    assert _pick_source_slide([visual], spec) is visual


def test_source_population_preserves_real_picture_and_clears_prototype_copy():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "Prototype title"
    slide.placeholders[1].text = "Prototype subtitle"
    # Valid 1x1 transparent PNG. The regression is relationship preservation,
    # not image rendering, so a tiny deterministic fixture is sufficient.
    png = io.BytesIO(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAHnOcQAAAAABJRU5ErkJggg=="
        )
    )
    slide.shapes.add_picture(png, Inches(8), Inches(1), width=Inches(1))
    before = sum(
        shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in slide.shapes
    )

    report = _fill_source_slide(
        slide,
        PopulateSlide(
            archetype="hero/section",
            title="Preserved design",
            subtitle="Real photography remains attached",
        ),
        1,
        prs.slide_height,
    )

    after = sum(shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in slide.shapes)
    assert before == after == 1
    assert slide.shapes.title.text == "Preserved design"
    assert slide.placeholders[1].text == "Real photography remains attached"
    assert report["font_sizes_pt"]["TITLE"][0] >= 30
    assert report["font_sizes_pt"]["SUBTITLE"][0] >= 22
    assert report["omitted_body_blocks"] == 0


def test_render_rejects_failed_quality_before_upload(monkeypatch, tmp_path: Path):
    def fake_slidify(_slide_dir, out, report):
        Presentation().save(out)
        report.write_text(
            '{"editabilityPassed": false, "editabilityFailingSlides": [0]}',
            encoding="utf-8",
        )
        return True, ""

    monkeypatch.setattr("renderer.main._run_slidify", fake_slidify)
    monkeypatch.setattr(
        "renderer.main._upload_to_gcs",
        lambda *_args, **_kwargs: pytest.fail("failed deck was uploaded"),
    )

    response = TestClient(app).post(
        "/render",
        json={
            "deck_title": "Rejected",
            "brand_id": "reference",
            "slides": [{"title": "One", "html": "<html>one</html>"}],
            "preview_pages": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "quality admission" in response.json()["error"].lower()


def test_render_rejects_missing_source_content_before_upload(monkeypatch):
    def fake_slidify(_slide_dir, out, report):
        prs = Presentation()
        _add_text_slide(prs, "Title", 30)
        prs.save(out)
        report.write_text('{"editabilityPassed": true}')
        return True, ""

    monkeypatch.setattr("renderer.main._run_slidify", fake_slidify)
    monkeypatch.setattr("renderer.main._upload_to_gcs", lambda *_: pytest.fail("incomplete deck uploaded"))
    response = TestClient(app).post("/render", json={"deck_title": "Rejected", "brand_id": "reference",
        "slides": [{"html": "<html><body><h1>Title</h1><table><tr><td>Missing cells</td></tr></table></body></html>"}], "preview_pages": 0})
    assert response.json()["ok"] is False
    assert response.json()["content_failures"][0]["missing_words"] == 2


def test_full_profile_measures_fidelity_without_raster_correction(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDIFY_PROFILE", "full")
    monkeypatch.setattr("renderer.main.shutil.which", lambda name: f"/bin/{name}")
    out, report = tmp_path / "deck.pptx", tmp_path / "report.json"
    commands = []

    def convert(cmd, **_kwargs):
        commands.append(cmd)
        Presentation().save(out)
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("renderer.main.subprocess.run", convert)
    assert _run_slidify(tmp_path / "slides", out, report) == (True, "")
    assert len(commands) == 1
    assert "--low-memory" in commands[0]
    assert "--no-oracle" not in commands[0]
    assert "--no-tier3" not in commands[0]


def test_visual_discrepancies_remain_in_quality_report(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(
        '{"editability_passed":true,"fidelity_reports":['
        '{"slide_index":0,"passed":false,"ssim":0.89,"ocr_recall":0.94}]}'
    )
    quality = _quality_report(report)
    assert quality["editability_passed"] is True
    assert quality["fidelity_failures"][0]["ocr_recall"] == 0.94


_GLYPH_DUMP = (
    "Glyph names: ['a', 'b', 'c', 'five', 'fl', 'four', 'g', 'greater', "
    "'hyphen', 'l.alt', 'uni2047', 'y.alt', 'zero']\n"
    "Glyph IDs: [0, 1, 2, 3, 4, 5]\n"
    "Retaining 120 glyphs\n"
    "hmtx subsetted\n"
    "GDEF pruned\n"
)


def test_glyph_narration_is_not_reported_as_the_failure_reason():
    """Font embedding is the last thing to write before slidify exits.

    A fixed-size tail of stderr is therefore glyph names, and that tail used
    to reach the agent, which showed it to the user as the whole explanation.
    """
    assert _slidify_failure_reason(_GLYPH_DUMP, "") == ""


def test_the_real_error_line_is_picked_out_of_the_narration():
    stderr = (
        "2026-08-31T06:59:47Z [info     ] api.fonts_embedded n=4\n"
        + _GLYPH_DUMP
        + "slidify: conversion failed: Chromium is not installed\n"
    )

    reason = _slidify_failure_reason(stderr, "")

    assert reason == "slidify: conversion failed: Chromium is not installed"


def test_a_structlog_error_event_counts_as_a_reason():
    stderr = "2026-08-31T06:59:47Z [error    ] emitter.save_failed path=/tmp/out.pptx\n"

    assert "emitter.save_failed" in _slidify_failure_reason(stderr, "")


def test_stdout_is_only_consulted_when_stderr_has_no_reason():
    assert _slidify_failure_reason("", "MemoryError: out of memory") == (
        "MemoryError: out of memory"
    )
