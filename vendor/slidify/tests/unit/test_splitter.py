"""Tests for slidify.splitter."""

from __future__ import annotations

from slidify.splitter import split_slides


def test_empty_returns_empty():
    assert split_slides("") == []
    assert split_slides("   ") == []


def test_single_slide_no_doctype():
    html = "<html><body>hi</body></html>"
    out = split_slides(html)
    assert out == [html]


def test_single_slide_with_doctype():
    html = "<!DOCTYPE html><html><body>hi</body></html>"
    out = split_slides(html)
    assert out == [html]


def test_multi_slide_split():
    html = (
        '<!DOCTYPE html><html><body>one</body></html>'
        '<!DOCTYPE html><html><body>two</body></html>'
        '<!DOCTYPE html><html><body>three</body></html>'
    )
    out = split_slides(html)
    assert len(out) == 3
    assert "one" in out[0]
    assert "two" in out[1]
    assert "three" in out[2]
    for chunk in out:
        assert chunk.startswith("<!DOCTYPE html>") or chunk.startswith("<!doctype html>")


def test_doctype_case_insensitive():
    html = (
        "<!doctype HTML><HTML><body>one</body></HTML>"
        "<!DOCTYPE html><html lang=\"en\"><body>two</body></html>"
    )
    out = split_slides(html)
    assert len(out) == 2


def test_single_document_slide_class_deck_splits_to_static_slides():
    html = """<!DOCTYPE html>
<html>
  <head><style>.slide{display:none}.slide.active{display:flex}</style></head>
  <body>
    <div id="deck-container">
      <div class="slide active"><h1>One</h1></div>
      <div class="slide"><h1>Two</h1></div>
      <div class="bottom-bar"><span id="current-slide">1</span> / 2</div>
    </div>
    <script>document.querySelectorAll('.slide').forEach(function(){})</script>
  </body>
</html>"""

    out = split_slides(html)

    assert len(out) == 2
    assert "One" in out[0]
    assert "Two" not in out[0]
    assert "Two" in out[1]
    assert "One" not in out[1]
    assert 'data-pptx-slide-index="2"' in out[1]
    assert '<span id="current-slide">2</span>' in out[1]
    assert 'data-slidify-static-split="true"' in out[1]
    assert "transform: scale(0.6666667)" in out[1]
    assert "<script>" not in out[0]


def test_static_split_collapses_carousel_deck_wrapper():
    html = """<!DOCTYPE html>
<html>
  <head><style>#deck{width:10000vw;display:flex}.slide{width:100vw}</style></head>
  <body>
    <div id="deck">
      <section class="slide"><h1>One</h1></section>
      <section class="slide"><h1>Two</h1></section>
    </div>
  </body>
</html>"""

    out = split_slides(html)

    assert len(out) == 2
    assert "#deck," in out[0]
    assert "width: 100vw !important" in out[0]
    assert "max-width: 100vw !important" in out[0]
    assert "transition: none !important" in out[0]
