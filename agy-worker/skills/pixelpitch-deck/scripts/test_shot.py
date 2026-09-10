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

"""The probe is the gate law's only mechanical half, so it is tested like code.

A false negative ships a broken slide behind a green gate, which is the exact
failure the skill exists to prevent. A false positive is worse in practice: an
author who has been told three times that a correct slide is broken stops
reading the output, and then the true findings go past too. Both directions
are asserted here.

The buried-text cases are a defect that shipped. Six slides went out with a
subhead under an opaque panel, `deck lint` returned `{}` and `layout.json` was
empty, because every check the probe had compared text to text.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image
from shot import capture

pytest.importorskip("playwright.sync_api")

PAGE = """<!doctype html><html><body style="margin:0;width:1280px;height:720px;
background:#0E0D26">{}</body></html>"""

TEXT = ("position:absolute;left:60px;top:300px;width:600px;font:600 32px sans-serif;"
        "color:#FFFFFF")
PANEL = "position:absolute;left:40px;top:280px;width:700px;height:120px"


def findings(tmp_path, body: str) -> list[str]:
    path = tmp_path / "slide-01.html"
    path.write_text(PAGE.format(body), encoding="utf-8")
    _, defects = capture([{"path": str(path)}], tmp_path / "out")
    return defects.get("1", [])


def buried(found: list[str]) -> list[str]:
    return [f for f in found if "is painted over by" in f]


def faint(found: list[str]) -> list[str]:
    return [f for f in found if "contrast floor" in f]


def plate(*bands: tuple[int, str]) -> str:
    """A template plate, as an img. Contrast is only measured against artwork,
    so every case below has to stand on some, exactly as a real slide does."""
    image = Image.new("RGB", (1280, 720))
    x = 0
    for width, colour in bands:
        image.paste(Image.new("RGB", (width, 720), colour), (x, 0))
        x += width
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = base64.b64encode(buffer.getvalue()).decode()
    return (f'<img src="data:image/png;base64,{data}" '
            'style="position:absolute;left:0;top:0;width:1280px;height:720px">')


def test_an_opaque_panel_over_a_run_is_reported(tmp_path):
    found = buried(findings(
        tmp_path,
        f'<p style="{TEXT}">Permission aware by construction</p>'
        f'<div style="{PANEL};background:#FFFFFF"></div>',
    ))
    assert len(found) == 1
    assert "100%" in found[0] and "Permission aware" in found[0]


def test_a_panel_over_half_a_run_is_reported(tmp_path):
    """Burial is a proportion, because half a sentence is not a readable one."""
    found = buried(findings(
        tmp_path,
        f'<p style="{TEXT}">Permission aware by construction</p>'
        f'<div style="{PANEL};left:200px;width:540px;background:#FFFFFF"></div>',
    ))
    assert len(found) == 1


def test_a_panel_clipping_the_last_word_is_not_reported(tmp_path):
    """Under the threshold on purpose. The false-positive budget is small and
    the containment checks already cover text running past its own box."""
    assert not buried(findings(
        tmp_path,
        f'<p style="{TEXT}">Permission aware by construction</p>'
        f'<div style="{PANEL};left:340px;width:400px;background:#FFFFFF"></div>',
    ))


def test_an_image_over_a_run_is_reported(tmp_path):
    dot = ("data:image/gif;base64,"
           "R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw==")
    found = buried(findings(
        tmp_path,
        f'<p style="{TEXT}">Permission aware by construction</p>'
        f'<img src="{dot}" style="{PANEL}">',
    ))
    assert len(found) == 1


def test_a_run_with_nothing_over_it_is_not_reported(tmp_path):
    assert not buried(findings(
        tmp_path, f'<p style="{TEXT}">Permission aware by construction</p>'))


def test_a_transparent_layer_over_a_run_is_not_reported(tmp_path):
    """The commonest shape on a template slide, and not a defect."""
    assert not buried(findings(
        tmp_path,
        f'<p style="{TEXT}">Permission aware by construction</p>'
        f'<div style="{PANEL};background:transparent"></div>',
    ))


def test_a_run_sitting_on_its_own_panel_is_not_reported(tmp_path):
    """The panel is behind its own text, which is how every card is built."""
    assert not buried(findings(
        tmp_path,
        f'<div style="{PANEL};background:#1971ED">'
        f'<p style="font:600 32px sans-serif;color:#FFFFFF">Permission aware</p></div>',
    ))


def test_a_run_inside_a_child_of_a_panel_is_not_reported(tmp_path):
    """Nesting depth between the painted box and the run must not matter."""
    assert not buried(findings(
        tmp_path,
        f'<div style="{PANEL};background:#1971ED"><div><span>'
        f'<p style="font:600 32px sans-serif;color:#FFFFFF">Permission aware</p>'
        f'</span></div></div>',
    ))


