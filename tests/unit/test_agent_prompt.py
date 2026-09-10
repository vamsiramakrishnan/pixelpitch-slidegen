# Copyright 2026 Google LLC

"""Behaviour and layout contracts for Gemini Enterprise deck composition."""

import json
import re

import pytest

from app.a2ui_composer import (
    TEMPLATE_LIMIT,
    compose_error,
    compose_intake,
    compose_result,
)
from app.tools import DEFAULT_SLIDES, MAX_SLIDES


def _messages(wire: str) -> list[dict]:
    return json.loads(wire.split("<a2ui-json>\n", 1)[1].split("\n</a2ui-json>", 1)[0])


def _data_model(wire: str) -> dict:
    return next(
        m["updateDataModel"]["value"] for m in _messages(wire) if "updateDataModel" in m
    )


def _components(wire: str) -> list[dict]:
    return next(
        m["updateComponents"]["components"]
        for m in _messages(wire)
        if "updateComponents" in m
    )


def _intake(**overrides) -> str:
    args = {
        "topic": "Board update",
        "audience": "Board",
        "goal": "Decide the next investment",
        "brands": [
            {"id": "stripe", "name": "Stripe", "palette": ["#061B31", "#533AFD"]}
        ],
        "styles": [{"id": "editorial", "name": "Editorial"}],
        "templates": [
            {
                "revision": "tr1",
                "display_name": "Retail QBR",
                "admitted": True,
                "preview_image_tokens": ["__TEMPLATE_PREVIEW_IMAGE_01__"],
            }
        ],
    }
    return compose_intake(**{**args, **overrides})


def _by_id(wire: str) -> dict:
    return {item["id"]: item for item in _components(wire)}


def test_final_result_uses_flat_image_preview_tokens():
    wire = compose_result(
        {
            "slide_count": 2,
            "preview_image_tokens": ["__SLIDE_PREVIEW_IMAGE_01__"],
            "https_url": "https://example.com/deck.pptx",
        },
        brief={"topic": "Retail growth"},
    )
    components = _by_id(wire)
    assert components["preview_image_0"]["url"] == "__SLIDE_PREVIEW_IMAGE_01__"
    assert components["preview_image_0"]["fit"] == "contain"
    assert "preview_card_0" in components["root"]["children"]
    assert "IFrame" not in wire
    assert "base64" not in wire


def test_offered_template_is_not_selected_without_user_intent():
    brief = _data_model(_intake())["brief"]
    assert brief["template_revision"] == ""
    assert brief["brand_id"] == "stripe"
    assert brief["style_id"] == "editorial"


def test_native_selects_have_visible_and_accessible_labels():
    components = _by_id(_intake())
    for key in ("brand", "style", "template"):
        assert components[key]["component"] == "MaterialSelect"
        assert components[key]["label"] == components[key]["ariaLabel"]


def test_template_selection_can_be_cleared_and_disables_overridden_choices():
    wire = _intake(selected_template_revision="tr1")
    components = _by_id(wire)
    assert _data_model(wire)["brief"]["template_revision"] == "tr1"
    assert components["template"]["options"][0] == {
        "value": "",
        "label": "Use brand & slide style",
    }
    for key in ("brand", "style"):
        assert components[key]["disabled"] == {
            "call": "required",
            "args": {"value": {"path": "/brief/template_revision"}},
            "returnType": "boolean",
        }
    assert "overrides" in components["template_picker_note"]["text"]
    # A gallery does not make a static selected-state claim that goes stale
    # as soon as the two-way bound select changes.
    assert "Pinned" not in wire


def test_only_prepared_templates_are_offered_and_gallery_matches_picker():
    templates = [
        {"revision": f"tr{n}", "name": f"Template {n}", "admitted": True}
        for n in range(TEMPLATE_LIMIT + 3)
    ] + [{"revision": "raw", "name": "Unprepared", "admitted": False}]
    components = _by_id(_intake(templates=templates))
    cards = [key for key in components if key.startswith("template_card_")]
    options = components["template"]["options"][1:]
    assert len(cards) == TEMPLATE_LIMIT
    assert [option["value"] for option in options] == [
        f"tr{n}" for n in range(TEMPLATE_LIMIT)
    ]
    assert "Unprepared" not in json.dumps(components)
    assert components["template_panel"]["expanded"] is False
    assert components["template_grid"]["component"] == "MaterialRow"
    assert components["template_grid"]["style"]["flexWrap"] == "wrap"


def test_selected_template_is_not_lost_when_it_moves_beyond_gallery_limit():
    templates = [
        {"revision": f"tr{n}", "name": f"Template {n}", "admitted": True}
        for n in range(TEMPLATE_LIMIT + 2)
    ]
    selected = f"tr{TEMPLATE_LIMIT + 1}"
    wire = _intake(templates=templates, selected_template_revision=selected)
    assert _data_model(wire)["brief"]["template_revision"] == selected
    options = _by_id(wire)["template"]["options"][1:]
    assert len(options) == TEMPLATE_LIMIT
    assert selected in {option["value"] for option in options}


