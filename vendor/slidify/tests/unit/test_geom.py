"""Tests for slidify.geom and slidify.fonts."""

from __future__ import annotations

from slidify.fonts import is_bold, parse_weight, resolve
from slidify.geom import EMU_PER_PX, parse_pt, parse_px, px_to_emu


def test_px_to_emu():
    assert px_to_emu(0) == 0
    assert px_to_emu(1) == EMU_PER_PX  # 9525
    assert px_to_emu(96) == 96 * EMU_PER_PX  # 1 inch


def test_parse_px():
    assert parse_px("14.5px") == 14.5
    assert parse_px("0px") == 0
    assert parse_px("none") == 0
    assert parse_px(None) == 0
    assert parse_px("16") == 16.0


def test_parse_pt():
    # 16px = 12pt
    assert abs(parse_pt("16px") - 12.0) < 1e-6
    assert parse_pt(None) == 0.0


def test_resolve_known_fonts():
    assert resolve("Inter, Helvetica") == "Inter"
    assert resolve("Helvetica, Arial") == "Helvetica"
    assert resolve("system-ui") == "Calibri"
    assert resolve("") == "Calibri"


def test_resolve_quoted_fonts():
    assert resolve('"Helvetica Neue", Helvetica, sans-serif') == "Arial"


def test_parse_weight():
    assert parse_weight("400") == 400
    assert parse_weight("bold") == 700
    assert parse_weight("normal") == 400
    assert parse_weight("garbage") == 400


def test_is_bold():
    assert is_bold("700") is True
    assert is_bold("600") is True
    assert is_bold("400") is False
    assert is_bold("bold") is True
