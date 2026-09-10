"""OCR must read slide text without treating artwork as a document layout."""

import io
import shutil

from PIL import Image, ImageDraw, ImageFont
import pytest

from slidify.models import BoundingBox, DomElement
from slidify.oracle import compute_ocr_recall, prepare_text_crop, text_region_crops


def element(text="Template Fidelity Proof", **kwargs):
    return DomElement(id=1, parent_id=None, depth=0, tag="DIV",
                      bbox=BoundingBox(x=40, y=80, w=540, h=80), text=text, **kwargs)


def test_regions_include_native_tables_and_skip_non_content():
    table = element(None, is_table=True, table_data={"rows": [[{"text": "Check"}]]})
    table.bbox = BoundingBox(x=30, y=300, w=900, h=200)
    image = element(None, is_img=True)
    hidden = element(opacity=0)
    offscreen = element()
    offscreen.bbox = BoundingBox(x=-1000, y=-1000, w=100, h=30)
    assert text_region_crops([element(), element(), table, image, hidden, offscreen], (1280, 720)) == [
        (28, 68, 592, 172), (18, 288, 942, 512),
    ]


def test_dark_regions_become_light_with_enough_pixels_for_small_type():
    picture = Image.new("RGB", (20, 10), "#1971ed")
    ImageDraw.Draw(picture).rectangle((2, 2, 3, 7), fill="white")
    crop = prepare_text_crop(picture, (0, 0, 20, 10))
    assert crop.size == (60, 30)
    assert crop.getpixel((0, 0)) == 255
    assert crop.getpixel((7, 10)) < 128


def png(text, *, footer=False):
    picture = Image.new("RGB", (1280, 720), "#1971ed")
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 11 if footer else 34)
    except OSError:
        pytest.skip("DejaVu Sans is not installed")
    ImageDraw.Draw(picture).text((40, 90), text, font=font, fill="white")
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract is not installed")
@pytest.mark.parametrize("text,footer", [
    ("Template Fidelity Proof", False), ("Internal Engineering Review", True),
])
def test_real_ocr_reads_white_type_and_small_footers(text, footer):
    source = png(text, footer=footer)
    score, expected, actual = compute_ocr_recall(source, source, elements=[element(text)])
    assert score == 1
    assert "fidelity" in expected if not footer else "internal" in expected
    assert expected == actual


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract is not installed")
def test_real_ocr_still_rejects_a_missing_title():
    score, expected, actual = compute_ocr_recall(
        png("Template Fidelity Proof"), png(""), elements=[element()],
    )
    assert expected == {"template", "fidelity", "proof"}
    assert not actual
    assert score == 0
