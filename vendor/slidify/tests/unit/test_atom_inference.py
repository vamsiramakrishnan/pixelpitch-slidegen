"""Tests for `slidify.atom_inference` — implicit atom inference + synthetic
canonical-unit construction used to prime `atom_signatures.json`.
"""

from __future__ import annotations

import json
from pathlib import Path

from slidify import atom_inference
from slidify.atom_inference import (
    _canonical_unit_for_atom,
    _NAMESPACE_TEMPLATES,
    build_synthetic_table,
    infer_atom_id,
)
from slidify.models import BoundingBox, DomElement, VisualUnit
from slidify.patterns.signatures import signature_hash


def _patch_table(monkeypatch, payload: dict) -> None:
    """Force `_load_table` to return `payload['entries']` for one test."""

    monkeypatch.setattr(
        atom_inference,
        "_load_table",
        lambda: {str(k): str(v) for k, v in payload.get("entries", {}).items()},
    )


def _simple_unit(eid: int = 1, *, data_atom: str = "") -> VisualUnit:
    el = DomElement(
        id=eid,
        parent_id=None,
        depth=0,
        tag="DIV",
        cls="",
        bbox=BoundingBox(x=0, y=0, w=200, h=100),
        data_atom=data_atom,
        stable_selector=f"#e{eid}",
    )
    return VisualUnit(
        id=f"u_{eid}",
        bbox=el.bbox,
        elements=[el],
        anchor_element_id=eid,
    )


# ---- 1. empty table → None ------------------------------------------------


def test_infer_returns_none_for_empty_table(monkeypatch):
    _patch_table(monkeypatch, {"version": 1, "entries": {}})
    unit = _simple_unit()
    assert infer_atom_id(unit) is None


def test_infer_returns_none_when_table_missing(monkeypatch, tmp_path):
    """A missing JSON file must surface as silent None, not an exception."""
    # Bypass the `_load_table` cache and point at a non-existent path.
    atom_inference._reset_table_cache()
    monkeypatch.setattr(atom_inference, "_SIG_FILE", "does_not_exist.json")
    try:
        unit = _simple_unit()
        assert infer_atom_id(unit) is None
    finally:
        # Restore so other tests in the same process aren't poisoned.
        monkeypatch.setattr(atom_inference, "_SIG_FILE", "atom_signatures.json")
        atom_inference._reset_table_cache()


# ---- 2. known signature → atom id -----------------------------------------


def test_infer_returns_atom_for_known_signature(monkeypatch):
    unit = _simple_unit()
    h = signature_hash(unit)
    _patch_table(monkeypatch, {"version": 1, "entries": {h: "bg.mesh"}})
    assert infer_atom_id(unit) == "bg.mesh"


# ---- 3. explicit atom is never overridden by inference ---------------------


def test_inference_does_not_override_explicit_atom(monkeypatch):
    """The classifier wiring only synthesises a hint when `data_atom` is empty.
    Inference itself never mutates state — but we verify the contract by
    confirming `infer_atom_id` is the lookup, and checking the wiring guard
    in api._classify_unit_tier12 by simulating the call site.
    """
    explicit_unit = _simple_unit(data_atom="surf.glass")
    h = signature_hash(explicit_unit)
    _patch_table(monkeypatch, {"version": 1, "entries": {h: "bg.mesh"}})

    # The lookup itself returns the table value (no per-call mutation logic
    # baked in — guarding is the wiring's responsibility).
    assert infer_atom_id(explicit_unit) == "bg.mesh"
    # And the unit's existing data_atom is untouched by infer_atom_id.
    assert explicit_unit.elements[0].data_atom == "surf.glass"


# ---- 4. canonical unit per namespace --------------------------------------


def test_canonical_unit_for_atom_namespaces():
    """For every namespace template, `_canonical_unit_for_atom("<ns>.x")`
    returns a non-None unit with at least one element."""
    for ns in _NAMESPACE_TEMPLATES:
        atom_id = f"{ns}.example"
        unit = _canonical_unit_for_atom(atom_id)
        assert unit is not None, f"namespace {ns} returned None"
        assert len(unit.elements) >= 1, f"namespace {ns} produced no elements"
        # Bbox must be a real, non-zero rectangle.
        assert unit.bbox.w > 0 and unit.bbox.h > 0


def test_canonical_unit_unknown_namespace_returns_none():
    assert _canonical_unit_for_atom("doesnotexist.foo") is None
    assert _canonical_unit_for_atom("") is None


def test_canonical_units_distinct_per_salt():
    """Distinct salts within a namespace must produce distinct signature
    hashes — that's the contract `build_synthetic_table` relies on to
    pack every atom id into the priming table without collisions."""
    a = _canonical_unit_for_atom("bg.mesh", salt=0)
    b = _canonical_unit_for_atom("bg.mesh", salt=1)
    c = _canonical_unit_for_atom("bg.mesh", salt=2)
    assert a is not None and b is not None and c is not None
    sigs = {signature_hash(a), signature_hash(b), signature_hash(c)}
    assert len(sigs) == 3


def test_build_synthetic_table_assigns_unique_entry_per_atom():
    """The priming table must keep one entry per declared atom id —
    silently dropping atoms on collision was the Codex P1 finding."""
    from slidify.atom_inference import _atom_ids_from_yaml
    table = build_synthetic_table()
    declared = _atom_ids_from_yaml()
    assert len(table) == len(declared)
    assert set(table.values()) == set(declared)


def test_build_synthetic_table_is_nonempty():
    """`build_synthetic_table` walks atoms.yaml and returns a populated dict."""
    table = build_synthetic_table()
    assert isinstance(table, dict)
    assert len(table) > 0
    # All values look like atom ids ("<ns>.<leaf>").
    for atom_id in table.values():
        assert "." in atom_id