def test_svg_text_over_its_own_bar_is_not_reported(tmp_path):
    """A label on the bar it measures is the point of the exhibit."""
    assert not buried(findings(
        tmp_path,
        '<svg style="display:block" width="1280" height="720"><rect x="60" y="300" width="600" '
        'height="60" fill="#1971ED"/><text x="80" y="340" font-size="28" '
        'fill="#FFFFFF">Range review</text></svg>',
    ))


def test_a_run_crossing_onto_light_artwork_is_reported(tmp_path):
    """The plate defect that shipped. Part of the headline is on brand blue and
    reads, part is on the photograph inside the plate, and there is no element
    to find because the photograph is not an element."""
    found = faint(findings(
        tmp_path,
        plate((250, "#0E0D26"), (1030, "#F2F2F2"))
        + f'<p style="{TEXT}">Permission aware by construction</p>',
    ))
    assert len(found) == 1
    assert "3.0:1" in found[0]


def test_a_run_on_artwork_it_reads_against_is_not_reported(tmp_path):
    assert not faint(findings(
        tmp_path,
        plate((1280, "#0E0D26")) + f'<p style="{TEXT}">Permission aware by construction</p>'))


def test_a_tint_of_the_plate_is_reported(tmp_path):
    """Brand tint on brand base, 2.1:1. Every palette gate passes because both
    colours are in the palette, and nobody past the third row can read it."""
    assert faint(findings(
        tmp_path,
        plate((1280, "#1971ED"))
        + f'<p style="{TEXT};color:#7FB3F5">Permission aware by construction</p>'))


def test_a_run_on_a_declared_colour_is_left_to_the_source_gate(tmp_path):
    """3.03:1, and the editorial muted tone on twelve of the sixteen gated
    gallery slides. Two colours the DOM holds are `deck lint`'s call, made once
    at source. A second stricter opinion here only teaches authors to skim."""
    assert not faint(findings(
        tmp_path,
        f'<div style="position:absolute;inset:0;background:#F2F1EE"></div>'
        f'<p style="{TEXT};color:#8B8B85;font-size:20px">Source: safety analysis set</p>'))


def test_large_text_is_held_to_the_large_text_floor(tmp_path):
    """3.62:1. Readable at 32px and not at 16px, which is what WCAG says and
    what a projector confirms, so one threshold for both would be wrong twice."""
    grey, ground = "color:#6C6C6C", plate((1280, "#0E0D26"))
    assert not faint(findings(
        tmp_path, ground + f'<p style="{TEXT};{grey}">Permission aware by construction</p>'))
    assert faint(findings(
        tmp_path,
        ground + f'<p style="{TEXT};{grey};font-size:16px">Permission aware by construction</p>'))


def test_glyph_strokes_do_not_read_as_their_own_ground(tmp_path):
    """Heavy display type is where estimating the ground from the finished
    pixels breaks: at x-height the ink is more than half the row, so any window
    wide enough to span two glyphs returns white and calls white text on navy
    unreadable. The ground is screenshotted with the text hidden instead."""
    assert not faint(findings(
        tmp_path,
        plate((1280, "#0E0D26"))
        + f'<p style="{TEXT};font-size:64px;font-weight:800">Governed</p>'))


def test_absolutely_positioned_runs_are_measured_for_burial(tmp_path):
    """They are exempt from the overlap check, so this is their only cover."""
    found = findings(
        tmp_path,
        f'<p style="{TEXT}">Permission aware by construction</p>'
        f'<div style="{PANEL};background:#FFFFFF"></div>',
    )
    assert buried(found)
    assert not [f for f in found if f.startswith("text overlaps")]


def test_a_run_on_a_panel_over_the_ground_is_reported(tmp_path):
    """1.91:1, and neither gate had it. `deck lint` proves a pair declared in
    one rule; the panel's colour and the run's colour are two rules on two
    elements, so it passes. This measured only artwork, so it passed too. A
    worked-example slide shipped its labels this way."""
    assert faint(findings(
        tmp_path,
        '<div style="position:absolute;left:640px;top:0;width:640px;height:720px;'
        'background:#1971ED"></div>'
        f'<p style="{TEXT};left:700px;color:#66C5FF;font-size:20px">Governed execution</p>'))


def test_a_panel_the_run_reads_against_is_not_reported(tmp_path):
    """4.54:1. The same shape as above and correct as drawn, so widening the
    scope must not cost the author a false alarm on every coloured half."""
    assert not faint(findings(
        tmp_path,
        '<div style="position:absolute;left:640px;top:0;width:640px;height:720px;'
        'background:#1971ED"></div>'
        f'<p style="{TEXT};left:700px;color:#FFFFFF;font-size:20px">Governed execution</p>'))
