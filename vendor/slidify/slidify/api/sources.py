"""Input source normalization for the public conversion API."""

from __future__ import annotations

import base64
import re
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path

from slidify.splitter import split_slides

SlideSource = str | Path | Iterable[str | Path] | AsyncIterable[str | Path]


@dataclass(frozen=True, slots=True)
class SlideInput:
    """A slide's HTML and optional on-disk source path.

    When *source_path* is set the renderer can use ``page.goto(file://…)``
    so that relative ``<script src>``, ``<link href>``, etc. resolve against
    the file's directory.
    """
    html: str
    source_path: Path | None = None


async def _normalize_source(source: SlideSource) -> AsyncIterator[SlideInput]:
    """Yield ``SlideInput`` objects from any supported source form."""
    if isinstance(source, str):
        for chunk in split_slides(source):
            yield SlideInput(html=chunk)
        return

    if isinstance(source, Path):
        if source.is_dir():
            for path in sorted(source.glob("*.html")):
                yield SlideInput(
                    html=_inline_local_images(path.read_text(encoding="utf-8"), path.parent),
                    source_path=path,
                )
            return
        text = source.read_text(encoding="utf-8")
        chunks = split_slides(text)
        for chunk in chunks:
            yield SlideInput(
                html=_inline_local_images(chunk, source.parent),
                source_path=source if len(chunks) == 1 else None,
            )
        return

    if hasattr(source, "__aiter__"):
        async for item in source:  # type: ignore[union-attr]
            yield SlideInput(html=_read_item(item))
        return

    if hasattr(source, "__iter__"):
        for item in source:  # type: ignore[union-attr]
            yield SlideInput(html=_read_item(item))
        return

    raise TypeError(f"unsupported slide source type: {type(source).__name__}")


_IMG_SRC_RE = re.compile(
    r'(<img\b[^>]*\bsrc\s*=\s*)(["\'])([^"\']+)\2',
    re.IGNORECASE,
)


def _inline_local_images(html: str, base_dir: Path) -> str:
    """Inline relative local ``<img>`` references as data URIs."""

    def _rewrite(match: re.Match) -> str:
        prefix, quote, src = match.group(1), match.group(2), match.group(3)
        if src.startswith(("http://", "https://", "data:", "file://", "//")):
            return match.group(0)
        candidate = (base_dir / src).resolve()
        if not candidate.is_file():
            return match.group(0)
        try:
            data = candidate.read_bytes()
        except OSError:
            return match.group(0)
        mime = _guess_mime(candidate.suffix.lower())
        b64 = base64.b64encode(data).decode("ascii")
        return f'{prefix}{quote}data:{mime};base64,{b64}{quote}'

    return _IMG_SRC_RE.sub(_rewrite, html)


def _guess_mime(suffix: str) -> str:
    return {
        ".gif": "image/gif",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".bmp": "image/bmp",
    }.get(suffix, "application/octet-stream")


def _read_item(item: str | Path) -> str:
    if isinstance(item, Path):
        return item.read_text(encoding="utf-8")
    if isinstance(item, str):
        return item
    raise TypeError(f"slide item must be str or Path, got {type(item).__name__}")

