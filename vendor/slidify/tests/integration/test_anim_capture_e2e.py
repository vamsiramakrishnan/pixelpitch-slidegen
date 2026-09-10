"""End-to-end test: drive Playwright through anim_capture against a tiny
animated HTML fixture and verify the produced GIF is genuinely animated
(more than one frame, frames are not all identical)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from slidify.anim_capture import capture_html_to_gif

# A self-contained CSS @keyframes animation so we don't depend on a CDN
# being reachable from CI. Uses an infinite-iteration animation so any
# wall-clock jitter in the capture loop still hits a unique frame —
# without iteration the animation finishes before some frames land.
# The point isn't to test Framer or GSAP per se; it's to confirm the
# pipeline doesn't freeze the animation.
ANIM_FIXTURE_HTML = """<!DOCTYPE html>
<html><head>
<meta name="slidify-capture-duration" content="800">
<meta name="slidify-capture-fps" content="10">
<style>
html, body { margin:0; padding:0; width:1280px; height:720px;
             background:#000; overflow:hidden; }
.dot { position:absolute; top:300px; left:0; width:120px; height:120px;
       border-radius:50%; background:#f0f;
       animation: slide 0.8s linear infinite; }
@keyframes slide {
  0%   { transform: translateX(0); }
  50%  { transform: translateX(1100px); }
  100% { transform: translateX(0); }
}
</style></head>
<body><div class="dot"></div></body></html>
"""


@pytest.mark.asyncio
async def test_capture_produces_animated_gif(tmp_path: Path):
    html_path = tmp_path / "anim.html"
    html_path.write_text(ANIM_FIXTURE_HTML, encoding="utf-8")
    gif_path = tmp_path / "anim.gif"

    await capture_html_to_gif(html_path, gif_path)

    assert gif_path.exists()
    assert gif_path.stat().st_size > 0

    with Image.open(gif_path) as img:
        assert img.format == "GIF"
        n_frames = getattr(img, "n_frames", 1)
        # An 800ms loop sampled at 10fps over 800ms yields 8 frames; we
        # want at least most of them distinct. Real-time wall-clock
        # jitter means we don't get a perfect 8/8.
        assert n_frames >= 3, f"too few frames: {n_frames}"

        # Sample the first and last frames; they MUST differ — if the
        # freeze script leaked into the capture path, both frames would
        # be identical.
        img.seek(0)
        first = img.convert("RGB").copy()
        img.seek(n_frames - 1)
        last = img.convert("RGB").copy()

    # Compare raw pixel buffers; identical → animation was frozen.
    assert first.tobytes() != last.tobytes(), \
        "first and last frames are identical — animation was frozen"
