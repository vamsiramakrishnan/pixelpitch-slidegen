from __future__ import annotations

import pytest

from slidify.emitter.core import Emitter
from slidify.models import (
    BoundingBox,
    Decision,
    DecisionKind,
    DomElement,
    EmitOp,
    RenderedSlide,
    VisualUnit,
)


class _RecordingEmitter(Emitter):
    def __init__(self) -> None:
        super().__init__()
        self.region_sources: list[bytes] = []

    def _emit_region_raster(self, slide, ground_truth_png: bytes, bbox: BoundingBox) -> None:
        self.region_sources.append(ground_truth_png)


class _NoRegionRenderer:
    async def screenshot_region(self, *args, **kwargs):
        return None


def _unit(text: str | None = None) -> VisualUnit:
    bbox = BoundingBox(x=0, y=0, w=1280, h=720)
    return VisualUnit(
        id="u",
        bbox=bbox,
        elements=[
            DomElement(
                id=1,
                parent_id=None,
                depth=0,
                tag="DIV",
                bbox=bbox,
                text=text,
            )
        ],
    )


def _op() -> EmitOp:
    return EmitOp(
        unit_id="u",
        decision=Decision(kind=DecisionKind.Raster),
        z_order=0,
        bbox=BoundingBox(x=0, y=0, w=1280, h=720),
    )


def _rendered() -> RenderedSlide:
    return RenderedSlide(
        html="<div></div>",
        elements=[],
        ground_truth_png=b"ground-truth",
        no_text_png=b"no-text",
        viewport_w=1280,
        viewport_h=720,
    )


@pytest.mark.asyncio
async def test_textless_raster_uses_decoration_only_source():
    emitter = _RecordingEmitter()
    try:
        await emitter._emit_raster(None, _unit(), _op(), _rendered(), _NoRegionRenderer())
    finally:
        emitter.close()

    assert emitter.region_sources == [b"no-text"]


@pytest.mark.asyncio
async def test_textful_raster_keeps_ground_truth_source():
    emitter = _RecordingEmitter()
    try:
        await emitter._emit_raster(
            None,
            _unit("Ship the future"),
            _op(),
            _rendered(),
            _NoRegionRenderer(),
        )
    finally:
        emitter.close()

    assert emitter.region_sources == [b"ground-truth"]
