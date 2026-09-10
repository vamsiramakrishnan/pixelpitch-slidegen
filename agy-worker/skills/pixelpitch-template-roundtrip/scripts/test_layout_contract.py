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

"""Each of these is a defect that shipped in a real customer deck.

Twenty of twenty-eight plates arrived flat, three slides sat on rectangles an
authoring turn invented, seven baselines were unregistered so the seam route
never ran, and the author reinvented geometry the spec already held. Nothing
caught any of it, because the only question ever asked of a plate was whether
an ``<img>`` tag pointed at something.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from layout_contract import (
    ERASED_LIMIT,
    FURNITURE_ERASED_LIMIT,
    bundle_findings,
    build,
    classify,
    plate_findings,
    plate_health,
    sha256,
)
from PIL import Image

CANVAS = (1280.0, 720.0)
WHITE = (255, 255, 255)
BLUE = (25, 113, 237)
TITLE_RECT = (48.0, 56.0, 1183.5, 80.8)


def canvas(colour=WHITE) -> np.ndarray:
    return np.full((720, 1280, 3), colour, dtype=np.uint8)


LOCKUP_RECT = (46.9, 616.3, 322.4, 67.5)


def motif(image: np.ndarray) -> np.ndarray:
    """The template's own artwork: the wave the occasioning template sweeps
    across its lower right, plus the lockup every slide carries bottom-left."""
    out = image.copy()
    out[360:720, 700:1280] = BLUE
    out[616:684, 47:369] = BLUE
    return out


def without_lockup(image: np.ndarray) -> np.ndarray:
    """Everything the template draws except its brand mark."""
    out = motif(image)
    out[616:684, 47:369] = WHITE
    return out


def headline(image: np.ndarray) -> np.ndarray:
    """Glyph-width bars inside the title rect, sparse the way text is."""
    out = image.copy()
    for x in range(60, 700, 14):
        out[60:130, x : x + 4] = (14, 13, 38)
    return out


def save(path, image: np.ndarray):
    Image.fromarray(image).save(path)
    return path


def title_shape(**overrides) -> dict:
    x, y, w, h = TITLE_RECT
    return {
        "kind": "PLACEHOLDER",
        "placeholder": "TITLE",
        "x": x, "y": y, "w": w, "h": h,
        "text": [{"runs": [{"text": "Headline 60pt Roboto Medium"}]}],
        **overrides,
    }


def lockup(slide_no: int) -> dict:
    """The recurring brand mark, small and bottom-left, as the spec records it."""
    return {
        "kind": "PICTURE",
        "x": 46.9, "y": 616.3, "w": 322.4, "h": 67.5,
        "image": f"s{slide_no:02d}-img01.png",
    }


@pytest.fixture
def template(tmp_path):
    """A staged spec-out/ and clean/ pair, one slide per supplied shape list."""

    def stage(slides: list[dict], plates: dict[int, np.ndarray] | None = None):
        spec_root = tmp_path / "spec-out"
        spec_dir, base_dir, media = spec_root / "spec", spec_root / "base", spec_root / "spec" / "media"
        clean = tmp_path / "clean"
        for directory in (spec_dir, base_dir, media, clean):
            directory.mkdir(parents=True, exist_ok=True)

        for index, shapes in enumerate(slides, start=1):
            (spec_dir / f"slide-{index:02d}.json").write_text(
                json.dumps(
                    {"slide": index, "layout": "Text Styles_1",
                     "canvas": list(CANVAS), "shapes": shapes}
                )
            )
            for shape in shapes:
                if shape.get("image"):
                    save(media / shape["image"], canvas(BLUE)[:68, :322])
            base = headline(motif(canvas()))
            save(base_dir / f"slide-{index:02d}.png", base)
            given = (plates or {}).get(index)
            save(clean / f"slide-{index:02d}.png", motif(canvas()) if given is None else given)
        return spec_root, clean

    return stage


# --------------------------------------------------------------------------
# plate health


def test_a_plate_that_kept_the_artwork_and_lost_the_text_is_live(tmp_path):
    """What a correct `deck plates` run produces. The painter may touch the
    text rects and nothing else, so the motif has to survive it untouched."""
    base = save(tmp_path / "b.png", headline(motif(canvas())))
    plate = save(tmp_path / "p.png", motif(canvas()))
    erased, _, _ = plate_health(base, plate, [TITLE_RECT], CANVAS)
    assert erased <= ERASED_LIMIT


def test_a_plate_flattened_to_one_colour_is_dead(tmp_path):
    """Twenty of twenty-eight shipped like this. The logo and the wave motif
    were gone, so no slide authored on one could look like the template, and
    the count of `<img>` tags was identical either way."""
    base = save(tmp_path / "b.png", headline(motif(canvas())))
    plate = save(tmp_path / "p.png", canvas())
    erased, _, _ = plate_health(base, plate, [TITLE_RECT], CANVAS)
    assert erased > ERASED_LIMIT


def test_losing_only_the_lockup_needs_the_furniture_rect_to_see_it(tmp_path):
    """No global threshold can catch this. A lockup is 2.3% of the canvas, so
    a plate that keeps the whole layout and drops only the brand mark scores
    under any at-large limit worth having, and is exactly as unusable. The
    contract already knows where the furniture is, so it is measured there."""
    base = save(tmp_path / "b.png", headline(motif(canvas())))
    plate = save(tmp_path / "p.png", without_lockup(canvas()))
    erased, _, pieces = plate_health(
        base, plate, [TITLE_RECT], CANVAS, [LOCKUP_RECT]
    )
    assert erased <= ERASED_LIMIT
    assert pieces[0] > FURNITURE_ERASED_LIMIT


def test_furniture_that_survived_reads_as_intact(tmp_path):
    """The other half of the pair. Widening a gate must not cost the author a
    false alarm on every correctly painted plate."""
    base = save(tmp_path / "b.png", headline(motif(canvas())))
    plate = save(tmp_path / "p.png", motif(canvas()))
    _, _, pieces = plate_health(base, plate, [TITLE_RECT], CANVAS, [LOCKUP_RECT])
    assert pieces == [pytest.approx(0.0)]


def test_a_base_at_a_different_resolution_does_not_read_as_erased(tmp_path):
    """LibreOffice renders at whatever width it likes — 2400x1350 for some
    slides of the occasioning template and 1280x721 for the rest — while
    plates are uniformly 1280x720. Comparing those without care reports every
    photographic plate as 100% changed, which is how the first attempt at this
    measurement wasted an afternoon."""
    big = Image.fromarray(headline(motif(canvas()))).resize((2400, 1350))
    base = tmp_path / "b.png"
    big.save(base)
    plate = save(tmp_path / "p.png", motif(canvas()))
    erased, _, _ = plate_health(base, plate, [TITLE_RECT], CANVAS)
    assert erased <= ERASED_LIMIT


# --------------------------------------------------------------------------
# the contract


def test_the_contract_carries_the_numbers_the_author_reinvents(template):
    """The whole point. The spec has always known the title sits at y=56.0;
    it just never reached the author, who used 36."""
    spec_root, clean = template([[title_shape()]])
    contract = build(spec_root, clean, None)
    zone = contract["layouts"][0]["zones"][0]
    assert (zone["role"], zone["y"], zone["ground"]) == ("TITLE", 56.0, "#FFFFFF")


def test_a_recurring_small_picture_is_named_as_furniture(template):
    """The lockup is a real asset on 17 slides of the occasioning template.
    Twelve deck slides rendered it as the typed sentence "Better together for
    100 years" because nothing ever pointed at the file."""
    spec_root, clean = template([[title_shape(), lockup(n)] for n in (1, 2, 3)])
    contract = build(spec_root, clean, None)
    furniture = contract["layouts"][0]["furniture"]
    assert len(furniture) == 1
    assert furniture[0]["slides"] == 3
    assert furniture[0]["asset"].endswith("s01-img01.png")


