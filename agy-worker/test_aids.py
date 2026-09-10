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

"""The geometry has to be right, because nothing downstream re-checks it.

A wrong free region is not caught by any gate: the aid renders, the slide has
an image layer, the plate hashes clean, and the artwork sits on the lockup. So
the rectangle is tested directly, including the case that produced the defect
this exists for, which is a region that looks free because the furniture was
never subtracted.
"""

from __future__ import annotations

import json

import pytest
from aids import MIN_AREA, Region, build_aid_subagents, free_region, region_for

CANVAS = (1280, 720)


def rect(x, y, w, h, **extra) -> dict:
    return {"x": x, "y": y, "w": w, "h": h, **extra}


def layout(**extra) -> dict:
    base = {
        "slide": 1,
        "layout": "Text Styles_1",
        "archetype": "statement",
        "plate": "clean/slide-01.png",
        "protected": False,
        "live": True,
        "zones": [rect(48, 128, 560, 150, role="TITLE", ground="#1971ED")],
        "furniture": [rect(47, 616, 322, 68, asset="media/lockup.png", slides=13)],
    }
    base.update(extra)
    return base


BRAND = {
    "palette": {"brand": "#1971ED", "rule": "#DEDEDF", "not_a_colour": "blue"},
    "fonts": {"major": "Roboto Medium", "minor": "Roboto"},
    "visual_aids_style": "Hairline borders, one accent, no card grids.",
    "donts": ["Do not reuse or imitate Indigenous artwork"],
}


@pytest.fixture
def workspace(tmp_path):
    def stage(layouts: list[dict], brand: dict | None = BRAND, canvas=CANVAS):
        (tmp_path / "layouts.json").write_text(
            json.dumps({"schema_version": 1, "canvas": list(canvas), "layouts": layouts})
        )
        if brand is not None:
            (tmp_path / "brand-contract.json").write_text(json.dumps(brand))
        return tmp_path

    return stage


# --------------------------------------------------------------------------
# the rectangle


def test_an_empty_canvas_is_entirely_free():
    assert free_region(CANVAS, []) == Region(0, 0, 1280, 720)


def test_a_title_across_the_top_leaves_the_bottom():
    found = free_region(CANVAS, [rect(0, 0, 1280, 200)], margin=0)
    assert (found.y, found.h, found.w) == (200, 520, 1280)


def test_a_column_of_text_leaves_the_other_column():
    found = free_region(CANVAS, [rect(0, 0, 600, 720)], margin=0)
    assert (found.x, found.w) == (600, 680)


def test_furniture_is_subtracted_as_well_as_text():
    """The defect. A lockup is 2.3% of the canvas, so a region computed from
    the text zones alone reads as free right through it and the aid lands on
    the brand mark."""
    lockup = rect(47, 616, 322, 68)
    without = free_region(CANVAS, [rect(0, 0, 1280, 200)], margin=0)
    with_it = free_region(CANVAS, [rect(0, 0, 1280, 200), lockup], margin=0)
    assert without.h > with_it.h or without.w > with_it.w
    assert not _overlaps(with_it, lockup)


def _overlaps(region: Region, other: dict) -> bool:
    return (
        region.x < other["x"] + other["w"]
        and other["x"] < region.x + region.w
        and region.y < other["y"] + other["h"]
        and other["y"] < region.y + region.h
    )


def test_the_region_clears_every_rect_it_was_given():
    rects = [
        rect(48, 128, 560, 150),
        rect(48, 324, 564, 76),
        rect(47, 616, 322, 68),
        rect(700, 40, 500, 300),
    ]
    found = free_region(CANVAS, rects)
    assert found.area > 0
    for other in rects:
        assert not _overlaps(found, other), other


def test_a_margin_keeps_the_aid_off_the_text():
    tight = free_region(CANVAS, [rect(0, 0, 1280, 200)], margin=0)
    spaced = free_region(CANVAS, [rect(0, 0, 1280, 200)], margin=20)
    assert spaced.y > tight.y


def test_a_crowded_layout_has_no_region_worth_offering():
    """A subagent that draws into a sliver crowds the slide. Better to have no
    aid agent on that layout than one that produces a 90px strip."""
    found = free_region(CANVAS, [rect(0, 0, 1280, 640)])
    assert not found.usable()
    assert found.area < MIN_AREA


# --------------------------------------------------------------------------
# giving up a placeholder


CROWDED = layout(
    slide=26,
    archetype="body",
    zones=[
        rect(48, 40, 1180, 120, role="TITLE", ground="#FFFFFF"),
        rect(48, 200, 1180, 400, role="BODY", ground="#FFFFFF"),
        rect(48, 620, 700, 60, role="BODY", ground="#FFFFFF"),
    ],
)


def test_a_layout_with_no_empty_space_gives_up_a_placeholder():
    """This is the layout the broken deck reused five times. Its placeholders
    cover everything but a strip, so an aid there is in a placeholder or it is
    nowhere, and `nowhere` is how twenty slides came out as walls of text."""
    region, yielded = region_for(CANVAS, CROWDED)
    assert yielded == "BODY"
    assert region.usable()


