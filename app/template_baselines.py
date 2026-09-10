"""Compile immutable HTML shells from measured source shapes, without inference.

The author owns the narrative inside seams. Source OOXML owns the surrounding
geometry, type scale, and artwork. This module needs only the serialized spec,
so the A2A process does not need PowerPoint or a browser installed.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping


def _css(values: dict) -> str:
    return html.escape(
        ";".join(f"{key}:{value}" for key, value in values.items()), quote=True
    )


def _run_style(run: dict) -> dict:
    if not run.get("font") or not run.get("size_px") or not run.get("color"):
        raise ValueError("source text has unresolved font, size, or colour")
    return {
        "font-family": f"'{run['font']}'",
        "font-size": f"{run['size_px']:g}px",
        "font-weight": "700" if run.get("bold") else "400",
        "font-style": "italic" if run.get("italic") else "normal",
        "color": run["color"],
    }


def _paragraphs(paragraphs: list[dict]) -> str:
    parts = []
    for paragraph in paragraphs:
        runs = [run for run in paragraph["runs"] if run.get("text")]
        if not runs:
            parts.append("<br>")
            continue
        spacing = paragraph.get("spacing") or {}
        line = spacing.get("lnSpc") or {"multiple": 1.2}
        style = {
            "margin": "0",
            "text-align": paragraph.get("align", "LEFT").lower(),
            "line-height": str(line["multiple"])
            if "multiple" in line
            else f"{line['px']:g}px",
        }
        for source, target in (("spcBef", "margin-top"), ("spcAft", "margin-bottom")):
            if "px" in spacing.get(source, {}):
                style[target] = f"{spacing[source]['px']:g}px"
        contents = "".join(
            f'<span style="{_css(_run_style(run))}">{html.escape(run["text"])}</span>'
            for run in runs
        )
        parts.append(f'<p style="{_css(style)}">{contents}</p>')
    return "".join(parts)


def _text(shape: dict) -> str:
    return "".join(
        run.get("text", "") for p in shape.get("text", []) for run in p["runs"]
    )


def _text_shapes(record: dict) -> list[dict]:
    shapes = []
    for shape in record["shapes"]:
        if "table" not in shape:
            shapes.append(shape)
            continue
        table = shape["table"]
        for row_index, row in enumerate(table["rows"]):
            for column_index, cell in enumerate(row):
                if cell.get("spanned"):
                    continue
                shapes.append(
                    {
                        "shape_id": f"{shape['shape_id']}-r{row_index}-c{column_index}",
                        "placeholder": "TABLE_CELL",
                        "x": shape["x"] + sum(table["widths"][:column_index]),
                        "y": shape["y"] + sum(table["heights"][:row_index]),
                        "w": sum(
                            table["widths"][
                                column_index : column_index + cell["colspan"]
                            ]
                        ),
                        "h": sum(
                            table["heights"][row_index : row_index + cell["rowspan"]]
                        ),
                        "text": cell["text"],
                        "insets": cell.get("insets", [0, 0, 0, 0]),
                    }
                )
    return shapes


def compile_baseline(record: dict) -> tuple[dict, str]:
    """One real specimen, with its existing text boxes as the editable seams."""
    number = record["slide"]
    width, height = record["canvas"]
    if width != 1280 or height != 720:
        raise ValueError("template canvas is not supported by the 1280x720 converter")
    slide_id = f"source-{number:02d}"
    elements, seams = [], []
    number_shape = record.get("slide_number")
    if number_shape:
        # Source slide artwork sits above inherited fields. A cover photograph
        # that covers the field's box must keep covering it in the reconstruction.
        covered = any(
            s.get("kind") == "PICTURE"
            and s.get("x", 0) <= number_shape["x"]
            and s.get("y", 0) <= number_shape["y"]
            and s.get("x", 0) + s.get("w", 0) >= number_shape["x"] + number_shape["w"]
            and s.get("y", 0) + s.get("h", 0) >= number_shape["y"] + number_shape["h"]
            for s in record["shapes"]
        )
        if not covered:
            run = next(
                run
                for p in number_shape["text"]
                for run in p["runs"]
                if run.get("text")
            )
            style = {
                "position": "absolute",
                "left": f"{number_shape['x']:g}px",
                "top": f"{number_shape['y']:g}px",
                "width": f"{number_shape['w']:g}px",
                "height": f"{number_shape['h']:g}px",
                "display": "flex",
                "flex-direction": "column",
                "justify-content": "flex-end",
                "text-align": "right",
                "z-index": "2",
                **_run_style(run),
            }
            elements.append(
                f'<div style="{_css(style)}"><span data-pptx-role="footer" style="align-self:flex-end;display:block">'
                f'<!-- pp:slide-number -->{number}</span></div>'
            )
    shapes = _text_shapes(record)
    # Group coordinates need the full group transform, not the child's raw
    # x/y. Until extracted, retain that artwork in the plate, not a fake seam.
    if any(
        s.get("children") and any(_text(c).strip() for c in s["children"])
        for s in shapes
    ):
        raise ValueError("editable grouped text needs transformed source geometry")
    for shape in shapes:
        if shape.get("slide_number_field"):
            continue
        if not _text(shape).strip():
            continue
        seam_id = f"shape-{shape['shape_id']}"
        runs = [run for p in shape["text"] for run in p["runs"] if run.get("text")]
        style = {
            "position": "absolute",
            "left": f"{shape['x']:g}px",
            "top": f"{shape['y']:g}px",
            "width": f"{shape['w']:g}px",
            "height": f"{shape['h']:g}px",
            "padding": " ".join(f"{v:g}px" for v in shape.get("insets", [0, 0, 0, 0])),
            "overflow": "hidden",
            "z-index": "2",
            **_run_style(runs[0]),
        }
        if shape.get("rotation"):
            style["transform"] = f"rotate({shape['rotation']:g}deg)"
        if shape.get("anchor") in {"ctr", "b"}:
            style.update(
                {
                    "display": "flex",
                    "flex-direction": "column",
                    "justify-content": "center"
                    if shape["anchor"] == "ctr"
                    else "flex-end",
                }
            )
        role = shape.get("placeholder", "TEXT")
        aid = role in {"BODY", "OBJECT"}
        tags = ["p", "span", "br", "strong", "em"]
        styles = [
            "margin",
            "margin-top",
            "margin-bottom",
            "text-align",
            "line-height",
            "font-weight",
            "font-style",
            "color",
        ]
        if aid:
            tags += [
                "div",
                "table",
                "thead",
                "tbody",
                "tr",
                "th",
                "td",
                "svg",
                "g",
                "path",
                "rect",
                "circle",
                "line",
                "text",
                "polyline",
                "polygon",
            ]
            styles += [
                "display",
                "grid-template-columns",
                "gap",
                "width",
                "height",
                "padding",
                "border",
                "border-bottom",
                "border-collapse",
                "background",
                "background-color",
                "align-items",
                "justify-content",
            ]
        seams.append(
            {
                "id": seam_id,
                "kind": "visual_aid" if aid else "content",
                "source_shape_id": shape["shape_id"],
                "role": role,
                "allowed_tags": tags,
                "allowed_style_properties": styles,
            }
        )
        elements.append(
            f'<div data-pp-source-shape="{shape["shape_id"]}" style="{_css(style)}">'
            f"<!-- pp:seam:{seam_id}:start -->{_paragraphs(shape['text'])}<!-- pp:seam:{seam_id}:end --></div>"
        )
    if not seams:
        raise ValueError("source slide has no supported text seams")
    archetype = (
        "body"
        if any(seam["kind"] == "visual_aid" for seam in seams)
        else "hero/section"
    )
    if any("table" in shape for shape in record["shapes"]):
        archetype = "table"
    document = (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        "*{box-sizing:border-box}body{margin:0;width:1280px;height:720px;position:relative;overflow:hidden}"
        "</style></head><body>"
        f'<img alt="Source artwork" src="../clean/slide-{number:02d}.png" '
        'style="position:absolute;left:0;top:0;width:1280px;height:720px;z-index:1">'
        + "".join(elements)
        + "</body></html>"
    )
    return {
        "id": slide_id,
        "source_slide": number,
        "archetype": archetype,
        "baseline_path": f"baselines/{slide_id}.html",
        "seams": seams,
    }, document


def compile_source_baselines(
    artifacts: Mapping[str, bytes], protected: set[int] | None = None
) -> dict[str, bytes]:
    """Compile safe source slides; preserve exclusions as explicit evidence."""
    types, excluded, result = [], [], {}
    for path, raw in sorted(artifacts.items()):
        if not path.startswith("spec/slide-") or not path.endswith(".json"):
            continue
        record = json.loads(raw)
        number = record["slide"]
        fixed = " ".join(
            _text(shape) for shape in record["shapes"] if not shape.get("placeholder")
        )
        long_fixed_placeholder = any(
            shape.get("placeholder") == "SUBTITLE" and len(_text(shape)) > 300
            for shape in record["shapes"]
        )
        reason = (
            "protected source material"
            if number in (protected or set())
            or len(fixed) > 80
            or long_fixed_placeholder
            else None
        )
        try:
            if reason:
                raise ValueError(reason)
            slide_type, baseline = compile_baseline(record)
        except ValueError as exc:
            excluded.append({"slide": number, "reason": str(exc)})
            continue
        types.append(slide_type)
        result[slide_type["baseline_path"]] = baseline.encode()
    if not types:
        raise ValueError("no source layouts can be safely compiled")
    result["slide-types.json"] = json.dumps(
        {"slide_types": types, "excluded": excluded}, indent=2
    ).encode()
    return result
