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

"""`deck check` is the last thing between a turn and a delivered deck.

For most of its life the only question it asked of a plate was whether an
``<img>`` tag existed. A deck shipped through it with three slides sitting on
flat rectangles an authoring turn had typed into ``clean/`` mid-run, and the
verdict was ``mechanical: pass``. These run the real command against a staged
workspace, because the defect was in the wiring rather than in either half.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

DECK = Path(__file__).resolve().parent / "deck"

# A 1x1 PNG. The gate reads bytes and hashes, never pixels, so this is a plate
# as far as anything under test is concerned.
PIXEL = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c636060606000000005000117bfe4c5"
    "0000000049454e44ae426082"
)


def run(workspace: Path) -> tuple[int, dict]:
    done = subprocess.run(
        [sys.executable, str(DECK), "check", "--workspace", str(workspace)],
        capture_output=True,
        text=True,
    )
    return done.returncode, json.loads(done.stdout)


def slide(src: str | None) -> dict:
    """A slide with nothing wrong with it except, sometimes, its plate."""
    layer = f'<img class="plate" src="{src}" alt="">' if src else ""
    return {
        "title": "A headline that asserts something",
        "html": "<!doctype html><html><body "
        'style="width:1280px;height:720px;margin:0"><section>'
        + layer
        + '<h1 style="font-size:60px" data-pptx-role="title">Assertion</h1>'
        "</section></body></html>",
    }


@pytest.fixture
def workspace(tmp_path):
    """A staged deck, with as much or as little of the contract as asked for."""

    def stage(slides: list[dict], plates: list[str], pinned: bool = True):
        (tmp_path / "slides.json").write_text(json.dumps(slides))
        clean = tmp_path / "clean"
        clean.mkdir()
        for name in plates:
            (clean / name).write_bytes(PIXEL)
        if pinned:
            (tmp_path / "layouts.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "canvas": [1280, 720],
                        "dead_plates": [],
                        "layouts": [
                            {
                                "slide": index,
                                "layout": "Text Styles_1",
                                "archetype": "statement",
                                "plate": f"clean/{name}",
                                "plate_sha256": hashlib.sha256(PIXEL).hexdigest(),
                                "protected": False,
                                "live": True,
                                "erased_fraction": 0.0,
                                "mean_delta": 0.0,
                                "zones": [],
                                "furniture": [],
                            }
                            for index, name in enumerate(plates, start=1)
                        ],
                    }
                )
            )
        return tmp_path

    return stage


def test_a_slide_on_a_pinned_plate_passes(workspace):
    root = workspace([slide("../clean/slide-01.png")], ["slide-01.png"])
    code, report = run(root)
    assert (code, report["defects"]) == (0, {})
    assert report["plates_pinned"] is True


def test_an_invented_plate_fails_the_check(workspace):
    """The defect this command was rewired for. `navy-statement.png` was a flat
    rectangle written into clean/ beside the real plates, and it passed."""
    root = workspace([slide("../clean/navy-statement.png")], ["slide-01.png"])
    (root / "clean" / "navy-statement.png").write_bytes(PIXEL)
    code, report = run(root)
    assert code == 1
    assert "not one of the template's plates" in report["defects"]["0"][0]


def test_a_plate_edited_after_pinning_fails_the_check(workspace):
    root = workspace([slide("../clean/slide-01.png")], ["slide-01.png"])
    (root / "clean" / "slide-01.png").write_bytes(PIXEL + b"\x00")
    code, report = run(root)
    assert code == 1
    assert "overwritten since precompute" in report["defects"]["0"][0]


def test_plates_with_no_contract_beside_them_fail_the_check(workspace):
    """A clean/ directory nobody pinned is a clean/ directory nobody can
    defend. Without the contract every question below reduces to whether an
    `<img>` exists, which is where this started."""
    root = workspace([slide("../clean/slide-01.png")], ["slide-01.png"], pinned=False)
    code, report = run(root)
    assert code == 1
    assert "deck layouts" in report["defects"]["deck"][0]
    assert report["plates_pinned"] is False


def test_an_unregistered_baseline_fails_the_check(workspace):
    """Seven of eleven baselines went unregistered on the occasioning run, so
    no patch could target them and all twenty slides were authored freehand."""
    root = workspace([slide("../clean/slide-01.png")], ["slide-01.png"])
    (root / "baselines").mkdir()
    (root / "baselines" / "white_metric_grid.html").write_text("<html></html>")
    (root / "slide-types.json").write_text(json.dumps({"slide_types": []}))
    code, report = run(root)
    assert code == 1
    assert "white_metric_grid.html" in report["defects"]["bundle"][0]
