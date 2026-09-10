"""Programmatically rewrite a deck's theme color scheme.

PPTX themes ship with a 12-color `<a:clrScheme>` that drives every
`<a:schemeClr val="accent1"/>` reference. python-pptx loads themes but
exposes nothing for editing them, so we drop down to lxml.

Why bother: when the user later changes the deck's theme (or imports it
into a corporate template), shapes that referenced theme colors recolor
cleanly. Shapes that hard-coded `<a:srgbClr val="6366F1"/>` don't.

Usage:

    from slidify.theme import set_theme_accents
    set_theme_accents(
        prs,
        primary="#6366F1",
        secondary="#EC4899",
        accents=["#A78BFA", "#F472B6", "#34D399", "#FACC15"],
    )

Today we only set `accent1..accent6`. Background and text colors stay at
their defaults — overriding them risks invisibility on common templates.
"""

from __future__ import annotations

from collections.abc import Iterable

from lxml import etree

from slidify.colors import parse_color
from slidify.gradients import LinearGradient, RadialGradient, parse_gradient

NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"

# Colors that almost every deck ships and that should never dominate the
# accent palette: pure white/black, "near-white" text, the standard slate
# grey, etc. Keep the list short — over-filtering hides real brand colors.
_BORING = {
    "ffffff", "000000", "f8fafc", "f1f5f9", "e2e8f0",
    "0a0a0a", "171717", "262626", "404040", "525252",
    "737373", "a3a3a3", "d4d4d4",
}


def _normalize_hex(c: str) -> str:
    """Strip leading '#' and uppercase to OOXML format."""
    s = c.strip()
    if s.startswith("#"):
        s = s[1:]
    return s.upper()


def _set_scheme_color(scheme: etree._Element, slot: str, hex_color: str) -> None:
    """Set the srgbClr value of a <a:scheme> child slot. No-op if the slot
    doesn't exist (some themes are minimal)."""
    slot_el = scheme.find(f"{{{NS_A}}}{slot}")
    if slot_el is None:
        return
    for tag in ("srgbClr", "sysClr"):
        for existing in slot_el.findall(f"{{{NS_A}}}{tag}"):
            slot_el.remove(existing)
    etree.SubElement(
        slot_el, f"{{{NS_A}}}srgbClr", attrib={"val": _normalize_hex(hex_color)}
    )


def set_theme_accents(
    prs,
    *,
    primary: str | None = None,
    secondary: str | None = None,
    accents: list[str] | None = None,
) -> bool:
    """Override accent colors in the slide master's color scheme.

    `primary` becomes accent1, `secondary` accent2, `accents[0..3]` become
    accent3..accent6. Returns True iff the scheme was found and patched.
    """
    if not prs.slide_masters:
        return False
    master_part = prs.slide_masters[0].part
    theme_part = None
    for rel in master_part.rels.values():
        if "theme" in rel.reltype.lower():
            theme_part = rel.target_part
            break
    if theme_part is None:
        return False
    # python-pptx exposes the theme as a generic Part (raw bytes), not an
    # XmlPart with `._element`. Parse → mutate → write the blob back.
    blob = getattr(theme_part, "blob", None)
    if blob is None:
        return False
    theme_el = etree.fromstring(blob)
    schemes = theme_el.findall(
        f".//{{{NS_A}}}themeElements/{{{NS_A}}}clrScheme"
    )
    if not schemes:
        return False
    scheme = schemes[0]

    if primary is not None:
        _set_scheme_color(scheme, "accent1", primary)
    if secondary is not None:
        _set_scheme_color(scheme, "accent2", secondary)
    if accents:
        for i, hex_ in enumerate(accents[:4]):
            _set_scheme_color(scheme, f"accent{3 + i}", hex_)

    theme_part._blob = etree.tostring(  # noqa: SLF001
        theme_el, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    return True


def _rgb_to_hex(rgb) -> str:
    return f"{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _harvest_colors_from_text(value: str | None) -> list[str]:
    """Return all distinct hex colors found in a CSS color-or-gradient value."""
    if not value or value in ("none", "transparent"):
        return []
    out: list[str] = []
    grad = parse_gradient(value)
    if grad is not None:
        stops = grad.stops if isinstance(grad, (LinearGradient, RadialGradient)) else []
        for s in stops:
            if s.alpha > 0:
                out.append(s.color_hex.lower())
        return out
    parsed = parse_color(value)
    if parsed is not None:
        rgb, alpha = parsed
        if alpha > 0:
            out.append(_rgb_to_hex(rgb))
    return out


def derive_accents_from_elements(
    elements_iter: Iterable, max_accents: int = 6
) -> list[str]:
    """Infer brand colors from a stream of `DomElement` objects.

    Strategy: count every distinct hex color that appears in `color`,
    `background_color`, or as a stop inside `background_image` gradients.
    Filter out boring greys / pure black-and-white. Return the top-N by
    occurrence count in '#RRGGBB' form (lowercase, with leading hash).

    Gradient stops carry weight 2 (each stop counted twice) so a deck
    whose hero uses a 3-stop gradient surfaces those colors above
    one-off solid fills.
    """
    counter: dict[str, int] = {}
    for el in elements_iter:
        # Solid foreground / background — weight 1 each.
        for hex_ in _harvest_colors_from_text(getattr(el, "color", None)):
            counter[hex_] = counter.get(hex_, 0) + 1
        for hex_ in _harvest_colors_from_text(getattr(el, "background_color", None)):
            counter[hex_] = counter.get(hex_, 0) + 1
        # Gradient stops — weight 2 (these are the "designed" brand colors).
        bg_image = getattr(el, "background_image", None)
        if bg_image and bg_image not in ("none", "transparent"):
            for hex_ in _harvest_colors_from_text(bg_image):
                counter[hex_] = counter.get(hex_, 0) + 2

    ranked = [
        (h, n) for h, n in sorted(counter.items(), key=lambda kv: -kv[1])
        if h not in _BORING
    ]
    out: list[str] = []
    for h, _ in ranked:
        if len(out) >= max_accents:
            break
        out.append(f"#{h}")
    return out


__all__ = [
    "derive_accents_from_elements",
    "set_theme_accents",
]
