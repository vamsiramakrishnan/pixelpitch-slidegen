"""Generate and download one real deck through a running loopback MCP preview.

This deliberately calls the configured live model and renderer. It can incur
normal generation charges. It never deploys or changes cloud registration.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def smoke(base: str, output: Path, timeout: int):
    parsed = urlsplit(base)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.path
        or parsed.username
    ):
        raise ValueError(
            "Use an explicit loopback preview origin, e.g. http://127.0.0.1:18093"
        )
    output.mkdir(parents=True, exist_ok=True)
    async with streamable_http_client(f"{base}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            started = time.monotonic()
            opened = await session.call_tool(
                "open_pixelpitch", {"topic": "Pixelpitch MCP release test"}
            )
            if opened.isError:
                raise RuntimeError("The workspace could not be opened")
            connection = opened.meta["pixelpitch"]
            timing = {"workspace_seconds": round(time.monotonic() - started, 3)}

            async def call(name, **args):
                result = await session.call_tool(name, {**connection, **args})
                if result.isError:
                    raise RuntimeError(f"{name} failed; inspect the server log")
                return result.structuredContent

            submitted = await call(
                "start_deck",
                request_id=uuid.uuid4().hex,
                brief={
                    "topic": "Pixelpitch MCP release test. Create exactly one slide showing the flow: brief, live drafts, PowerPoint. Use these labels only, no invented statistics or research.",
                    "audience": "Product team",
                    "goal": "A clear visual explanation of progressive deck generation",
                    "slide_count": 1,
                    "brand_id": "stripe",
                },
            )
            job = submitted["job"]
            timing["submission_seconds"] = round(time.monotonic() - started, 3)
            print(json.dumps(timing), flush=True)
            last_state = None
            try:
                while job["status"] in ("queued", "running", "cancelling"):
                    if time.monotonic() - started >= timeout:
                        raise TimeoutError(
                            "The live deck did not finish within the smoke-test deadline"
                        )
                    state = (
                        job["status"],
                        job["progress"].get("stage"),
                        len(job["slides"]),
                    )
                    if state != last_state:
                        print(
                            json.dumps(
                                {
                                    "status": state[0],
                                    "stage": state[1],
                                    "drafts": state[2],
                                    "elapsed_seconds": round(
                                        time.monotonic() - started, 1
                                    ),
                                }
                            ),
                            flush=True,
                        )
                        last_state = state
                    if job["slides"] and "first_draft_seconds" not in timing:
                        timing["first_draft_seconds"] = round(
                            time.monotonic() - started, 3
                        )
                        draft = await call("deck_slide", job_id=job["id"], index=0)
                        (output / "draft.html").write_text(
                            draft["html"], encoding="utf-8"
                        )
                    await asyncio.sleep(2)
                    job = (await call("deck_status", job_id=job["id"]))["job"]
            except BaseException:
                await call("cancel_deck", job_id=job["id"])
                raise

            report = {
                **timing,
                "status": job["status"],
                "total_seconds": round(time.monotonic() - started, 3),
                "drafts": len(job["slides"]),
            }
            (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
            if job["status"] != "completed":
                raise RuntimeError(job["error"] or "Live generation did not complete")
            url = (job["result"] or {}).get("https_url")
            if (
                not url
                or urlsplit(url).scheme != "https"
                or urlsplit(url).hostname != "storage.googleapis.com"
            ):
                raise RuntimeError(
                    "No expected HTTPS Google Storage download was returned"
                )
            async with httpx.AsyncClient(follow_redirects=False, timeout=60) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    raise RuntimeError(
                        f"The signed download returned HTTP {response.status_code}"
                    )
                content = response.content
            with zipfile.ZipFile(io.BytesIO(content)) as deck:
                assert "[Content_Types].xml" in deck.namelist()
                slides = [
                    name
                    for name in deck.namelist()
                    if name.startswith("ppt/slides/slide") and name.endswith(".xml")
                ]
                assert len(slides) == 1, "Expected a one-slide PowerPoint"
                assert b"<a:t>" in deck.read(slides[0]), "The slide has no native text"
            (output / "deck.pptx").write_bytes(content)
            print(
                json.dumps(
                    {**report, "pptx_bytes": len(content), "output": str(output)}
                ),
                flush=True,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:18093")
    parser.add_argument("--output", type=Path, default=Path(".tmp/mcp-live-proof"))
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    asyncio.run(smoke(args.base, args.output, args.timeout))


if __name__ == "__main__":
    main()
