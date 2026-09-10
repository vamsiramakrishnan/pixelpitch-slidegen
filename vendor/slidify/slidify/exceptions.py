"""Custom exception hierarchy for slidify."""

from __future__ import annotations


class SlidifyError(Exception):
    """Base class for all slidify errors."""

    def __init__(
        self,
        message: str,
        *,
        slide_index: int | None = None,
        unit_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.slide_index = slide_index
        self.unit_id = unit_id


class RenderError(SlidifyError):
    """Browser rendering failure."""


class ClassificationError(SlidifyError):
    """Classification pipeline failure."""


class EmitError(SlidifyError):
    """PPTX emission failure."""


class OracleError(SlidifyError):
    """Fidelity oracle failure (rendering, SSIM, OCR)."""
