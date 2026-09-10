"""Compatibility shim for ``slidify.assets.font_embed``."""

from slidify.assets.font_embed import (
    FontBindingAudit,
    FontVariants,
    audit_font_bindings,
    discover_inter,
    embed_fonts_in_pptx,
)


def embed_default_fonts(pptx_path) -> bool:
    """Embed Inter if present, preserving monkeypatch behavior on this shim."""
    inter = discover_inter()
    if inter is None:
        return False
    embed_fonts_in_pptx(pptx_path, [inter])
    return True

__all__ = [
    "FontBindingAudit",
    "FontVariants",
    "audit_font_bindings",
    "discover_inter",
    "embed_default_fonts",
    "embed_fonts_in_pptx",
]
