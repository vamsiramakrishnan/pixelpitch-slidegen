"""End-to-end: render an HTML deck with a real <table>, convert it, and
verify the produced PPTX contains a native table primitive (editable cells,
not floating textboxes or a raster crop)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pptx import Presentation

from slidify.api import ConversionConfig, convert

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "table_deck.html"


@pytest.mark.asyncio
async def test_convert_html_table_to_native_pptx_table(tmp_path: Path):
    pptx = tmp_path / "table_deck.pptx"
    cfg = ConversionConfig(run_oracle=False, run_tier3=False)
    result = await convert(FIXTURE, pptx, cfg)
    assert pptx.exists() and pptx.stat().st_size > 0
    assert result.n_slides == 1

    prs = Presentation(str(pptx))
    s = prs.slides[0]
    tables = [shp for shp in s.shapes if shp.has_table]
    assert len(tables) == 1, (
        f"expected exactly one native table, got {len(tables)} "
        f"(shapes: {[shp.shape_type for shp in s.shapes]})"
    )
    t = tables[0].table
    # 1 header row + 4 body rows; 3 cols.
    assert len(t.rows) == 5
    assert len(t.columns) == 3
    # Header text round-trips.
    assert t.cell(0, 0).text_frame.text == "Quarter"
    assert t.cell(0, 2).text_frame.text == "YoY"
    # A body cell round-trips.
    assert t.cell(2, 1).text_frame.text == "$3.9M"


@pytest.mark.asyncio
async def test_table_over_template_plate_keeps_every_cell(tmp_path: Path):
    """A full-slide template image must not absorb the table above it."""
    import base64
    import io

    from PIL import Image

    plate = io.BytesIO()
    Image.new("RGB", (16, 9), "white").save(plate, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(plate.getvalue()).decode()
    html = FIXTURE.read_text().replace(
        "<body>",
        '<body><img style="position:absolute;left:0;top:0;width:1280px;'
        f'height:720px;z-index:1" src="{data_uri}">',
    ).replace(
        '<table class="q">',
        '<div style="position:absolute;left:0;top:0;z-index:2"><table class="q">',
    ).replace("</table>", "</table></div>")
    source = tmp_path / "plate-table.html"
    source.write_text(html)
    output = tmp_path / "plate-table.pptx"

    result = await convert(source, output, ConversionConfig(run_oracle=False, run_tier3=False))

    shapes = Presentation(output).slides[0].shapes
    tables = [shape.table for shape in shapes if shape.has_table]
    assert len(tables) == 1, "table content was silently absorbed by the template image"
    assert tables[0].cell(2, 1).text == "$3.9M"
    assert result.editability_passed
