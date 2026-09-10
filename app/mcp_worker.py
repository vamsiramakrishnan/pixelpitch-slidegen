"""Independent worker consuming durable MCP jobs through the native agent."""

from __future__ import annotations

import asyncio
import html as html_module
import logging
import re
import time
import uuid
from pathlib import Path

from app.mcp_jobs import JobStore
from app.mcp_store import DeckStore

logger = logging.getLogger(__name__)
_DRAFT = re.compile(r"__SLIDE_DRAFT_(\d+)__")
_EXPORT = re.compile(r"__SLIDE_PREVIEW_IMAGE_(\d+)__")


def export_previews(fragments: dict):
    """Store image payloads through the slide store, never in Firestore jobs."""
    for token, image in sorted(fragments.items()):
        match = _EXPORT.fullmatch(token)
        if (
            not match
            or not isinstance(image, str)
            or not image.startswith("data:image/")
        ):
            continue
        index = int(match[1]) - 1
        title = fragments.get(f"__SLIDE_DRAFT_TITLE_{index}__") or f"Slide {index + 1}"
        markup = (
            "<!doctype html><html><head><style>body{margin:0;width:1280px;height:720px;"
            "background:white}img{width:100%;height:100%;object-fit:contain}</style></head><body>"
            f'<img alt="{html_module.escape(title, quote=True)}" '
            f'src="{html_module.escape(image, quote=True)}"></body></html>'
        )
        if len(markup) <= 2_000_000:
            yield {"index": index, "title": title, "html": markup, "kind": "export"}


PHASES = {
    "intake": "Checking your brief",
    "brief": "Checking your brief",
    "reference": "Preparing your reference template",
    "authoring": "Drafting your slides",
    "conversion": "Creating the PowerPoint",
    "quality": "Checking the exported deck",
    "delivery": "Preparing your download",
}


class DeckRunFailed(Exception):
    """A bounded, user-facing failure already scrubbed by the native agent."""


async def generate(
    job: dict, store: DeckStore, worker: str, *, persist_previews: bool = False
) -> dict:
    from google.adk.agents.run_config import RunConfig, StreamingMode
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from app.agent import root_agent
    from app.deck_run import _title_of
    from app.pixelpitch_agent import _user_facing_error
    from app.tools import clear_cached_fragments, get_cached_fragments
    from app.turns import encode_action_turn

    sessions = InMemorySessionService()
    session = await sessions.create_session(
        app_name="pixelpitch_mcp", user_id=job["workspace"], session_id=job["id"]
    )
    runner = Runner(
        agent=root_agent.model_copy(update={"queue_decks": False}),
        session_service=sessions,
        app_name="pixelpitch_mcp",
    )
    revisions: dict[int, str] = {}
    sequence = 0
    final_text = ""
    try:
        async for event in runner.run_async(
            user_id=job["workspace"],
            session_id=session.id,
            new_message=types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=encode_action_turn("generate_deck", job["brief"]),
                    )
                ],
            ),
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        ):
            info = (event.custom_metadata or {}).get("pixelpitch", {})
            stage = str(info.get("stage") or "brief")
            # Expose observed milestones, never tool names or thought text.
            progress = {
                "stage": stage,
                "title": PHASES.get(stage, "Preparing your deck"),
                "current": info.get("current"),
                "total": info.get("total"),
            }
            if event.partial and info.get("kind") == "progress":
                await asyncio.to_thread(store.record, job["id"], worker, progress)
            fragments = dict(get_cached_fragments(f"session:{session.id}"))
            for token, html in fragments.items():
                match = _DRAFT.fullmatch(token)
                if match and isinstance(html, str):
                    index = int(match[1])
                    if revisions.get(index) == html:
                        continue
                    revisions[index] = html
                    sequence += 1
                    await asyncio.to_thread(
                        store.record,
                        job["id"],
                        worker,
                        progress,
                        slide={
                            "index": index,
                            "title": fragments.get(f"__SLIDE_DRAFT_TITLE_{index}__")
                            or _title_of(html, f"Slide {index + 1}"),
                            "html": html,
                            "revision": sequence,
                        },
                    )
            if not event.partial and event.content:
                final_text = "\n".join(
                    part.text for part in event.content.parts or [] if part.text
                )
        stored = await sessions.get_session(
            app_name="pixelpitch_mcp", user_id=job["workspace"], session_id=session.id
        )
        result = stored.state.get("pixelpitch_result") if stored else None
        if not result:
            detail = final_text.split("<a2ui-json>", 1)[0]
            raise DeckRunFailed(
                _user_facing_error(
                    detail,
                    "The deck could not be produced. Review your brief and try again.",
                )
            )
        fragments = get_cached_fragments(f"session:{session.id}")
        for preview in export_previews(fragments):
            sequence += 1
            await asyncio.to_thread(
                store.record,
                job["id"],
                worker,
                {"stage": "delivery", "title": PHASES["delivery"]},
                slide=preview | {"revision": sequence},
            )
        if persist_previews:
            # The A2A API is a separate process. Persist real image data, not
            # tokens that only resolve inside this worker's fragment cache.
            fragments = get_cached_fragments(f"session:{session.id}")
            result = dict(result)
            result["preview_images"] = [
                value
                for key, value in sorted(fragments.items())
                if key.startswith("__SLIDE_PREVIEW_IMAGE_")
                and isinstance(value, str)
                and value.startswith("data:image/")
            ][:6]
        return result
    finally:
        clear_cached_fragments(f"session:{session.id}")
        await runner.close()


async def run_job(
    job: dict,
    store: DeckStore,
    worker: str,
    pipeline=generate,
    *,
    heartbeat_seconds=0.5,
    timeout_seconds=1800,
) -> None:
    task = asyncio.create_task(pipeline(job, store, worker))
    deadline = time.monotonic() + timeout_seconds
    try:
        while not task.done():
            if time.monotonic() >= deadline:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise DeckRunFailed(
                    "Generation reached its time limit. Review the brief and try a smaller deck."
                )
            if not await asyncio.to_thread(store.heartbeat, job["id"], worker):
                task.cancel()
                break
            await asyncio.wait({task}, timeout=heartbeat_seconds)
        result = await task
        await asyncio.to_thread(
            store.finish, job["id"], worker, "completed", result=result
        )
    except asyncio.CancelledError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        try:
            if asyncio.current_task().cancelling():
                await asyncio.to_thread(
                    store.finish,
                    job["id"],
                    worker,
                    "interrupted",
                    error="The worker restarted. Review your brief and start a new attempt.",
                )
            else:
                await asyncio.to_thread(store.finish, job["id"], worker, "cancelled")
        finally:
            # Shutdown must still stop the consumer if shared storage is unavailable.
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
    except DeckRunFailed as error:
        await asyncio.to_thread(
            store.finish, job["id"], worker, "failed", error=str(error)
        )
    except Exception:
        logger.exception("MCP deck job failed: %s", job["id"])
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(
            store.finish,
            job["id"],
            worker,
            "failed",
            error="The deck could not be produced. Review your brief and retry; the worker log has the details.",
        )


async def work(path: Path) -> None:
    store, worker = JobStore(path), uuid.uuid4().hex
    while True:
        job = store.claim(worker)
        if job:
            await run_job(job, store, worker)
        else:
            await asyncio.sleep(0.25)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(work(args.db))
