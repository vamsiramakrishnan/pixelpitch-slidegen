# Copyright 2026 Google LLC

"""The catalog is all that stands between a typo and a component that vanishes.

An undeclared property is not a loud failure. ``get_a2ui_format`` applies
``remove_strict_validation``, which strips ``additionalProperties: false``, and
no Material component declares ``unevaluatedProperties``. So an invented
property passes the wire step and strict jsonschema alike, reaches Gemini
Enterprise, and is dropped there: the surface renders with the property
silently ignored, or the component missing entirely. Nothing between here and
production reports it. These checks are that report.
"""

import json
import re
from pathlib import Path
from typing import Any

from app.a2ui_composer import compose_error, compose_intake, compose_result
from app.app_utils.a2ui_format import GEMINI_ENTERPRISE_CATALOG_PATH

_CATALOG = json.loads(Path(GEMINI_ENTERPRISE_CATALOG_PATH).read_text())
_COMPONENTS = _CATALOG["components"]
_DEFS = _CATALOG["$defs"]


def _resolve(node, into: dict, seen: tuple = ()) -> None:
    """Flatten a component schema's properties across $ref and combinators."""
    if not isinstance(node, dict):
        return
    name = node.get("$ref", "").rsplit("/", 1)[-1]
    if name in _DEFS and name not in seen:
        _resolve(_DEFS[name], into, (*seen, name))
    for key, value in (node.get("properties") or {}).items():
        into.setdefault(key, value)
    for combinator in ("allOf", "anyOf", "oneOf"):
        for branch in node.get(combinator) or []:
            _resolve(branch, into, seen)


def _declared(component: str) -> dict:
    resolved: dict = {}
    _resolve(_COMPONENTS.get(component) or {}, resolved)
    return resolved


# Only the 28 Material components carry `style`, and it is an allowlist of
# exactly these CSS keys. `position`, `zIndex`, `opacity` and `transform` are
# excluded on purpose (the catalog cites CWE-1021, UI redress).
_STYLE_KEYS = set(_declared("MaterialText")["style"].get("properties") or {})


def catalog_violations(components: list[dict]) -> list[str]:
    """Every way a component list can disagree with the catalog."""
    problems: list[str] = []
    for item in components:
        where = item.get("id", "<unidentified>")
        name = str(item.get("component") or "")
        if name not in _COMPONENTS:
            problems.append(f"{where}: '{name}' is not a catalog component")
            continue
        declared = _declared(name)
        for key, value in item.items():
            if key in ("id", "component"):
                continue
            if key not in declared:
                problems.append(f"{where} ({name}): undeclared property '{key}'")
                continue
            enum = declared[key].get("enum")
            if enum and isinstance(value, str) and value not in enum:
                problems.append(f"{where} ({name}): {key}={value!r} not one of {enum}")
            if key == "style" and isinstance(value, dict):
                for css in value:
                    if css not in _STYLE_KEYS:
                        problems.append(
                            f"{where} ({name}): style key '{css}' is barred"
                        )
            if key == "options" and isinstance(value, list):
                for option in value:
                    if isinstance(option, dict) and set(option) - {"label", "value"}:
                        extra = sorted(set(option) - {"label", "value"})
                        problems.append(f"{where} ({name}): option keys {extra} barred")
    return problems


def _components(wire: str) -> list[dict]:
    messages = json.loads(
        wire.split("<a2ui-json>\n", 1)[1].split("\n</a2ui-json>", 1)[0]
    )
    return next(
        message["updateComponents"]["components"]
        for message in messages
        if "updateComponents" in message
    )


# --- the checker itself has teeth


