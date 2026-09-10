"""Unit tests for slidify.anim_capture (parser + GIF encoder).

The Playwright-driven capture itself lives in
tests/integration/test_anim_capture_e2e.py; here we just exercise the
pure-Python pieces.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from slidify.anim_capture import (
    DEFAULT_DURATION_MS,
    DEFAULT_FPS,
    CaptureSpec,
    _write_gif,
    parse_capture_spec,
)


def _png_bytes(rgb: tuple[int, int, int], size: tuple[int, int] = (32, 18)) -> bytes:
    img = Image.new("RGB", size, rgb)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_parse_capture_spec_defaults_when_no_meta():
    spec = parse_capture_spec("<html><body>hi</body></html>")
    assert spec.duration_ms == DEFAULT_DURATION_MS
    assert spec.fps == DEFAULT_FPS


def test_parse_capture_spec_reads_meta_tags():
    html = """
    <html><head>
      <meta name="slidify-capture-duration" content="2500">
      <meta name="slidify-capture-fps" content="24">
    </head></html>
    """
    spec = parse_capture_spec(html)
    assert spec.duration_ms == 2500
    assert spec.fps == 24


def test_parse_capture_spec_ignores_garbage_values():
    html = """
    <meta name="slidify-capture-duration" content="not-a-number">
    <meta name="slidify-capture-fps" content="-3">
    """
    spec = parse_capture_spec(html)
    # negative fps is rejected, falls back to default.
    assert spec.fps == DEFAULT_FPS
    assert spec.duration_ms == DEFAULT_DURATION_MS


def test_capture_spec_frame_math():
    spec = CaptureSpec(duration_ms=2000, fps=15)
    assert spec.frame_count == 30
    assert spec.frame_interval_ms == 67  # 1000/15 rounded


def test_capture_spec_frame_count_minimum_two():
    # Even pathologically tiny configs must produce at least 2 frames so
    # the GIF is animated rather than a single still.
    spec = CaptureSpec(duration_ms=10, fps=1)
    assert spec.frame_count >= 2


def test_write_gif_produces_animated_file(tmp_path: Path):
    frames = [
        _png_bytes((255, 0, 0)),
        _png_bytes((0, 255, 0)),
        _png_bytes((0, 0, 255)),
    ]
    out = tmp_path / "demo.gif"
    _write_gif(frames, out, fps=15)
    assert out.exists()
    assert out.stat().st_size > 0

    with Image.open(out) as img:
        assert img.format == "GIF"
        # n_frames > 1 confirms it's animated, not a static GIF.
        assert getattr(img, "n_frames", 1) == len(frames)
