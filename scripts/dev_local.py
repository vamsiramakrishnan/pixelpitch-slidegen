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

"""Run the slidegen server locally, clean, and prove that it serves.

Local runs drift. A previous shell exported an agent-engine id, a deploy left
``LOGS_BUCKET_NAME`` set, a stale uvicorn still holds the port, and the next
failure gets blamed on the code. ``up`` starts from a scrubbed environment on
its own port with its own state directory, and prints what it removed.

``check`` is the part worth having. It probes the four surfaces this app
actually serves and reports each one separately, because they fail for
different reasons: the Dev UI is static assets, ``/run_sse`` is ADK native,
``/a2a/app`` is JSON-RPC with an opt-in extension, and the agent card is what a
remote caller reads before any of the others.

Nothing here talks to Cloud Build, Cloud Run, or Agent Engine. Model calls
still go to Vertex through the workstation's metadata credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
STATE_ROOT = ROOT / ".tmp" / "dev-local"
DEFAULT_PORT = 18090

# Set by a deploy, a previous shell, or Cloud Run itself. Each one moves state
# off this machine, which is the opposite of what a local run is for.
CLOUD_ONLY_VARS = (
    "GOOGLE_CLOUD_AGENT_ENGINE_ID",
    "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION",
    "SESSION_SERVICE_URI",
    "LOGS_BUCKET_NAME",
    "K_SERVICE",
    "K_REVISION",
    "K_CONFIGURATION",
    "PORT",
)

A2UI_EXTENSION = "https://a2ui.org/a2a-extension/a2ui/v0.9"
COMPOSITE_CATALOG = "gemini_enterprise_composite_catalog.json"

ACTION = {
    "name": "generate_deck",
    "context": {
        "topic": "Phoenix Retail: one-page board pulse on customer growth",
        "audience": "Board",
        "goal": "A concise, evidence-led decision brief",
        "slide_count": "1",
        "brand_id": ["stripe"],
        "style_id": [],
    },
}


def action_text(template_revision: str | None) -> str:
    action = json.loads(json.dumps(ACTION))
    action["context"]["template_revision"] = (
        [template_revision] if template_revision else []
    )
    return "PIXELPITCH_ACTION_V1:" + json.dumps(action)


def workstation_host(port: int) -> str | None:
    host = os.environ.get("WEB_HOST")
    return f"https://{port}-{host}" if host else None


# --------------------------------------------------------------------------
# lifecycle


def state_dir(port: int) -> Path:
    return STATE_ROOT / str(port)


def read_pid(port: int, kind: str = "server") -> int | None:
    pidfile = state_dir(port) / f"{kind}.pid"
    if not pidfile.is_file():
        return None
    try:
        pid = int(pidfile.read_text().strip())
        os.kill(pid, 0)
    except (ValueError, OSError):
        return None
    # A durable directory can outlive its process. Never signal a reused PID.
    command = subprocess.run(
        ["ps", "-p", str(pid), "-o", "args="], capture_output=True, text=True
    ).stdout.split()
    expected = (
        ["app.a2a_worker", "--db", str(state_dir(port) / "a2a" / "jobs.sqlite")]
        if kind == "worker"
        else ["app.fast_api_app:app", "--port", str(port)]
    )
    return pid if all(part in command for part in expected) else None


def build_env(
    port: int, public_base: str | None = None
) -> tuple[dict[str, str], list[str]]:
    env = dict(os.environ)
    removed = [name for name in CLOUD_ONLY_VARS if env.pop(name, None) is not None]
    env["PYTHONUNBUFFERED"] = "1"
    env["GOOGLE_CLOUD_DISABLE_GRPC"] = env.get("GOOGLE_CLOUD_DISABLE_GRPC", "true")
    env["APP_URL"] = public_base or workstation_host(port) or f"http://127.0.0.1:{port}"
    env["SLIDEGEN_A2A_JOB_DB"] = str(state_dir(port) / "a2a" / "jobs.sqlite")
    return env, removed


def start_worker(port: int, env: dict[str, str]) -> int:
    if (pid := read_pid(port, "worker")) is not None:
        return pid
    where = state_dir(port)
    (where / "a2a").mkdir(mode=0o700, parents=True, exist_ok=True)
    with (where / "worker.log").open("a") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.a2a_worker",
                "--db",
                env["SLIDEGEN_A2A_JOB_DB"],
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (where / "worker.pid").write_text(str(process.pid))
    return process.pid


def up(args: argparse.Namespace) -> int:
    port, where = args.port, state_dir(args.port)
    if (pid := read_pid(port)) is not None:
        print(f"already up on {port} (pid {pid}). `down --port {port}` first.")
        return 1
    # Job state and worker ownership must survive an API restart.
    where.mkdir(mode=0o700, parents=True, exist_ok=True)

    if args.sync:
        sync = ["uv", "sync", "--frozen", "--extra", "lint"]
        print(" ".join(sync))
        if subprocess.run(sync, cwd=ROOT).returncode:
            return 1

    env, removed = build_env(port, args.public_base)
    (where / "env.json").write_text(
        json.dumps({"removed": removed, "APP_URL": env["APP_URL"]}, indent=2)
    )
    if removed:
        print("scrubbed from the environment: " + ", ".join(removed))

    worker_pid = start_worker(port, env)
    log = (where / "server.log").open("a")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.fast_api_app:app",
            "--host",
            args.host,
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (where / "server.pid").write_text(str(process.pid))

    base = f"http://127.0.0.1:{port}"
    if not wait_for(f"{base}/a2a/app/.well-known/agent-card.json", args.boot_timeout):
        print(f"did not come up in {args.boot_timeout}s. tail {where / 'server.log'}")
        return 1
    print(f"up on {base} (pid {process.pid}), log {where / 'server.log'}")
    if read_pid(port, "worker") is None:
        print(f"worker exited during startup; inspect {where / 'worker.log'}")
        return 1
    print(f"deck worker pid {worker_pid}; durable queue {env['SLIDEGEN_A2A_JOB_DB']}")
    print_access(port)
    return 0 if args.no_check else check(args)


def wait_for(url: str, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


def stop_process(port: int, kind: str) -> None:
    pid = read_pid(port, kind)
    if pid is None:
        return
    os.killpg(os.getpgid(pid), signal.SIGINT)
    for _ in range(50):
        if read_pid(port, kind) is None:
            break
        time.sleep(0.2)
    else:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    (state_dir(port) / f"{kind}.pid").unlink(missing_ok=True)
    print(f"stopped {kind} {pid} on {port}; saved jobs were preserved")


def down(args: argparse.Namespace) -> int:
    stop_process(args.port, "server")
    if not args.keep_worker:
        stop_process(args.port, "worker")
    else:
        print("deck worker kept running")
    return 0


def ps() -> int:
    listing = subprocess.run(
        ["ps", "-eo", "pid,lstart,args"], capture_output=True, text=True
    ).stdout.splitlines()
    rows = [
        line for line in listing if "app.fast_api_app" in line and "ps -eo" not in line
    ]
    if not rows:
        print("no slidegen server running")
        return 0
    ours = {
        read_pid(int(entry.name))
        for entry in (STATE_ROOT.iterdir() if STATE_ROOT.is_dir() else [])
        if entry.name.isdigit()
    }
    for row in rows:
        pid = int(row.split(None, 1)[0])
        port = match.group(1) if (match := re.search(r"--port\s+(\d+)", row)) else "?"
        print(f"{'ours ' if pid in ours else 'other'}  pid {pid}  port {port}")
    return 0


def print_access(port: int) -> None:
    if not (host := workstation_host(port)):
        return
    name = os.environ.get("CLOUD_WORKSTATIONS_WORKSTATION_RESOURCE_NAME", "")
    fields = dict(zip(name.split("/")[::2], name.split("/")[1::2], strict=False))
    print(f"\nbrowser, through the workstation proxy: {host}/dev-ui/?app=app")
    print("  Load that URL top-level first. The proxy issues its auth cookie per")
    print("  port-origin on a navigation, and a JS chunk fetched without it is")
    print("  redirected cross-origin and surfaces in devtools as 403.")
    if fields.get("workstations"):
        print("\nbrowser, bypassing the proxy entirely, from your laptop:")
        print(
            f"  gcloud workstations start-tcp-tunnel {fields['workstations']} {port} "
            f"--local-host-port=:{port} \\\n"
            f"    --project={fields.get('projects', '')} "
            f"--region={fields.get('locations', '')} \\\n"
            f"    --cluster={fields.get('workstationClusters', '')} "
            f"--config={fields.get('workstationConfigs', '')}"
        )
        print(f"  then http://localhost:{port}/dev-ui/?app=app")


# --------------------------------------------------------------------------
# the checks


@dataclass
class Result:
    name: str
    ok: bool
    detail: str


AUTH: dict[str, str] = {}


def identity_token() -> str:
    token = subprocess.run(
        ["gcloud", "auth", "print-identity-token"], capture_output=True, text=True
    )
    if token.returncode:
        raise SystemExit(token.stderr.strip())
    return token.stdout.strip()


def get(url: str, timeout: int = 30) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=AUTH)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def delete(url: str) -> None:
    """Best effort. A smoke test that litters a real session store is not one
    you can run often, and the probe is worthless if cleanup can fail it."""
    request = urllib.request.Request(url, method="DELETE", headers=AUTH)
    try:
        urllib.request.urlopen(request, timeout=30).close()
    except (urllib.error.URLError, OSError):
        pass


def cancel(rpc: str, task: str) -> None:
    request = urllib.request.Request(
        rpc,
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": f"cancel-{uuid.uuid4().hex[:8]}",
                "method": "tasks/cancel",
                "params": {"id": task},
            }
        ).encode(),
        headers={"Content-Type": "application/json", **AUTH},
    )
    try:
        urllib.request.urlopen(request, timeout=30).close()
    except (urllib.error.URLError, OSError):
        pass


def post_sse(url: str, body: dict, headers: dict[str, str], deadline: float):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **AUTH, **headers},
    )
    response = urllib.request.urlopen(request, timeout=max(1.0, deadline - time.time()))
    for raw in response:
        if time.time() > deadline:
            break
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("data: "):
            yield time.time(), json.loads(line[6:])
    response.close()


def check_list_apps(base: str) -> Result:
    status, body = get(f"{base}/list-apps")
    apps = json.loads(body) if status == 200 else []
    return Result("list-apps", "app" in apps, f"{status} {apps}")


def check_dev_ui(base: str) -> Result:
    """Every asset the served HTML names, fetched from the server that served it.

    This is the check that separates a broken Dev UI build from a proxy problem.
    A stale HTML/chunk pair fails here; a workstation auth cookie problem does
    not, because it never reaches this process.
    """
    index = f"{base}/dev-ui/"
    status, body = get(index)
    if status != 200:
        return Result("dev-ui", False, f"index {status}")
    html = body.decode("utf-8", "replace")
    assets = {
        match.group(2)
        for match in re.finditer(r'(src|href)="([^"]+)"', html)
        if not match.group(2).startswith(("http", "//", "#"))
    }
    bad = [
        f"{asset} {code}"
        for asset in sorted(assets)
        if (code := get(urljoin(index, asset))[0]) != 200
    ]
    return Result(
        "dev-ui",
        not bad,
        f"{len(assets)} assets, all 200" if not bad else ", ".join(bad),
    )


def check_agent_card(base: str) -> Result:
    status, body = get(f"{base}/a2a/app/.well-known/agent-card.json")
    if status != 200:
        return Result("agent-card", False, f"{status}")
    card = json.loads(body)
    catalogs = [
        catalog
        for extension in card.get("capabilities", {}).get("extensions", [])
        for catalog in (extension.get("params") or {}).get("supportedCatalogIds", [])
    ]
    ok = any(COMPOSITE_CATALOG in catalog for catalog in catalogs)
    return Result("agent-card", ok, f"url={card.get('url')} catalogs={len(catalogs)}")


def check_run_sse(base: str, template: str | None, budget: int) -> Result:
    """ADK native. Measures time to first status, then stops.

    A full deck run is minutes of model time and proves nothing about the
    server. What the transport has to show is that the first event leaves
    before the agent has finished thinking.
    """
    # Both random. On Cloud Run the session service is Vertex-backed and
    # session ids are global to the reasoning engine, not scoped by user, so a
    # fixed id passes once and then 500s on every later run for the lifetime of
    # the engine.
    user, session = f"probe-{uuid.uuid4().hex[:8]}", f"probe-{uuid.uuid4().hex[:8]}"
    url = f"{base}/apps/app/users/{user}/sessions/{session}"
    request = urllib.request.Request(
        url, data=b"{}", headers={"Content-Type": "application/json", **AUTH}
    )
    try:
        urllib.request.urlopen(request, timeout=30).close()
    except urllib.error.HTTPError as error:
        return Result("run_sse", False, f"session create {error.code}")

    started = time.time()
    first, texts, drained = None, [], False
    try:
        for at, event in post_sse(
            f"{base}/run_sse",
            {
                "app_name": "app",
                "user_id": user,
                "session_id": session,
                "new_message": {
                    "role": "user",
                    "parts": [{"text": action_text(template)}],
                },
                "streaming": True,
            },
            {},
            started + budget,
        ):
            first = first if first is not None else at - started
            texts += [
                part["text"]
                for part in (event.get("content") or {}).get("parts", [])
                if part.get("text")
            ]
            if len(texts) >= 3:
                break
        else:
            drained = True
    except (urllib.error.URLError, OSError) as error:
        return Result("run_sse", False, str(error))
    finally:
        # Only once the stream is genuinely over. Walking away after three
        # events leaves the turn running, and deleting the session out from
        # under it makes every later append 404 and land in the service's
        # production error log. Random ids already prevent the id collision
        # this cleanup was added for.
        if drained:
            delete(url)

    if first is None:
        return Result("run_sse", False, f"no event in {budget}s")
    head = texts[0].splitlines()[0] if texts else ""
    return Result(
        "run_sse", True, f"first status in {first:.2f}s, {len(texts)} events, {head!r}"
    )


def check_a2a(base: str, template: str | None, budget: int, deep: bool) -> Result:
    """The same turn over JSON-RPC with the A2UI extension activated.

    The extension is opt-in, so a client that does not send the header gets
    text and concludes the catalog wiring is broken.

    The catalog itself only shows up at the end. Progress events are text by
    design, and the A2UI DataParts are re-emitted on the final artifact, so
    ``--deep`` is the only mode that can assert them and it costs a whole deck
    run to do it.
    """
    started = time.time()
    statuses, kinds, catalogs, task = 0, set(), 0, None
    try:
        for _, envelope in post_sse(
            f"{base}/a2a/app",
            {
                "jsonrpc": "2.0",
                "id": f"probe-{uuid.uuid4().hex[:8]}",
                "method": "message/stream",
                "params": {
                    "message": {
                        "messageId": f"msg-{uuid.uuid4().hex}",
                        "role": "user",
                        "parts": [{"kind": "text", "text": action_text(template)}],
                    }
                },
            },
            {"X-A2A-Extensions": A2UI_EXTENSION},
            started + budget,
        ):
            if error := envelope.get("error"):
                return Result("a2a", False, json.dumps(error)[:200])
            result = envelope.get("result") or {}
            kinds.add(result.get("kind", "?"))
            task = result.get("taskId") or result.get("id") or task
            message = (result.get("status") or {}).get("message") or {}
            statuses += bool(message.get("parts"))
            catalogs += json.dumps(result).count(COMPOSITE_CATALOG)
            if not deep and statuses >= 2:
                break
            if deep and result.get("final"):
                break
    except (urllib.error.URLError, OSError) as error:
        return Result("a2a", False, str(error))
    finally:
        # Walking away from the stream does not stop the turn. Without this the
        # smoke test starts a whole deck run on the service every time it is
        # called and then abandons it.
        if task and not deep:
            cancel(f"{base}/a2a/app", task)

    detail = f"{statuses} status messages in {time.time() - started:.2f}s, kinds={sorted(kinds)}"
    if not deep:
        return Result("a2a", statuses > 0, detail)
    return Result("a2a-deep", catalogs > 0, f"{detail}, catalog parts={catalogs}")


def check(args: argparse.Namespace) -> int:
    base = (getattr(args, "base", None) or f"http://127.0.0.1:{args.port}").rstrip("/")
    if getattr(args, "auth", False):
        AUTH["Authorization"] = f"Bearer {identity_token()}"
    print(f"checking {base}")
    results = [
        check_list_apps(base),
        check_dev_ui(base),
        check_agent_card(base),
        check_run_sse(base, args.template, args.budget),
        check_a2a(base, args.template, args.deep or args.budget, bool(args.deep)),
    ]
    print()
    for result in results:
        print(f"  {'PASS' if result.ok else 'FAIL'}  {result.name:<12} {result.detail}")
    return 0 if all(result.ok for result in results) else 1


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("up", help="start a clean server and check it")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument(
        "--public-base", help="Stable HTTPS relay origin advertised by the agent card"
    )
    start.add_argument("--no-sync", dest="sync", action="store_false")
    start.add_argument("--no-check", action="store_true")
    start.add_argument("--boot-timeout", type=int, default=120)
    start.set_defaults(func=up)

    probe = sub.add_parser("check", help="probe a running server")
    probe.add_argument("--base", help="probe this URL instead of localhost")
    probe.add_argument(
        "--auth",
        action="store_true",
        help="send a gcloud identity token, for a private Cloud Run service",
    )
    probe.set_defaults(func=check)

    stop = sub.add_parser("down", help="stop the server on --port")
    stop.add_argument(
        "--keep-worker",
        action="store_true",
        help="Restart only the API without interrupting background decks",
    )
    stop.set_defaults(func=down)
    listing = sub.add_parser("ps", help="list slidegen servers")
    listing.set_defaults(func=lambda *_: ps())

    mcp = sub.add_parser(
        "mcp",
        help="run the local-only MCP App and durable job worker in the foreground",
    )
    mcp.add_argument("--port", type=int, default=18091)

    def run_mcp(args):
        from dev_mcp import run

        return run(args)

    mcp.set_defaults(func=run_mcp)

    for command in (start, probe, stop):
        command.add_argument("--port", type=int, default=DEFAULT_PORT)
    for command in (start, probe):
        command.add_argument(
            "--template", default=None, help="template_revision to pin"
        )
        command.add_argument("--budget", type=int, default=90)
        command.add_argument(
            "--deep",
            nargs="?",
            type=int,
            const=1800,
            default=0,
            metavar="SECONDS",
            help="run the A2A turn to completion and assert the A2UI catalog parts",
        )

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
