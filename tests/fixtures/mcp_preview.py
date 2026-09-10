"""Local browser fixture. Real MCP, SQLite, worker and ADK; synthetic author/render."""

import argparse
import asyncio
import html
import signal
import subprocess
import sys
import time
from pathlib import Path

import uvicorn

from app import deck_run, tools
from app.mcp_server import create_app
from app.mcp_worker import work


def authored(*, topic, slide_count, slide_ready, cancellation_event=None, **_):
    if topic.startswith("Fail"):
        return {
            "status": "error",
            "error": "Synthetic authoring failure for the browser test.",
        }
    slides = []
    titles = [
        "The decision in front of us",
        "What needs to change",
        "A clear next step",
    ]
    for index in range(slide_count):
        for _ in range(20):
            if cancellation_event and cancellation_event.is_set():
                return {"status": "error", "code": "cancelled"}
            time.sleep(0.1)
        title = titles[index % len(titles)]
        slide = {
            "index": index,
            "title": title,
            "html": f"""<!doctype html><html><head><style>
                *{{box-sizing:border-box}}body{{margin:0;width:1280px;height:720px;background:#102b46;color:white;font-family:Arial,sans-serif;padding:72px}}
                h1{{font-size:70px;line-height:1.05;max-width:900px;margin:56px 0 36px}}p{{font-size:26px;line-height:1.5;max-width:800px;color:#c5d5e4}}footer{{position:absolute;bottom:50px;font-size:18px;color:#c5d5e4}}.rule{{width:100px;height:8px;background:#58d7c5}}
                </style></head><body><div class="rule"></div><h1>{title}</h1><p>{html.escape(topic)}. This is a synthetic slide used to verify the live preview, not a generated business recommendation.</p><footer>Browser test fixture · {index + 1:02d}</footer></body></html>""",
        }
        slides.append(slide)
        slide_ready(slide)
    return {
        "status": "ok",
        "slides": slides,
        "titles": [slide["title"] for slide in slides],
    }


def rendered(*, slides, deck_title="", tool_context=None, **_):
    time.sleep(4)
    if tool_context:
        import base64
        import io

        from PIL import Image

        output = io.BytesIO()
        Image.new("RGB", (1280, 720), "#102b46").save(output, format="PNG")
        tools._cache_preview_image_tokens(
            tools._preview_key(tool_context),
            [base64.b64encode(output.getvalue()).decode()] * len(slides),
            "image/png",
        )
    return {
        "status": "ok",
        "https_url": None
        if deck_title.startswith("No download")
        else "https://example.com/test-fixture.pptx",
        "slide_count": len(slides),
        "quality": {"editability_passed": True},
    }


def templates(**_):
    # Slow noncritical catalog must not stop the brief becoming usable.
    time.sleep(2)
    return {"status": "ok", "templates": []}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18092)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    tools.list_templates = templates
    if args.worker:
        deck_run.run_designed_deck = authored
        tools.render_deck_to_pptx = rendered
        asyncio.run(work(args.db))
    else:
        worker = subprocess.Popen(
            [sys.executable, __file__, "--worker", "--db", str(args.db)]
        )
        try:
            uvicorn.run(
                create_app(args.db, args.port), host="127.0.0.1", port=args.port
            )
        finally:
            worker.send_signal(signal.SIGINT)
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
