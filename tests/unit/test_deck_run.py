# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The deck lane: what Python stages, what it harvests, and what it stays out of."""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from pathlib import Path

import pytest

from app import deck_run
from app.template_policy import BaselineSpecimen, build_prepared_manifest

from test_template_policy import BASELINE, _artifacts, _source, _slide_type

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "agy-worker" / "skills"


def _slide(title: str) -> str:
    return f"<html><body><h1 data-pptx-role='title'>{title}</h1></body></html>"


# --------------------------------------------------------------------------
# the harness picks its own skills


def test_system_prompt_points_at_the_skill_instead_of_restating_it():
    prompt = deck_run.DECK_SYSTEM
    assert "pixelpitch-deck" in prompt
    assert "workspace.md" in prompt
    # A pointer, not a manual. The number is generous; the failure it guards
    # against is someone pasting a playbook back in here.
    assert len(prompt) < 800


def test_no_skill_body_is_injected_into_python_prompts():
    """The harness must discover method by reading skills, not by being told.

    Every sentence of real content in a SKILL.md is a sentence the harness
    should reach by progressive disclosure. Finding one hard-coded in the
    Python means the prompt has quietly become a second, staler copy of the
    skill, and it will drift the moment the skill is edited.
    """
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((PROJECT_ROOT / "app").glob("*.py"))
    )
    skill_files = sorted(SKILLS_ROOT.glob("*/SKILL.md"))
    assert skill_files, "no skills found to check against"

    leaked: list[str] = []
    for path in skill_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            sentence = line.strip().lstrip("-*# ").strip()
            if len(sentence) < 40 or sentence.startswith(("|", "<!--")):
                continue
            if sentence in sources:
                leaked.append(f"{path.parent.name}: {sentence[:70]}")
    assert not leaked, "skill body text found in Python prompts:\n" + "\n".join(leaked)


# --------------------------------------------------------------------------
# staging


def test_brief_carries_the_whole_brief(tmp_path: Path):
    deck_run._write_brief(
        tmp_path,
        topic="centenary",
        audience="the board",
        goal="celebrate",
        slide_count=7,
        expressions=["white-ledger (Editorial Light / ledger_timeline)"],
    )
    brief = (tmp_path / "brief.md").read_text(encoding="utf-8")
    for needle in ("centenary", "the board", "celebrate", "exactly 7", "ledger_timeline"):
        assert needle in brief


def test_sample_spreads_across_the_specimens():
    paths = [Path(f"slide-{i:02d}.png") for i in range(1, 41)]
    sampled = deck_run._sample(paths, limit=8)
    assert len(sampled) == 8
    assert sampled[0].endswith("slide-01.png")
    # An evenly spread sample, not the first eight.
    assert sampled[-1].endswith("slide-36.png")


# --------------------------------------------------------------------------
# harvesting authored slides


def test_index_json_decides_the_running_order(tmp_path: Path):
    slides_dir = tmp_path / "slides"
    slides_dir.mkdir()
    (slides_dir / "01-a.html").write_text(_slide("First"))
    (slides_dir / "02-b.html").write_text(_slide("Second"))
    (slides_dir / "index.json").write_text(
        json.dumps(
            {
                "slides": [
                    {"file": "02-b.html", "role": "hero"},
                    {"file": "01-a.html", "title": "Pinned title"},
                ]
            }
        )
    )

    slides, error = deck_run.harvest_slides(tmp_path)

    assert error is None
    assert [slide["index"] for slide in slides] == [0, 1]
    # Titles come from index.json when given, from the slide's own title role
    # when not.
    assert [slide["title"] for slide in slides] == ["Second", "Pinned title"]
    assert [slide["role"] for slide in slides] == ["hero", "body"]


def test_filenames_order_the_deck_when_no_index_is_written(tmp_path: Path):
    slides_dir = tmp_path / "slides"
    slides_dir.mkdir()
    (slides_dir / "02-b.html").write_text(_slide("Second"))
    (slides_dir / "01-a.html").write_text(_slide("First"))

    slides, error = deck_run.harvest_slides(tmp_path)

    assert error is None
    assert [slide["title"] for slide in slides] == ["First", "Second"]


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        (lambda root: None, "no slides/ directory"),
        (lambda root: (root / "slides").mkdir(), "no slide HTML"),
    ],
)
def test_an_empty_workspace_is_reported_not_swallowed(tmp_path: Path, setup, expected):
    setup(tmp_path)
    slides, error = deck_run.harvest_slides(tmp_path)
    assert slides == []
    assert error is not None and expected in error


