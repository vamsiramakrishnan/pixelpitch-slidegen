"""Public conversion API."""

from slidify.api.config import ConversionConfig
from slidify.api.oracle import (
    force_full_raster as _force_full_raster,
)
from slidify.api.oracle import (
    force_raster_overlapping as _force_raster_overlapping,
)
from slidify.api.pipeline import convert, convert_sync
from slidify.api.sources import SlideSource, _inline_local_images, _normalize_source
from slidify.api.state import SlidePlan as _SlidePlan
from slidify.api.state import SlideSummary as _SlideSummary
from slidify.models import ConversionResult

__all__ = [
    "ConversionConfig",
    "ConversionResult",
    "SlideSource",
    "_SlidePlan",
    "_SlideSummary",
    "_force_full_raster",
    "_force_raster_overlapping",
    "_inline_local_images",
    "_normalize_source",
    "convert",
    "convert_sync",
]