def test_a_one_off_picture_is_not_furniture(template):
    """A photograph on one slide is that slide's content. Only what the
    template repeats is the brand showing through."""
    spec_root, clean = template([[title_shape(), lockup(1)], [title_shape()]])
    contract = build(spec_root, clean, None)
    assert contract["layouts"][0]["furniture"] == []


def test_dead_plates_are_named_in_the_contract(template):
    """Refusing to hand twenty flat rectangles to the next stage is the
    cheapest place to stop this. It costs a re-run; letting it through cost a
    finished deck."""
    spec_root, clean = template([[title_shape()], [title_shape()]], plates={2: canvas()})
    contract = build(spec_root, clean, None)
    assert contract["dead_plates"] == [2]
    assert [entry["live"] for entry in contract["layouts"]] == [True, False]


def test_protected_slides_are_flagged_from_the_brand_contract(template, tmp_path):
    """Slides 14 to 16 of the occasioning template carry David Williams'
    Acknowledgement of Country artwork. Culturally and legally protected, and
    a flag on the layout is what keeps a later turn from reaching for one."""
    spec_root, clean = template([[title_shape()], [title_shape()]])
    contract_path = tmp_path / "brand-contract.json"
    contract_path.write_text(json.dumps({"protected": [{"slides": ["2"]}]}))
    contract = build(spec_root, clean, contract_path)
    assert [entry["protected"] for entry in contract["layouts"]] == [False, True]


