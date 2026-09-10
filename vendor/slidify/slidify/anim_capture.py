"""Capture an animated HTML slide as an animated GIF.

The static slidify pipeline freezes CSS animations and captures one
steady-state frame; that throws away the whole point of a Framer Motion
or GSAP slide. This module runs the *opposite* renderer — Playwright
without the freeze script — samples N evenly-spaced frames over the
declared duration, and writes an animated GIF that PowerPoint replays
natively when the deck is opened in slideshow mode.

HTML opt-in via meta tags (sensible defaults if missing):

    <meta name="slidify-capture-duration" content="3000">  <!-- ms -->
    <meta name="slidify-capture-fps"      content="15">

The resulting GIF is referenced from a normal slide HTML as
``<img src="anim-NN.gif">`` and survives the static-emit pipeline
intact (NativePicture → add_picture preserves GIF bytes verbatim).
"""

from __future__ import annotations

import asyncio
import io
import re
from dataclasses import dataclass
from pathlib import Path

import structlog
from playwright.async_api import async_playwright

log = structlog.get_logger(__name__)


DEFAULT_DURATION_MS = 3000
DEFAULT_FPS = 15
DEFAULT_VIEWPORT = (1280, 720)

# Pillow palette quantization: 128 colors gives passable gradients in our
# slide aesthetic without bloating the file. Bump to 256 for photo-rich
# slides; drop to 64 if file size is the binding constraint.
_GIF_PALETTE_COLORS = 128


@dataclass
class CaptureSpec:
    duration_ms: int = DEFAULT_DURATION_MS
    fps: int = DEFAULT_FPS
    viewport: tuple[int, int] = DEFAULT_VIEWPORT

    @property
    def frame_count(self) -> int:
        return max(2, int(round(self.duration_ms / 1000 * self.fps)))

    @property
    def frame_interval_ms(self) -> int:
        return int(round(1000 / self.fps))


_META_RE = re.compile(
    r'<meta[^>]+name=["\']slidify-capture-(duration|fps)["\']'
    r'[^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def parse_capture_spec(html: str, *, viewport: tuple[int, int] = DEFAULT_VIEWPORT) -> CaptureSpec:
    """Pull duration / fps from <meta> tags; fall back to module defaults."""
    spec = CaptureSpec(viewport=viewport)
    for key, value in _META_RE.findall(html):
        try:
            n = int(value)
        except ValueError:
            continue
        if key.lower() == "duration" and n > 0:
            spec.duration_ms = n
        elif key.lower() == "fps" and n > 0:
            spec.fps = n
    return spec


async def capture_html_to_gif(
    html_path: Path,
    gif_path: Path,
    *,
    duration_ms: int | None = None,
    fps: int | None = None,
    viewport: tuple[int, int] = DEFAULT_VIEWPORT,
    clip: tuple[float, float, float, float] | None = None,
    settle_ms: int = 250,
) -> Path:
    """Render ``html_path`` in headless Chromium and write an animated GIF.

    The browser does NOT receive the slidify animation-freeze init script,
    so Framer Motion / GSAP / CSS @keyframes all play normally.

    ``duration_ms`` / ``fps`` override the values declared in the HTML's
    <meta> tags. ``settle_ms`` is the post-load delay before the first
    frame — gives CDN-hosted libs a beat to register their scripts.
    """
    html_path = Path(html_path).resolve()
    gif_path = Path(gif_path)
    gif_path.parent.mkdir(parents=True, exist_ok=True)

    raw = html_path.read_text(encoding="utf-8")
    spec = parse_capture_spec(raw, viewport=viewport)
    if duration_ms is not None:
        spec.duration_ms = duration_ms
    if fps is not None:
        spec.fps = fps

    log.info(
        "anim_capture.start",
        html=str(html_path), gif=str(gif_path),
        duration_ms=spec.duration_ms, fps=spec.fps,
        frames=spec.frame_count,
    )

    pngs = await _capture_frames(html_path, spec, settle_ms=settle_ms, clip=clip)
    _write_gif(pngs, gif_path, fps=spec.fps)
    log.info("anim_capture.done", gif=str(gif_path),
             frames=len(pngs), bytes=gif_path.stat().st_size)
    return gif_path


async def _capture_frames(
    html_path: Path,
    spec: CaptureSpec,
    *,
    settle_ms: int,
    clip: tuple[float, float, float, float] | None = None,
) -> list[bytes]:
    """Drive Playwright through the slide and sample frames at the
    declared fps.

    Sequence:
      1. Open the page. Wait for ``load`` + ``networkidle`` so any
         CDN-hosted libs (Framer Motion ESM, GSAP) finish downloading.
      2. **Reload** the page. Animations restart from frame 0; libs
         are now in disk cache so the reload is much faster than the
         first navigation.
      3. Wait ``settle_ms`` for the libs' DOMContentLoaded handlers to
         register (Framer's animate() / GSAP timelines).
      4. Loop: take a CDP screenshot, ``wait_for_timeout(interval_ms)``.

    We use raw CDP ``Page.captureScreenshot`` instead of Playwright's
    ``page.screenshot()``: the latter blocks on a "page is stable" wait
    that re-introduces ~30ms of jitter per call. CDP just reads the
    current compositor frame.

    Wall-clock based — the screenshot itself takes ~30-80ms which means
    a fast-finishing animation may have its tail compressed. For most
    1-3s intro animations this is fine: Pillow's GIF encoder collapses
    redundant trailing frames so the resulting file animates at the
    declared cadence and pads the static tail by extending the last
    frame's duration.
    """
    pngs: list[bytes] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": spec.viewport[0], "height": spec.viewport[1]},
            device_scale_factor=1,
            ignore_https_errors=True,
        )
        # No animation-freeze init script — animations must run.
        page = await ctx.new_page()
        client = await ctx.new_cdp_session(page)
        url = html_path.as_uri()

        # First load: warms the disk cache for any CDN-hosted libs.
        try:
            await page.goto(url, wait_until="load", timeout=15_000)
        except Exception as e:
            log.warning("anim_capture.goto_failed", error=str(e))
            await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

        # Reload so any one-shot intro animation starts at frame 0.
        await page.reload(wait_until="domcontentloaded", timeout=10_000)
        await page.wait_for_timeout(settle_ms)

        interval = spec.frame_interval_ms
        n = spec.frame_count
        for i in range(n):
            png = await _cdp_screenshot(client, clip=clip)
            pngs.append(png)
            if i < n - 1:
                await page.wait_for_timeout(interval)

        await ctx.close()
        await browser.close()
    return pngs


