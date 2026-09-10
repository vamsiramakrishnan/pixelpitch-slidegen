"""End-to-end smoke test: convert the fixture deck and inspect the result."""

from __future__ import annotations

from pathlib import Path

import pytest

from slidify.api import ConversionConfig, convert

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_deck.html"


@pytest.mark.asyncio
async def test_convert_sample_deck(tmp_path: Path):
    pptx = tmp_path / "out.pptx"
    html = FIXTURE.read_text(encoding="utf-8")
    cfg = ConversionConfig(
        run_oracle=False,  # oracle is slow; covered by separate test
        run_tier3=False,   # avoid LLM calls in unit/integration tests
    )
    result = await convert(html, pptx, cfg)

    assert pptx.exists() and pptx.stat().st_size > 0
    assert result.n_slides == 3
    # Without tier 3, ratio should still be > 0 because tier 1 catches simple text/shapes.
    assert result.native_area_ratio > 0.0
    # No LLM calls when tier3 is off.
    assert result.llm_calls == 0


@pytest.mark.asyncio
async def test_convert_with_oracle(tmp_path: Path):
    pptx = tmp_path / "out_oracle.pptx"
    html = FIXTURE.read_text(encoding="utf-8")
    cfg = ConversionConfig(
        run_oracle=True,
        run_tier3=False,
    )
    result = await convert(html, pptx, cfg)
    assert pptx.exists()
    assert len(result.fidelity_reports) == 3
    for r in result.fidelity_reports:
        # Don't assert pass/fail (depends on font availability), but the
        # numbers must be in valid ranges.
        assert 0.0 <= r.ssim <= 1.0
        assert 0.0 <= r.ocr_recall <= 1.0
