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

"""One-shot JSON stdin/stdout runner for Google Antigravity SDK."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from google.antigravity import (
    Agent,
    CapabilitiesConfig,
    CustomSystemInstructions,
    LocalAgentConfig,
    hooks,
    types,
)
from google.antigravity.hooks import policy
from google.antigravity.types import (
    BudgetConfig,
    BuiltinTools,
    QuestionHookResult,
    QuestionResponse,
    RunCommandConfig,
)


def _vertex_enabled() -> bool:
    return any(
        os.getenv(name, "").strip().lower() in {"1", "true", "yes"}
        for name in ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE")
    )


# Argument keys that carry filesystem targets (wire URIs are normalized by
# the harness before policies see them, but tolerate both forms regardless).
_PATH_ARGUMENT_KEYS = ("path", "file_path", "directory_path", "TargetFile", "output_path")


def _resolve_arg_path(value: str) -> Path | None:
    """Resolve a tool-call path argument to an absolute local path."""
    if value.startswith(("file://", "cns://")):
        value = urlparse(value).path
    try:
        return Path(value).expanduser().resolve()
    except OSError:
        return None


def _path_escapes_workspaces(args, roots: list[Path]) -> bool:
    """True when any path-bearing argument resolves outside ``roots``."""
    if not isinstance(args, dict) or not roots:
        return False
    for key in _PATH_ARGUMENT_KEYS:
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        resolved = _resolve_arg_path(value)
        if resolved is None:
            return True
        if not any(resolved == root or root in resolved.parents for root in roots):
            return True
    return False


def _scoped_policies(workspaces: list[str]) -> list[policy.Policy]:
    """Deny file tools that escape the staged workspaces; allow the rest.

    ``policy.workspace_only`` is a stub in SDK 0.1.15 — it deletes its
    ``workspaces`` argument and denies all file tools unconditionally — so
    scoping is done with explicit dynamic predicates instead. Unmatched tool
    calls default to allow, so this preserves the previous autonomous
    behaviour everywhere except out-of-scope file access.
    """
    roots: list[Path] = []
    for workspace in workspaces:
        try:
            roots.append(Path(workspace).resolve())
        except OSError:
            continue

    def escapes(args) -> bool:
        return _path_escapes_workspaces(args, roots)

    scoped_denies = [
        policy.deny(tool.value, when=escapes, name="workspace_scope")
        for tool in BuiltinTools.file_tools()
    ]
    # And inside the workspace, the extracted template is not a turn's to
    # revise once `deck layouts` has hashed it. See guards.py: this is the
    # after-the-fact plate gate moved one tool call earlier, where it costs a
    # refusal instead of a re-run.
    from guards import seal_policies

    # The Go evaluator fail-closes any tool with no matching rule, which
    # silently blocks builtin tools like list_directory/view_file. Specific
    # denies sort before wildcards, so the scoped file denies still win while
    # everything else (including multimodal file viewing) stays available.
    return [
        *scoped_denies,
        *seal_policies(workspaces),
        policy.allow("*", name="autonomous_default"),
    ]


def _budget_from_env() -> BudgetConfig | None:
    """Build an optional budget from explicitly-set env vars, else None.

    No env vars set -> no BudgetConfig -> the session is unlimited and the
    harness decides how much work the artifact needs.
    """
    limits: dict[str, int] = {}
    for field, env_name in (
        ("max_model_calls", "SLIDEGEN_AGY_MAX_MODEL_CALLS"),
        ("max_tool_calls", "SLIDEGEN_AGY_MAX_TOOL_CALLS"),
        ("max_input_tokens", "SLIDEGEN_AGY_MAX_INPUT_TOKENS"),
        ("max_output_tokens", "SLIDEGEN_AGY_MAX_OUTPUT_TOKENS"),
        ("max_total_tokens", "SLIDEGEN_AGY_MAX_TOTAL_TOKENS"),
    ):
        raw = (os.getenv(env_name) or "").strip()
        if raw.isdigit():
            limits[field] = int(raw)
    return BudgetConfig(**limits) if limits else None


@hooks.on_interaction
async def _skip_questions(spec) -> QuestionHookResult:
    """Headless worker: never block a turn waiting for a human answer."""
    questions = list(getattr(spec, "questions", None) or [])
    return QuestionHookResult(
        responses=[QuestionResponse(skipped=True) for _ in questions]
    )


@hooks.post_tool_call
def _log_tool_call(result) -> None:
    """Emit one stderr line per tool call for pipeline telemetry."""
    name = (
        getattr(result, "name", None)
        or getattr(result, "tool_name", None)
        or "tool"
    )
    print(f"[agy] tool: {name}", file=sys.stderr, flush=True)


@hooks.post_turn
def _log_turn(response) -> None:
    text = response if isinstance(response, str) else ""
    print(f"[agy] turn complete: {len(text)} chars", file=sys.stderr, flush=True)

def _emit_event(kind: str, **fields) -> None:
    """One NDJSON progress event on stderr — the live channel out."""
    print(json.dumps({"agy_evt": kind, **fields}), file=sys.stderr, flush=True)


# A refused tool call arrives as assistant text, so the refusal ends up
# concatenated into the turn's result and a caller reading `text` sees a turn
# that appears to have failed when it recovered on the next step. The refusal
# is telemetry; it belongs on the stderr event stream with the rest of it.
_DENIAL = re.compile(
    r'Access to path "[^"]*" is denied\.[^\n]*?\(\"?denied by pre-tool hook:.*?\)\s*',
    re.S,
)


def _strip_denials(text: str) -> tuple[str, int]:
    """Split a text chunk into what the model said and how often it was refused."""
    kept, count = _DENIAL.subn("", text)
    return kept, count


async def _drain_stream(response, disclosure=None) -> str:
    """Consume the native ChatResponse stream in real time.

    ChatResponse IS an async stream of semantic chunks (Text, Thought,
    ToolCall, ToolResult). Consuming ``.chunks`` gives one cursor that sees
    everything as it happens: text deltas accumulate into the final result,
    and every chunk emits an NDJSON event on stderr for live progress.

    The same cursor is where a turn's skill reads are counted. It is the only
    place they are visible: frontmatter matching happens inside the Go harness,
    so what the model was offered is knowable from the filesystem but what it
    took is knowable only from the tool calls going past here.
    """
    parts: list[str] = []
    async for chunk in response.chunks:
        if isinstance(chunk, types.Text):
            if chunk.text:
                kept, denials = _strip_denials(chunk.text)
                if denials:
                    _emit_event("denied", count=denials)
                if kept:
                    parts.append(kept)
                    _emit_event("text", delta=len(kept))
        elif isinstance(chunk, types.Thought):
            _emit_event("thought", delta=len(chunk.text or ""))
        elif isinstance(chunk, types.ToolCall):
            name = getattr(chunk, "name", None) or "tool"
            if disclosure is not None:
                from disclosure import call_path

                disclosure.observe(
                    name,
                    call_path(
                        getattr(chunk, "args", None),
                        getattr(chunk, "canonical_path", None),
                    ),
                )
            _emit_event("tool", name=name, phase="call")
        elif isinstance(chunk, types.ToolResult):
            _emit_event("tool", phase="result")
    return "".join(parts)

def _build_subagents(specs: list) -> list[types.SubagentConfig]:
    """Construct static subagents from declarative request specs.

    Spec: {"name": "...", "description": "...", "system_instructions": "..."}.
    Static subagents default to READ-ONLY builtin tools — the judging/fixing
    separation (review panels cannot edit) is enforced by capability, not
    instruction. Subagents needing write tools pass "capabilities": "write".
    """
    built = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name", "")).strip()
        if not name:
            continue
        capabilities = None
        if spec.get("capabilities") == "write":
            capabilities = types.SubagentCapabilities(
                agent_behavior=types.AgentBehavior.AUTONOMOUS
            )
        built.append(
            types.SubagentConfig(
                name=name,
                description=str(spec.get("description", ""))[:500],
                system_instructions=str(spec.get("system_instructions", "")) or None,
                capabilities=capabilities,
            )
        )
    return built


def _build_triggers(specs: list) -> list:
    """Construct SDK triggers from declarative request specs.

    Triggers push messages into the session when EXTERNAL events happen —
    the complement to hooks (lifecycle). Spec shapes:
      {"kind": "file_change", "path": "...", "message": "...",
       "on": ["added", "modified"]}
      {"kind": "every", "interval": 300, "message": "..."}
    Paths are resolved against the staged workspaces when relative.
    """
    from google.antigravity.triggers import FileChangeKind, helpers

    built = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        kind = spec.get("kind")
        message = str(spec.get("message", "")).strip()
        if not message:
            continue
        if kind == "file_change":
            path = Path(str(spec.get("path", ""))).expanduser()
            if not path.is_absolute():
                continue
            wanted = {
                FileChangeKind[k.upper()].value
                for k in spec.get("on", ["added", "modified"])
            }
            target = path.name

            async def _on_change(ctx, changes, _message=message, _wanted=wanted, _target=target):
                for change in changes:
                    if Path(change.path).name != _target:
                        continue
                    if not _wanted or change.kind.value in _wanted:
                        await ctx.send(_message)
                        return

            # Watch the parent DIRECTORY: watchfiles cannot observe a file
            # that does not exist yet, and artifact targets are usually
            # created by the turn itself.
            built.append(helpers.on_file_change(str(path.parent), _on_change))
        elif kind == "every":
            interval = float(spec.get("interval", 300))

            async def _tick(ctx, _message=message):
                while True:
                    await asyncio.sleep(interval)
                    await ctx.send(_message)

            built.append(_tick)
    return built

def _build_prompt(request: dict):
    """Prompt as plain text, or a multimodal list with attached files.

    chat() accepts a mixed list of text and content objects; ``from_file``
    resolves staged images/PDFs into real multimodal parts, so the model
    SEES the attached renders instead of shelling out to open them.
    """
    files = [p for p in (request.get("contents_files") or []) if Path(p).is_file()]
    if not files:
        return request["contents"]
    from google.antigravity.types import from_file

    return [request["contents"], *[from_file(path) for path in files]]


def _system_text(request: dict, workspaces: list[str], artifacts: Path) -> str:
    """Caller's system text plus the two paths the turn keeps guessing wrong.

    Without this, every turn opens by probing ``/`` or ``/workspace`` and
    eating a denial, and any prompt that says "write a markdown file" sends
    the model to the artifact tool, which refuses a workspace path outright.
    Both are cheap to prevent and expensive to leave in: they cost a step, and
    the denial text lands in the transcript as if the turn had gone wrong.
    """
    roots = "\n".join(f"  {path}" for path in workspaces) or "  (none staged)"
    return (
        f"{request['system']}\n\n"
        "## Where you may write\n\n"
        "These directories, and nothing above them, are readable and writable "
        "with the file tools:\n\n"
        f"{roots}\n\n"
        "Every relative path in your instructions resolves against the first "
        "of them. Do not list or probe the filesystem root; there is nothing "
        "for you there and the attempt is denied.\n\n"
        "Deliverables named in the prompt are ordinary files. Write them with "
        f"the file tools. The artifact tool writes only under {artifacts} and "
        "is not how a named deliverable is produced."
    )


def _mirror_artifacts(app_data: Path, destination: Path) -> int:
    """Copy anything the artifact tool produced into the caller's workspace.

    The artifact store lives under the session's throwaway app-data directory,
    so an artifact is deleted with the temp dir the moment the turn ends. A
    caller who asked for a file and got an artifact would otherwise be told
    the work was done and find nothing.
    """
    brain = app_data / "brain"
    if not brain.is_dir():
        return 0
    copied = 0
    for source in sorted(brain.rglob("*")):
        if not source.is_file():
            continue
        target = destination / source.name
        if target.exists():
            continue
        target.write_bytes(source.read_bytes())
        copied += 1
    return copied


def _with_aid_subagents(specs: list, workspaces: list[str]) -> list:
    """Request specs, plus one aid subagent per free region the template has.

    The request's own specs win a name collision: a caller that wrote an aid
    subagent by hand meant it, and a synthesised spec silently replacing it
    would be the harder bug to find. Synthesis is skipped entirely when the
    workspace has no layout contract, since without one there is no geometry
    to tell a subagent about and a generic aid agent is what we already had.
    """
    named = {
        str(spec.get("name", "")) for spec in specs if isinstance(spec, dict)
    }
    out = list(specs)
    for workspace in workspaces:
        try:
            from aids import build_aid_subagents

            for spec in build_aid_subagents(workspace):
                if spec["name"] not in named:
                    named.add(spec["name"])
                    out.append(spec)
        except Exception as exc:
            _emit_event("aids_skipped", detail=str(exc))
    if len(out) > len(specs):
        _emit_event("aids", count=len(out) - len(specs))
    return out


def _disclosure_log(skills_paths: list[str]):
    """What the turn was offered, ready to record what it opened.

    The lint runs here rather than in CI because here is where the paths are
    real. A run can be handed any directory; a page that is unreachable in the
    set actually loaded is unreachable for this turn whatever the repository
    looks like. Findings are emitted, never raised — a badly linked skill is a
    worse deck, not a failed run.
    """
    if not skills_paths:
        return None
    try:
        from disclosure import DisclosureLog, disclosure_findings, skill_cards

        cards = skill_cards(list(skills_paths))
        for problem in disclosure_findings(list(skills_paths)):
            _emit_event("disclosure_defect", detail=problem)
        return DisclosureLog(cards)
    except Exception as exc:
        _emit_event("disclosure_defect", detail=f"could not read skills: {exc}")
        return None


async def _run(request: dict) -> str:
    with tempfile.TemporaryDirectory(prefix="pixelpitch-agy-") as temp_dir:
        root = Path(temp_dir)
        workspaces = [
            path for path in request.get("workspaces", []) if Path(path).is_dir()
        ]
        skills_paths = [
            path for path in request.get("skills_paths", []) if Path(path).is_dir()
        ]
        disclosure = _disclosure_log(skills_paths)
        app_data = root / "data"
        config = LocalAgentConfig(
            system_instructions=CustomSystemInstructions(
                text=_system_text(request, workspaces, app_data / "brain")
            ),
            model=request["model"],
            vertex=_vertex_enabled(),
            project=os.getenv("GOOGLE_CLOUD_PROJECT") or None,
            location=os.getenv("GOOGLE_CLOUD_LOCATION") or None,
            capabilities=CapabilitiesConfig(
                # Full native surface, no artificial limits: every builtin
                # tool the harness exposes (files, shell, search, image
                # generation, subagents), daemons included — the harness
                # decides what the artifact needs. Parallel subagent
                # delegation is its core strength, so allow two levels of
                # recursion below the root.
                enable_subagents=True,
                max_subagent_depth=3,
                # Defer context compaction far past any single turn: on SDK
                # 0.115 the compaction request can be submitted with empty
                # contents on tool-heavy turns, which Vertex rejects with a
                # fatal 400. Our turns carry the whole pipeline; losing one
                # to a compaction bug is worse than a large context.
                compaction_threshold=int(
                    os.getenv("SLIDEGEN_AGY_COMPACTION_THRESHOLD", "1000000")
                ),
                run_command_config=RunCommandConfig(enable_daemons=True),
            ),
            # The worker is non-interactive, so approval prompts would hang;
            # the on_interaction hook answers questions instead. Tool scope:
            # file tools may only touch the staged workspaces (dynamic deny
            # predicates — policy.workspace_only is a stub in SDK 0.1.15),
            # everything else stays autonomous. The runtime/container remains
            # the operating-system boundary.
            policies=_scoped_policies(workspaces),
            hooks=[_skip_questions, _log_tool_call, _log_turn],
            triggers=_build_triggers(request.get("triggers") or []),
            subagents=_build_subagents(
                _with_aid_subagents(request.get("subagents") or [], workspaces)
            ),
            workspaces=workspaces,
            skills_paths=skills_paths,
            save_dir=str(root / "session"),
            app_data_dir=str(app_data),
            # No budget by default: the harness works until the artifact is
            # done, using as many model/tool calls as it needs. Caps remain
            # available as an explicit env opt-in for cost-sensitive runs.
            budget_config=_budget_from_env(),
        )
        async with Agent(config) as agent:
            prompt = _build_prompt(request)
            response = await agent.chat(prompt)
            text = await _drain_stream(response, disclosure)
        if disclosure is not None:
            _emit_event("disclosure", **disclosure.record())
        if workspaces:
            rescued = _mirror_artifacts(app_data, Path(workspaces[0]))
            if rescued:
                _emit_event("artifacts", rescued=rescued)
        return text


def main() -> int:
    try:
        request = json.load(sys.stdin)
        text = asyncio.run(_run(request))
        print(json.dumps({"ok": True, "text": text}), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