async def _cdp_screenshot(
    client, *, clip: tuple[float, float, float, float] | None = None
) -> bytes:
    """Take a screenshot via raw CDP — much faster than Playwright's
    ``page.screenshot()`` because it skips the wait-for-stable logic."""
    import base64

    params: dict = {"format": "png"}
    if clip is not None:
        x, y, w, h = clip
        params["clip"] = {
            "x": max(0.0, float(x)),
            "y": max(0.0, float(y)),
            "width": max(1.0, float(w)),
            "height": max(1.0, float(h)),
            "scale": 1,
        }
    res = await client.send("Page.captureScreenshot", params)
    return base64.b64decode(res["data"])


def _write_gif(pngs: list[bytes], out_path: Path, *, fps: int) -> None:
    """Encode the captured PNGs as an animated GIF via Pillow."""
    from PIL import Image

    if not pngs:
        raise ValueError("no frames captured")

    frames = []
    for raw in pngs:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        # Quantize each frame independently against an adaptive palette;
        # GIF can only carry 256 colors per frame, and a global palette
        # built once from frame 0 makes mid-animation colors flicker.
        quant = img.quantize(colors=_GIF_PALETTE_COLORS, method=Image.Quantize.MEDIANCUT)
        frames.append(quant)

    # Use ms-per-frame from fps (Pillow rounds to 10ms internally).
    duration_ms = max(20, int(round(1000 / fps)))
    frames[0].save(
        out_path,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
        disposal=2,  # restore-to-bg between frames; avoids palette cross-talk
    )


def capture_html_to_gif_sync(
    html_path: Path, gif_path: Path, **kwargs
) -> Path:
    """Sync wrapper around capture_html_to_gif for CLI / scripts."""
    return asyncio.run(capture_html_to_gif(html_path, gif_path, **kwargs))


__all__ = [
    "CaptureSpec",
    "DEFAULT_DURATION_MS",
    "DEFAULT_FPS",
    "DEFAULT_VIEWPORT",
    "capture_html_to_gif",
    "capture_html_to_gif_sync",
    "parse_capture_spec",
]
