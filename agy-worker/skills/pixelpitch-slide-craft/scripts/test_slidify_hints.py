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

"""Keep `references/slidify-hints.md` honest about what slidify actually does.

A reference page that documents an attribute the converter dropped is worse
than no page: the author writes the hint, the deck builds, and nothing happens.
So the page is checked against the converter's own compatibility matrix rather
than reviewed by eye.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

HINTS = Path(__file__).resolve().parent.parent / "references" / "slidify-hints.md"
_ATTR = re.compile(r"data-[a-z-]+")


def matrix() -> dict:
    try:
        out = subprocess.run(
            ["slidify", "compat", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"slidify not runnable here: {exc}")
    if out.returncode != 0:
        pytest.skip(f"slidify compat failed: {out.stderr[:200]}")
    return json.loads(out.stdout)


def sections() -> tuple[str, str]:
    text = HINTS.read_text(encoding="utf-8")
    head, _, tail = text.partition("## Not real yet")
    assert tail, "the page lost its planned-features section"
    return head, tail


def slidify_source() -> str:
    """Every line of the installed converter, as one blob to search.

    The compatibility matrix is not enough on its own. It lists conversion
    features, and two of the attributes here are gate consents rather than
    conversion features: `data-pptx-allow-overflow` and
    `data-slidify-allow-raster` live in the overflow gate and the checker, so
    they never appear as a matrix row despite being entirely real.
    """
    try:
        import slidify
    except ImportError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"slidify not importable here: {exc}")
    if not slidify.__file__:  # pragma: no cover - namespace package
        pytest.skip("slidify has no file location to search")
    root = Path(slidify.__file__).resolve().parent
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in root.rglob("*.py"))


def test_every_documented_hint_exists_in_the_converter():
    source = slidify_source()
    documented = set(_ATTR.findall(sections()[0]))
    missing = sorted(attr for attr in documented if attr not in source)
    assert not missing, f"documented as real, absent from slidify: {missing}"


def test_nothing_planned_is_documented_as_real():
    planned = {
        attr
        for row in matrix()["rows"]
        if row["support"] == "planned"
        for attr in _ATTR.findall(row["feature"] + " " + row["note"] + " " + row["plan"])
    }
    head, tail = sections()
    leaked = sorted(planned & set(_ATTR.findall(head)))
    assert not leaked, f"listed as usable, still only planned: {leaked}"
    for attr in sorted(planned):
        assert attr in tail, f"newly planned attribute is undocumented: {attr}"
