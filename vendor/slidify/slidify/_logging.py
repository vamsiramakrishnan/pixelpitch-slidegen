"""Logging routing for slidify.

stdout is reserved for machine-readable output (`--json`, JSON reports, the
output path on success). All progress / debug / warning logs go to stderr.

The CLI is meant to be driven by LLM agents that tail stderr line-by-line.
Two output formats are supported on stderr:

  * ``console`` (default) — readable by humans, colored when on a TTY.
  * ``ndjson``            — one structured JSON event per line, ideal for
                            agent loops that want to parse events as they
                            stream in. Switch via ``SLIDIFY_LOG_FORMAT=ndjson``
                            or pass ``log_format="ndjson"`` to ``configure``.

Without this, structlog's default ``PrintLoggerFactory`` writes via plain
``print()`` to stdout, which corrupts ``--json`` consumers (the resulting file
is logs + JSON concatenated, breaking ``slidify field`` and any ``jq`` reader).
"""

from __future__ import annotations

import logging
import os
import sys

import structlog

_configured = False


def _log_format_from_env() -> str:
    raw = os.environ.get("SLIDIFY_LOG_FORMAT", "").strip().lower()
    return raw if raw in ("ndjson", "console") else "console"


def _log_level_from_env(default: int) -> int:
    raw = os.environ.get("SLIDIFY_LOG_LEVEL", "").strip().upper()
    if not raw:
        return default
    return int(getattr(logging, raw, default))


def configure(*, quiet: bool = False, log_format: str | None = None) -> None:
    """Route all logs to stderr. Idempotent.

    Args:
        quiet: when True, raise the floor to WARNING (used for ``--json`` /
               ``--quiet`` so progress noise doesn't clutter the terminal).
        log_format: ``"console"`` or ``"ndjson"``. Defaults to the env var
                    ``SLIDIFY_LOG_FORMAT`` (then to ``"console"``).
    """
    global _configured

    fmt = (log_format or _log_format_from_env()).lower()
    level = _log_level_from_env(logging.WARNING if quiet else logging.INFO)

    # stdlib logging (used by fontTools subset, etc.) → stderr.
    root = logging.getLogger()
    root.setLevel(level)

    # fontTools narrates every glyph it keeps and every table it prunes, at
    # INFO. Embedding four families buries the surrounding log under a few
    # thousand glyph names, and anything reading a bounded tail of stderr to
    # find out what went wrong reads glyph names instead. Its warnings still
    # come through, which is the part that ever mattered.
    logging.getLogger("fontTools").setLevel(logging.WARNING)
    if not root.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setLevel(level)
        root.addHandler(h)
    else:
        for h in root.handlers:
            stream = getattr(h, "stream", None)
            if stream is sys.stdout:
                h.stream = sys.stderr
            h.setLevel(level)

    # Renderer choice. ndjson = one JSON event per line; tail-friendly for
    # LLM agents. console = human-readable, colored on TTYs.
    if fmt == "ndjson":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.WriteLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    _configured = True


def ensure_configured() -> None:
    """Apply default (info-level) routing once if nothing else has."""
    if not _configured:
        configure(quiet=False)
