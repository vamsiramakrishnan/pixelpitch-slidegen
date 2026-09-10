"""Reporting, progress, and compatibility matrix helpers."""

from slidify.reporting.compatibility import (
    MATRIX,
    MATRIX_VERSION,
    CompatRow,
    Support,
    code_path_exists,
    matrix,
    matrix_summary,
    to_markdown,
)
from slidify.reporting.progress import (
    ProgressCallback,
    ProgressEvent,
    ProgressReporter,
    emit_progress,
    progress_callback,
)

__all__ = [
    "MATRIX",
    "MATRIX_VERSION",
    "CompatRow",
    "ProgressCallback",
    "ProgressEvent",
    "ProgressReporter",
    "Support",
    "code_path_exists",
    "emit_progress",
    "matrix",
    "matrix_summary",
    "progress_callback",
    "to_markdown",
]