def test_duplicate_revisions_do_not_duplicate_gallery_entries():
    template = {"revision": "tr1", "name": "Retail", "admitted": True}
    components = _by_id(_intake(templates=[template, template]))
    assert len(components["template"]["options"]) == 2
    assert components["template_grid"]["children"] == ["template_card_0"]


def test_unavailable_templates_leave_no_empty_gallery():
    components = _by_id(_intake(templates=[{"revision": "raw", "admitted": False}]))
    assert "template" not in components
    assert "template_panel" not in components


def test_template_preview_missing_is_explicit():
    components = _by_id(
        _intake(
            templates=[
                {"revision": "tr1", "name": "Retail.pptx", "admitted": True},
            ]
        )
    )
    assert components["template_meta_0"]["text"] == "Preview unavailable"
    assert components["template_name_0"]["text"] == "Retail"
    assert components["template"]["options"][1]["label"] == "Retail"


def test_slide_count_round_trips_up_to_the_pipeline_ceiling():
    wire = _intake(slide_count=MAX_SLIDES)
    field = _by_id(wire)["slide_count"]
    assert _data_model(wire)["brief"]["slide_count"] == str(MAX_SLIDES)
    for value in range(1, MAX_SLIDES + 1):
        assert re.fullmatch(field["validationRegexp"], str(value))
    for value in ("0", "-1", "1.5", "", str(MAX_SLIDES + 1)):
        assert not re.fullmatch(field["validationRegexp"], value)


@pytest.mark.parametrize("value", [MAX_SLIDES + 1, 0, "bad", None])
def test_invalid_initial_count_uses_default(value):
    assert _data_model(_intake(slide_count=value))["brief"]["slide_count"] == str(
        DEFAULT_SLIDES
    )


def test_generate_action_resolves_all_editable_fields():
    components = _by_id(_intake())
    generate = components["generate"]
    assert generate["component"] == "MaterialButton"
    assert generate["label"] == "Generate deck"
    assert generate["action"]["event"] == {
        "name": "generate_deck",
        "context": {
            **{
                key: {"path": f"/brief/{key}"}
                for key in (
                    "topic", "audience", "goal", "slide_count", "brand_id",
                    "style_id", "template_revision",
                )
            },
            "request_id": {"path": "/request_id"},
        },
    }
    assert components["root"]["children"][-1] == "action_bar"


def test_ui_theme_does_not_depend_on_deck_brand_or_template():
    themes = [
        next(
            m["createSurface"]["theme"] for m in _messages(wire) if "createSurface" in m
        )
        for wire in (
            _intake(),
            _intake(selected_template_revision="tr1"),
            _intake(brands=[{"id": "bright", "palette": ["#FFFFFF", "#FFFF00"]}]),
        )
    ]
    assert all(theme["primaryColor"] == "#0B57D0" for theme in themes)


def test_no_fixed_height_grids_or_nested_cards():
    for wire in (
        _intake(),
        compose_result(
            {
                "slide_count": 6,
                "preview_image_tokens": [f"__IMAGE_{n}__" for n in range(6)],
            },
            brief={"topic": "Retail"},
        ),
    ):
        components = _by_id(wire)
        assert not any(
            c["component"] in ("MaterialGridList", "MaterialCard")
            for c in components.values()
        )
        assert components["root"]["style"]["gap"]
        assert components["root"]["style"]["maxWidth"] == "760px"


def test_error_has_recovery_action_carrying_the_unsubmitted_brief():
    brief = {"topic": "", "audience": "Board", "slide_count": "12", "goal": "Decide"}
    components = _by_id(
        compose_error("Add a topic", "Your brief needs a subject.", brief=brief)
    )
    assert components["recovery"]["action"]["event"] == {
        "name": "edit_brief",
        "context": brief,
    }


def test_reload_keeps_all_current_form_fields():
    components = _by_id(_intake(brands=[], styles=[], templates=[]))
    assert components["reload"]["action"]["event"] == {
        "name": "edit_brief",
        "context": {
            key: value
            for key, value in components["generate"]["action"]["event"]["context"].items()
            if key != "request_id"
        },
    }
    assert "reload" in components["action_bar"]["children"]


def test_every_component_is_reachable_from_root():
    for wire in (
        _intake(),
        _intake(brands=[], styles=[], templates=[]),
        compose_result(
            {
                "slide_count": 2,
                "slide_titles": ["One", "Two"],
                "preview_image_tokens": ["__ONE__", "__TWO__"],
            },
            brief={},
        ),
        compose_error("Error", "Try again."),
    ):
        components = _by_id(wire)
        reached = set()
        pending = ["root"]
        while pending:
            key = pending.pop()
            if key in reached:
                continue
            reached.add(key)
            pending.extend(components[key].get("children", []))
            if components[key].get("child"):
                pending.append(components[key]["child"])
        assert set(components) == reached
