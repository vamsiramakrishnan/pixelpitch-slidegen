# Coding agent guide

This is the standalone slide-generation repository. Run commands from its
root. The renderer depends on `vendor/slidify`, not a parent Pixelpitch checkout.
Use [Cloud Shell deployment](CLOUD_SHELL.md) for the maintained setup procedure.

## Prerequisites

Install the CLI once:

```bash
uv tool install google-agents-cli
```

---

## Development Phases

### Phase 1: Understand Requirements
Before writing any code, understand the project's requirements, constraints, and success criteria.

### Phase 2: Build and Implement
Implement agent logic in `app/`. Use `agents-cli playground` for interactive testing. Iterate based on user feedback.

### Phase 3: The Evaluation Loop (Main Iteration Phase)
Start with 1-2 eval cases, run `agents-cli eval generate`, then `agents-cli eval grade`, iterate by making changes and rerunning both commands until satisfied. Expect 5-10+ iterations. Once you have a baseline, reach for `agents-cli eval compare` (regression diffs), `agents-cli eval analyze` (cluster failure modes), and `agents-cli eval optimize` (auto-tune prompts). See the **Evaluation Guide** for metrics, dataset schema, LLM-as-judge config, and common gotchas.

### Phase 4: Pre-Deployment Tests
Run `uv run pytest tests/unit tests/integration`. Fix issues until all tests pass.

#### Running it locally

`scripts/dev_local.py` starts the server the way a local run should start: its
own port, its own state directory under `.tmp/dev-local/<port>/`, and an
environment with the deploy-only variables removed, since an inherited
`GOOGLE_CLOUD_AGENT_ENGINE_ID` or `LOGS_BUCKET_NAME` silently moves sessions
and artifacts off the machine. Nothing in it touches Cloud Build or Cloud Run.

```bash
uv run python scripts/dev_local.py up            # start clean on 18090, then check
uv run python scripts/dev_local.py check --deep  # + run one A2A turn to completion
uv run python scripts/dev_local.py ps            # every slidegen server, ours or not
uv run python scripts/dev_local.py down
```

`check` probes the four surfaces separately because they fail for unrelated
reasons: static Dev UI assets, ADK `/run_sse`, the A2A JSON-RPC endpoint, and
the agent card. The Dev UI check fetches every asset the served HTML names,
which is what distinguishes a stale build from a Cloud Workstations proxy
problem. The proxy issues its auth cookie per port-origin on a top-level
navigation, so a JS chunk requested before that cookie exists is redirected
cross-origin and shows up in devtools as a 403 that looks like CORS. `up`
prints both the proxy URL and the `gcloud workstations start-tcp-tunnel`
command that skips the proxy entirely.

`--deep` is the only mode that asserts the A2UI composite-catalog DataParts.
Progress events are text by design and the catalog parts are re-emitted on the
final artifact, so proving them costs a whole deck run.

The same checks run against a deployed revision, which is the smoke test worth
running after every deploy:

```bash
uv run python scripts/dev_local.py check --auth \
  --base https://YOUR_REGISTERED_A2A_HOST
```

`--auth` sends a `gcloud auth print-identity-token` bearer, since the service
is deployed `--no-allow-unauthenticated`. Only `--deep` reaches the AGY worker,
so it is the only run that proves the worker's sibling modules made it into the
image.

### Phase 5: Deploy to Dev
**Requires explicit human approval.** Run `agents-cli deploy` only after user confirms. See the **Deployment Guide** for details.

Before any Cloud Run deploy, validate the four bundled slide skills:

```bash
uv run python scripts/prepare_agy_skills.py --check
```

The worker discovers only `agy-worker/skills` by default. Its four skills cover
deck orchestration, slide craft, brand derivation, and template round-tripping.
Keep the broad repository skill catalog out of the worker and deployment
context. `SLIDEGEN_AGY_SKILLS_PATHS` is an explicit opt-in for custom skill
directories. `SLIDEGEN_AGY_WORKSPACES` can grant access to additional workspace
directories; otherwise the worker uses this project's directory, not its parent.

