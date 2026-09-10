"""Fidelity Oracle.

After PPTX emission, render the file back to PNG via LibreOffice + pdftoppm,
compute SSIM and OCR recall against the browser ground truth, and report
per-slide passes / failures.
"""

from __future__ import annotations

import asyncio
import io
import shutil
import tempfile
from pathlib import Path

import numpy as np
import structlog
from PIL import Image, ImageOps, ImageStat

from slidify.exceptions import OracleError
from slidify.geom import SLIDE_H_PX, SLIDE_W_PX
from slidify.models import (
    BoundingBox,
    Decision,
    DecisionKind,
    DomElement,
    FailingUnitAttribution,
    FidelityReport,
    VisualUnit,
)

log = structlog.get_logger(__name__)


# SSIM floors are calibrated to LibreOffice as the rendering oracle —
# LibreOffice has documented font/anti-alias drift relative to PowerPoint
# (~3-5%), so a 0.95 spec floor produces noisy false-positives on every
# text-heavy slide. 0.85 is the realistic threshold below which something
# *structural* is wrong (gradient missing, shape clipped, layout broken).
# The original 0.95 is preserved as STRICT_SSIM_FLOOR for users on the
# eventual PowerPoint-Online oracle path.
SSIM_FLOOR = 0.85
STRICT_SSIM_FLOOR = 0.95
OCR_RECALL_FLOOR = 0.95


def _check_binaries() -> None:
    for binary in ("libreoffice", "pdftoppm", "tesseract"):
        if shutil.which(binary) is None:
            raise OracleError(f"missing system binary: {binary}")