@pytest.mark.parametrize(
    "shapes, expected",
    [
        ([title_shape()], "statement"),
        ([title_shape(), {"kind": "TABLE", "x": 48.0, "y": 172.7, "w": 157.5, "h": 157.5}], "table"),
        ([title_shape(), {"kind": "PICTURE", "x": 585.3, "y": 0.0, "w": 694.7, "h": 720.0}], "photo-split"),
        (
            [
                title_shape(),
                {"kind": "PLACEHOLDER", "placeholder": "BODY", "x": 48.5, "y": 199.3,
                 "w": 1183.5, "h": 427.8, "text": [{"runs": [{"text": "Body copy"}]}]},
            ],
            "body",
        ),
        ([{"kind": "PICTURE", "x": 0.0, "y": 0.0, "w": 1280.0, "h": 720.0}], "blank"),
    ],
)
def test_archetypes_come_from_what_the_slide_is_made_of(shapes, expected):
    """Not from a model's opinion of it, so the same template classifies the
    same way twice and a cached contract stays trustworthy."""
    assert classify(shapes, CANVAS) == expected


# --------------------------------------------------------------------------
# the gate


@pytest.fixture
def workspace(tmp_path, template):
    spec_root, clean = template([[title_shape()], [title_shape()]])
    root = tmp_path
    (root / "layouts.json").write_text(json.dumps(build(spec_root, clean, None)))
    return root


def slide(src: str) -> dict:
    return {"html": f'<section><img class="plate" src="{src}" alt=""><h1>x</h1></section>'}


def test_a_real_pinned_plate_passes(workspace):
    assert plate_findings(workspace, [slide("../clean/slide-01.png")]) == {}


def test_an_invented_plate_is_rejected(workspace):
    """`navy-statement.png`, `blue-statement.png` and `white-content.png`:
    single-colour rectangles an authoring turn wrote into clean/ mid-run.
    Three slides shipped on them and every gate passed, because each one had
    an image layer and that was the whole question."""
    (workspace / "clean" / "navy-statement.png").write_bytes(
        (workspace / "clean" / "slide-01.png").read_bytes()
    )
    found = plate_findings(workspace, [slide("../clean/navy-statement.png")])
    assert "not one of the template's plates" in found["0"][0]


def test_a_plate_overwritten_after_precompute_is_caught(workspace):
    """Pinning is what makes the difference between "a file with this name
    exists" and "the template's own artwork is under this slide"."""
    save(workspace / "clean" / "slide-01.png", canvas(BLUE))
    found = plate_findings(workspace, [slide("../clean/slide-01.png")])
    assert "overwritten since precompute" in found["0"][0]


