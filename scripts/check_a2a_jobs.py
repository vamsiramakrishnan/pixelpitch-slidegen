"""Measure a real A2A job turn without confusing chat completion with delivery.

Status is read-only. Submit and cancel require their explicit subcommands.
An authenticated request keeps the gcloud identity token in memory only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid

import httpx

EXTENSION = "https://a2ui.org/a2a-extension/a2ui/v0.9"


def objects(value):
    if isinstance(value, dict):
        yield value
        for key, item in value.items():
            if key == "adk_custom_metadata" and isinstance(item, str):
                try:
                    item = json.loads(item)
                except ValueError:
                    continue
            yield from objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from objects(item)


def request(base: str, context: str, name: str, values: dict, *, auth=False) -> dict:
    headers = {"X-A2A-Extensions": EXTENSION, "Accept": "text/event-stream"}
    if auth:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-identity-token"], text=True
        ).strip()
        headers["Authorization"] = f"Bearer {token}"
    message = {
        "role": "user",
        "kind": "message",
        "messageId": uuid.uuid4().hex,
        "contextId": context,
        "parts": [
            {
                "kind": "text",
                "text": "PIXELPITCH_ACTION_V1:"
                + json.dumps({"name": name, "context": values}),
            }
        ],
    }
    start = time.monotonic()
    report = {"context_id": context, "action": name, "chat_turn_completed": False}
    with httpx.stream(
        "POST",
        base.rstrip("/") + "/a2a/app",
        json={
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/stream",
            "params": {"message": message},
        },
        headers=headers,
        timeout=30,
        follow_redirects=False,
    ) as response:
        response.raise_for_status()
        report["execution"] = response.headers.get("X-Pixelpitch-Execution", "direct")
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            report.setdefault(
                "first_event_ms", round((time.monotonic() - start) * 1000, 1)
            )
            event = json.loads(line[5:].strip())
            if "error" in event:
                raise RuntimeError(f"A2A error: {event['error'].get('code')}")
            result = event.get("result") or {}
            for item in objects(result):
                if item.get("kind") == "job" and item.get("job_id"):
                    report.update(job_id=item["job_id"], job_status=item["status"])
                if "createSurface" in item:
                    report.setdefault(
                        "first_surface_ms", round((time.monotonic() - start) * 1000, 1)
                    )
                if item.get("role") == "agent":
                    texts = [
                        p["text"]
                        for p in item.get("parts", [])
                        if p.get("kind") == "text"
                    ]
                    if texts:
                        report["message"] = " ".join(texts)[:500]
            state = (result.get("status") or {}).get("state")
            if result.get("final") and state in {"completed", "failed", "canceled"}:
                report["chat_turn_completed"] = state == "completed"
                report["complete_ms"] = round((time.monotonic() - start) * 1000, 1)
                break
    if not report["chat_turn_completed"] or "job_status" not in report:
        raise RuntimeError("No completed saved-job response: " + json.dumps(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:18090")
    parser.add_argument("--auth", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    submit = commands.add_parser("submit", help="Start one real deck job")
    submit.add_argument("--context", default=None)
    submit.add_argument("--request-id", default=None)
    submit.add_argument("--topic", required=True)
    submit.add_argument("--slides", type=int, default=1)
    submit.add_argument("--brand", default="stripe")
    submit.add_argument("--template", default="")
    for command in (commands.add_parser("status"), commands.add_parser("cancel")):
        command.add_argument("--context", required=True)
        command.add_argument("--job", required=True)
    args = parser.parse_args()
    if args.command == "submit":
        context = args.context or uuid.uuid4().hex
        name = "generate_deck"
        values = {
            "topic": args.topic,
            "slide_count": args.slides,
            "brand_id": args.brand,
            "template_revision": args.template,
            "request_id": args.request_id or uuid.uuid4().hex,
        }
    else:
        context, name = (
            args.context,
            {"status": "check_deck", "cancel": "cancel_deck"}[args.command],
        )
        values = {"job_id": args.job}
    print(
        json.dumps(request(args.base, context, name, values, auth=args.auth), indent=2)
    )


if __name__ == "__main__":
    main()
