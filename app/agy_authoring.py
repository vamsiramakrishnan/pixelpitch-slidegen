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

"""Subprocess boundary to the isolated Google Antigravity authoring worker.

AGY SDK and ADK currently require incompatible protobuf major versions. The
worker therefore has its own locked environment; JSON over stdin/stdout keeps
the outer ADK/A2A application and all Pixelpitch tools intact.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_POLL_SECONDS = 0.25


class AuthoringCancelled(RuntimeError):
    """The caller cancelled the turn; the worker was killed."""

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_RUNNER = _PROJECT_ROOT / "agy-worker" / "runner.py"
_LOCAL_PYTHON = _PROJECT_ROOT / "agy-worker" / ".venv" / "bin" / "python"


def _worker_command() -> list[str]:
    python = os.getenv("SLIDEGEN_AGY_PYTHON")
    if not python:
        python = str(_LOCAL_PYTHON) if _LOCAL_PYTHON.is_file() else sys.executable
    runner = os.getenv("SLIDEGEN_AGY_RUNNER", str(_DEFAULT_RUNNER))
    return [python, runner]


def _path_list_from_env(name: str) -> list[str]:
    return [part for part in os.getenv(name, "").split(os.pathsep) if part]


def _runtime_paths() -> tuple[list[str], list[str]]:
    """Resolve skill roots and writable workspaces visible to the worker."""
    skill_candidates = [
        Path(p) for p in _path_list_from_env("SLIDEGEN_AGY_SKILLS_PATHS")
    ]
    skill_candidates.append(_PROJECT_ROOT / "agy-worker" / "skills")

    workspace_candidates = [
        Path(p) for p in _path_list_from_env("SLIDEGEN_AGY_WORKSPACES")
    ]
    workspace_candidates.append(_PROJECT_ROOT)

    def existing_unique(paths: list[Path]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for path in paths:
            try:
                resolved = str(path.resolve())
            except OSError:
                continue
            if resolved in seen or not Path(resolved).is_dir():
                continue
            seen.add(resolved)
            result.append(resolved)
        return result

    return existing_unique(skill_candidates), existing_unique(workspace_candidates)


def _pump_stderr(pipe, on_event) -> None:
    """Forward worker stderr lines: NDJSON events to ``on_event``, all to us.

    Runs in a daemon thread beside the subprocess so live events reach the
    caller (companion messages, A2UI progress) while the turn is still
    running; non-JSON lines pass through untouched.
    """
    import sys
    import threading

    def run() -> None:
        for line in pipe:
            sys.stderr.write(line if line.endswith("\n") else line + "\n")
            sys.stderr.flush()
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                event = json.loads(stripped)
            except ValueError:
                continue
            if isinstance(event, dict) and "agy_evt" in event:
                try:
                    on_event(event)
                except Exception:  # engagement must never break authoring
                    logger.exception("event callback failed")

    threading.Thread(target=run, daemon=True).start()


def _communicate_cancellable(
    process: subprocess.Popen,
    request: str,
    timeout: float | None,
    cancellation_event: threading.Event,
) -> str:
    """``communicate`` that also watches a cancellation flag."""
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(request)
    process.stdin.close()

    collected: list[str] = []
    reader = threading.Thread(
        target=lambda: collected.append(process.stdout.read()),  # type: ignore[union-attr]
        daemon=True,
    )
    reader.start()

    deadline = time.monotonic() + timeout if timeout else None
    while process.poll() is None:
        if cancellation_event.is_set():
            process.kill()
            process.wait(timeout=30)
            raise AuthoringCancelled("authoring turn cancelled by the caller")
        if deadline is not None and time.monotonic() > deadline:
            process.kill()
            process.wait(timeout=30)
            raise subprocess.TimeoutExpired(process.args, timeout or 0)
        time.sleep(_POLL_SECONDS)
    reader.join(timeout=30)
    return collected[0] if collected else ""


def generate(
    *,
    contents: str,
    system: str,
    model: str,
    contents_files: list[str] | None = None,
    triggers: list[dict] | None = None,
    subagents: list[dict] | None = None,
    workspaces: list[str] | None = None,
    on_event=None,
    cancellation_event: threading.Event | None = None,
) -> str:
    """Run one skill-aware AGY turn with scoped filesystem and shell access.

    ``contents_files`` are staged file paths attached to the prompt as real
    multimodal content (images arrive as images, PDFs as documents) — the
    agent SEES them without shell round-trips. ``triggers`` are declarative
    external-event specs (file_change / every) the runner turns into SDK
    triggers that push messages into the session mid-turn. ``subagents`` are
    declarative static-subagent specs (name/description/system_instructions,
    read-only by default) registered on the session for delegation.
    ``on_event`` receives the worker's live NDJSON progress events
    ({"agy_evt": "tool"|"text"|"thought"|..., ...}) as they happen — the
    perceived-latency channel. ``cancellation_event`` is polled while the turn
    runs; setting it kills the worker and raises ``AuthoringCancelled``, so a
    user who walks away does not keep paying for the turn.
    """
    logger.info("authoring backend=google-antigravity model=%s", model)
    # No outer deadline by default: the harness works until the artifact is
    # done (its own budget is unlimited). An explicit env value stays available
    # for operational runs that genuinely need a wall-clock bound.
    raw_timeout = os.getenv("SLIDEGEN_AGY_TIMEOUT_SECONDS")
    timeout = float(raw_timeout) if raw_timeout else None
    skills_paths, runtime_workspaces = _runtime_paths()
    requested_workspaces = [
        str(Path(path).resolve())
        for path in (workspaces or [])
        if path and Path(path).is_dir()
    ]
    effective_workspaces = list(
        dict.fromkeys([*requested_workspaces, *runtime_workspaces])
    )
    logger.info(
        "AGY capabilities skills=%d workspaces=%s shell=enabled",
        len(skills_paths),
        effective_workspaces,
    )
    process = subprocess.Popen(
        _worker_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    if on_event is not None:
        _pump_stderr(process.stderr, on_event)
    else:
        # No consumer: passthrough keeps events visible in logs.
        threading.Thread(
            target=lambda: [sys.stderr.write(line) for line in process.stderr or []],
            daemon=True,
        ).start()
    request = json.dumps(
        {
            "contents": contents,
            "contents_files": [p for p in (contents_files or []) if p],
            "triggers": triggers or [],
            "subagents": subagents or [],
            "system": system,
            "model": model,
            "skills_paths": skills_paths,
            "workspaces": effective_workspaces,
        }
    )
    if cancellation_event is None:
        try:
            stdout, _ = process.communicate(input=request, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            raise
    else:
        stdout = _communicate_cancellable(
            process, request, timeout, cancellation_event
        )
    lines = [line for line in stdout.splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1]) if lines else {}
    except ValueError as exc:
        raise RuntimeError(
            f"Antigravity worker returned an invalid response: {stdout[-500:]}"
        ) from exc
    if process.returncode != 0 or payload.get("ok") is not True:
        # stderr streams through live now, so the payload error is the
        # remaining machine-readable detail.
        detail = payload.get("error") or "worker failed (see streamed stderr)"
        raise RuntimeError(f"Antigravity authoring failed: {detail}")
    text = payload.get("text")
    if not isinstance(text, str) or not text:
        raise RuntimeError("Antigravity SDK returned an empty authoring response")
    return text