def test_the_title_is_never_the_placeholder_given_up():
    """A slide whose claim moved into a picture has no claim."""
    region, yielded = region_for(CANVAS, CROWDED)
    assert yielded != "TITLE"
    assert not _overlaps(region, CROWDED["zones"][0])


def test_the_largest_body_placeholder_is_the_one_given_up():
    region, _ = region_for(CANVAS, CROWDED)
    assert not _overlaps(region, CROWDED["zones"][2])


def test_a_layout_with_room_keeps_all_its_placeholders():
    region, yielded = region_for(CANVAS, layout())
    assert yielded == ""
    assert region.usable()


def test_a_layout_that_is_title_only_and_full_yields_nothing():
    """No body placeholder to give up, and no empty space. Correctly no aid."""
    full = layout(zones=[rect(0, 0, 1280, 700, role="TITLE", ground="#FFF")])
    region, yielded = region_for(CANVAS, full)
    assert (yielded, region.usable()) == ("", False)


def test_the_subagent_is_told_what_the_aid_cost(workspace):
    text = build_aid_subagents(workspace([CROWDED]))[0]["system_instructions"]
    assert "the template's\n`BODY` placeholder, given over to you" in text
    assert "If it cannot, say so and draw nothing." in text


def test_a_subagent_with_room_is_told_nothing_about_placeholders(workspace):
    text = build_aid_subagents(workspace([layout()]))[0]["system_instructions"]
    assert "given over to you" not in text


# --------------------------------------------------------------------------
# the subagent


def test_the_instruction_carries_the_measured_box(workspace):
    spec = build_aid_subagents(workspace([layout()]))[0]
    region = free_region(
        CANVAS, layout()["zones"] + layout()["furniture"]
    )
    text = spec["system_instructions"]
    assert f'viewBox="0 0 {region.w} {region.h}"' in text
    assert f"({region.x}, {region.y})" in text


def test_the_instruction_names_the_furniture_it_is_drawing_beside(workspace):
    text = build_aid_subagents(workspace([layout()]))[0]["system_instructions"]
    assert "lockup.png" in text
    assert "on 13 template slides" in text


def test_the_ground_comes_from_the_layouts_own_zones(workspace):
    text = build_aid_subagents(workspace([layout()]))[0]["system_instructions"]
    assert "The ground behind you is #1971ED" in text


def test_a_layout_with_no_recorded_ground_is_told_to_look(workspace):
    text = build_aid_subagents(workspace([layout(zones=[])]))[0][
        "system_instructions"
    ]
    assert "Open clean/slide-01.png and look" in text


def test_only_real_colours_reach_the_palette(workspace):
    """`brand-contract.json` mixes hex values with prose under the same key."""
    text = build_aid_subagents(workspace([layout()]))[0]["system_instructions"]
    assert "`#1971ED` brand" in text
    assert "not_a_colour" not in text


def test_the_decks_own_donts_are_carried_through(workspace):
    text = build_aid_subagents(workspace([layout()]))[0]["system_instructions"]
    assert "Do not reuse or imitate Indigenous artwork" in text


def test_a_protected_layout_gets_no_subagent_at_all(workspace):
    """Slides 14 to 16 carry David Williams' 'A Brave Heart for a Better
    Tomorrow'. The contract says do not reuse or imitate it. An instruction
    saying so is a sentence a turn can reason past; no agent existing is not."""
    assert build_aid_subagents(workspace([layout(slide=14, protected=True)])) == []


def test_a_dead_plate_gets_no_subagent_either(workspace):
    """Nothing should be composed against a ground whose artwork was erased."""
    assert build_aid_subagents(workspace([layout(live=False)])) == []


def test_layouts_that_leave_the_same_region_collapse_to_one(workspace):
    """Twenty-eight layouts over eleven grounds. Twenty-eight subagents that
    differ in nothing but a slide number is a menu no turn reads to the end."""
    same = [layout(slide=n) for n in range(1, 6)]
    assert len(build_aid_subagents(workspace(same))) == 1


def test_different_geometry_gets_its_own_subagent(workspace):
    wide = layout(slide=2, archetype="table", zones=[rect(48, 40, 1180, 160)])
    specs = build_aid_subagents(workspace([layout(), wide]))
    assert [spec["name"] for spec in specs] == ["aid-slide-01", "aid-slide-02"]


def test_a_workspace_with_no_contract_synthesises_nothing(tmp_path):
    """Without geometry there is nothing to tell a subagent that a generic aid
    agent does not already say, and a generic aid agent is what produced the
    off-template slides."""
    assert build_aid_subagents(tmp_path) == []


def test_a_deck_with_no_brand_contract_still_gets_its_geometry(workspace):
    text = build_aid_subagents(workspace([layout()], brand=None))[0][
        "system_instructions"
    ]
    assert "viewBox" in text
    assert "Stay plain: hairlines, one accent." in text


def test_the_canvas_may_be_recorded_as_floats(workspace):
    """PowerPoint measures a template authored in inches."""
    specs = build_aid_subagents(workspace([layout()], canvas=(1280.0, 720.0)))
    assert len(specs) == 1