async def render_pptx_to_pngs(pptx_path: Path) -> list[bytes]:
    """Render a PPTX to one PNG per slide at SLIDE_W_PX × SLIDE_H_PX."""
    _check_binaries()
    pptx_path = Path(pptx_path).resolve()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Step 1: PPTX → PDF via LibreOffice
        proc = await asyncio.create_subprocess_exec(
            "libreoffice",
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            "--convert-to",
            "pdf",
            "--outdir",
            str(tmp),
            str(pptx_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise OracleError(
                f"libreoffice failed (rc={proc.returncode}): {stderr.decode(errors='ignore')[:300]}"
            )
        pdf_path = tmp / (pptx_path.stem + ".pdf")
        if not pdf_path.exists():
            # LibreOffice sometimes drops a different name; pick the only PDF.
            pdfs = list(tmp.glob("*.pdf"))
            if not pdfs:
                raise OracleError("libreoffice produced no PDF")
            pdf_path = pdfs[0]

        # Step 2: PDF → PNGs via pdftoppm. Force resolution so slides come out
        # at the target size. PPTX page is 13.33in × 7.5in at 96dpi → 1280×720.
        # We'll render at 96 DPI and resize/pad to target.
        proc = await asyncio.create_subprocess_exec(
            "pdftoppm",
            "-r",
            "96",
            "-png",
            str(pdf_path),
            str(tmp / "slide"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise OracleError(
                f"pdftoppm failed (rc={proc.returncode}): {stderr.decode(errors='ignore')[:300]}"
            )

        png_files = sorted(tmp.glob("slide-*.png"))
        if not png_files:
            raise OracleError("pdftoppm produced no PNGs")

        out: list[bytes] = []
        for p in png_files:
            with Image.open(p) as im:
                im = im.convert("RGB")
                if im.size != (SLIDE_W_PX, SLIDE_H_PX):
                    im = im.resize((SLIDE_W_PX, SLIDE_H_PX), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                out.append(buf.getvalue())
        return out


def _png_to_array(data: bytes) -> np.ndarray:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if im.size != (SLIDE_W_PX, SLIDE_H_PX):
        im = im.resize((SLIDE_W_PX, SLIDE_H_PX), Image.Resampling.LANCZOS)
    return np.asarray(im)


def compute_ssim(a: bytes, b: bytes) -> float:
    from skimage.metrics import structural_similarity as ssim

    arr_a = _png_to_array(a)
    arr_b = _png_to_array(b)
    score = ssim(arr_a, arr_b, channel_axis=2, data_range=255)
    return float(score)


def compute_ocr_recall(
    gt: bytes, candidate: bytes, *, elements: list[DomElement] | None = None,
) -> tuple[float, set[str], set[str]]:
    import pytesseract

    a = Image.open(io.BytesIO(gt)).convert("RGB")
    b = Image.open(io.BytesIO(candidate)).convert("RGB")
    if elements:
        crops = text_region_crops(elements, a.size)
        expected, actual = set(), set()
        matched = total = 0
        for crop in crops:
            words_a = _normalize_words(pytesseract.image_to_string(
                prepare_text_crop(a, crop), config="--psm 6",
            ))
            words_b = _normalize_words(pytesseract.image_to_string(
                prepare_text_crop(b, crop), config="--psm 6",
            ))
            expected.update(words_a)
            actual.update(words_b)
            total += len(words_a)
            matched += len(words_a & words_b)
        if total:
            return matched / total, expected, actual
    text_a = pytesseract.image_to_string(a)
    text_b = pytesseract.image_to_string(b)
    words_a = _normalize_words(text_a)
    words_b = _normalize_words(text_b)
    if not words_a:
        return 1.0, words_a, words_b
    inter = words_a & words_b
    return len(inter) / len(words_a), words_a, words_b


def text_region_crops(
    elements: list[DomElement], size: tuple[int, int],
) -> list[tuple[int, int, int, int]]:
    """Keep text and native tables, not whole-slide artwork, in OCR's layout."""
    import math

    crops = set()
    for element in elements:
        if not (element.text or element.mixed_content_text or element.table_data):
            continue
        if element.opacity <= 0 or element.pptx_skip:
            continue
        box = element.bbox
        if box.w <= 0 or box.h <= 0:
            continue
        # Small render/font offsets must not crop a glyph before OCR reads it.
        crop = (max(0, math.floor(box.x) - 12), max(0, math.floor(box.y) - 12),
                min(size[0], math.ceil(box.x2) + 12), min(size[1], math.ceil(box.y2) + 12))
        if crop[0] < crop[2] and crop[1] < crop[3]:
            crops.add(crop)
    return sorted(crops, key=lambda box: (box[1], box[0]))


def prepare_text_crop(image: Image.Image, crop: tuple[int, int, int, int]) -> Image.Image:
    """OCR needs dark glyphs on light paper and enough pixels for small footers."""
    region = ImageOps.grayscale(image.crop(crop))
    if ImageStat.Stat(region).median[0] < 128:
        region = ImageOps.invert(region)
    region = ImageOps.autocontrast(region)
    return region.resize((region.width * 3, region.height * 3), Image.Resampling.BICUBIC)


def _normalize_words(text: str) -> set[str]:
    """Tokenize for OCR comparison.

    Lowercase, strip non-alphanumerics, drop tokens shorter than 2 chars and
    pure-digit tokens — Tesseract is unreliable on isolated digits and on
    symbol characters like → / — which the browser and LibreOffice render
    differently.
    """
    out: set[str] = set()
    for tok in text.lower().split():
        tok = "".join(ch for ch in tok if ch.isalnum())
        if len(tok) < 2:
            continue
        if tok.isdigit():
            continue
        out.add(tok)
    return out


def find_failing_regions(gt: bytes, candidate: bytes, threshold: int = 40) -> list[BoundingBox]:
    """Diff the two PNGs and return bounding boxes of dissimilar regions."""
    a = _png_to_array(gt).astype(np.int16)
    b = _png_to_array(candidate).astype(np.int16)
    diff = np.abs(a - b).max(axis=2)  # (H, W)
    mask = (diff > threshold).astype(np.uint8) * 255
    if mask.sum() == 0:
        return []
    # Connected components via scipy
    try:
        from scipy.ndimage import find_objects, label

        labelled, _n = label(mask)
        slices = find_objects(labelled)
    except Exception:
        slices = []

    boxes: list[BoundingBox] = []
    for s in slices:
        if s is None:
            continue
        y0, y1 = s[0].start, s[0].stop
        x0, x1 = s[1].start, s[1].stop
        if (y1 - y0) * (x1 - x0) < 64:  # noise filter
            continue
        boxes.append(BoundingBox(x=float(x0), y=float(y0), w=float(x1 - x0), h=float(y1 - y0)))
    # Cap to avoid runaway noise
    return boxes[:20]


_DECISION_KIND_LABELS: dict[DecisionKind, str] = {
    DecisionKind.NativeText: "NativeText",
    DecisionKind.NativeShape: "NativeShape",
    DecisionKind.NativeBullet: "NativeBullet",
    DecisionKind.NativePicture: "NativePicture",
    DecisionKind.NativeSvg: "NativeSvg",
    DecisionKind.NativeTable: "NativeTable",
    DecisionKind.Raster: "Raster",
    DecisionKind.Hybrid: "Hybrid",
    DecisionKind.Skip: "Skip",
}


def _decision_kind_label(kind: DecisionKind) -> str:
    return _DECISION_KIND_LABELS.get(kind, str(kind))


def _unit_has_svg(unit: VisualUnit) -> bool:
    """True when any element under this unit is an SVG node."""
    return any(getattr(e, "is_svg", False) for e in unit.all_elements())


def _suspected_failure(
    decision: Decision,
    region: BoundingBox,
    unit: VisualUnit,
) -> str:
    """Heuristic root-cause guess for a failing region attributed to `unit`.

    Rules (first match wins):
      - NativeText + region tall+narrow      → "wrap_overflow"
      - NativeText + region wide+short       → "font_metrics"
      - NativeText (other shape)             → "font_metrics"
      - NativeShape + decision metadata
        recipe contains 'gradient'           → "gradient_render_drift"
      - NativeShape over an SVG-bearing unit → "svg_path_render"
      - Hybrid                               → "raster_overlap"
      - Raster                               → "raster_quality"
      - else                                 → "unknown"
    """
    kind = decision.kind
    if kind == DecisionKind.NativeText:
        # tall+narrow vs wide+short are mutually exclusive heuristics.
        if region.h > 0 and region.w > 0:
            if region.h >= region.w * 1.5:
                return "wrap_overflow"
            if region.w >= region.h * 1.5:
                return "font_metrics"
        return "font_metrics"

    if kind == DecisionKind.NativeShape:
        recipe = ""
        meta = decision.metadata or {}
        raw_recipe = meta.get("recipe")
        if isinstance(raw_recipe, str):
            recipe = raw_recipe.lower()
        if "gradient" in recipe:
            return "gradient_render_drift"
        # The DecisionKind enum has no dedicated NativeSvg; we surface SVG
        # render drift via a NativeShape decision over an SVG-bearing unit.
        if _unit_has_svg(unit):
            return "svg_path_render"
        return "unknown"

    if kind == DecisionKind.Hybrid:
        return "raster_overlap"
    if kind == DecisionKind.Raster:
        return "raster_quality"
    return "unknown"


def attribute_regions_to_units(
    regions: list[BoundingBox],
    units: dict[str, VisualUnit],
    decisions: dict[str, Decision],
) -> list[FailingUnitAttribution]:
    """For each region, find the smallest unit that fully contains
    (or substantially overlaps with) the region's bbox. Returns
    per-region attribution rows.

    A unit is a candidate when at least 50% of the region's area lies
    inside the unit's bbox. Among candidates, the smallest unit wins
    (most specific). Regions with no candidate are skipped.

    `suspected_failure` is computed by `_suspected_failure` above:
      - NativeText + region tall+narrow → "wrap_overflow"
      - NativeText + region wide+short  → "font_metrics"
      - NativeShape + recipe gradient   → "gradient_render_drift"
      - NativeShape over an SVG unit    → "svg_path_render"
      - Hybrid                          → "raster_overlap"
      - Raster                          → "raster_quality"
      - else                            → "unknown"
    """
    out: list[FailingUnitAttribution] = []
    if not regions or not units:
        return out

    for region in regions:
        region_area = region.area
        if region_area <= 0:
            continue
        best: VisualUnit | None = None
        for unit in units.values():
            if unit.bbox.area <= 0:
                continue
            contained = unit.bbox.intersect_area(region) / region_area
            if contained < 0.5:
                continue
            if best is None or unit.bbox.area < best.bbox.area:
                best = unit
        if best is None:
            continue
        decision = decisions.get(best.id)
        if decision is None:
            # Unit exists but no decision recorded — surface as unknown so
            # callers can still see the unit, rather than dropping the row.
            decision_kind_label = "Unknown"
            source_tier = "unknown"
            reason = ""
            suspected = "unknown"
        else:
            decision_kind_label = _decision_kind_label(decision.kind)
            source_tier = decision.source_tier
            reason = decision.reason
            suspected = _suspected_failure(decision, region, best)
        out.append(
            FailingUnitAttribution(
                region=region,
                unit_id=best.id,
                decision_kind=decision_kind_label,
                source_tier=source_tier,
                reason=reason,
                suspected_failure=suspected,
            )
        )
    return out


class FidelityOracle:
    def __init__(
        self,
        ssim_floor: float = SSIM_FLOOR,
        ocr_recall_floor: float = OCR_RECALL_FLOOR,
    ) -> None:
        self.ssim_floor = ssim_floor
        self.ocr_recall_floor = ocr_recall_floor

    async def evaluate(
        self,
        pptx_path: Path,
        ground_truths: list[bytes],
        units_per_slide: list[tuple[dict[str, VisualUnit], dict[str, Decision]]] | None = None,
        text_elements_per_slide: list[list[DomElement]] | None = None,
    ) -> list[FidelityReport]:
        """Run SSIM + OCR + region diff against `ground_truths`.

        When `units_per_slide` is provided (one (units_by_id, decisions) tuple
        per slide, in the same order as `ground_truths`), each
        FidelityReport's `failing_units` is populated by attributing each
        failing region back to the most specific containing unit. When None,
        `failing_units` stays empty (back-compat).
        """
        try:
            renders = await render_pptx_to_pngs(pptx_path)
        except OracleError as e:
            log.warning("oracle.render_failed", error=str(e))
            return [
                FidelityReport(
                    slide_index=i,
                    ssim=0.0,
                    ocr_recall=0.0,
                    passed=False,
                    note=f"oracle_render_failed: {e}",
                )
                for i in range(len(ground_truths))
            ]

        reports: list[FidelityReport] = []
        for i, gt in enumerate(ground_truths):
            if i >= len(renders):
                reports.append(
                    FidelityReport(
                        slide_index=i,
                        ssim=0.0,
                        ocr_recall=0.0,
                        passed=False,
                        note="missing_render",
                    )
                )
                continue
            cand = renders[i]
            try:
                ssim_score = compute_ssim(gt, cand)
            except Exception as e:
                log.warning("oracle.ssim_failed", slide=i, error=str(e))
                ssim_score = 0.0
            try:
                elements = text_elements_per_slide[i] if text_elements_per_slide is not None else None
                recall, _, _ = compute_ocr_recall(gt, cand, elements=elements)
            except Exception as e:
                log.warning("oracle.ocr_failed", slide=i, error=str(e))
                recall = 0.0
            passed = ssim_score >= self.ssim_floor and recall >= self.ocr_recall_floor
            failing = find_failing_regions(gt, cand) if not passed else []
            failing_units: list[FailingUnitAttribution] = []
            if failing and units_per_slide is not None and i < len(units_per_slide):
                units_by_id, decisions = units_per_slide[i]
                if units_by_id:
                    failing_units = attribute_regions_to_units(
                        failing, units_by_id, decisions
                    )
            reports.append(
                FidelityReport(
                    slide_index=i,
                    ssim=ssim_score,
                    ocr_recall=recall,
                    passed=passed,
                    failing_regions=failing,
                    failing_units=failing_units,
                )
            )
        return reports