def test_the_checker_rejects_what_the_wire_step_lets_through():
    """Each of these validates locally and renders as nothing in production."""
    assert catalog_violations([{"id": "a", "component": "NotAThing"}])
    # `accessibility` reads like a real A2UI property. It appears nowhere in
    # the catalog, on any of the 57 components.
    assert catalog_violations(
        [{"id": "a", "component": "ChoicePicker", "accessibility": {"label": "x"}}]
    )
    # `checks` is declared by MaterialInput and by no other input component.
    assert catalog_violations([{"id": "a", "component": "TextField", "checks": []}])
    # Legal on MaterialText.usageHint, illegal on basic Text.variant.
    assert catalog_violations(
        [{"id": "a", "component": "Text", "variant": "subtitle2"}]
    )
    # Legal on basic Button.variant, illegal on MaterialButton.appearance.
    assert catalog_violations(
        [{"id": "a", "component": "MaterialButton", "appearance": "primary"}]
    )
    # Basic components have no `style` at all, which is the whole reason the
    # form could not express spacing before.
    assert catalog_violations(
        [{"id": "a", "component": "Column", "style": {"gap": "8px"}}]
    )
    assert catalog_violations(
        [{"id": "a", "component": "MaterialCard", "style": {"position": "absolute"}}]
    )
    assert catalog_violations(
        [
            {
                "id": "a",
                "component": "ChoicePicker",
                "options": [{"label": "l", "value": "v", "icon": "x"}],
            }
        ]
    )


def test_the_checker_passes_what_gemini_enterprise_actually_renders():
    assert (
        catalog_violations(
            [
                {
                    "id": "a",
                    "component": "MaterialText",
                    "usageHint": "subtitle2",
                    "text": "x",
                },
                {
                    "id": "b",
                    "component": "MaterialCard",
                    "appearance": "raised",
                    "children": ["a"],
                },
                {"id": "c", "component": "MaterialColumn", "style": {"gap": "16px"}},
                {
                    "id": "d",
                    "component": "MaterialButton",
                    "appearance": "filled",
                    "label": "Go",
                },
            ]
        )
        == []
    )


# --- the surfaces this agent actually emits


def _intake(**overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "topic": "Agent platform benefits",
        "audience": "Leadership team",
        "goal": "Clear, confident, and on-brand",
        "brands": [
            {"id": "stripe", "name": "Stripe", "palette": ["#061B31", "#533AFD"]}
        ],
        "styles": [{"id": "editorial", "name": "Deck Guizang Editorial"}],
        "templates": [
            {
                "revision": "tr1_example-retail",
                "name": "100 Years Example Retail.pptx",
                "display_name": "100 Years Example Retail",
                "admitted": True,
                "preview_image_tokens": ["__TEMPLATE_PREVIEW_IMAGE_01__"],
            }
        ],
    }
    kwargs.update(overrides)
    return compose_intake(**kwargs)


def test_the_full_brief_form_matches_the_catalog():
    assert catalog_violations(_components(_intake())) == []


def test_the_brief_form_matches_the_catalog_with_a_template_pinned():
    wire = _intake(selected_template_revision="tr1_example-retail")
    assert catalog_violations(_components(wire)) == []


def test_the_brief_form_matches_the_catalog_with_nothing_to_choose():
    wire = _intake(brands=[], styles=[], templates=[])
    assert catalog_violations(_components(wire)) == []


_TITLES = [
    "Where retail growth stalled",
    "Three markets, one pattern",
    "What the store data says",
    "The cost of standing still",
    "A single operating model",
    "Phase one, the flagship",
    "Phase two, the region",
    "Phase three, the network",
    "What it takes to fund it",
    "How we measure the turn",
    "Risks we are watching",
    "What we need from you",
]


def _result(**overrides: Any) -> str:
    """A delivered twelve-slide deck, previewed six slides deep.

    The renderer caps previews at six and authors a title per slide, so the
    outline is always longer than the gallery. There is no ``title`` key,
    because the pipeline never emits one and the heading comes from the brief.
    """
    result: dict[str, Any] = {
        "status": "ok",
        "brand": "Phoenix Retail",
        "slide_count": 12,
        "slide_titles": list(_TITLES),
        "preview_image_tokens": [
            f"__SLIDE_PREVIEW_IMAGE_{index:02d}__" for index in range(1, 7)
        ],
        "https_url": "https://example.com/deck.pptx",
    }
    result.update(overrides)
    # A key the pipeline did not produce is absent, not None, so overriding
    # with None is how a test says "this deck came back without that key".
    result = {key: value for key, value in result.items() if value is not None}
    return compose_result(result, brief={"topic": "Retail growth", "slide_count": 12})


