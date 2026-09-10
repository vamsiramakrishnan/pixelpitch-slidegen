from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from slidify.api import ConversionConfig, convert
from slidify.models import ConversionResult, UnmatchedSignature


def _walk_corpus(corpus_dir: Path) -> list[Path]:
    """Return all `*.html` files under `corpus_dir` in sorted order."""
    if corpus_dir.is_file() and corpus_dir.suffix.lower() == ".html":
        return [corpus_dir]
    if not corpus_dir.is_dir():
        return []
    return sorted(
        p for p in corpus_dir.glob("**/*.html")
        if p.is_file() and not _is_generated_index(corpus_dir, p)
    )


def _is_generated_index(root: Path, path: Path) -> bool:
    """Skip generated directory catalogues when harvesting a bench tree."""
    if path.name != "index.html":
        return False
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if len(relative.parts) != 1:
        return False
    return (path.parent / "index.json").exists()


async def _harvest_result_async(html_path: Path) -> ConversionResult:
    """Run slidify on a single deck and return the full conversion telemetry.

    Uses the fast config (`ConversionConfig.fast()`) — no oracle, no LLM, no
    editability check. Output PPTX is written to a temp file and discarded.
    """
    cfg = ConversionConfig.fast()
    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as tf:
        out = Path(tf.name)
    try:
        return await convert(html_path, out, cfg)
    finally:
        try:
            out.unlink()
        except FileNotFoundError:
            pass


async def _harvest_one_async(html_path: Path) -> list[UnmatchedSignature]:
    """Run slidify on a single deck and return its `unmatched_signatures`."""
    result = await _harvest_result_async(html_path)
    return list(result.unmatched_signatures)


def _harvest_result(html_path: Path) -> ConversionResult:
    """Synchronous wrapper around :func:`_harvest_result_async`."""
    return asyncio.run(_harvest_result_async(html_path))


def _harvest_one(html_path: Path) -> list[UnmatchedSignature]:
    """Synchronous wrapper around :func:`_harvest_one_async`."""
    return asyncio.run(_harvest_one_async(html_path))
