# Copyright 2026 Google LLC

"""ADK transport agent with deterministic UI and native progress events."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Awaitable
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.tools import ToolContext
from google.genai import types

from app.a2ui_composer import compose_error, compose_intake, compose_job, compose_result
from app.progress import (
    ProgressUpdate,
    clear_session_progress,
    display_percent,
    get_session_progress_updates,
)
from app.tools import (
    DEFAULT_SLIDES,
    MAX_SLIDES,
    generate_deck,
    list_brands,
    list_styles,
    list_templates,
    use_reference_deck,
)
from app.turns import ActionTurn, decode_action_turn

logger = logging.getLogger(__name__)

_STATE_BRIEF = "pixelpitch_brief"
_STATE_RESULT = "pixelpitch_result"

_SLIDE_COUNT_PATTERN = re.compile(r"\b(\d{1,3})[\s-]*(?:slide|page)s?\b", re.IGNORECASE)


def _requested_slide_count(text: str) -> int | None:
    """The size asked for in prose, so "a 20 slide deck" arrives as 20.

    Only the field is authoritative. This seeds it, because a brief that says
    twenty and a form that says six is the kind of disagreement a user reads as
    the agent ignoring them.
    """
    match = _SLIDE_COUNT_PATTERN.search(text)
    if not match:
        return None
    count = int(match.group(1))
    return count if 1 <= count <= MAX_SLIDES else None


# A run of short quoted tokens, or a stack trace. Neither is a sentence.
_MACHINE_NOISE = re.compile(r"(?:'[^']{0,32}',\s*){4,}|Traceback \(most recent")
_MAX_ERROR_CHARS = 240


def _user_facing_error(raw: Any, fallback: str) -> str:
    """A sentence a person can act on, never a slice of a machine's output.

    Upstream failures arrive as whatever the failing process last wrote, and
    this is the final hop before a human reads it. One of them put two
    thousand characters of font glyph names in the chat window under the
    heading "Deck generation stopped". Callers log the raw text; what crosses
    into the transcript is bounded, single-line, and dropped entirely when it
    still looks like a data dump. Losing detail to the log beats showing
    someone a glyph table and calling it an explanation.
    """
    text = " ".join(str(raw or "").split())
    if not text or _MACHINE_NOISE.search(text):
        return fallback
    if len(text) > _MAX_ERROR_CHARS:
        return text[: _MAX_ERROR_CHARS - 3].rstrip() + "..."
    return text


def _content_text(content: types.Content | None) -> str:
    if content is None:
        return ""
    return "\n".join(
        part.text for part in (content.parts or []) if isinstance(part.text, str)
    ).strip()


def _scalar(value: Any, default: str = "") -> str:
    if isinstance(value, list):
        value = value[0] if value else default
    if value is None:
        value = default
    return str(value).strip()


def _progress_text(update: ProgressUpdate, *, invocation_id: str) -> str:
    """One progress line. Text, because GE renders A2UI only at a turn
    boundary; interim DataParts reach the Thinking panel as garbage."""
    pct = display_percent(invocation_id, update)
    count = ""
    if update.current is not None and update.total:
        count = f" · {update.current}/{update.total}"
    detail = update.detail
    if update.slide_title:
        detail = f"{detail} · {update.slide_title}" if detail else update.slide_title
    heading = f"{update.title or update.stage}{count} · {pct}%"
    return f"{heading}\n{detail}" if detail else heading


class PixelpitchAgent(BaseAgent):
    """One ADK turn owner; Antigravity remains the deck reasoning system."""

    queue_decks: bool = False

    def _event(
        self,
        ctx: InvocationContext,
        text: str,
        *,
        partial: bool,
        actions: EventActions | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Event:
        return Event(
            author=self.name,
            invocationId=ctx.invocation_id,
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=text)]
            ),
            partial=partial,
            turnComplete=not partial,
            actions=actions or EventActions(),
            customMetadata=metadata,
        )

    def _status_event(
        self, ctx: InvocationContext, title: str, detail: str, *, stage: str
    ) -> Event:
        return self._event(
            ctx,
            f"{title}\n{detail}" if detail else title,
            partial=True,
            metadata={
                "pixelpitch": {
                    "kind": "progress",
                    "stage": stage,
                    "title": title,
                    "detail": detail,
                }
            },
        )

    def _progress_event(self, ctx: InvocationContext, update: ProgressUpdate) -> Event:
        return self._event(
            ctx,
            _progress_text(update, invocation_id=ctx.invocation_id),
            partial=True,
            metadata={
                "pixelpitch": {
                    "kind": "progress",
                    "stage": update.stage,
                    "title": update.title,
                    "detail": update.detail,
                    "stage_index": update.stage_index,
                    "stage_total": update.stage_total,
                    "current": update.current,
                    "total": update.total,
                    "slide_index": update.slide_index,
                    "slide_title": update.slide_title,
                }
            },
        )

    async def _build_intake(
        self,
        ctx: InvocationContext,
        *,
        user_text: str,
        brief_override: dict[str, Any] | None = None,
        cta_label: str = "Generate deck",
    ) -> Event:
        event_actions = EventActions()
        tool_context = ToolContext(ctx, event_actions=event_actions)
        prior = brief_override or {}
        topic = _scalar(prior["topic"]) if "topic" in prior else user_text
        audience = _scalar(prior.get("audience"), "Leadership team")
        goal = _scalar(prior.get("goal"), "Clear, confident, and on-brand")

        # Styles load unfiltered, a full page of them. The same lesson as the
        # templates below: a guessed query narrows the catalog before the user
        # has said anything. "professional" matched one style of forty-nine, and
        # compose_intake auto-selects the first option, so the form's second
        # required question was a single pre-selected chip dressed as a choice.
        # 12 is the ceiling list_styles accepts.
        #
        # Templates load on every intake. Gating them on the words "template",
        # "phoenix" or "retail" meant a deck "based on the 100Y Example Retail
        # deck" surfaced none, and the hardcoded "phoenix retail" query ranked
        # the wrong ones when it did fire. The user's own words are what the
        # ranker was built for. The call joins the gather so it costs no extra
        # wall clock.
        brands_result, styles_result, templates_result = await asyncio.gather(
            asyncio.to_thread(list_brands, tool_context=tool_context),
            asyncio.to_thread(
                list_styles,
                page=1,
                page_size=12,
                tool_context=tool_context,
            ),
            asyncio.to_thread(
                list_templates, query=user_text, tool_context=tool_context
            ),
        )
        brands = (
            brands_result.get("brands") if brands_result.get("status") == "ok" else []
        )
        styles = (
            styles_result.get("styles") if styles_result.get("status") == "ok" else []
        )
        templates = (
            templates_result.get("templates")
            if templates_result.get("status") == "ok"
            else []
        )

        try:
            wire = compose_intake(
                topic=topic,
                audience=audience,
                goal=goal,
                brands=brands or [],
                styles=styles or [],
                templates=templates or [],
                slide_count=(
                    prior.get("slide_count")
                    or _requested_slide_count(user_text)
                    or DEFAULT_SLIDES
                ),
                selected_brand_id=_scalar(prior.get("brand_id")),
                selected_style_id=_scalar(prior.get("style_id")),
                selected_template_revision=_scalar(prior.get("template_revision")),
                cta_label=cta_label,
            )
        except Exception as error:
            logger.exception("intake A2UI composition failed")
            return self._event(
                ctx,
                f"I could not compose the deck brief: {error}",
                partial=False,
                actions=event_actions,
            )
        return self._event(
            ctx,
            "Your editable deck brief is ready.\n" + wire,
            partial=False,
            actions=event_actions,
        )

    async def _yield_progress_while(
        self,
        ctx: InvocationContext,
        awaitable: Awaitable[dict],
        result_box: dict[str, Any],
        *,
        after_sequence: int,
    ) -> AsyncGenerator[Event, None]:
        """Project the synchronous pipeline's real milestones into ADK events."""
        task = asyncio.create_task(awaitable)
        last_sequence = after_sequence
        try:
            while not task.done():
                for update in get_session_progress_updates(
                    ctx.session.id, last_sequence
                ):
                    yield self._progress_event(ctx, update)
                    last_sequence = update.sequence
                await asyncio.wait({task}, timeout=0.2)
            result_box["result"] = await task
            # A hard final drain preserves updates published just before the
            # worker future completed.
            for update in get_session_progress_updates(ctx.session.id, last_sequence):
                yield self._progress_event(ctx, update)
                last_sequence = update.sequence
        finally:
            result_box["last_sequence"] = last_sequence
            if not task.done():
                task.cancel()

    async def _generate(
        self, ctx: InvocationContext, action: ActionTurn
    ) -> AsyncGenerator[Event, None]:
        values = action.context
        topic = _scalar(values.get("topic"))
        audience = _scalar(values.get("audience"), "Leadership team")
        goal = _scalar(values.get("goal"), "Clear, confident, and on-brand")
        brand_id = _scalar(values.get("brand_id"))
        style_id = _scalar(values.get("style_id"))
        template_revision = _scalar(values.get("template_revision"))
        try:
            slide_count = int(_scalar(values.get("slide_count"), str(DEFAULT_SLIDES)))
        except ValueError:
            slide_count = 0

        validation_error = ""
        if not topic:
            validation_error = "Add a topic before generating."
        elif not 1 <= slide_count <= MAX_SLIDES:
            validation_error = f"Slide count must be between 1 and {MAX_SLIDES}."
        elif not template_revision and not brand_id:
            validation_error = "Choose a brand or a pinned template revision."
        if validation_error:
            try:
                wire = compose_error(
                    "The brief needs one adjustment", validation_error, brief=values
                )
                text = validation_error + "\n" + wire
            except Exception:
                text = validation_error
            yield self._event(ctx, text, partial=False)
            return

        clear_session_progress(ctx.session.id)
        event_actions = EventActions()
        tool_context = ToolContext(ctx, event_actions=event_actions)
        yield self._status_event(
            ctx,
            "Deck request accepted",
            "Validating the pinned design direction before authoring begins",
            stage="intake",
        )

        last_sequence = 0
        if template_revision:
            template_result = await asyncio.to_thread(
                list_templates, query="", tool_context=tool_context
            )
            template = next(
                (
                    item
                    for item in template_result.get("templates") or []
                    if item.get("revision") == template_revision
                ),
                None,
            )
            if template is None:
                detail = "This reference template is no longer available. Edit the brief to choose another."
                yield self._event(
                    ctx,
                    detail
                    + "\n"
                    + compose_error("Choose another template", detail, brief=values),
                    partial=False,
                    actions=event_actions,
                )
                return
            yield self._status_event(
                ctx,
                "Template revision pinned",
                str(template.get("name") or template_revision),
                stage="reference",
            )
            reference_box: dict[str, Any] = {}
            async for event in self._yield_progress_while(
                ctx,
                use_reference_deck(
                    source_url=str(template.get("gs_uri") or ""),
                    template_revision=template_revision,
                    tool_context=tool_context,
                ),
                reference_box,
                after_sequence=last_sequence,
            ):
                yield event
            last_sequence = int(reference_box.get("last_sequence") or 0)
            reference_result = reference_box.get("result") or {}
            if reference_result.get("status") != "ok":
                error = _user_facing_error(
                    reference_result.get("error"), "Template ingestion failed."
                )
                try:
                    error_wire = compose_error(
                        "Template could not be prepared", error, brief=values
                    )
                    error = error + "\n" + error_wire
                except Exception:
                    pass
                yield self._event(ctx, error, partial=False, actions=event_actions)
                return
            brand_id = "reference"
            style_id = ""
        else:
            catalog = await asyncio.to_thread(list_brands, tool_context=tool_context)
            valid_brand_ids = {
                item.get("id") for item in catalog.get("brands") or [] if item.get("id")
            }
            if brand_id not in valid_brand_ids:
                detail = "This brand is no longer available. Edit the brief to choose another."
                yield self._event(
                    ctx,
                    detail
                    + "\n"
                    + compose_error("Choose another brand", detail, brief=values),
                    partial=False,
                    actions=event_actions,
                )
                return
            if style_id:
                style_page = await asyncio.to_thread(
                    list_styles,
                    query=style_id,
                    page=1,
                    page_size=12,
                    tool_context=tool_context,
                )
                if not any(
                    item.get("id") == style_id
                    for item in style_page.get("styles") or []
                ):
                    detail = "This slide style is no longer available. Edit the brief to choose another."
                    yield self._event(
                        ctx,
                        detail
                        + "\n"
                        + compose_error(
                            "Choose another slide style", detail, brief=values
                        ),
                        partial=False,
                        actions=event_actions,
                    )
                    return

        brief = {
            "topic": topic[:4000],
            "audience": audience[:1000],
            "goal": goal[:2000],
            "slide_count": slide_count,
            "brand_id": brand_id,
            "style_id": style_id,
            "template_revision": template_revision,
        }
        result_box: dict[str, Any] = {}
        async for event in self._yield_progress_while(
            ctx,
            generate_deck(
                topic=brief["topic"],
                audience=brief["audience"],
                goal=brief["goal"],
                slide_count=brief["slide_count"],
                brand_id=brief["brand_id"],
                style_id=brief["style_id"],
                tool_context=tool_context,
            ),
            result_box,
            after_sequence=last_sequence,
        ):
            yield event
        result = result_box.get("result") or {}
        clear_session_progress(ctx.session.id)

        if result.get("status") != "ok":
            logger.error("deck generation failed: %s", result.get("error"))
            error = _user_facing_error(
                result.get("error"),
                "The deck could not be produced and nothing was delivered.",
            )
            try:
                error = (
                    error
                    + "\n"
                    + compose_error("Deck generation stopped", error, brief=values)
                )
            except Exception:
                pass
            yield self._event(ctx, error, partial=False, actions=event_actions)
            return

        event_actions.state_delta[_STATE_BRIEF] = brief
        event_actions.state_delta[_STATE_RESULT] = {
            key: result.get(key)
            for key in (
                "gs_uri",
                "https_url",
                "slide_count",
                "slide_titles",
                "brand",
                "template_bundle_id",
            )
            if result.get(key) is not None
        }
        try:
            wire = compose_result(result, brief=brief)
            text = "Your editable deck is ready.\n" + wire
        except Exception as error:
            logger.exception("result A2UI composition failed")
            text = (
                "The deck was generated, but its result surface could not be "
                f"composed: {error}. Download: {result.get('https_url') or result.get('gs_uri')}"
            )
        yield self._event(
            ctx,
            text,
            partial=False,
            actions=event_actions,
            metadata={"pixelpitch": {"kind": "result", "status": "ok"}},
        )

    async def _job_turn(self, ctx: InvocationContext, action: ActionTurn) -> Event:
        from pydantic import ValidationError

        from app.a2a_jobs import handle_action, job_summary

        try:
            job = await asyncio.to_thread(
                handle_action,
                app_name=ctx.app_name,
                user_id=ctx.user_id,
                session_id=ctx.session.id,
                name=action.name,
                values=action.context,
            )
        except PermissionError:
            return self._event(
                ctx,
                "This deck is unavailable in this conversation. Open the conversation where you submitted it.",
                partial=False,
            )
        except (ValueError, ValidationError) as error:
            detail = (
                "Check the topic, slide count, and design selection, then submit again."
                if isinstance(error, ValidationError)
                else str(error)
            )
            return self._event(
                ctx,
                detail
                + "\n"
                + compose_error(
                    "The deck request needs attention", detail, brief=action.context
                ),
                partial=False,
            )
        except Exception:
            logger.exception("A2A deck queue unavailable")
            return self._event(
                ctx,
                "The saved deck queue is unavailable. No automatic retry was made. Check progress before submitting again.",
                partial=False,
            )
        if job is None:
            return self._event(
                ctx,
                "There is no saved deck job in this conversation yet.",
                partial=False,
            )

        actions = EventActions(
            state_delta={"pixelpitch_job_id": job["id"], _STATE_BRIEF: job["brief"]}
        )
        if job["status"] == "completed" and job.get("result"):
            actions.state_delta[_STATE_RESULT] = {
                key: value
                for key, value in job["result"].items()
                if key != "preview_images"
            }
            text = "Your editable deck is ready.\n" + compose_result(
                job["result"], brief=job["brief"]
            )
        else:
            actions.state_delta[_STATE_RESULT] = None
            title, detail = job_summary(job)
            prefix = (
                "A deck is already running in this conversation. Showing that job; no second deck was submitted.\n"
                if job.get("submission_conflict")
                else ""
            )
            text = prefix + title + ". " + detail + "\n" + compose_job(job)
        logger.info(
            "a2a_job_response job=%s status=%s action=%s",
            job["id"],
            job["status"],
            action.name,
        )
        return self._event(
            ctx,
            text,
            partial=False,
            actions=actions,
            metadata={
                "pixelpitch": {
                    "kind": "job",
                    "job_id": job["id"],
                    "status": job["status"],
                }
            },
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        text = _content_text(ctx.user_content)
        action = decode_action_turn(text)

        if self.queue_decks:
            if not action and re.fullmatch(
                r"(?:status|check progress|check deck|is (?:it|my deck) ready|where is my deck)[?.!\s]*",
                text,
                re.IGNORECASE,
            ):
                action = ActionTurn("check_deck", {})
            if action and action.name in {"generate_deck", "check_deck", "cancel_deck"}:
                yield await self._job_turn(ctx, action)
                return

        if action and action.name == "generate_deck":
            async for event in self._generate(ctx, action):
                yield event
            return

        if action and action.name == "new_deck":
            yield self._status_event(
                ctx,
                "Starting a new deck",
                "Loading a fresh brief and available visual systems",
                stage="intake",
            )
            event = await self._build_intake(ctx, user_text="")
            event.actions.state_delta[_STATE_BRIEF] = None
            event.actions.state_delta[_STATE_RESULT] = None
            yield event
            return

        prior = dict(ctx.session.state.get(_STATE_BRIEF) or {})
        if action and action.name == "edit_brief":
            draft = {**prior, **action.context}
            yield await self._build_intake(
                ctx,
                user_text=_scalar(draft.get("topic")),
                brief_override=draft,
            )
            return

        if action and action.name == "revise_deck":
            if not prior:
                yield self._event(
                    ctx,
                    "There is no completed deck in this session to revise.",
                    partial=False,
                )
                return
            instruction = _scalar(action.context.get("instruction"))
            instruction_text = {
                "punchier": "Use punchier, shorter slide titles.",
                "more_aids": "Add more meaningful visual aids and reduce prose.",
                "new_expressions": "Explore a different visual expression while preserving the story.",
            }.get(instruction, instruction)
            detail = _scalar(action.context.get("detail"))
            instruction_text = " ".join(
                part for part in (instruction_text, detail) if part
            )
            prior["goal"] = f"{prior.get('goal', '')} {instruction_text}".strip()
            yield self._status_event(
                ctx,
                "Preparing a new revision",
                instruction_text or "Reopening your current brief",
                stage="revision",
            )
            yield await self._build_intake(
                ctx,
                user_text="",
                brief_override=prior,
                cta_label="Generate revised deck",
            )
            return

        if action:
            yield self._event(
                ctx,
                f"Unsupported Pixelpitch action: {action.name}",
                partial=False,
            )
            return

        yield self._status_event(
            ctx,
            "Preparing your deck brief",
            "Loading trusted brand, style, and template options",
            stage="intake",
        )
        if prior:
            # What the user just typed is the brief they want now. Folding it
            # into `goal` and keeping the old `topic` handed back the previous
            # deck's subject, and `goal` grew by one "Follow-up request:" clause
            # every turn. The form is editable, so a wrong guess costs an edit;
            # the old behaviour gave no way to change the topic at all.
            prior["topic"] = text
            yield await self._build_intake(
                ctx,
                user_text=text,
                brief_override=prior,
                cta_label="Generate revised deck",
            )
        else:
            yield await self._build_intake(ctx, user_text=text)
