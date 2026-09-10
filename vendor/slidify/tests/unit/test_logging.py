"""Log routing contracts that other tools depend on."""

from __future__ import annotations

import logging

from slidify._logging import configure


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_fonttools_narration_is_muted_but_its_warnings_survive():
    """Font embedding narrates every glyph it keeps, at INFO.

    Four families is a few thousand lines, which buries whatever slidify
    logged before it. Anything reading a bounded tail of stderr to find out
    why a conversion failed then reads glyph names instead of the error.

    Captured with a plain handler rather than ``caplog``, whose ``at_level``
    would raise the very level under test.
    """
    configure(quiet=False)
    capture = _Capture()
    root = logging.getLogger()
    root.addHandler(capture)
    try:
        subset = logging.getLogger("fontTools.subset")
        subset.info("Retaining 120 glyphs")
        subset.warning("could not subset OTF")
    finally:
        root.removeHandler(capture)

    assert capture.messages == ["could not subset OTF"]


def test_slidify_progress_logging_is_untouched():
    configure(quiet=False)

    assert logging.getLogger().getEffectiveLevel() == logging.INFO