def test_an_index_naming_a_missing_file_is_an_error(tmp_path: Path):
    slides_dir = tmp_path / "slides"
    slides_dir.mkdir()
    (slides_dir / "01-a.html").write_text(_slide("First"))
    (slides_dir / "index.json").write_text(json.dumps({"slides": [{"file": "gone.html"}]}))

    slides, error = deck_run.harvest_slides(tmp_path)

    assert slides == []
    assert error is not None and "gone.html" in error


def test_a_corrupt_index_falls_back_to_filenames(tmp_path: Path):
    slides_dir = tmp_path / "slides"
    slides_dir.mkdir()
    (slides_dir / "01-a.html").write_text(_slide("First"))
    (slides_dir / "index.json").write_text("{not json")

    slides, error = deck_run.harvest_slides(tmp_path)

    assert error is None
    assert [slide["title"] for slide in slides] == ["First"]


# --------------------------------------------------------------------------
# inlining local images


PLATE = b"\x89PNG\r\n\x1a\nnot-really-a-png-but-bytes-are-bytes"


def _plate_at(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PLATE)


INLINED = "data:image/png;base64," + base64.b64encode(PLATE).decode("ascii")


def test_a_relative_plate_crosses_to_the_renderer_as_bytes(tmp_path: Path):
    slides_dir = tmp_path / "slides"
    slides_dir.mkdir()
    _plate_at(tmp_path / "clean" / "slide-01.png")
    (slides_dir / "01-a.html").write_text(
        '<html><body><img class="plate" src="../clean/slide-01.png" alt="Clean Plate">'
        "<h1 data-pptx-role='title'>First</h1></body></html>"
    )

    slides, error = deck_run.harvest_slides(tmp_path)

    assert error is None
    # The slide leaves this module as a bare string, so a relative src resolves
    # against nothing on the renderer and slidify emits no picture for an <img>
    # the DOM still declares. That is the editability failure, in one assert.
    assert "../clean/slide-01.png" not in slides[0]["html"]
    assert INLINED in slides[0]["html"]


@pytest.mark.parametrize(
    ("src", "plate"),
    [
        ("../clean/slide-01.png", "clean/slide-01.png"),
        ("clean/slide-01.png", "clean/slide-01.png"),
    ],
)
def test_either_bundle_prefix_convention_resolves(tmp_path: Path, src: str, plate: str):
    # Bundles disagree about the prefix. Example Retail writes `../clean/x.png`
    # relative to `baselines/`; Everyday writes `clean/x.png` from the same
    # place, which is broken even on disk. Neither bundle has to be re-cut.
    baselines = tmp_path / "baselines"
    baselines.mkdir()
    _plate_at(tmp_path / plate)

    html = deck_run._inline_local_images(
        f'<img src="{src}">', base_dir=baselines, workdir=tmp_path
    )

    assert INLINED in html


def test_either_quote_style_is_inlined(tmp_path: Path):
    # The designed lane hands this an LLM's markup, which quotes attributes
    # however it likes. A double-quote-only match would silently skip half of
    # it and the slide would fail conversion with nothing to point at.
    _plate_at(tmp_path / "art.png")

    html = deck_run._inline_local_images(
        "<img src='art.png'><img src=\"art.png\">", base_dir=tmp_path, workdir=tmp_path
    )

    assert html.count(INLINED) == 2
    assert html.startswith("<img src='data:")


@pytest.mark.parametrize(
    "src",
    [
        "data:image/png;base64,AAAA",
        "https://cdn.example.com/plate.png",
        "http://cdn.example.com/plate.png",
        "//cdn.example.com/plate.png",
    ],
)
def test_sources_that_already_resolve_are_left_alone(tmp_path: Path, src: str):
    html = deck_run._inline_local_images(
        f'<img src="{src}">', base_dir=tmp_path, workdir=tmp_path
    )

    assert html == f'<img src="{src}">'


