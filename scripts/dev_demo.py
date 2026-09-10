"""Run the real MCP widget and job lifecycle with synthetic slide generation."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dev_local import workstation_host

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18092)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535")
    for asset in ("index.html", "host/host.html"):
        if not (ROOT / "mcp-app/dist" / asset).is_file():
            parser.error("The widget is not built. Run mise run setup-dev first.")
    state = ROOT / ".tmp/demo" / str(args.port)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(
        f"Open http://127.0.0.1:{args.port}\n"
        "Local simulation: real MCP, saved jobs, drafts, and cancellation.\n"
        "No model calls or real PowerPoint export. Not Gemini Enterprise.\n"
        "Try a six-slide brief, or start the topic with Fail to test recovery.\n"
        "Press Ctrl+C to stop the API and worker. Saved jobs remain local.",
        flush=True,
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
    origin = workstation_host(args.port)
    preview_args = ["--preview-origin", origin] if origin else []
    if origin:
        print(
            f"Workstation preview: {origin}\nOpen this URL top-level to authenticate the workstation proxy.",
            flush=True,
        )
    return subprocess.call(
        [
            sys.executable,
            str(ROOT / "tests/fixtures/mcp_preview.py"),
            "--port",
            str(args.port),
            "--db",
            str(state / "jobs.sqlite"),
            *preview_args,
        ],
        cwd=ROOT,
        env=env,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
