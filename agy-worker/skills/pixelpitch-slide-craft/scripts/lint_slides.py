#!/usr/bin/env python3
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

"""Deterministic slide gates: anti-slop lint plus derived-brand lint.

Stdlib only, so it runs under any interpreter the harness can reach. The
detectors live beside this file in ``slop.py`` (AI tells) and
``brand_gate.py`` (palette drift, contrast floors).

Usage:
  lint_slides.py <slides_json> [brand_contract_json]

``slides_json`` is any shape ``slides_io.load_slides`` accepts: an inline
array, a ``{"slides": [...]}`` wrapper, the workspace's ``slides/index.json``
of file references, or a directory of ``.html``. Supplying the derived
``brand-contract.json`` additionally gates every colour against that
template's real palette.

Prints ``{}`` and exits 0 when clean; prints ``{"<index>": ["defect", ...]}``
and exits 1 otherwise. Defects that belong to the whole deck rather than to one
slide, such as three consecutive slides built on one skeleton, appear under the
key ``"deck"``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import brand_gate  # noqa: E402
import slop  # noqa: E402
from slides_io import load_slides  # noqa: E402


def lint(slides: list[dict], contract: dict | None = None) -> dict[str, list[str]]:
    defects: dict[str, list[str]] = {
        str(index): issues for index, issues in slop.lint_slides(slides).items()
    }
    # Keyed "deck" rather than a slide index: monotony belongs to no one slide,
    # which is exactly why per-slide linting never catches it.
    across = slop.lint_deck(slides)
    if across:
        defects["deck"] = across
    palette = [
        value
        for value in ((contract or {}).get("palette") or {}).values()
        if isinstance(value, str) and value.startswith("#")
    ]
    if palette:
        for index, issues in brand_gate.lint_slides(slides, palette).items():
            defects.setdefault(str(index), []).extend(issues)
    return defects


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    slides = load_slides(Path(argv[1]))
    contract = None
    if len(argv) > 2 and Path(argv[2]).is_file():
        contract = json.loads(Path(argv[2]).read_text(encoding="utf-8"))

    defects = lint(slides, contract)
    print(json.dumps(defects, indent=2, sort_keys=True))
    return 1 if defects else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
