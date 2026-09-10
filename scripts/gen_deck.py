#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Drive the deployed slidegen agent exactly like Gemini Enterprise does.

Streams A2A message/stream, prints the live Thinking-panel lines (the same
text GE renders), then summarizes the result. Stdlib-only; authenticates
with `gcloud auth print-identity-token`.

Usage:
  uv run python scripts/gen_deck.py "3 slides using our company template" --server https://YOUR_A2A_HOST
  uv run python scripts/gen_deck.py "brief" --server https://YOUR_A2A_HOST --slides 12 --pull out.pptx
  uv run python scripts/gen_deck.py "brief" --server https://YOUR_A2A_HOST --faithful
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request

EXT = "https://a2ui.org/a2a-extension/a2ui/v0.9"


def identity_token(server: str) -> str:
    proc = subprocess.run(
        ["gcloud", "auth", "print-identity-token", f"--audiences={server}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def stream(server: str, token: str, brief: str) -> tuple[list[str], dict | None]:
    """Yield live status text lines; return (lines, generate_deck result)."""
    request_id = f"cli-{int(time.time() * 1000)}"
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "message/stream",
            "params": {
                "message": {
                    "messageId": f"m-{request_id}",
                    "role": "user",
                    "parts": [{"kind": "text", "text": brief}],
                    "extensions": [EXT],
                }
            },
        }
    ).encode()
    req = urllib.request.Request(
        f"{server}/a2a/app",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    lines: list[str] = []
    result: dict | None = None
    with urllib.request.urlopen(req, timeout=1800) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:])
            except ValueError:
                continue
            r = event.get("result", {})
            state = (r.get("status") or {}).get("state", "")
            for part in (r.get("status", {}).get("message") or {}).get("parts", []):
                if part.get("kind") == "data" and part.get("name") == "generate_deck":
                    result = part.get("response") or {}
                elif part.get("text") and state in ("working", "completed"):
                    text = part["text"]
                    if text not in lines:  # dedupe echoed snapshots
                        lines.append(text)
                        print(text, flush=True)
    return lines, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("brief", help="deck brief, in your words")
    parser.add_argument("--slides", type=int, help="slide count hint in the brief")
    parser.add_argument(
        "--server",
        required=True,
        help="Approved A2A service origin; there is no shared deployment default",
    )
    parser.add_argument(
        "--faithful",
        action="store_true",
        help="ask for literal text-fill (population) mode",
    )
    parser.add_argument(
        "--pull", metavar="FILE", help="gsutil cp the produced PPTX to this path"
    )
    args = parser.parse_args()

    brief = args.brief
    if args.slides:
        brief = f"{args.slides} slides. {brief}"
    if args.faithful:
        brief = f"{brief} (faithful_fill=true: exactly fill the template layouts, text only)"

    print(f"→ {args.server}\n", flush=True)
    token = identity_token(args.server)
    _, result = stream(args.server, token, brief)

    print("\n──────── result ────────")
    if not result:
        print("no generate_deck result captured (task may have failed)")
        return 1
    print(f"status : {result.get('status')}")
    print(f"mode   : {result.get('mode')}")
    print(
        f"slides : {result.get('slide_count')} · titles: "
        f"{len(result.get('slide_titles') or [])}"
    )
    print(f"deck   : {result.get('gs_uri')}")
    if result.get("error"):
        print(f"error  : {result['error']}")
    if args.pull and result.get("gs_uri"):
        subprocess.run(["gsutil", "cp", result["gs_uri"], args.pull], check=False)
        print(f"pulled : {args.pull}")
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
