# Copyright 2026 Google LLC

"""Catalog-validated, native Gemini Enterprise deck surfaces.

The brief reads as a form; the result leads with the actual slide. Optional
galleries and outlines disclose on demand. Material owns control states and
theme colours; deck branding belongs to the artifact, not the host interface.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.app_utils.a2ui_format import get_a2ui_format, get_supported_catalog_ids
from app.tools import DEFAULT_SLIDES, MAX_SLIDES

TEMPLATE_LIMIT = 6
_PRIMARY = "#0B57D0"
_STACK = {"display": "flex", "flexDirection": "column", "minWidth": "0px"}
_ROW = {"display": "flex", "flexWrap": "wrap", "gap": "16px", "width": "100%"}
_HEADING = {"fontSize": "26px", "lineHeight": "1.25", "fontWeight": "600"}
_BODY = {"fontSize": "14px", "lineHeight": "1.5", "overflowWrap": "anywhere"}
_PANEL = {"boxShadow": "none", "borderRadius": "12px", "minWidth": "0px"}
_BRIEF_KEYS = (
    "topic",
    "audience",
    "goal",
    "slide_count",
    "brand_id",
    "style_id",
    "template_revision",
)


def _slide_count_regexp(maximum: int) -> str:
    return "^(?:" + "|".join(str(n) for n in range(1, maximum + 1)) + ")$"


def _clamp_slide_count(value: Any) -> int:
    try:
        count = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_SLIDES
    return count if 1 <= count <= MAX_SLIDES else DEFAULT_SLIDES


def _wire(messages: list[dict[str, Any]]) -> str:
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    a2ui_format = get_a2ui_format()
    compiled = a2ui_format.parser.compile(serialized, is_final=True)
    a2ui_format.get_selected_catalog().validator.validate(compiled, root_id="root")
    return f"<a2ui-json>\n{serialized}\n</a2ui-json>"


def _surface(surface_id: str, components: list[dict], data: dict) -> str:
    return _wire(
        [
            {
                "version": "v0.9",
                "createSurface": {
                    "surfaceId": surface_id,
                    "catalogId": get_supported_catalog_ids()[0],
                    "theme": {"primaryColor": _PRIMARY},
                },
            },
            {
                "version": "v0.9",
                "updateDataModel": {
                    "surfaceId": surface_id,
                    "path": "/",
                    "value": data,
                },
            },
            {
                "version": "v0.9",
                "updateComponents": {
                    "surfaceId": surface_id,
                    "components": components,
                },
            },
        ]
    )


def _text(key: str, text: str, hint: str = "body2", **style: str) -> dict:
    return {
        "id": key,
        "component": "MaterialText",
        "text": text,
        "usageHint": hint,
        "style": {**_BODY, **style},
    }


def _column(key: str, children: list[str], gap: str = "12px", **style: str) -> dict:
    return {
        "id": key,
        "component": "MaterialColumn",
        "children": children,
        "align": "stretch",
        "style": {**_STACK, "gap": gap, **style},
    }


def _row(key: str, children: list[str], **style: str) -> dict:
    return {
        "id": key,
        "component": "MaterialRow",
        "children": children,
        "align": "start",
        "style": {**_ROW, **style},
    }


def _root(children: list[str]) -> dict:
    return _column(
        "root",
        children,
        "24px",
        width="100%",
        maxWidth="760px",
        paddingTop="8px",
        paddingBottom="8px",
        boxSizing="border-box",
    )


def _button(
    key: str,
    label: str,
    event: str,
    context: dict,
    appearance: str = "filled",
    **props: Any,
) -> dict:
    return {
        "id": key,
        "component": "MaterialButton",
        "label": label,
        "appearance": appearance,
        "action": {"event": {"name": event, "context": context}},
        "style": {"minHeight": "44px", "maxWidth": "100%"},
        **props,
    }


def _panel(
    key: str, title: str, children: list[str], *, expanded: bool = False
) -> dict:
    return {
        "id": key,
        "component": "MaterialExpansionPanel",
        "title": title,
        "ariaLabel": title,
        "expanded": expanded,
        "children": children,
        "style": dict(_PANEL),
    }


def _choice_options(items: list[dict], *, fallback_prefix: str) -> list[dict]:
    options: list[dict] = []
    for index, item in enumerate(items):
        value = str(item.get("revision") or item.get("id") or "").strip()
        if not value or any(option["value"] == value for option in options):
            continue
        label = str(
            item.get("display_name") or item.get("name") or item.get("label") or ""
        ).strip()
        if label.lower().endswith(".pptx"):
            label = label[:-5]
        options.append(
            {"value": value, "label": label or f"{fallback_prefix} {index + 1}"}
        )
    return options


def _select(key: str, label: str, options: list[dict], path: str, **props: Any) -> dict:
    return {
        "id": key,
        "component": "MaterialSelect",
        "label": label,
        "ariaLabel": label,
        "options": options,
        "value": {"path": path},
        "style": {"width": "100%", "minWidth": "0px"},
        **props,
    }


def _required(path: str) -> dict:
    return {
        "call": "required",
        "args": {"value": {"path": path}},
        "returnType": "boolean",
    }


def compose_intake(
    *,
    topic: str,
    audience: str,
    goal: str,
    brands: list[dict],
    styles: list[dict],
    templates: list[dict],
    slide_count: Any = DEFAULT_SLIDES,
    selected_brand_id: str = "",
    selected_style_id: str = "",
    selected_template_revision: str = "",
    cta_label: str = "Generate deck",
) -> str:
    """An editable brief with native selects and optional reference browsing."""
    admitted = {
        str(item["revision"]): item
        for item in templates
        if item.get("admitted") and item.get("revision")
    }
    shown = list(admitted.values())[:TEMPLATE_LIMIT]
    # Keep the selected template when a revision's search order changes.
    if (
        selected_template_revision in admitted
        and admitted[selected_template_revision] not in shown
    ):
        shown[-1:] = [admitted[selected_template_revision]]
    brand_options = _choice_options(brands, fallback_prefix="Brand")
    style_options = _choice_options(styles, fallback_prefix="Style")
    template_options = _choice_options(shown, fallback_prefix="Template")

    if selected_brand_id not in {option["value"] for option in brand_options}:
        selected_brand_id = brand_options[0]["value"] if brand_options else ""
    if selected_style_id not in {option["value"] for option in style_options}:
        selected_style_id = style_options[0]["value"] if style_options else ""
    if selected_template_revision not in {
        option["value"] for option in template_options
    }:
        selected_template_revision = ""

    root_children = ["masthead", "brief_form"]
    components = [
        _root(root_children),
        _column("masthead", ["title", "intro"], "8px"),
        _text("title", "Your presentation brief", "h4", **_HEADING),
        _text(
            "intro",
            "Set the story and the look. You can review and refine the deck after it's created.",
        ),
        _column("brief_form", ["topic", "brief_row", "goal"], "16px"),
        {
            "id": "topic",
            "component": "TextField",
            "label": "What is the presentation about?",
            "value": {"path": "/brief/topic"},
            "variant": "longText",
            "validationRegexp": r"[\s\S]*\S[\s\S]*",
        },
        _row("brief_row", ["audience", "slide_count"]),
        {
            "id": "audience",
            "component": "MaterialInput",
            "label": "Audience",
            "ariaLabel": "Audience",
            "value": {"path": "/brief/audience"},
            "style": {"flex": "2 1 220px", "minWidth": "0px"},
        },
        {
            "id": "slide_count",
            "component": "MaterialInput",
            "label": f"Slides (1-{MAX_SLIDES})",
            "ariaLabel": f"Number of slides, 1 to {MAX_SLIDES}",
            "type": "number",
            "value": {"path": "/brief/slide_count"},
            "validationRegexp": _slide_count_regexp(MAX_SLIDES),
            "checks": [
                {
                    "condition": {
                        "call": "regex",
                        "args": {
                            "value": {"path": "/brief/slide_count"},
                            "pattern": _slide_count_regexp(MAX_SLIDES),
                        },
                        "returnType": "boolean",
                    },
                    "message": f"Enter a whole number from 1 to {MAX_SLIDES}.",
                }
            ],
            "style": {"flex": "1 1 120px", "minWidth": "0px"},
        },
        {
            "id": "goal",
            "component": "TextField",
            "label": "Outcome and tone",
            "value": {"path": "/brief/goal"},
            "variant": "longText",
        },
    ]

    look_children = ["look_heading"]
    if template_options:
        look_children.extend(["template", "template_picker_note"])
        components.extend(
            [
                _select(
                    "template",
                    "Reference template",
                    [
                        {
                            "value": "",
                            "label": "Use brand & slide style"
                            if brand_options
                            else "Choose a reference template",
                        },
                        *template_options,
                    ],
                    "/brief/template_revision",
                ),
                _text(
                    "template_picker_note",
                    "A reference template overrides the brand system and slide style below."
                    if brand_options
                    else "Choose a reference template to set the colours, type and layout.",
                    "caption",
                ),
            ]
        )
    look_groups = []
    for key, heading, options, path in (
        ("brand", "Brand", brand_options, "/brief/brand_id"),
        ("style", "Slide style", style_options, "/brief/style_id"),
    ):
        if options:
            look_groups.append(f"{key}_group")
            components.extend(
                [
                    _select(
                        key,
                        heading,
                        options,
                        path,
                        disabled=_required("/brief/template_revision"),
                    ),
                    _column(f"{key}_group", [key], flex="1 1 240px"),
                ]
            )
    if look_groups:
        look_children.append("look_row")
        components.append(_row("look_row", look_groups))
    if template_options or look_groups:
        root_children.append("look_section")
        components.extend(
            [
                _text(
                    "look_heading",
                    "Visual direction",
                    "subtitle1",
                    fontSize="18px",
                    fontWeight="600",
                ),
                _column("look_section", look_children, "12px"),
            ]
        )

    if template_options:
        gallery = []
        for index, (template, option) in enumerate(
            zip(shown, template_options, strict=True)
        ):
            key = f"template_card_{index}"
            children = [f"template_name_{index}"]
            tokens = template.get("preview_image_tokens") or []
            if tokens and isinstance(tokens[0], str) and tokens[0].strip():
                children.insert(0, f"template_image_{index}")
                components.append(
                    {
                        "id": f"template_image_{index}",
                        "component": "MaterialImage",
                        "url": tokens[0],
                        "alt": f"{option['label']} reference preview",
                        "fit": "contain",
                        "width": "100%",
                        "aspectRatio": "16/9",
                        "borderRadius": "8px",
                    }
                )
            else:
                children.append(f"template_meta_{index}")
                components.append(
                    _text(f"template_meta_{index}", "Preview unavailable", "caption")
                )
            components.extend(
                [
                    _text(
                        f"template_name_{index}",
                        option["label"],
                        "body2",
                        fontWeight="500",
                    ),
                    _column(key, children, "8px", flex="1 1 280px"),
                ]
            )
            gallery.append(key)
        look_children.append("template_panel")
        components.extend(
            [
                _row("template_grid", gallery, gap="20px"),
                _panel(
                    "template_panel",
                    f"Browse reference templates ({len(gallery)})",
                    ["template_grid"],
                ),
            ]
        )

    root_children.append("action_bar")
    brief_context = {key: {"path": f"/brief/{key}"} for key in _BRIEF_KEYS}
    submission_context = {**brief_context, "request_id": {"path": "/request_id"}}
    action_children = ["generate", "action_note"]
    if not brand_options and not template_options:
        root_children.insert(-1, "catalog_unavailable")
        components.append(
            _text(
                "catalog_unavailable",
                "No brands or reference templates are available. Reload choices to try again.",
            )
        )
        action_children.insert(0, "reload")
        components.append(
            _button("reload", "Reload choices", "edit_brief", brief_context)
        )
    components.extend(
        [
            _row("action_bar", action_children, alignItems="center"),
            _button(
                "generate",
                cta_label,
                "generate_deck",
                submission_context,
                trailingIcon="arrow_forward",
                disabled={
                    "call": "not",
                    "args": {
                        "value": {
                            "call": "and",
                            "args": {
                                "values": [
                                    {
                                        "call": "areComponentsValid",
                                        "args": {
                                            "componentIds": ["topic", "slide_count"]
                                        },
                                        "returnType": "boolean",
                                    },
                                    {
                                        "call": "or",
                                        "args": {
                                            "values": [
                                                _required("/brief/brand_id"),
                                                _required("/brief/template_revision"),
                                            ]
                                        },
                                        "returnType": "boolean",
                                    },
                                ]
                            },
                            "returnType": "boolean",
                        }
                    },
                    "returnType": "boolean",
                },
            ),
            _text(
                "action_note",
                "Editable PowerPoint · Preview before you download",
                "caption",
                flex="1 1 200px",
            ),
        ]
    )
    return _surface(
        "pixelpitch_deck_intake",
        components,
        {
            "request_id": uuid.uuid4().hex,
            "brief": {
                "topic": topic,
                "audience": audience,
                "goal": goal,
                "slide_count": str(_clamp_slide_count(slide_count)),
                "brand_id": selected_brand_id,
                "style_id": selected_style_id,
                "template_revision": selected_template_revision,
            },
        },
    )


def _slide_title(titles: list, index: int) -> str:
    entry = titles[index] if index < len(titles) else None
    return (
        entry.strip()
        if isinstance(entry, str) and entry.strip()
        else f"Slide {index + 1}"
    )


def compose_result(result: dict, *, brief: dict) -> str:
    """A large first-slide reveal with downloadable output and guided revision."""
    title = str(result.get("title") or brief.get("topic") or "Your presentation")
    slide_count = int(result.get("slide_count") or brief.get("slide_count") or 0)
    brand = str(result.get("brand") or brief.get("brand_id") or "Custom")
    raw_titles = result.get("slide_titles")
    titles = raw_titles if isinstance(raw_titles, list) else []
    root_children = ["masthead"]
    components = [
        _root(root_children),
        _column("masthead", ["title", "meta"], "8px"),
        _text("title", title, "h4", **_HEADING),
        _text(
            "meta",
            f"{slide_count} editable {'slide' if slide_count == 1 else 'slides'} · {brand}",
        ),
    ]
    https_url = str(result.get("https_url") or "")
    if https_url.startswith("https://"):
        root_children.append("download_bar")
        download = _button(
            "download", "Download PowerPoint", "", {}, leadingIcon="download"
        )
        download["action"] = {
            "functionCall": {
                "call": "openUrl",
                "args": {"url": https_url},
                "returnType": "void",
            }
        }
        components.extend(
            [
                download,
                _text(
                    "download_note",
                    ".pptx · Editable in PowerPoint",
                    "caption",
                    flex="1 1 180px",
                ),
                _row(
                    "download_bar", ["download", "download_note"], alignItems="center"
                ),
            ]
        )
    else:
        root_children.append("download_unavailable")
        components.append(
            _text(
                "download_unavailable",
                "The download link is unavailable. Review the brief below to generate a new version.",
            )
        )

    previews = []
    for index, token in enumerate(
        result.get("preview_images") or result.get("preview_image_tokens") or []
    ):
        if not isinstance(token, str) or not token.strip():
            continue
        caption = _slide_title(titles, index)
        key = f"preview_card_{index}"
        components.extend(
            [
                {
                    "id": f"preview_image_{index}",
                    "component": "MaterialImage",
                    "url": token,
                    "alt": f"Slide {index + 1}: {caption}",
                    "fit": "contain",
                    "width": "100%",
                    "aspectRatio": "16/9",
                    "borderRadius": "8px",
                },
                _text(
                    f"preview_number_{index}",
                    f"{index + 1:02d}",
                    "caption",
                    fontWeight="600",
                ),
                _text(f"preview_caption_{index}", caption, "caption", flex="1 1 160px"),
                _row(
                    f"preview_label_{index}",
                    [f"preview_number_{index}", f"preview_caption_{index}"],
                    gap="8px",
                ),
                _column(
                    key, [f"preview_image_{index}", f"preview_label_{index}"], "8px"
                ),
            ]
        )
        previews.append(key)
    if previews:
        root_children.append(previews[0])
        if len(previews) > 1:
            root_children.append("more_previews")
            for item in components:
                if item["id"] in previews[1:]:
                    item["style"]["flex"] = "1 1 280px"
            components.extend(
                [
                    _row("preview_grid", previews[1:], gap="20px"),
                    _panel(
                        "more_previews",
                        f"More slide previews ({len(previews) - 1})",
                        ["preview_grid"],
                    ),
                ]
            )
        if slide_count > len(previews):
            root_children.append("preview_note")
            components.append(
                _text(
                    "preview_note",
                    f"Showing {len(previews)} of {slide_count} slides. The PowerPoint contains the full deck.",
                    "caption",
                )
            )
    else:
        root_children.append("preview_unavailable")
        components.append(
            _text(
                "preview_unavailable",
                "Slide previews are unavailable. Download the PowerPoint to review your deck."
                if https_url.startswith("https://")
                else "Slide previews are unavailable for this version.",
            )
        )

    if any(isinstance(entry, str) and entry.strip() for entry in titles):
        rows = []
        for index in range(max(slide_count, len(titles))):
            key = f"outline_row_{index}"
            rows.append(key)
            components.append(
                _text(key, f"{index + 1:02d} · {_slide_title(titles, index)}")
            )
        root_children.append("outline_card")
        components.extend(
            [
                _column("outline_list", rows, "12px"),
                _panel(
                    "outline_card", f"Full deck outline ({len(rows)})", ["outline_list"]
                ),
            ]
        )

    root_children.append("revise_section")
    components.extend(
        [
            _column(
                "revise_section",
                [
                    "revise_heading",
                    "revise_note",
                    "revision_choice",
                    "revision_detail",
                    "revise_actions",
                ],
                "12px",
            ),
            _text(
                "revise_heading",
                "Refine this deck",
                "subtitle1",
                fontSize="18px",
                fontWeight="600",
            ),
            _text(
                "revise_note",
                "Choose a direction or add your own changes. Review the updated brief before generating a new version.",
            ),
            _select(
                "revision_choice",
                "What would you like to change?",
                [
                    {"value": "", "label": "Keep the current direction"},
                    {"value": "punchier", "label": "Make titles punchier"},
                    {"value": "more_aids", "label": "Add more visual explanations"},
                    {
                        "value": "new_expressions",
                        "label": "Try a different visual style",
                    },
                ],
                "/revision/instruction",
            ),
            {
                "id": "revision_detail",
                "component": "TextField",
                "label": "Specific changes (optional)",
                "value": {"path": "/revision/detail"},
                "variant": "longText",
            },
            _button(
                "revise",
                "Review changes",
                "revise_deck",
                {
                    "instruction": {"path": "/revision/instruction"},
                    "detail": {"path": "/revision/detail"},
                },
                "tonal",
                trailingIcon="arrow_forward",
            ),
            _button("new", "Start a new deck", "new_deck", {}, "text"),
            _row("revise_actions", ["revise", "new"], alignItems="center"),
        ]
    )
    return _surface(
        "pixelpitch_deck_result",
        components,
        {"revision": {"instruction": "", "detail": ""}},
    )


def compose_job(job: dict) -> str:
    """A completed chat turn with a saved job, not a claim of a finished deck."""
    from app.a2a_jobs import job_summary

    title, detail = job_summary(job)
    status = job["status"]
    active = status in {"queued", "running", "cancelling"}
    elapsed = max(0, int(job["elapsed"]))
    age = max(0, int(time.time() - job["updated"]))
    children = ["job_heading", "job_topic", "job_detail", "job_timing"]
    components = [
        _root(children),
        _text("job_heading", title, "h5", **_HEADING),
        _text("job_topic", str(job["brief"]["topic"]), "body1"),
        _text("job_detail", detail),
        _text(
            "job_timing",
            f"Elapsed {elapsed // 60}m {elapsed % 60:02d}s · Last activity {age}s ago",
            "caption",
        ),
    ]
    if status == "running" and age >= 30:
        children.append("quiet")
        components.append(
            _text(
                "quiet",
                "No new milestone has been recorded recently. The worker lease is still active; this is not a completion estimate.",
            )
        )
    slides = job.get("slides") or []
    if slides:
        children.append("draft_titles")
        components.append(
            _text(
                "draft_titles",
                "Drafted so far\n"
                + "\n".join(
                    f"{slide['slide_index'] + 1}. {slide['title']}" for slide in slides
                ),
            )
        )
    context = {"job_id": job["id"]}
    buttons = []
    if active:
        buttons.append("check_job")
        components.append(
            _button(
                "check_job",
                "Check progress",
                "check_deck",
                context,
                leadingIcon="refresh",
            )
        )
        if status != "cancelling":
            buttons.append("cancel_job")
            components.append(
                _button("cancel_job", "Cancel deck", "cancel_deck", context, "outlined")
            )
    else:
        buttons.append("review_job")
        components.append(
            _button("review_job", "Review brief", "edit_brief", job["brief"])
        )
    children.extend(["job_actions", "job_note", "job_reference"])
    components.extend(
        [
            _row("job_actions", buttons, gap="12px"),
            _text(
                "job_note",
                "This card is a snapshot. Check progress to refresh it; you do not need to keep this chat turn open."
                if active
                else "Your saved brief is available for another attempt.",
                "caption",
            ),
            _text("job_reference", f"Job {job['id']}", "caption"),
        ]
    )
    return _surface(f"pixelpitch_job_{job['id']}", components, {})


def compose_error(title: str, detail: str, *, brief: dict | None = None) -> str:
    """An actionable failure that can reopen the brief without losing edits."""
    return _surface(
        "pixelpitch_error",
        [
            _root(["error_body", "recovery"]),
            _column("error_body", ["title", "detail"], "8px"),
            _text("title", title, "h5", fontSize="20px", fontWeight="600"),
            _text("detail", detail),
            _button("recovery", "Edit brief", "edit_brief", brief or {}, "tonal"),
        ],
        {},
    )