def _emitted(wire: str) -> str:
    return json.dumps(_components(wire), ensure_ascii=False)


def test_the_delivered_deck_surface_matches_the_catalog():
    assert catalog_violations(_components(_result())) == []


def test_the_delivered_deck_surface_uses_only_components_that_can_be_styled():
    """Basic components take no `style`, so spacing on them silently vanishes.

    Dividers were what stood in for that spacing before, and a rule drawn
    between every section is louder than the gap it replaces.
    """
    emitted = {item["component"] for item in _components(_result())}
    assert emitted.isdisjoint({"Divider", "Text", "Button", "Column"})


def test_the_outline_names_every_slide_the_previews_could_not_show():
    emitted = _emitted(_result())
    for title in _TITLES:
        assert title in emitted
    # Every slide was authored with a title, so nothing should reach the user
    # captioned by its index.
    captions = [
        item["text"]
        for item in _components(_result())
        if item["id"].startswith("preview_caption_")
    ]
    assert not any(re.search(r"^Slide \d+$", caption) for caption in captions)


def test_a_preview_and_its_outline_row_name_the_same_slide():
    """Two captions for one slide is worse than one, because both are trusted."""
    wire = _result(slide_titles=_TITLES[:3])
    by_id = {item["id"]: item for item in _components(wire)}
    for index in range(3):
        row = by_id[f"outline_row_{index}"]["text"]
        assert row.endswith(by_id[f"preview_caption_{index}"]["text"])
        assert row.startswith(f"{index + 1:02d} · ")


def test_the_revision_round_trip_still_matches_what_the_agent_decodes():
    """These names and paths are the contract with ``pixelpitch_agent``.

    The picker moved inside ``_choice_group`` and both buttons became
    ``MaterialButton``. Either change could have quietly renamed the event or
    dropped the context path, and the surface would still validate, still
    render, and still do nothing when pressed.
    """
    by_id = {item["id"]: item for item in _components(_result())}

    picker = by_id["revision_choice"]
    assert picker["value"] == {"path": "/revision/instruction"}
    assert {option["value"] for option in picker["options"]} == {
        "",
        "punchier",
        "more_aids",
        "new_expressions",
    }
    assert picker["component"] == "MaterialSelect"
    assert picker["label"] == picker["ariaLabel"]

    assert by_id["revise"]["action"] == {
        "event": {
            "name": "revise_deck",
            "context": {
                "instruction": {"path": "/revision/instruction"},
                "detail": {"path": "/revision/detail"},
            },
        }
    }
    assert by_id["new"]["action"] == {"event": {"name": "new_deck", "context": {}}}
    assert by_id["download"]["action"]["functionCall"]["call"] == "openUrl"


def test_the_delivered_deck_surface_matches_the_catalog_without_slide_titles():
    wire = _result(slide_titles=None)
    assert catalog_violations(_components(wire)) == []
    # No outline to draw, but the previews still have to be captioned.
    assert "outline_card" not in _emitted(wire)
    assert "Slide 1" in _emitted(wire)


def test_the_delivered_deck_surface_matches_the_catalog_with_blank_slide_titles():
    wire = _result(slide_titles=["", "   ", ""])
    assert catalog_violations(_components(wire)) == []
    assert "outline_card" not in _emitted(wire)


def test_the_delivered_deck_surface_matches_the_catalog_with_fewer_titles_than_previews():
    wire = _result(slide_titles=_TITLES[:3])
    assert catalog_violations(_components(wire)) == []
    emitted = _emitted(wire)
    assert _TITLES[2] in emitted
    assert "Slide 4" in emitted


def test_the_delivered_deck_surface_matches_the_catalog_without_a_download_url():
    wire = _result(https_url=None)
    assert catalog_violations(_components(wire)) == []
    assert "download_bar" not in _emitted(wire)


def test_the_delivered_deck_surface_matches_the_catalog_without_previews():
    wire = _result(preview_image_tokens=None)
    assert catalog_violations(_components(wire)) == []
    assert "preview_grid" not in _emitted(wire)


def test_error_recovery_matches_the_catalog():
    assert (
        catalog_violations(
            _components(compose_error("Add a topic", "Your brief needs a subject."))
        )
        == []
    )