Do not add `google-antigravity` to the root project environment. It uses a
separate locked environment under `agy-worker/` because its Protobuf major
version is incompatible with the ADK/Agent Engine graph. Do not replace the
outer ADK agent: it owns the required A2A/A2UI and Gemini Enterprise composite
catalog behavior.

#### The AGY worker's own modules

`agy-worker/runner.py` is the harness entry point. Four modules hang off it,
each answering a question the deck gates could only answer too late.

| Module | What it does |
|---|---|
| `guards.py` | Denies writes into `clean/` and `spec-out/` once `layouts.json` has hashed them. A gate reports an invented plate after the turn is spent; this refuses the tool call. |
| `disclosure.py` | Lints the loaded skills for pages nothing links to, and records which skills the turn actually opened. Frontmatter matching happens in the Go harness, so the tool-call stream is the only place retrieval is observable. |
| `aids.py` | Synthesises one read-only SVG subagent per free region the template leaves, from `layouts.json` plus `brand-contract.json`. Protected layouts get none. |
| `eval_guidance.py` | Pairs each gate defect with what the turn had read when it made it, so a bad deck says whether to fix a description, a link, or a page. |

Run the tests on the worker's own environment, which is separate from the root
project's for the Protobuf reason above:

```bash
agy-worker/.venv/bin/python -m pytest agy-worker -q
```

`agy-worker/skills/pixelpitch-template-roundtrip/scripts` and
`agy-worker/skills/pixelpitch-deck/scripts/test_shot.py`
need Pillow and run under the renderer's interpreter instead.

Scoring a run:

```bash
agy-worker/.venv/bin/python agy-worker/eval_guidance.py \
  --events run.ndjson --defects check.json --defects lint.json
```

`run.ndjson` is the worker's stderr. The verdict's `fix_first` names the
cheapest edit: a skill nothing opened outranks a page read and ignored,
however many defects each accounts for.

### Phase 6: Production Deployment
Ask the user: Option A (simple single-project) or Option B (full CI/CD pipeline with `agents-cli infra cicd`).

## Development Commands

| Command | Purpose |
|---------|---------|
| `agents-cli playground` | Interactive local testing |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests |
| `agents-cli eval dataset synthesize` | Synthesize multi-turn eval scenarios for your agent |
| `agents-cli eval generate` | Run agent on eval dataset, produce traces |
| `agents-cli eval grade` | Run agent evaluations on the traces |
| `agents-cli eval compare` | Compare two grade-results files (regression check) |
| `agents-cli eval analyze` | Cluster failure modes from grade results |
| `agents-cli eval metric list` | List built-in metrics available in the SDK |
| `agents-cli eval optimize` | Auto-tune agent prompts using eval data |
| `agents-cli lint` | Check code quality |
| `agents-cli infra single-project` | Set up project infrastructure (Terraform) |
| `agents-cli deploy` | Deploy to dev |
| `agents-cli scaffold enhance` | Add deployment target or CI/CD to project |
| `agents-cli scaffold upgrade` | Upgrade project to latest version |

---

## Operational Guidelines for Coding Agents

- Git commits must not include `Co-authored-by` trailers or other co-author metadata.
- **Code preservation**: Only modify code directly targeted by the user's request. Preserve all surrounding code, config values (e.g., `model`), comments, and formatting.
- **NEVER change the model** unless explicitly asked.
- **Model 404 errors**: Fix `GOOGLE_CLOUD_LOCATION` (e.g., `global` instead of `us-central1`), not the model name.
- **ADK tool imports**: Import the tool instance, not the module: `from google.adk.tools.load_web_page import load_web_page`
- **Run Python with `uv`**: `uv run python script.py`. Run `agents-cli install` first.
- **Stop on repeated errors**: If the same error appears 3+ times, fix the root cause instead of retrying.
- **Terraform conflicts** (Error 409): Use `terraform import` instead of retrying creation.
