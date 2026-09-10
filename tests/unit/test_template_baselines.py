import json

from app.template_baselines import compile_baseline
from app.template_policy import (
    AdmittedSlideType,
    BaselineSpecimen,
    SeamReplacement,
    SlidePatch,
    apply_seam_patch,
)
from test_template_policy import _artifacts


def test_source_box_and_type_are_fixed_while_body_can_contain_a_table():
    record = json.loads(_artifacts()["spec/slide-01.json"])
    record["shapes"][0].update(placeholder="BODY", anchor="b")
    slide_type, html = compile_baseline(record)
    baseline = BaselineSpecimen.from_html(AdmittedSlideType.from_dict(slide_type), html)
    authored = apply_seam_patch(
        baseline,
        SlidePatch(
            baseline.sha256,
            (
                SeamReplacement(
                    "shape-1",
                    "<table><tr><td>Layout</td><td>Preserved</td></tr></table>",
                ),
            ),
        ),
    )
    assert "top:56px" in authored
    assert "font-size:40px" in authored
    assert "justify-content:flex-end" in authored
    assert "<td>Preserved</td>" in authored
    assert "../clean/slide-01.png" in authored


def test_source_table_cells_get_their_own_measured_seams():
    record = json.loads(_artifacts()["spec/slide-01.json"])
    text = record["shapes"][0]["text"]
    record["shapes"].append(
        {
            "shape_id": 12,
            "x": 50,
            "y": 200,
            "table": {
                "widths": [200, 300],
                "heights": [40],
                "rows": [
                    [
                        {"text": text, "rowspan": 1, "colspan": 1},
                        {"text": text, "rowspan": 1, "colspan": 1},
                    ]
                ],
            },
        }
    )
    slide_type, baseline = compile_baseline(record)
    assert slide_type["archetype"] == "table"
    assert "pp:seam:shape-12-r0-c1:start" in baseline
    assert "left:250px;top:200px;width:300px;height:40px" in baseline


def test_page_field_is_system_owned_not_an_author_editable_seam():
    record = json.loads(_artifacts()["spec/slide-01.json"])
    record["slide"] = 26
    record["slide_number"] = {**record["shapes"][0], "shape_id": 44,
                              "x": 1163.5, "y": 661.6, "w": 68.5, "h": 14}
    slide_type, baseline = compile_baseline(record)
    assert "<!-- pp:slide-number -->26" in baseline
    assert len(slide_type["seams"]) == 1
