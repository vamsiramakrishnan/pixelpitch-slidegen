# Deploy Pixelpitch from Cloud Shell

Use this guide for a customer-owned deployment. It installs a private renderer
and your choice of A2A, MCP App, or both. It does not use a workstation tunnel.

Deployment creates billable resources and changes IAM. The MCP option keeps a
4-CPU/8-GiB worker running continuously. A successful deploy is not proof that
Gemini Enterprise can render the widget: complete the signed-in release test.

## 1. Prepare the customer project

Ask the project administrator to confirm billing, Vertex model access, allowed
regions, and permission to enable APIs, build images, manage the listed services,
create service accounts, and apply their scoped IAM bindings. The deployment
does not grant the operator Owner or modify organization policy.

Use an existing Gemini Enterprise app. Record its full resource name, for example
`projects/GE_PROJECT_ID/locations/GE_LOCATION/collections/default_collection/engines/GE_APP_ID`.
The app can be in a different project. Its service agent receives Invoker on
the selected frontend service, not on every Cloud Run service in the project.

Open Cloud Shell and clone the repository into its persistent home directory:

```bash
git clone https://github.com/vamsiramakrishnan/pixelpitch-slidegen.git
cd pixelpitch-slidegen
```

Use your approved release revision. All commands below run from this repository
root. Do not deploy from `/tmp`, and do not copy a development `.env`.

