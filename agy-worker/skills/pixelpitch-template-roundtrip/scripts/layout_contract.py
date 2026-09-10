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

"""The join between a template's geometry and the plates authored on top of it.

Precompute already produces the rects (``spec/slide-NN.json``) and the plates
(``clean/slide-NN.png``) and never relates them, so what reaches the author is
a palette and some prose. They then reinvent the numbers. The template that
occasioned this declares its body title at y=56.0 and its body copy at y=199.3;
the deck built from it used 36 and 172 on slides whose plates came from that
very layout.

Two failures follow from the same missing join, and both shipped:

A plate can die without anyone noticing. The painter may only touch text
rects, so everything else must survive it byte for byte. On the occasioning
run, twenty of twenty-eight plates came out a single flat colour — the
template's logo lockup and its wave motif erased, one plate down from 249,396
distinct colours to one — and every downstream gate passed them, because the
only question ever asked of a plate was whether an ``<img>`` tag pointed at
something.

And the plate namespace is open. Three slides shipped on ``navy-statement.png``,
``blue-statement.png`` and ``white-content.png``: flat rectangles an authoring
turn invented mid-run and dropped into ``clean/`` beside the real ones. Pinning
every plate by hash here is what lets a later gate tell the template's own
artwork from a rectangle somebody typed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# Pillow, numpy and the painter's rect logic are imported where they are used
# rather than here. Building the contract needs all three; consuming it needs
# none of them, because the gate only reads layouts.json and hashes files. That
# is what lets `deck check` stay on whatever interpreter the author happens to
# be holding while `deck layouts` re-execs under the renderer's virtualenv.

# Plate health is measured on a coarse grid rather than at full resolution.
# Base renders arrive at whatever width LibreOffice chose (2400x1350 for some
# slides of the occasioning template, 1280x721 for the rest) while plates are
# uniformly 1280x720, so any honest comparison resamples. Area-averaging down
# to this makes resampling error vanish into the tenths while leaving a wave
# motif or a logo lockup an unmissable block of difference.
GRID = (160, 90)

# A cell counts as erased when the plate departs this far from the base in any
# channel. Same threshold the painter uses to tell glyph from ground, for the
# same reason: every pair a brand contract permits clears it by a wide margin.
ERASED_DELTA = 40.0

# And the plate is dead at this fraction of its non-text area erased. Measured,
# not guessed: on the occasioning template the healthy plates erase between
# 0.00% and 0.73% of that area and the dead ones between 14.10% and 100%, so
# the threshold sits with roughly three times' margin on either side.
ERASED_LIMIT = 0.05

# A lockup is 2.3% of the canvas, so a plate can lose the brand mark and
# nothing else while scoring under any global threshold worth having — and be
# exactly as unusable, because the one thing every slide of the template shows
# is gone. Furniture is therefore measured inside its own rect, where losing it
# is unambiguous, and the two signals answer two different questions.
FURNITURE_ERASED_LIMIT = 0.5

# A picture this large is the slide's artwork, not a piece of its furniture.
# The lockup on the occasioning template is 322x67 against 1280x720, which is
# 2.3%; the photographs it has to be told apart from run full-bleed.
FURNITURE_MAX_AREA = 0.40

# Furniture is what the template repeats. One picture on one slide is that
# slide's content; the same rect on this many is the brand showing through.
FURNITURE_MIN_SLIDES = 3


@dataclass(frozen=True)
class Zone:
    """A rect the template reserves for text, and what the plate does under it."""

    role: str
    x: float
    y: float
    w: float
    h: float
    ground: str
    busy: float


@dataclass(frozen=True)
class Furniture:
    """A picture the template repeats. The brand mark, usually."""

    asset: str | None
    x: float
    y: float
    w: float
    h: float
    slides: int
    erased: float


@dataclass
class Layout:
    slide: int
    layout: str
    archetype: str
    plate: str
    plate_sha256: str
    protected: bool
    live: bool
    erased_fraction: float
    mean_delta: float
    zones: list[Zone] = field(default_factory=list)
    furniture: list[Furniture] = field(default_factory=list)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _grid(path: Path) -> np.ndarray:
    """Area-average an image onto the comparison grid."""
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    image = Image.open(path).convert("RGB").resize(GRID, Image.Resampling.BOX)
    return np.asarray(image, dtype=np.float32)


def _rect_mask(
    rects: list[tuple[float, float, float, float]],
    canvas: tuple[float, float],
    pad_px: float = 6.0,
) -> np.ndarray:
    """The text rects, grown a little, projected onto the comparison grid."""
    import numpy as np  # noqa: PLC0415

    columns, rows = GRID
    mask = np.zeros((rows, columns), dtype=bool)
    width, height = canvas
    for x, y, w, h in rects:
        x0 = max(0, int((x - pad_px) / width * columns))
        y0 = max(0, int((y - pad_px) / height * rows))
        x1 = min(columns, int((x + w + pad_px) / width * columns) + 1)
        y1 = min(rows, int((y + h + pad_px) / height * rows) + 1)
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
    return mask


def plate_health(
    base_path: Path,
    plate_path: Path,
    rects: list[tuple[float, float, float, float]],
    canvas: tuple[float, float],
    furniture: list[tuple[float, float, float, float]] | None = None,
) -> tuple[float, float, list[float]]:
    """How much of the base survived outside the rects the painter may touch.

    Returns ``(erased_fraction, mean_delta, furniture_erased)``. The first two
    describe the slide at large and catch wholesale destruction. The third is
    per furniture rect, because the brand mark is too small to move a global
    number and too important to lose.
    """
    import numpy as np  # noqa: PLC0415

    base = _grid(base_path)
    plate = _grid(plate_path)
    delta = np.abs(base - plate).max(axis=2)
    erased = delta > ERASED_DELTA

    furniture_mask = _rect_mask(furniture or [], canvas, pad_px=0.0)
    outside = ~_rect_mask(rects, canvas) & ~furniture_mask
    at_large = (
        (float(erased[outside].mean()), float(delta[outside].mean()))
        if outside.any()
        else (0.0, 0.0)
    )

    per_piece: list[float] = []
    for rect in furniture or []:
        cells = _rect_mask([rect], canvas, pad_px=0.0)
        per_piece.append(float(erased[cells].mean()) if cells.any() else 0.0)
    return at_large[0], at_large[1], per_piece


def _hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(int(round(float(c))) for c in rgb))


def _zone_reading(
    plate: np.ndarray, x: float, y: float, w: float, h: float,
    canvas: tuple[float, float],
) -> tuple[str, float]:
    """The plate's ground colour under a rect, and how busy it is there.

    ``ground`` is what a run placed here would actually read against, measured
    off the plate rather than inferred from the palette, because a rect can sit
    on a photograph or straddle a panel edge. ``busy`` says which of those it
    is: flat ground scores zero, artwork does not.
    """
    import numpy as np  # noqa: PLC0415

    columns, rows = GRID
    width, height = canvas
    x0 = max(0, int(x / width * columns))
    y0 = max(0, int(y / height * rows))
    x1 = min(columns, max(x0 + 1, int((x + w) / width * columns)))
    y1 = min(rows, max(y0 + 1, int((y + h) / height * rows)))
    patch = plate[y0:y1, x0:x1].reshape(-1, 3)
    if not len(patch):
        return "#000000", 0.0
    median = np.median(patch, axis=0)
    busy = float((np.abs(patch - median).max(axis=1) > ERASED_DELTA).mean())
    return _hex(median), busy


def _shapes(record: dict) -> list[dict]:
    """Every shape in the slide, children flattened in with their parents."""
    out: list[dict] = []

    def walk(shape: dict) -> None:
        out.append(shape)
        for child in shape.get("children") or []:
            walk(child)

    for shape in record.get("shapes") or []:
        walk(shape)
    return out


def classify(shapes: list[dict], canvas: tuple[float, float]) -> str:
    """Which of the template's worlds this slide belongs to.

    Named from what the slide is made of rather than from a model's opinion of
    it, so the same template classifies the same way twice.
    """
    from make_clean_plates import _text_rects  # noqa: PLC0415

    width, height = canvas
    texted = [s for s in shapes if _text_rects(s)]
    roles = {str(s.get("placeholder") or "").upper() for s in texted}
    full_bleed = any(
        s.get("kind") == "PICTURE"
        and float(s.get("w") or 0) >= 0.45 * width
        and float(s.get("h") or 0) >= 0.85 * height
        for s in shapes
    )
    if not texted:
        return "blank"
    if any(s.get("kind") == "TABLE" for s in shapes):
        return "table"
    if full_bleed:
        return "photo-split"
    if len(texted) == 1 and "TITLE" in roles:
        return "statement"
    if {"TITLE", "BODY"} <= roles:
        return "body"
    return "content"


def _furniture_recurrence(records: list[dict]) -> dict[tuple, int]:
    """How many slides repeat each small picture, keyed by rounded rect."""
    counts: dict[tuple, int] = {}
    for record in records:
        canvas = _canvas(record)
        seen = set()
        for shape in _shapes(record):
            key = _furniture_key(shape, canvas)
            if key is not None:
                seen.add(key)
        for key in seen:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _box(shape: dict) -> tuple[float, float, float, float] | None:
    """A shape's rect in canvas px, or None when it does not declare one."""
    try:
        x, y, w, h = (float(shape[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def _furniture_key(shape: dict, canvas: tuple[float, float]) -> tuple | None:
    if shape.get("kind") != "PICTURE":
        return None
    box = _box(shape)
    if box is None:
        return None
    x, y, w, h = box
    if w * h > FURNITURE_MAX_AREA * canvas[0] * canvas[1]:
        return None
    return (round(x), round(y), round(w), round(h))


def _canvas(record: dict) -> tuple[float, float]:
    raw = list(record.get("canvas") or (1280, 720))
    return float(raw[0]), float(raw[1])


def _protected_slides(contract_path: Path | None) -> set[int]:
    if contract_path is None or not contract_path.is_file():
        return set()
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except ValueError:
        return set()
    out: set[int] = set()
    for entry in contract.get("protected") or []:
        for number in entry.get("slides") or []:
            try:
                out.add(int(str(number).strip()))
            except ValueError:
                continue
    return out


def build(spec_root: Path, clean_dir: Path, contract_path: Path | None) -> dict:
    """Emit the layout contract for one extracted template."""
    from make_clean_plates import _text_rects  # noqa: PLC0415

    spec_dir = spec_root / "spec" if (spec_root / "spec").is_dir() else spec_root
    base_dir = spec_root / "base"
    media_dir = spec_dir / "media"
    paths = sorted(spec_dir.glob("slide-*.json"))
    if not paths:
        raise SystemExit(f"no slide specs under {spec_dir}")

    records = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    recurrence = _furniture_recurrence(records)
    protected = _protected_slides(contract_path)

    layouts: list[dict] = []
    dead: list[int] = []
    for path, record in zip(paths, records):
        slide_no = int(record.get("slide") or re.findall(r"(\d+)", path.stem)[-1])
        canvas = _canvas(record)
        shapes = _shapes(record)
        plate_path = clean_dir / f"slide-{slide_no:02d}.png"
        base_path = base_dir / f"slide-{slide_no:02d}.png"
        if not plate_path.is_file():
            raise SystemExit(f"no plate for slide {slide_no} at {plate_path}")

        rects = [rect for shape in shapes for rect in _text_rects(shape)]
        pieces = [
            (shape, box)
            for shape in shapes
            for key in [_furniture_key(shape, canvas)]
            for box in [_box(shape)]
            if key is not None
            and box is not None
            and recurrence.get(key, 0) >= FURNITURE_MIN_SLIDES
        ]
        erased, mean_delta, piece_erased = (
            plate_health(
                base_path, plate_path, rects, canvas, [box for _, box in pieces]
            )
            if base_path.is_file()
            else (0.0, 0.0, [0.0] * len(pieces))
        )
        live = erased <= ERASED_LIMIT and all(
            value <= FURNITURE_ERASED_LIMIT for value in piece_erased
        )
        if not live:
            dead.append(slide_no)

        plate_grid = _grid(plate_path)
        zones: list[Zone] = []
        for shape in shapes:
            own = _text_rects({**shape, "children": []})
            if not own:
                continue
            x, y, w, h = own[0]
            ground, busy = _zone_reading(plate_grid, x, y, w, h, canvas)
            zones.append(
                Zone(
                    role=str(shape.get("placeholder") or shape.get("kind") or "TEXT"),
                    x=round(x, 1),
                    y=round(y, 1),
                    w=round(w, 1),
                    h=round(h, 1),
                    ground=ground,
                    busy=round(busy, 3),
                )
            )

        furniture: list[Furniture] = []
        for (shape, box), lost in zip(pieces, piece_erased):
            asset = shape.get("image")
            x, y, w, h = box
            key = _furniture_key(shape, canvas)
            furniture.append(
                Furniture(
                    asset=(
                        str((media_dir / asset).relative_to(spec_root.parent))
                        if asset and (media_dir / asset).is_file()
                        else asset
                    ),
                    x=round(x, 1),
                    y=round(y, 1),
                    w=round(w, 1),
                    h=round(h, 1),
                    slides=recurrence.get(key, 0) if key else 0,
                    erased=round(lost, 3),
                )
            )

        layouts.append(
            asdict(
                Layout(
                    slide=slide_no,
                    layout=str(record.get("layout") or ""),
                    archetype=classify(shapes, canvas),
                    plate=f"clean/slide-{slide_no:02d}.png",
                    plate_sha256=sha256(plate_path),
                    protected=slide_no in protected,
                    live=live,
                    erased_fraction=round(erased, 4),
                    mean_delta=round(mean_delta, 2),
                    zones=zones,
                    furniture=furniture,
                )
            )
        )

    return {
        "schema_version": 1,
        "canvas": list(_canvas(records[0])),
        "layouts": layouts,
        "dead_plates": dead,
    }


# --------------------------------------------------------------------------
# the consuming side


_IMG_SRC = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)


def plate_findings(workspace: Path, slides: list[dict]) -> dict[str, list[str]]:
    """Every slide's plate, checked against the pinned template plates.

    This is the gate the invented flat rectangles walked through. A plate is
    admissible only if the contract pins it and the bytes still hash to what
    was pinned, which is a different question from whether an ``<img>`` exists.
    """
    contract_path = workspace / "layouts.json"
    if not contract_path.is_file():
        return {}
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except ValueError:
        return {"_layouts": ["layouts.json is not valid JSON"]}

    pinned = {
        Path(entry["plate"]).name: entry for entry in contract.get("layouts") or []
    }
    findings: dict[str, list[str]] = {}
    for index, slide in enumerate(slides):
        html = slide.get("html") or ""
        sources = _IMG_SRC.findall(html)
        plates = [s for s in sources if "clean/" in s or Path(s).name in pinned]
        if not plates:
            findings.setdefault(str(index), []).append(
                "no plate: every slide on the roundtrip route sits on a real "
                "template plate from clean/"
            )
            continue
        name = Path(plates[0]).name
        entry = pinned.get(name)
        if entry is None:
            findings.setdefault(str(index), []).append(
                f"plate {name!r} is not one of the template's plates. Invented "
                "grounds carry none of the template's furniture; pick a layout "
                "from layouts.json instead."
            )
            continue
        actual = workspace / "clean" / name
        if not actual.is_file():
            findings.setdefault(str(index), []).append(f"plate {name!r} is missing")
        elif sha256(actual) != entry["plate_sha256"]:
            findings.setdefault(str(index), []).append(
                f"plate {name!r} has been overwritten since precompute pinned it"
            )
        elif not entry.get("live", True):
            lost = [
                piece
                for piece in entry.get("furniture") or []
                if piece.get("erased", 0.0) > FURNITURE_ERASED_LIMIT
            ]
            cause = (
                f"the brand furniture at {lost[0]['x']:g},{lost[0]['y']:g} was "
                "painted out"
                if lost
                else f"{entry['erased_fraction']:.0%} of the template's own "
                "artwork was erased"
            )
            findings.setdefault(str(index), []).append(
                f"plate {name!r} is dead: {cause}. A slide on this plate carries "
                "none of the template's furniture. Re-run `deck plates` and "
                "rebuild layouts.json before authoring on it."
            )
        if entry is not None and entry.get("protected"):
            findings.setdefault(str(index), []).append(
                f"plate {name!r} belongs to a protected slide and must not be reused"
            )
    return findings


def bundle_findings(workspace: Path) -> list[str]:
    """The prepared bundle is complete, or it is not a bundle.

    Eleven baselines and four registered slide types shipped together on the
    occasioning run. The seven unregistered ones could not be patched, so every
    slide was authored freehand instead and the seam route never ran once.
    """
    baselines = workspace / "baselines"
    types_path = workspace / "slide-types.json"
    if not baselines.is_dir() and not types_path.is_file():
        return []

    problems: list[str] = []
    if not types_path.is_file():
        return ["baselines/ exists with no slide-types.json to register them"]
    try:
        declared = json.loads(types_path.read_text(encoding="utf-8"))
    except ValueError:
        return ["slide-types.json is not valid JSON"]

    registered: set[str] = set()
    for entry in declared.get("slide_types") or []:
        path = str(entry.get("baseline_path") or "")
        registered.add(Path(path).name)
        if not (workspace / path).is_file():
            problems.append(f"slide type {entry.get('id')!r} names a missing {path}")
        if not entry.get("seams"):
            problems.append(f"slide type {entry.get('id')!r} declares no seams")

    present = {p.name for p in baselines.glob("*.html")} if baselines.is_dir() else set()
    for orphan in sorted(present - registered):
        problems.append(
            f"baselines/{orphan} is not registered in slide-types.json, so no "
            "patch can target it and authoring falls back to freehand"
        )
    return problems


# --------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="layout_contract",
        description="Join a template's geometry to its plates.",
    )
    parser.add_argument("--spec", required=True, help="spec-out/ from `deck spec`")
    parser.add_argument("--clean", required=True, help="clean/ from `deck plates`")
    parser.add_argument("--out", required=True, help="path to write layouts.json")
    parser.add_argument("--contract", help="brand-contract.json, for protected slides")
    parser.add_argument(
        "--allow-dead-plates",
        action="store_true",
        help="emit the contract even when plates lost the template's artwork",
    )
    args = parser.parse_args(argv[1:])

    contract = build(
        Path(args.spec),
        Path(args.clean),
        Path(args.contract) if args.contract else None,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")

    dead = contract["dead_plates"]
    print(
        json.dumps(
            {
                "out": str(out_path),
                "layouts": len(contract["layouts"]),
                "dead_plates": dead,
                "archetypes": sorted(
                    {entry["archetype"] for entry in contract["layouts"]}
                ),
            },
            indent=2,
        )
    )
    if dead:
        print(
            f"\n{len(dead)} of {len(contract['layouts'])} plates lost the "
            "template's own artwork: "
            + ", ".join(str(n) for n in dead)
            + ".\nThese carry no logo, no rules and no motif, so a slide "
            "authored on one cannot look like the template. Re-run `deck "
            "plates` and rebuild this contract.",
            file=sys.stderr,
        )
        if not args.allow_dead_plates:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
