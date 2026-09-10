"""Foreground local MCP server plus a separately supervised queue worker."""

import asyncio
import os
import signal
import subprocess
import sys


def run(args):
    import uvicorn
    from dev_local import CLOUD_ONLY_VARS, ROOT, build_env, state_dir
    from dotenv import load_dotenv

    if os.getenv("K_SERVICE"):
        raise SystemExit(
            "The MCP preview is local-only. Production identity and durable hosting must be configured separately."
        )
    load_dotenv(ROOT / ".env")
    env, _ = build_env(args.port)
    for name in CLOUD_ONLY_VARS:
        os.environ.pop(name, None)
    os.environ.update(env)
    from app.mcp_server import create_app

    html = ROOT / "mcp-app" / "dist" / "index.html"
    if not html.is_file():
        raise SystemExit(
            "Build the widget first: cd mcp-app && npm ci && npm run build"
        )
    where = state_dir(args.port) / "mcp"
    where.mkdir(parents=True, exist_ok=True)
    where.chmod(0o700)
    db = where / "jobs.sqlite"
    worker = subprocess.Popen(
        [sys.executable, "-m", "app.mcp_worker", "--db", str(db)], cwd=ROOT, env=env
    )
    print(
        f"Local MCP preview: http://127.0.0.1:{args.port}\nMCP endpoint: http://127.0.0.1:{args.port}/mcp\nJob state: {db}\nNo cloud deployment or registration was changed.",
        flush=True,
    )
    try:
        server = uvicorn.Server(
            uvicorn.Config(create_app(db, args.port), host="127.0.0.1", port=args.port)
        )
        asyncio.run(server.serve())
    finally:
        if worker.poll() is None:
            worker.send_signal(signal.SIGINT)
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
    return 0