def test_a_slide_with_no_plate_is_caught(workspace):
    found = plate_findings(workspace, [{"html": "<section><h1>freehand</h1></section>"}])
    assert "no plate" in found["0"][0]


def test_a_dead_plate_is_refused_to_the_author(workspace, template):
    """Not just reported at precompute. The author is the one who has to not
    build on it, so the message has to arrive where they are working."""
    spec_root, clean = template([[title_shape()]], plates={1: canvas()})
    (workspace / "layouts.json").write_text(json.dumps(build(spec_root, clean, None)))
    for name in ("slide-01.png",):
        (workspace / "clean" / name).write_bytes((clean / name).read_bytes())
    found = plate_findings(workspace, [slide("../clean/slide-01.png")])
    assert "is dead" in found["0"][0]


def test_a_protected_plate_cannot_be_reused(workspace, tmp_path, template):
    spec_root, clean = template([[title_shape()]])
    contract_path = tmp_path / "bc.json"
    contract_path.write_text(json.dumps({"protected": [{"slides": ["1"]}]}))
    (workspace / "layouts.json").write_text(
        json.dumps(build(spec_root, clean, contract_path))
    )
    found = plate_findings(workspace, [slide("../clean/slide-01.png")])
    assert any("protected" in message for message in found["0"])


def test_no_contract_means_no_opinion(tmp_path):
    """A workspace that never ran precompute is not a workspace with bad
    plates, and this gate must not invent findings for the other routes."""
    assert plate_findings(tmp_path, [slide("../clean/slide-01.png")]) == {}


# --------------------------------------------------------------------------
# the bundle


def test_an_unregistered_baseline_is_reported(tmp_path):
    """Eleven baselines, four registered. The other seven could not be
    patched, so authoring fell back to freehand for all twenty slides and the
    seam route — the reason fidelity is supposed to survive a creative turn —
    never ran once."""
    (tmp_path / "baselines").mkdir()
    for name in ("white-content-standard.html", "white_metric_grid.html"):
        (tmp_path / "baselines" / name).write_text("<html></html>")
    (tmp_path / "slide-types.json").write_text(
        json.dumps(
            {"slide_types": [
                {"id": "white-content-standard",
                 "baseline_path": "baselines/white-content-standard.html",
                 "seams": [{"id": "headline"}]}
            ]}
        )
    )
    problems = bundle_findings(tmp_path)
    assert len(problems) == 1
    assert "white_metric_grid.html" in problems[0]


def test_a_registered_baseline_that_does_not_exist_is_reported(tmp_path):
    (tmp_path / "baselines").mkdir()
    (tmp_path / "slide-types.json").write_text(
        json.dumps(
            {"slide_types": [
                {"id": "ghost", "baseline_path": "baselines/ghost.html",
                 "seams": [{"id": "title"}]}
            ]}
        )
    )
    assert "missing" in bundle_findings(tmp_path)[0]


def test_a_slide_type_with_no_seams_is_reported(tmp_path):
    """A type with no seams is a picture of a layout, not a thing a patch can
    be applied to."""
    (tmp_path / "baselines").mkdir()
    (tmp_path / "baselines" / "a.html").write_text("<html></html>")
    (tmp_path / "slide-types.json").write_text(
        json.dumps({"slide_types": [{"id": "a", "baseline_path": "baselines/a.html"}]})
    )
    assert "declares no seams" in bundle_findings(tmp_path)[0]


def test_a_complete_bundle_is_quiet(tmp_path):
    (tmp_path / "baselines").mkdir()
    (tmp_path / "baselines" / "a.html").write_text("<html></html>")
    (tmp_path / "slide-types.json").write_text(
        json.dumps(
            {"slide_types": [
                {"id": "a", "baseline_path": "baselines/a.html",
                 "seams": [{"id": "title"}]}
            ]}
        )
    )
    assert bundle_findings(tmp_path) == []


def test_a_workspace_with_no_bundle_is_quiet(tmp_path):
    assert bundle_findings(tmp_path) == []


def test_sha256_is_stable(tmp_path):
    path = save(tmp_path / "a.png", canvas())
    assert sha256(path) == sha256(path)
