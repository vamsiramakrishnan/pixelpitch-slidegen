"""Headless Chromium renderer for individual slides."""

from __future__ import annotations

import asyncio
import mimetypes
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from threading import Thread
from types import TracebackType

import structlog
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from slidify.dom_walker import walk
from slidify.exceptions import RenderError
from slidify.geom import SLIDE_H_PX, SLIDE_W_PX
from slidify.models import RenderedSlide

log = structlog.get_logger(__name__)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _QuietHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves from an arbitrary directory without logging."""

    def log_message(self, format, *args):  # noqa: A002
        pass

    def guess_type(self, path):
        mime, _ = mimetypes.guess_type(path)
        if mime:
            return mime
        ext = Path(path).suffix.lower()
        return {
            ".jsx": "text/javascript",
            ".tsx": "text/javascript",
            ".mjs": "text/javascript",
        }.get(ext, "application/octet-stream")


@asynccontextmanager
async def _local_server(directory: Path):
    """Spin up a temporary HTTP server rooted at *directory*."""
    import http.server

    port = _find_free_port()
    handler = lambda *a, **kw: _QuietHandler(*a, directory=str(directory), **kw)
    server = http.server.HTTPServer(("127.0.0.1", port), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


# Injected before page load: kills animations and pauses video.
ANIM_FREEZE_JS = r"""
(() => {
    try {
        if (window.Chart && window.Chart.defaults && window.Chart.defaults.animation !== undefined) {
            window.Chart.defaults.animation = false;
        }
    } catch (_) {}
    const css = `
        *, *::before, *::after {
            transition-duration: 0s !important;
            transition-delay: 0s !important;
            animation-duration: 0s !important;
            animation-delay: 0s !important;
            animation-iteration-count: 1 !important;
        }`;
    const styleEl = document.createElement('style');
    styleEl.setAttribute('data-slidify-anim-freeze', 'true');
    styleEl.textContent = css;
    (document.head || document.documentElement).appendChild(styleEl);
})();
"""


class Renderer:
    """Owns a single Chromium instance; one context+page per render."""

    def __init__(
        self,
        viewport: tuple[int, int] = (SLIDE_W_PX, SLIDE_H_PX),
        timeout_ms: int = 15_000,
        *,
        differential: bool = False,
    ) -> None:
        self.viewport_w, self.viewport_h = viewport
        self.timeout_ms = timeout_ms
        self.differential = differential
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> Renderer:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        if self._browser is not None:
            return
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        log.info("renderer.start", viewport=(self.viewport_w, self.viewport_h))

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    @asynccontextmanager
    async def _page(self) -> AsyncIterator[tuple[BrowserContext, Page]]:
        if self._browser is None:
            await self.start()
        assert self._browser is not None
        ctx = await self._browser.new_context(
            viewport={"width": self.viewport_w, "height": self.viewport_h},
            device_scale_factor=1,
            # Many CI / container environments terminate TLS at a corporate
            # proxy whose cert chain Chromium doesn't trust. Without this,
            # `<img src="https://…">` references silently fail to load and
            # the ground-truth screenshot diverges from the embedded blip
            # (which httpx happily fetches via the system trust store),
            # tanking SSIM on photo-heavy slides. The renderer is offline-
            # safe — failed loads still yield a valid (text-only) shot.
            ignore_https_errors=True,
        )
        # Inject animation freeze script before any page load.
        await ctx.add_init_script(ANIM_FREEZE_JS)
        page = await ctx.new_page()
        try:
            yield ctx, page
        finally:
            await ctx.close()

    async def render(self, html: str) -> RenderedSlide:
        """Render one slide HTML; return DOM dump + ground-truth PNG."""
        async with self._page() as (_ctx, page):
            try:
                await page.set_content(html, wait_until="load", timeout=self.timeout_ms)
            except Exception as e:
                log.warning("renderer.set_content_failed", error=str(e))
                return RenderedSlide(
                    html=html,
                    elements=[],
                    ground_truth_png=b"",
                    viewport_w=self.viewport_w,
                    viewport_h=self.viewport_h,
                    degraded=True,
                    reason=f"set_content: {e}",
                )

            degraded = False
            reason = ""

            # Wait conditions: networkidle → fonts.ready → 2 rAFs.
            try:
                await page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
            except Exception as e:
                degraded = True
                reason = f"networkidle: {e}"
                log.warning("renderer.networkidle_timeout", error=str(e))

            try:
                await page.evaluate("document.fonts && document.fonts.ready")
            except Exception as e:
                log.warning("renderer.fonts_ready_failed", error=str(e))

            try:
                await page.evaluate(
                    "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
                )
            except Exception as e:
                log.warning("renderer.raf_failed", error=str(e))

            # Ground truth screenshot.
            try:
                png = await page.screenshot(
                    clip={
                        "x": 0,
                        "y": 0,
                        "width": self.viewport_w,
                        "height": self.viewport_h,
                    },
                    full_page=False,
                    type="png",
                )
            except Exception as e:
                raise RenderError(f"screenshot failed: {e}") from e

            # Second pass for differential mode: blank every text node, take a
            # decoration-only screenshot, restore. Layout stays identical so
            # the no-text image is pixel-aligned with the ground truth.
            no_text_png = b""
            if self.differential:
                try:
                    no_text_png = await self._capture_decoration_only(page)
                except Exception as e:
                    log.warning("renderer.differential_failed", error=str(e))

            # DOM walk.
            try:
                elements = await walk(page)
            except Exception as e:
                raise RenderError(f"dom walk failed: {e}") from e

            log.info(
                "renderer.render_ok",
                element_count=len(elements),
                png_bytes=len(png),
                differential=bool(no_text_png),
                degraded=degraded,
            )

            return RenderedSlide(
                html=html,
                elements=elements,
                ground_truth_png=png,
                no_text_png=no_text_png,
                viewport_w=self.viewport_w,
                viewport_h=self.viewport_h,
                degraded=degraded,
                reason=reason,
            )

    async def render_file(self, path: Path) -> list[RenderedSlide]:
        """Render an HTML file via a local HTTP server so relative paths resolve.

        Returns a list — one ``RenderedSlide`` per slide ``<section>`` found in
        the live DOM. If the page has no multi-slide structure, returns a single-
        element list with the whole page rendered as one slide.
        """
        async with _local_server(path.parent) as base_url:
            url = f"{base_url}/{path.name}"
            async with self._page() as (_ctx, page):
                try:
                    await page.goto(url, wait_until="load", timeout=self.timeout_ms)
                except Exception as e:
                    log.warning("renderer.goto_failed", error=str(e))
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                    except Exception as e2:
                        html = path.read_text(encoding="utf-8")
                        return [RenderedSlide(
                            html=html,
                            elements=[],
                            ground_truth_png=b"",
                            viewport_w=self.viewport_w,
                            viewport_h=self.viewport_h,
                            degraded=True,
                            reason=f"goto: {e2}",
                        )]

                degraded = False
                reason = ""

                try:
                    await page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
                except Exception as e:
                    degraded = True
                    reason = f"networkidle: {e}"
                    log.warning("renderer.networkidle_timeout", error=str(e))

                try:
                    await page.evaluate("document.fonts && document.fonts.ready")
                except Exception as e:
                    log.warning("renderer.fonts_ready_failed", error=str(e))

                try:
                    await page.evaluate(
                        "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
                    )
                except Exception as e:
                    log.warning("renderer.raf_failed", error=str(e))

                html = path.read_text(encoding="utf-8")
                n_sections = await page.evaluate(
                    "document.querySelectorAll('section').length"
                )

                if n_sections <= 1:
                    return [await self._snapshot_current_page(
                        page, html, degraded, reason, source_path=path,
                    )]

                # Inject transition-killer into the shadow DOM once so
                # goTo() activations are instant (the light-DOM anim-freeze
                # sheet can't reach ::slotted transitions).
                await page.evaluate(
                    """() => {
                        const stage = document.querySelector('deck-stage');
                        if (!stage || !stage.shadowRoot) return;
                        const s = document.createElement('style');
                        s.textContent = `
                            ::slotted(*) {
                                transition: none !important;
                            }
                        `;
                        stage.shadowRoot.appendChild(s);
                    }"""
                )

                results: list[RenderedSlide] = []
                for i in range(n_sections):
                    await page.evaluate(
                        """(idx) => {
                            const stage = document.querySelector('deck-stage');
                            if (stage && typeof stage.goTo === 'function') {
                                stage._index = -1;
                                stage.goTo(idx);
                            } else {
                                // Fallback: toggle data-deck-active directly.
                                const sections = document.querySelectorAll('section');
                                sections.forEach((s, j) => {
                                    if (j === idx) {
                                        s.setAttribute('data-deck-active', '');
                                    } else {
                                        s.removeAttribute('data-deck-active');
                                    }
                                });
                            }
                        }""",
                        i,
                    )
                    await page.evaluate(
                        "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
                    )
                    # Strip inherited presentation-level CSS (filter,
                    # backdrop-filter, transform) from the active section so
                    # the classifier doesn't raster the whole slide.
                    await page.evaluate(
                        """() => {
                            const s = document.querySelector('[data-deck-active]');
                            if (!s) return;
                            s.style.setProperty('filter', 'none', 'important');
                            s.style.setProperty('backdrop-filter', 'none', 'important');
                        }"""
                    )
                    rendered = await self._snapshot_current_page(
                        page, html, degraded, reason,
                        source_path=path,
                        root_selector="[data-deck-active]",
                    )
                    results.append(rendered)
                    log.info(
                        "renderer.render_file_slide",
                        slide=i,
                        element_count=len(rendered.elements),
                    )

                log.info(
                    "renderer.render_file_ok",
                    n_slides=len(results),
                    total_elements=sum(len(r.elements) for r in results),
                    degraded=degraded,
                )
                return results

    async def _snapshot_current_page(
        self,
        page: Page,
        html: str,
        degraded: bool,
        reason: str,
        *,
        source_path: Path | None = None,
        root_selector: str | None = None,
    ) -> RenderedSlide:
        """Screenshot + DOM-walk the current page state."""
        try:
            png = await page.screenshot(
                clip={
                    "x": 0,
                    "y": 0,
                    "width": self.viewport_w,
                    "height": self.viewport_h,
                },
                full_page=False,
                type="png",
            )
        except Exception as e:
            raise RenderError(f"screenshot failed: {e}") from e

        no_text_png = b""
        if self.differential:
            try:
                no_text_png = await self._capture_decoration_only(page)
            except Exception as e:
                log.warning("renderer.differential_failed", error=str(e))

        try:
            elements = await walk(page, root_selector=root_selector)
        except Exception as e:
            raise RenderError(f"dom walk failed: {e}") from e

        return RenderedSlide(
            html=html,
            elements=elements,
            ground_truth_png=png,
            no_text_png=no_text_png,
            viewport_w=self.viewport_w,
            viewport_h=self.viewport_h,
            degraded=degraded,
            reason=reason,
            source_path=source_path,
        )

    async def _capture_decoration_only(self, page: Page) -> bytes:
        """Hide text paint, screenshot, restore.

        The result is the decoration layer (gradients, shapes, borders,
        shadows) without text pixels. Do not blank text nodes: that changes
        layout in normal document flow and moves cards/charts before the
        raster backplate is captured.
        """
        await page.evaluate(
            r"""() => {
                const stash = [];
                const style = document.createElement('style');
                style.setAttribute('data-slidify-no-text', 'true');
                style.textContent = `
                    body *, body *::before, body *::after {
                        color: transparent !important;
                        -webkit-text-fill-color: transparent !important;
                        text-shadow: none !important;
                        caret-color: transparent !important;
                    }
                    svg text, svg tspan {
                        fill: transparent !important;
                        stroke: transparent !important;
                    }
                `;
                document.head.appendChild(style);
                stash.push(['style-node', style, null]);
                for (const el of Array.from(document.body.querySelectorAll('*'))) {
                    const cs = getComputedStyle(el);
                    const clip = [
                        cs.backgroundClip || '',
                        cs.webkitBackgroundClip || '',
                        cs.getPropertyValue('background-clip') || '',
                        cs.getPropertyValue('-webkit-background-clip') || '',
                    ].join(' ');
                    if (!clip.includes('text')) continue;
                    stash.push(['style', el, el.getAttribute('style')]);
                    el.style.setProperty('background-image', 'none', 'important');
                    el.style.setProperty('background-color', 'transparent', 'important');
                }
                window.__slidify_text_stash = stash;
            }"""
        )
        try:
            png = await page.screenshot(
                clip={
                    "x": 0,
                    "y": 0,
                    "width": self.viewport_w,
                    "height": self.viewport_h,
                },
                full_page=False,
                type="png",
            )
        finally:
            await page.evaluate(
                r"""() => {
                    const stash = window.__slidify_text_stash || [];
                    for (const item of stash) {
                        if (item[0] === 'style-node') {
                            item[1].remove();
                        } else if (item[0] === 'style') {
                            if (item[2] === null) item[1].removeAttribute('style');
                            else item[1].setAttribute('style', item[2]);
                        }
                    }
                    window.__slidify_text_stash = null;
                }"""
            )
        return png

    async def screenshot_region(
        self, html: str, selector: str, bbox: tuple[float, float, float, float]
    ) -> bytes:
        """Re-render the slide and screenshot a region. Used by emitter for raster fallback.

        bbox is (x, y, w, h) in viewport pixels.
        """
        async with self._page() as (_ctx, page):
            await page.set_content(html, wait_until="load", timeout=self.timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
            except Exception:
                pass
            try:
                await page.evaluate("document.fonts && document.fonts.ready")
            except Exception:
                pass
            try:
                await page.evaluate(
                    "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
                )
            except Exception:
                pass
            x, y, w, h = bbox
            x = max(0.0, x)
            y = max(0.0, y)
            w = max(1.0, min(w, self.viewport_w - x))
            h = max(1.0, min(h, self.viewport_h - y))
            png = await page.screenshot(
                clip={"x": x, "y": y, "width": w, "height": h},
                full_page=False,
                type="png",
            )
            return png

    async def screenshot_region_file(
        self, path: Path, selector: str, bbox: tuple[float, float, float, float]
    ) -> bytes:
        """Like screenshot_region but serves via a local HTTP server."""
        async with _local_server(path.parent) as base_url:
            url = f"{base_url}/{path.name}"
            async with self._page() as (_ctx, page):
                await page.goto(url, wait_until="load", timeout=self.timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
                except Exception:
                    pass
                try:
                    await page.evaluate("document.fonts && document.fonts.ready")
                except Exception:
                    pass
                try:
                    await page.evaluate(
                        "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
                    )
                except Exception:
                    pass
                x, y, w, h = bbox
                x = max(0.0, x)
                y = max(0.0, y)
                w = max(1.0, min(w, self.viewport_w - x))
                h = max(1.0, min(h, self.viewport_h - y))
                png = await page.screenshot(
                    clip={"x": x, "y": y, "width": w, "height": h},
                    full_page=False,
                    type="png",
                )
                return png
