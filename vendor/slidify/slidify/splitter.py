"""Split a multi-slide HTML blob into N single-slide HTML strings."""

from __future__ import annotations

import copy
import re

from lxml import html as lxml_html

_DOCTYPE_HTML = re.compile(
    r"<!DOCTYPE\s+html[^>]*>\s*<html[^>]*>",
    re.IGNORECASE,
)


def split_slides(html: str) -> list[str]:
    """Split on common HTML deck shapes; tolerate single-slide input.

    Each output is a complete, valid HTML document.
    """
    if not html or not html.strip():
        return []

    matches = list(_DOCTYPE_HTML.finditer(html))
    if len(matches) <= 1:
        slide_docs = _split_class_slide_deck(html)
        return slide_docs or [html]

    slides: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        chunk = html[start:end].rstrip()
        slides.append(chunk)
    return slides


def _split_class_slide_deck(source: str) -> list[str]:
    """Split one-page decks that contain multiple `.slide` elements.

    Many agent-authored decks use a browser runtime that toggles
    `.slide.active` instead of writing one standalone HTML document per slide.
    For PPTX export we need deterministic, static render inputs, so each output
    keeps the page chrome and styles, removes sibling slides, and marks the
    selected slide active.
    """
    try:
        root = lxml_html.fromstring(source)
    except Exception:
        return []

    slides = root.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' slide ')]"
    )
    if len(slides) <= 1:
        return []

    docs: list[str] = []
    for index in range(len(slides)):
        doc = copy.deepcopy(root)
        cloned_slides = doc.xpath(
            "//*[contains(concat(' ', normalize-space(@class), ' '), ' slide ')]"
        )
        for i, slide in enumerate(cloned_slides):
            if i != index:
                parent = slide.getparent()
                if parent is not None:
                    parent.remove(slide)
                continue
            classes = [
                c for c in (slide.get("class") or "").split()
                if c != "active"
            ]
            slide.set("class", " ".join([*classes, "active"]).strip())
            slide.set("data-pptx-slide-index", str(index + 1))

        for script in doc.xpath("//script"):
            parent = script.getparent()
            if parent is not None:
                parent.remove(script)

        counter = doc.xpath("//*[@id='current-slide']")
        if counter:
            counter[0].text = str(index + 1)

        _inject_static_split_style(doc)
        docs.append("<!DOCTYPE html>\n" + lxml_html.tostring(doc, encoding="unicode"))

    return docs


def _inject_static_split_style(doc) -> None:
    head = doc.find(".//head")
    if head is None:
        return
    style = lxml_html.Element("style")
    style.set("data-slidify-static-split", "true")
    style.text = """
html, body {
  width: 1280px !important;
  height: 720px !important;
  overflow: hidden !important;
  margin: 0 !important;
}
body {
  display: block !important;
  align-items: initial !important;
  justify-content: initial !important;
}
#deck-container {
  transform: scale(0.6666667) !important;
  transform-origin: top left !important;
  box-shadow: none !important;
}
#deck,
.deck,
[data-deck],
[data-slidify-deck] {
  position: fixed !important;
  inset: 0 !important;
  width: 100vw !important;
  height: 100vh !important;
  min-width: 0 !important;
  max-width: 100vw !important;
  display: block !important;
  transform: none !important;
  transition: none !important;
  overflow: hidden !important;
}
.slide {
  width: 100vw !important;
  height: 100vh !important;
  flex: 0 0 100vw !important;
  transform: none !important;
}
"""
    head.append(style)