If mise is not installed, use the [official installation instructions](https://mise.jdx.dev/getting-started.html).
Review this repository's `mise.toml` before trusting it. Then run:

```bash
mise trust
mise install
mise run setup
gcloud auth list
gcloud auth application-default login
mise run configure
mise run plan
```

`setup` installs the locked Python environments and checks the four slide skills.
Cloud Build installs the frontend's locked Node dependencies inside its image.
`configure` writes an ignored, owner-readable `deploy.env`; it does not deploy.
`plan` reads configuration only and does not contact Google Cloud.
Use [`mise run`](https://mise.jdx.dev/tasks/running-tasks.html) for these tasks.

### Customer choices

| Setting | Choices / effect |
|---|---|
| `DEPLOYMENT_MODE` | `a2a`, `mcp`, or `both`; default `a2a` |
| `PROJECT_ID`, `REGION` | Customer runtime project and Cloud Run region |
| `MODEL_LOCATION` | Vertex endpoint location, independently of container region |
| `AUTHORING_MODEL` | A model ID available to the customer; blank preserves an existing override or the release's code default |
| `DECK_BUCKET`, `ARTIFACT_REPO` | Output/template bucket and container repository |
| `TEMPLATE_PREPARATION` | `automatic` on upload, or `on-demand` when a template is first used |
| `GE_APP_ID` | Existing Gemini Enterprise app resource name |
| `GE_AGENT_ID` | Existing A2A registration ID, when reusing an entry |
| `MCP_OAUTH_CLIENT_ID` | Approved Google Web OAuth client ID; required for MCP |
| `MCP_ALLOWED_EMAIL_DOMAINS` | Exact user email domains, comma-separated; no wildcards |

Model selection is configuration, not a guarantee of model availability. Verify
the chosen model in the customer's project and location before a full deck run.
The setup does not silently switch existing conversation, reference, or critique
model overrides. Backups of an existing `.env` are private and ignored by Git.

For MCP, create the OAuth Web client first. Set its authorized redirect URI to
`https://vertexaisearch.cloud.google.com/oauth-redirect`. The **client secret goes
only into Gemini Enterprise's connector setup**, never into `deploy.env`, a build
argument, or the widget. See [MCP setup steps](docs/mcp-setup.md).

## 2. Review and deploy

```bash
mise run config
mise run preflight
mise run deploy
```

Confirm the target project when prompted, then review Terraform's resource and
IAM diffs before accepting its prompts. The template step prints and applies
a saved Terraform plan without a second confirmation prompt. The steps run sequentially: shared
infrastructure, immutable renderer build/deploy, selected frontends, optional
template preparation, registration, and service checks. MCP credentials are
validated before infrastructure work starts.

The common infrastructure currently creates both renderer and A2A service
accounts, even for MCP-only deployments; MCP-only does not deploy an A2A service
or create an A2A registration. New customer renderers use IAM plus a Secret
Manager API key. The MCP script detects that secret and mounts it in both MCP
services. It also supports an existing IAM-only renderer without inventing a key.

### A2A registration

The A2A path calls `agents-cli publish gemini-enterprise` with an explicit A2A
agent-card URL and the chosen app. Before publication, it lists existing
registrations and recognizes both URI-based and inline JSON cards. A matching
entry is reused. A listing error or a mismatched explicit `GE_AGENT_ID` stops
registration instead of creating a duplicate.

The registration must retain IAM authentication and the Gemini Enterprise
composite A2UI catalog. Do not add MCP OAuth configuration to the A2A entry.
An endpoint change on an existing registration requires a separate reviewed
update; the setup does not silently repoint an unrelated agent.

### MCP connection

The MCP path creates a separate private API and worker plus dedicated Firestore
and draft storage. It prints the `/mcp` endpoint and grants the selected GE app's
service agent Invoker access. Finish the guided OAuth/data-store steps in
[MCP setup](docs/mcp-setup.md). It does not create
the connector or handle its client secret automatically.

Google's [custom MCP connector guide](https://docs.cloud.google.com/gemini/enterprise/docs/connectors/custom-mcp-server/set-up-custom-mcp-server)
requires StreamableHTTP and describes private Cloud Run authentication: the
service token and the user's OAuth token are separate. Use the default `.run.app`
URL. Organization policy must allow the connector and its server/OAuth domains.

## 3. Understand the runtime

![MCP, A2A, and upload-triggered template preparation](docs/assets/architecture.svg)

Read the [plain-language architecture](docs/architecture.md) for the two flows.

Both interfaces share template parsing, source-bound authoring and renderer
quality gates. The source template contributes measured text/table geometry and
artwork; it is not merely an image reference. The output has editable native
text over preserved artwork. Some tables are rebuilt as editable text cells; original PowerPoint masters are not
transplanted. See [template architecture](docs/template-architecture.md).

The renderer compares actual exported content with authored content. OCR works
on normalized text regions, including tables, so a dark cover or small footer
does not disappear from page-segmentation OCR. This does not lower the threshold
or disable the independent missing-content check. Full-profile conversion keeps
quality measurement but does not allow oracle corrections to replace native
text with pictures. Small text inherited from a source may still trigger a
readability warning; it is not silently resized away from the source layout.

MCP opens the brief before generation, saves the job independently of the HTTP
request, shows drafts as they arrive, and replaces available previews with
actual exported-slide images. Preview images are not the editable deliverable;
the downloaded PPTX is. Images/HTML live in private GCS objects, not oversized
Firestore documents. The widget uses sanitized, script-free `srcdoc` previews
without blob URLs, workers, or external resource requests.

**A2A latency boundary:** the standalone Cloud Run A2A path still uses streamed
generation within its request. The fast saved-job A2A mode currently belongs to
the workstation experiment. Never set `SLIDEGEN_A2A_JOB_DB` on Cloud Run: its
SQLite backend explicitly rejects that environment. Choose MCP for the durable
off-request cloud job path; do not promise workstation timings for cloud A2A.

## 4. Upload a template and prove delivery

Follow the [template upload steps](docs/template-operations.md) to upload the
customer's `.pptx` directly under the configured bucket's `templates/` prefix.
With automatic preparation, each overwrite produces a new generation and a
deduplicated preparation job; prepared outputs do not trigger recursive work.

```bash
mise run templates-backfill
mise run templates-status
```

These commands require the automatic preparation deployment. In on-demand mode,
preparation happens on first use and can make that first request much slower.
For customer release, wait for the selected generation's ready bundle. Use the
[recovery guide](docs/template-operations.md) for a failed generation.

In the customer's signed-in Gemini Enterprise session:

1. A2A: open the existing agent, select the uploaded template, and request six slides.
2. MCP: open the widget, check the brief choices, and select **Generate deck**.
3. Check that progress describes actual milestones. Reconnect without resubmitting.
4. Download the actual PPTX. Check every slide against its selected source layout,
   inspect text and tables for editability, and review content/OCR quality results.
5. In MCP, confirm the final preview is labelled **Actual PowerPoint export**;
   any preview without an exported image must remain labelled as a draft.
6. Test cancellation and two-user isolation. Record click-to-acceptance,
   first-draft and final-download times separately.

Service checks (`mise run verify`, `mise run verify-mcp`) do not replace this test.
Run local UI/protocol tests with `mise run test-mcp`; those browser fixtures use
synthetic authoring/export and cannot establish actual deck quality or GE timing.

## 5. Operate and recover

- Keep this checkout and all three Terraform states in persistent Cloud Shell
  storage. Back them up securely before leaving or switching targets. They may
  contain sensitive values. Never commit state, plans, `.env` backups, or tokens.
- Use a separate checkout/state per project. Existing resources outside this
  state need reviewed Terraform imports, not deletion or blind recreation.
  For a team-managed release, configure your approved remote Terraform backend
  before the first apply; this bundle does not auto-create a state bucket.
- Rerun an individual failed task after fixing its cause. `mise run build`
  records a target-scoped digest; `deploy-renderer` never guesses `latest`.
- Drain MCP jobs before replacing its single worker. Replacement can interrupt
  an in-flight job; it never silently generates a second deck.
- Record service revisions and image digests. Roll back using Cloud Run traffic
  to a known-good revision; do not downgrade queue schemas blindly.
- MCP terminal records expire after seven days, draft objects after eight days,
  with a seven-day storage soft-delete window. Final-deck retention is managed
  by the shared bucket, separately from draft retention.
- `mise run destroy` is a legacy shared/A2A teardown, **not** a full MCP/template
  cleanup. Do not use it for a partial update. MCP storage has deletion guards;
  cleanup needs a separate reviewed plan and data-retention decision.

### Troubleshooting

| Symptom | Check |
|---|---|
| 503 `no tunnel here` | A stale workstation relay; customer deployment must use standalone Cloud Run, not that preview route |
| Renderer 403 | Caller service account's service-scoped Invoker and, when configured, renderer secret access/mount |
| MCP 401 after Cloud Run authentication | User OAuth token, expected client ID, verified email and allowed domain |
| Widget opens but job never starts | Warm worker, always-allocated CPU, Firestore IAM and worker logs |
| Template unavailable after overwrite | New generation's preparation status; do not reuse a stale revision |
| Source-looking slide fails OCR | Actual exported text/region OCR evidence; never resolve by rasterizing the text or lowering the gate |
| Existing registration not found | Correct GE app and read permissions; setup fails closed rather than creating a duplicate |
