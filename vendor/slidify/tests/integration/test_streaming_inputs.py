"""Test the multi-form slide source: directory, list of paths, async iterator."""

from __future__ import annotations

from pathlib import Path

import pytest

from slidify.api import ConversionConfig, convert
from slidify.splitter import split_slides

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_deck.html"


def _write_per_slide_files(tmp_path: Path) -> list[Path]:
    """Split the fixture deck into per-slide files in tmp_path."""
    slides = split_slides(FIXTURE.read_text(encoding="utf-8"))
    out: list[Path] = []
    for i, html in enumerate(slides, 1):
        p = tmp_path / f"{i:02d}.html"
        p.write_text(html, encoding="utf-8")
        out.append(p)
    return out


@pytest.mark.asyncio
async def test_convert_from_directory(tmp_path: Path):
    slides_dir = tmp_path / "slides"
    slides_dir.mkdir()
    _write_per_slide_files(slides_dir)
    pptx = tmp_path / "out.pptx"
    cfg = ConversionConfig(run_oracle=False, run_tier3=False)
    result = await convert(slides_dir, pptx, cfg)
    assert pptx.exists() and pptx.stat().st_size > 0
    assert result.n_slides == 3


@pytest.mark.asyncio
async def test_convert_from_list_of_paths(tmp_path: Path):
    paths = _write_per_slide_files(tmp_path)
    pptx = tmp_path / "out.pptx"
    cfg = ConversionConfig(run_oracle=False, run_tier3=False)
    result = await convert(paths, pptx, cfg)
    assert pptx.exists()
    assert result.n_slides == 3


@pytest.mark.asyncio
async def test_convert_from_list_of_strings(tmp_path: Path):
    slides = split_slides(FIXTURE.read_text(encoding="utf-8"))
    pptx = tmp_path / "out.pptx"
    cfg = ConversionConfig(run_oracle=False, run_tier3=False)
    result = await convert(slides, pptx, cfg)
    assert pptx.exists()
    assert result.n_slides == 3


@pytest.mark.asyncio
async def test_convert_from_async_iterator(tmp_path: Path):
    slides = split_slides(FIXTURE.read_text(encoding="utf-8"))

    async def stream():
        for s in slides:
            yield s

    pptx = tmp_path / "out.pptx"
    cfg = ConversionConfig(run_oracle=False, run_tier3=False)
    result = await convert(stream(), pptx, cfg)
    assert pptx.exists()
    assert result.n_slides == 3


@pytest.mark.asyncio
async def test_convert_low_memory_mode_drops_oracle_correction(tmp_path: Path):
    """When `keep_plans_for_oracle=False`, the oracle still runs once but
    auto-correction is skipped — so memory stays bounded."""
    pptx = tmp_path / "out.pptx"
    cfg = ConversionConfig(
        run_oracle=True,
        run_tier3=False,
        keep_plans_for_oracle=False,
    )
    result = await convert(FIXTURE, pptx, cfg)
    assert pptx.exists()
    assert result.n_slides == 3
    # First-pass reports must be returned regardless of correction.
    assert len(result.fidelity_reports) == 3


@pytest.mark.asyncio
async def test_empty_source_raises(tmp_path: Path):
    cfg = ConversionConfig(run_oracle=False, run_tier3=False)
    with pytest.raises(ValueError):
        await convert([], tmp_path / "out.pptx", cfg)
