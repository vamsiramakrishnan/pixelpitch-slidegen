"""Tests for slidify.api._inline_local_images — the data-URI rewriter
that lets relative ``<img src="local.gif">`` references resolve through
Playwright's ``set_content`` without a real base URL."""

from __future__ import annotations

import base64
from pathlib import Path

from slidify.api import _inline_local_images


def _make_gif(path: Path) -> bytes:
    # Minimal valid 1×1 GIF89a. Exact bytes don't matter for the test —
    # we just need a real file to inline.
    data = bytes.fromhex(
        "47494638396101000100800000ffffff00000021f90401000000002c"
        "0000000001000100000202440100003b"
    )
    path.write_bytes(data)
    return data


def test_inlines_relative_image(tmp_path: Path):
    gif = tmp_path / "anim.gif"
    raw = _make_gif(gif)
    html = '<html><body><img src="anim.gif"></body></html>'
    out = _inline_local_images(html, tmp_path)
    expected = "data:image/gif;base64," + base64.b64encode(raw).decode("ascii")
    assert expected in out
    assert 'src="anim.gif"' not in out


def test_leaves_remote_urls_alone(tmp_path: Path):
    html = (
        '<img src="https://example.com/foo.png">'
        '<img src="//cdn.example.com/bar.gif">'
        '<img src="data:image/gif;base64,R0lGOD...">'
    )
    out = _inline_local_images(html, tmp_path)
    assert out == html


def test_leaves_missing_files_alone(tmp_path: Path):
    html = '<img src="does-not-exist.gif">'
    out = _inline_local_images(html, tmp_path)
    assert out == html


def test_handles_single_quotes(tmp_path: Path):
    gif = tmp_path / "x.gif"
    _make_gif(gif)
    html = "<img src='x.gif'>"
    out = _inline_local_images(html, tmp_path)
    assert "data:image/gif;base64," in out


def test_preserves_other_img_attributes(tmp_path: Path):
    gif = tmp_path / "x.gif"
    _make_gif(gif)
    html = '<img class="hero" alt="An anim" src="x.gif" width="100">'
    out = _inline_local_images(html, tmp_path)
    assert 'class="hero"' in out
    assert 'alt="An anim"' in out
    assert 'width="100"' in out
    assert "data:image/gif;base64," in out


def test_no_imgs_no_op(tmp_path: Path):
    html = "<html><body><p>no images</p></body></html>"
    assert _inline_local_images(html, tmp_path) == html
