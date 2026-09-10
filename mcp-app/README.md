# Pixelpitch MCP App

For cloud setup, start with the [step-by-step MCP guide](../docs/mcp-setup.md).
This page covers local development. See [Architecture](../docs/architecture.md)
for the shared renderer and template pipeline.

An interactive deck workspace with a loopback preview and separate production
API/worker entrypoints. It does not replace the existing ADK/A2A/A2UI agent or
change its Gemini Enterprise registration. Complete a signed-in tenant test
before release. Local verification does not establish cloud OAuth or Gemini
Enterprise compatibility.

## Try it locally

From the repository root:

```bash
uv sync --extra mcp-app --extra lint
cd mcp-app
npm ci
npm run build
cd ..
uv run --extra mcp-app python scripts/dev_local.py mcp
```

Open **http://127.0.0.1:18091** for the local SDK host. The MCP endpoint is
**http://127.0.0.1:18091/mcp**. The preview banner deliberately distinguishes
this host from Gemini Enterprise. An SSH/local port tunnel can expose the
loopback listener to your browser; do not bind the preview to a public address.

The form uses the existing brand/style/template catalogs. Generating a real
deck needs the same authoring and renderer configuration as the existing agent.
The launcher loads `.env`, removes cloud-only session configuration, and starts
a separate worker process. No authoring model is changed.

Stop with Ctrl+C. State is retained in
`.tmp/dev-local/18091/mcp/jobs.sqlite`; restarting does not delete it. A queued
job can be picked up after restart. An expired running job is marked interrupted,
not silently regenerated. The local host replays its original tool result from
session storage on reload, so the widget can reconnect to its workspace.

## What reduces the wait

- `open_pixelpitch` returns the app and a workspace capability without loading
  catalogs, invoking a model, or contacting the renderer.
- The brief is editable while independent catalog calls complete. A slow
  template catalog does not block choosing a brand.
- `start_deck` commits a queued job and returns. A separate worker runs the
  existing `PixelpitchAgent` through ADK, including its validation and template
  preparation. HTTP disconnects do not cancel this worker.
- Status polling returns small snapshots, not slide HTML. It does not overlap
  requests, backs off on connection failures, slows in hidden tabs, and stops at
  completion or widget teardown.
- Actual authored slides become browsable while the worker continues. The
  template lane only exposes drafts after its patches are assembled. Rewrites
  replace a draft without inflating the slide count.
- Selecting a slide fetches only that draft. Local selection, focus, and
  submission feedback do not require another model turn.

There are no estimated percentages or invented ETAs. Previews without a matching
export image remain labelled as drafts after the exported file is ready.

## Safety and scope

Each workspace receives a random capability, stored hashed and sent
to the widget in tool-result `_meta`, outside the model-visible summary. Every
job read, slide read, submission and cancellation checks that capability and
workspace ownership. Retries use an idempotency key; changed requests cannot
reuse the same key. The server rejects foreign Host and Origin headers.

Preview HTML is sanitized, rendered in a script-free sandbox, and restricted
to inline styles and data-URL images/fonts. The app makes **no direct network
requests**. Previews use `srcdoc`, with no `blob:` frame permission. Confirm the
nested script-free preview in the customer's Gemini Enterprise host. There are
no workers, external JS, external fonts, or `unsafe-eval` requirements.

After export, available slide previews are replaced with images rendered from
the actual PowerPoint. Each preview is labelled as an export or an authoring
draft; an absent or oversized export image never silently relabels a draft.

Cancellation is cooperative. The worker forwards it to the existing pipeline.
An already submitted remote render cannot necessarily be recalled, even if
the local job is cancelled; the app does not publish its eventual result.

The local preview uses SQLite. Production uses a dedicated Firestore database
for transactional job state and private GCS objects for slide HTML, avoiding
Firestore's document size limit. A separate always-CPU worker claims jobs under
leases; API requests never own the generation task. One active deck per verified
Google user, idempotent submissions and bounded queue capacity prevent duplicate
or unbounded generation. Jobs have a configurable 30-minute execution deadline.

The cloud API verifies Google OAuth audience, subject, expiry and allowed email
domain, then binds every capability to that user. Cloud Run IAM independently
checks Gemini Enterprise's service identity. A shared Discovery Engine invoker
identity is not an end-user identity. Do not reuse the A2A registration's empty
authorization settings for MCP. Deployment and registration need separate approval.

See [DEPLOY.md](DEPLOY.md) for scoped IAM, private storage, retention, immutable
image builds, worker CPU allocation, OAuth configuration and the live release gate.
Use `app.mcp_cloud:create_app`, never the SQLite preview launcher, on Cloud Run.

## Connecting to Gemini Enterprise

Follow the [MCP setup guide](../docs/mcp-setup.md). Deploying an updated A2A agent
is not a prerequisite. MCP runs the native agent in its own worker and does not
invoke the A2A endpoint. Keep the existing A2A registration unchanged.

The [manual deployment reference](DEPLOY.md) describes resource and IAM details.
Choose one provisioning method per installation. Do not mix manual resource
creation with Terraform without reviewed imports.

## Verify

```bash
# From the repository root
uv run --extra mcp-app --extra lint python scripts/check_mcp.py all

# From mcp-app
npx playwright install chromium
```

Install the local Firestore emulator with `gcloud components install
cloud-firestore-emulator` (or the matching Cloud SDK package on apt-managed
installations). `check_mcp.py cloud` starts a temporary loopback emulator and
tests the shared queue with anonymous credentials and random test projects.
`context` inspects the actual Cloud SDK upload matcher without submitting a
build; `local` runs the agent tests, TypeScript/build checks and browser suite.
Install npm dependencies and Chromium before `all`. No check changes cloud state.

For an explicit live-generation check, start the preview on port 18093, then run:

```bash
uv run --extra mcp-app python scripts/smoke_mcp.py
```

This incurs normal model/rendering costs. It submits one slide through the real
MCP SDK, waits for native authoring and rendering, downloads the signed PPTX and
checks its slide count and native text. Evidence goes to `.tmp/mcp-live-proof/`.

The four browser scenarios run the built widget, official `AppBridge`, real MCP HTTP
transport, SQLite queue, independent worker, and native ADK agent. Only
authoring and rendering are replaced with clearly labeled synthetic fixtures.
It does not prove live authoring, a real PPTX download, or Gemini Enterprise
compatibility. Screenshots, timing measurements, and failure traces are saved
under `../.tmp/mcp-app-browser/`.

This npm package has its own lockfile for the UI SDK and build dependencies.
It does not require Bun or a parent JavaScript workspace. The generated
single-file `dist/index.html` is served as `text/html;profile=mcp-app`; it is a
build artifact, not a new hand-maintained JavaScript source.

Protocol references: [MCP Apps SDK](https://github.com/modelcontextprotocol/ext-apps),
[app-only polling](https://apps.extensions.modelcontextprotocol.io/api/documents/patterns.html),
[Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk).