def test_a_path_out_of_the_bundle_is_refused(tmp_path: Path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    _plate_at(tmp_path / "outside" / "secret.png")

    html = deck_run._inline_local_images(
        '<img src="../outside/secret.png">', base_dir=workdir, workdir=workdir
    )

    assert html == '<img src="../outside/secret.png">'


def test_a_missing_image_is_left_for_the_renderer_to_report(tmp_path: Path):
    html = deck_run._inline_local_images(
        '<img src="clean/gone.png">', base_dir=tmp_path, workdir=tmp_path
    )

    assert html == '<img src="clean/gone.png">'


# --------------------------------------------------------------------------
# live previews


def test_a_slide_is_published_once_its_write_settles(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(deck_run, "_WATCH_SECONDS", 0.05)
    slides_dir = tmp_path / "slides"
    slides_dir.mkdir()
    seen: list[dict] = []
    stop = threading.Event()
    deck_run._watch_slides(tmp_path, seen.append, stop)
    try:
        (slides_dir / "03-c.html").write_text(_slide("Third"))
        time.sleep(0.3)
        assert [(s["index"], s["title"]) for s in seen] == [(2, "Third")]

        # A rewrite republishes; last write wins, as the contract says.
        (slides_dir / "03-c.html").write_text(_slide("Third revised"))
        time.sleep(0.3)
        assert [s["title"] for s in seen] == ["Third", "Third revised"]
    finally:
        stop.set()


def test_a_failing_preview_never_breaks_the_turn(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(deck_run, "_WATCH_SECONDS", 0.05)
    (tmp_path / "slides").mkdir()
    stop = threading.Event()

    def explode(_slide):
        raise RuntimeError("preview backend is down")

    thread = deck_run._watch_slides(tmp_path, explode, stop)
    try:
        (tmp_path / "slides" / "01-a.html").write_text(_slide("First"))
        time.sleep(0.3)
        assert thread.is_alive()
    finally:
        stop.set()


# --------------------------------------------------------------------------
# harvesting seam patches


@pytest.fixture()
def bundle(tmp_path: Path):
    # This fixture tests patch application, independently of source admission.
    from types import SimpleNamespace
    manifest = SimpleNamespace(slide_types=[_slide_type()])
    (tmp_path / "baselines").mkdir()
    (tmp_path / "baselines" / "statement.html").write_text(BASELINE)
    specimen = BaselineSpecimen.from_html(manifest.slide_types[0], BASELINE)
    return manifest, specimen


def _patch(specimen, **overrides) -> dict:
    slide = {
        "slide_index": 0,
        "slide_type_id": "statement",
        "baseline_sha256": specimen.sha256,
        "title": "Revenue tripled",
        "role": "hero",
        "replacements": [
            {"seam_id": "title", "html_fragment": "Revenue <em>tripled</em>"},
            {"seam_id": "aid", "html_fragment": "<span>3x</span>"},
        ],
    }
    slide.update(overrides)
    return {"slides": [slide]}


def test_an_admitted_patch_changes_the_seams_and_nothing_else(tmp_path, bundle):
    manifest, specimen = bundle
    (tmp_path / "patches.json").write_text(json.dumps(_patch(specimen)))

    slides, error = deck_run.harvest_patches(tmp_path, manifest, 1)

    assert error is None
    html = slides[0]["html"]
    assert "Revenue <em>tripled</em>" in html
    assert "Sample" not in html
    # Everything outside the seams survives untouched.
    assert "Protected furniture" in html
    assert slides[0]["title"] == "Revenue tripled"


def test_source_page_number_is_rebound_to_output_order(tmp_path, bundle):
    manifest, _ = bundle
    html = BASELINE.replace("</body>", "<div><!-- pp:slide-number -->26</div></body>")
    specimen = BaselineSpecimen.from_html(manifest.slide_types[0], html)
    (tmp_path / "baselines/statement.html").write_text(html)
    (tmp_path / "patches.json").write_text(json.dumps(_patch(specimen)))
    slides, error = deck_run.harvest_patches(tmp_path, manifest, 1)
    assert error is None
    assert "<!-- pp:slide-number -->1" in slides[0]["html"]
    assert "<!-- pp:slide-number -->26" not in slides[0]["html"]


@pytest.mark.parametrize(
    ("overrides", "count", "expected"),
    [
        ({"baseline_sha256": "0" * 64}, 1, "stale baseline digest"),
        ({"slide_index": "0"}, 1, "must be an integer"),
        ({"slide_index": 4}, 1, "invalid or duplicate"),
        ({"slide_type_id": "nope"}, 1, "unknown slide type"),
        ({}, 2, "exactly 2 slides"),
    ],
)
def test_a_bad_patch_is_refused_with_the_reason(tmp_path, bundle, overrides, count, expected):
    manifest, specimen = bundle
    (tmp_path / "patches.json").write_text(json.dumps(_patch(specimen, **overrides)))

    slides, error = deck_run.harvest_patches(tmp_path, manifest, count)

    assert slides == []
    assert error is not None and expected in error


def test_a_missing_patch_file_is_reported(tmp_path, bundle):
    manifest, _ = bundle
    slides, error = deck_run.harvest_patches(tmp_path, manifest, 1)
    assert slides == []
    assert error is not None and "no patches.json" in error


def test_a_dropped_seam_is_refused(tmp_path, bundle):
    manifest, specimen = bundle
    patch = _patch(specimen)
    patch["slides"][0]["replacements"] = patch["slides"][0]["replacements"][:1]
    (tmp_path / "patches.json").write_text(json.dumps(patch))

    slides, error = deck_run.harvest_patches(tmp_path, manifest, 1)

    assert slides == []
    assert error is not None


def test_slug_normalises_corporate_file_noise():
    assert deck_run.slug_for("Copy of Acme Presentation Template") == "acme"
    assert deck_run.slug_for("Example Retail 100Y") == "example-retail-100y"
    assert deck_run.slug_for("") == "default"
    # Slugging an already-slugged name is a no-op, which is what lets callers
    # pass either form.
    assert deck_run.slug_for("example-retail-100y") == "example-retail-100y"


def test_title_falls_back_when_the_slide_declares_none():
    assert deck_run._title_of("<html><body>no title role</body></html>", "Slide 3") == "Slide 3"
    assert (
        deck_run._title_of(
            "<h1 data-pptx-role=\"title\">Revenue <em>tripled</em>\n  again</h1>", "x"
        )
        == "Revenue tripled again"
    )


def test_title_regex_is_anchored_to_the_role_attribute():
    # A stray marker in a comment or an attribute value must not become a title.
    assert re.search(deck_run._TITLE_ELEMENT, "<p>data-pptx-role=title</p>") is None


def test_object_slug_drops_the_extension_the_bundle_prefix_never_had():
    # A prepared bundle is published under templates/<slug>/, derived from the
    # file name without its suffix. Slugging the raw object name appends
    # "-pptx" and points every lookup at a prefix that does not exist.
    assert (
        deck_run.slug_for_object("Copy of 100 Years Example Retail Group Presentation Template.pptx")
        == "100-years-example-retail-group"
    )
    assert (
        deck_run.slug_for_object("gs://bucket/templates/Copy of Acme Template.pptx")
        == "acme"
    )
    # Dots inside the name are not the extension.
    assert deck_run.slug_for_object("Q1.2026 Review.pptx") == "q1-2026-review"
    # Extensionless names, and an already-resolved slug, both survive.
    assert deck_run.slug_for_object("example-retail-100y") == "example-retail-100y"
    assert deck_run.slug_for_object("") == "default"


def test_a_display_name_drops_the_filing_noise_a_slug_has_to_keep():
    # The slug cannot change: bundles are already published under it. The
    # label a user reads is free to be stricter, and "Copy of PLEASE MAKE A
    # COPY_Everyday AU main template feb 2026.pptx" as a chip is unreadable.
    assert (
        deck_run.display_name_for_object(
            "Copy of PLEASE MAKE A COPY_Everyday AU main template feb 2026.pptx"
        )
        == "Everyday AU main feb 2026"
    )
    assert (
        deck_run.display_name_for_object(
            "gs://b/templates/Copy of 100 Years Example Retail Group Presentation Template.pptx"
        )
        == "100 Years Example Retail Group"
    )
    assert (
        deck_run.display_name_for_object("Copy of Primary Connect Slides 16 x 9 template.pptx")
        == "Primary Connect Slides 16 x 9"
    )
    # Casing is preserved, never title-cased: "AU" is not "Au".
    assert deck_run.display_name_for_object("Everyday AU.pptx") == "Everyday AU"
    # A name that is nothing but noise keeps something to render.
    assert deck_run.display_name_for_object("template.pptx") == "template"
    assert deck_run.display_name_for_object("") == "Template"
