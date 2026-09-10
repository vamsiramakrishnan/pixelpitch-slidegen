"""Tests for `slidify check` — the pre-flight HTML checker.

Exercises the static-only path (no Chromium). Deep-mode is covered
separately by integration tests because it spins up a browser.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from slidify.checker import check_html
from slidify.cli import cli


# ---- Self-containment ------------------------------------------------------


SELF_CONTAINED_HTML = """<!doctype html>
<html><head>
<style>
  .x { background: #16162a; color: #f5f5f7; }
</style>
</head><body>
  <div class="x" data-atom="surf.card-flat">Hello</div>
</body></html>
"""


def test_self_contained_html_clean():
    rep = check_html(SELF_CONTAINED_HTML)
    assert rep.self_contained is True
    assert rep.external_assets == []
    assert rep.risky_css == []
    assert rep.atom_hints == ["surf.card-flat"]


def test_external_script_breaks_self_containment():
    html = (
        "<!doctype html><html><head>"
        "<script src='https://cdn.tailwindcss.com'></script>"
        "</head><body><div>x</div></body></html>"
    )
    rep = check_html(html)
    assert rep.self_contained is False
    assert any(a.kind == "script-src" and "tailwindcss" in a.url for a in rep.external_assets)


def test_external_stylesheet_breaks_self_containment():
    html = (
        "<!doctype html><html><head>"
        "<link rel='stylesheet' href='https://fonts.googleapis.com/css?family=Inter'>"
        "</head><body><div>x</div></body></html>"
    )
    rep = check_html(html)
    assert rep.self_contained is False
    assert any(a.kind == "link-stylesheet" and "googleapis" in a.url for a in rep.external_assets)


def test_external_image_url_breaks_self_containment():
    html = (
        "<!doctype html><html><body>"
        "<img src='https://images.example.com/hero.jpg'/>"
        "</body></html>"
    )
    rep = check_html(html)
    assert rep.self_contained is False
    assert any(a.kind == "img-src" for a in rep.external_assets)


def test_data_uri_image_is_self_contained():
    html = (
        "<!doctype html><html><body>"
        "<img src='data:image/png;base64,iVBORw0KGgo='/>"
        "</body></html>"
    )
    rep = check_html(html)
    assert rep.self_contained is True
    assert rep.external_assets == []


def test_relative_path_is_self_contained():
    html = "<!doctype html><html><body><img src='./hero.png'/></body></html>"
    rep = check_html(html)
    assert rep.self_contained is True


def test_inline_fetch_call_breaks_self_containment():
    html = (
        "<!doctype html><html><body>"
        "<script>fetch('https://api.example.com/data')</script>"
        "</body></html>"
    )
    rep = check_html(html)
    assert rep.self_contained is False
    assert any(a.kind == "fetch-call" for a in rep.external_assets)


# ---- Risky CSS -------------------------------------------------------------


def test_backdrop_filter_flagged():
    html = (
        "<!doctype html><html><head><style>"
        ".glass { backdrop-filter: blur(20px); }"
        "</style></head><body>"
        "<div class='glass' data-atom='surf.glass'>x</div>"
        "</body></html>"
    )
    rep = check_html(html)
    assert any(r.property == "backdrop-filter" for r in rep.risky_css)


def test_inline_style_risky_css_flagged():
    html = (
        "<!doctype html><html><body>"
        "<div class='glass' style='backdrop-filter: blur(20px); padding: 8px;'>x</div>"
        "</body></html>"
    )
    rep = check_html(html)
    assert any(r.property == "backdrop-filter" for r in rep.risky_css)


def test_mix_blend_mode_flagged():
    html = (
        "<!doctype html><html><head><style>"
        ".m { mix-blend-mode: multiply; }"
        "</style></head><body><div class='m'>x</div></body></html>"
    )
    rep = check_html(html)
    assert any(r.property == "mix-blend-mode" for r in rep.risky_css)


def test_clip_path_flagged():
    html = (
        "<!doctype html><html><body>"
        "<div style='clip-path: polygon(50% 0, 100% 50%, 50% 100%, 0 50%)'>x</div>"
        "</body></html>"
    )
    rep = check_html(html)
    assert any(r.property == "clip-path" for r in rep.risky_css)


def test_safe_transform_not_flagged():
    """Allowed transforms (translate/scale/rotate/matrix) should NOT fire."""
    html = (
        "<!doctype html><html><body>"
        "<div style='transform: translateX(10px) rotate(5deg)'>x</div>"
        "</body></html>"
    )
    rep = check_html(html)
    assert not any(r.property == "transform" for r in rep.risky_css)


def test_skew_transform_flagged():
    html = (
        "<!doctype html><html><body>"
        "<div style='transform: skew(10deg)'>x</div>"
        "</body></html>"
    )
    rep = check_html(html)
    assert any(r.property == "transform" for r in rep.risky_css)


# ---- Atom hints ------------------------------------------------------------


def test_atom_hints_collected_dedup():
    html = (
        "<!doctype html><html><body>"
        "<div data-atom=\"comp.hero-investor\"></div>"
        "<div data-atom='surf.card-flat'></div>"
        "<div data-atom=bg.aurora-band></div>"
        "<div data-atom='surf.card-flat'></div>"
        "</body></html>"
    )
    rep = check_html(html)
    assert rep.atom_hints == ["comp.hero-investor", "surf.card-flat", "bg.aurora-band"]


# ---- Warnings --------------------------------------------------------------


def test_iframe_warning():
    html = "<!doctype html><html><body><iframe src='./inner.html'></iframe></body></html>"
    rep = check_html(html)
    assert any("iframe" in w.lower() for w in rep.warnings)


def test_missing_doctype_warning():
    html = "<html><body><div>x</div></body></html>"
    rep = check_html(html)
    assert any("doctype" in w.lower() for w in rep.warnings)


# ---- CLI integration -------------------------------------------------------


def test_cli_check_clean_html_exits_zero(tmp_path: Path):
    p = tmp_path / "slide.html"
    p.write_text(SELF_CONTAINED_HTML, encoding="utf-8")
    runner = CliRunner()
    res = runner.invoke(cli, ["check", str(p)])
    assert res.exit_code == 0, res.output
    assert "self-contained" in res.output.lower()


def test_cli_check_external_assets_exits_nonzero(tmp_path: Path):
    p = tmp_path / "slide.html"
    p.write_text(
        "<!doctype html><html><head>"
        "<script src='https://cdn.tailwindcss.com'></script>"
        "</head><body>x</body></html>",
        encoding="utf-8",
    )
    runner = CliRunner()
    res = runner.invoke(cli, ["check", str(p)])
    assert res.exit_code == 2, res.output
    assert "NOT self-contained" in res.output


def test_cli_check_json_mode_emits_parseable_payload(tmp_path: Path):
    p = tmp_path / "slide.html"
    p.write_text(SELF_CONTAINED_HTML, encoding="utf-8")
    runner = CliRunner()
    res = runner.invoke(cli, ["check", str(p), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["self_contained"] is True
    assert payload["atom_hints"] == ["surf.card-flat"]
