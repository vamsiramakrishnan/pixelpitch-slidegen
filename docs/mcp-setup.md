# Set up the MCP App in Gemini Enterprise

Deploy the private MCP API and deck worker, then connect them to your Gemini
Enterprise app. You supply your project, OAuth client, and allowed user domains.
An existing A2A registration stays unchanged.

See [Architecture](architecture.md) for the service diagram and
[Template upload and recovery](template-operations.md) for automatic parsing.

## 1. Check prerequisites

Ask your administrator to confirm:

- Billing, Vertex model access, and approved Cloud Run regions in your runtime project.
- An existing Gemini Enterprise app and permission to create its data store.
- Permission to enable APIs, build images, deploy services, create service
  accounts, and apply scoped IAM grants. Do not grant yourself Owner to run setup.
- Permission to use a Google OAuth Web client with the intended users.
- Organization policy allows custom MCP connectors and their required domains.

The GE app can be in a different project from the runtime. The automatic
template trigger and its source bucket must be in the runtime project.

Deployment creates billable resources. MCP keeps one 4-CPU/8-GiB worker running
with CPU always allocated, plus a warm API instance. Review costs before deploying.

## 2. Prepare Cloud Shell

Open Cloud Shell and clone the repository into its persistent home directory:

```bash
git clone https://github.com/vamsiramakrishnan/pixelpitch-slidegen.git
cd pixelpitch-slidegen
```

Use your approved release revision. This checkout includes the renderer,
Slidify, and four slide-generation skills. No parent repository is required.

Install [mise](https://mise.jdx.dev/getting-started.html) if it is missing.
Review `mise.toml` before trusting it. From the checkout root, run:

```bash
mise trust
mise install
mise run setup
gcloud auth list
gcloud auth application-default login
```

Expected result: Python environments install, the four-skill check reports
`"status": "validated"` with four skills, and your intended Google account is active.

Keep Terraform state with this checkout, not in `/tmp` or Git. Use a separate
checkout and state per project. For team deployments, configure your approved
remote Terraform backend before the first apply.

## 3. Create a Google OAuth client

In Google Cloud Console, select the project that owns your OAuth configuration.

1. Open **Google Auth Platform**. Configure the app name, audience, support email,
   and contact information if prompted.
2. Use an internal audience for an organization-only app when available. For an
   external app in testing, add the intended test accounts.
3. Open **Clients** and create a **Web application** client.
4. Name it `Pixelpitch MCP` and add this exact authorized redirect URI:

   ```text
   https://vertexaisearch.cloud.google.com/oauth-redirect
   ```

5. Save the client ID. Keep the secret in your approved secret store until step 8.
   Do not put the secret in chat, Git, a terminal command, or `deploy.env`.
6. Choose exact user email domains to allow, such as `example.com,example.org`.
   Do not include `@`, wildcards, or spaces.

Expected result: a client ID ending in `.apps.googleusercontent.com`, a privately
stored secret, and an approved domain list. Use the same client in steps 4 and 8.
See Google's [Web OAuth guide](https://developers.google.com/identity/protocols/oauth2/web-server#creatingcred)
if console labels differ.

## 4. Configure your project

```bash
mise run configure
```

Enter your runtime project ID, region, model location, and deck bucket. Choose
`mcp` for interfaces and `automatic` for template preparation. Enter the client
ID and domains from step 3. Leave the authoring model blank to preserve an
existing override or use the release default.

Paste the full resource name of your GE app in this form:

```text
projects/GE_PROJECT_ID/locations/GE_LOCATION/collections/default_collection/engines/GE_APP_ID
```

Replace uppercase placeholders with your values. `GE_LOCATION` is the app's
location, not necessarily your Cloud Run region. The resource can contain a
project number instead of an ID. Deployment resolves the GE project's service
identity separately from the runtime project.

For an existing renderer, enter its actual deck bucket. Review `deploy.env` if
you need to change `ARTIFACT_REPO` from `pixelpitch-agents`. If that file already
exists, review it before `mise run configure --force`, which replaces it.

```bash
mise run config
mise run plan
mise run preflight
```

Expected result: mode `mcp`, your intended project and bucket, and passing
preflight. The plan is local and creates nothing. Configuration contains the
client ID, never its secret.

Load the validated values for the shell commands below:

```bash
eval "$(uv run python scripts/deploy_cli.py config --shell)"
```

Repeat this command if you reopen Cloud Shell. Commands below use these values,
not the shell's default Google Cloud project.

## 5. Deploy the services

Choose one path. Do not run both on the same installation.

### New deployment

If this checkout will own the new infrastructure, run:

```bash
mise run deploy
```

Confirm the project and review the infrastructure and IAM changes. With
`TEMPLATE_PREPARATION=automatic`, deployment also installs the upload trigger
and enqueues existing source templates. Mode `mcp` does not deploy an A2A service.

### Add MCP to an existing renderer

Do not run full `deploy` against shared infrastructure outside this checkout's
Terraform state. Confirm the renderer and image repository:

```bash
gcloud run services describe slidegen-renderer \
  --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)'
gcloud artifacts repositories describe "$ARTIFACT_REPO" \
  --project "$PROJECT_ID" --location "$REGION" --format='value(format)'
```

Expected result: the renderer URL and `DOCKER`. Then deploy only MCP:

```bash
mise run deploy-mcp
```

This does not change the A2A service or registration. To add automatic template
preparation, follow [Template upload and recovery](template-operations.md) after
MCP deploys. `deploy-mcp` alone does not install Eventarc.

### Check the deployment result

Expected result: a private `pixelpitch-mcp` API, a `pixelpitch-mcp-worker`, a
dedicated Firestore database, and private draft storage. Deployment uses an
immutable image and grants the GE service agent access to the API. It mounts
the renderer's Secret Manager key if needed.

Do not scale the worker to zero while accepting jobs. After a failed step,
keep Terraform state, fix the cause, and rerun that task. Do not delete an
existing resource to bypass an import or permission error.

## 6. Verify and get your MCP URL

```bash
mise run verify-mcp
gcloud run services list \
  --project "$PROJECT_ID" --region "$REGION" \
  --filter='metadata.name:pixelpitch-mcp' \
  --format='table(metadata.name,status.latestReadyRevisionName,status.url)'
```

Expected result: a healthy private API, rejection without a user token, and
both service revisions. Your verification account needs Cloud Run Invoker.
Ask an administrator if the check fails with 403.

Derive the endpoint using the same format as the deployment:

```bash
SLIDEGEN_PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
SLIDEGEN_MCP_ORIGIN="https://pixelpitch-mcp-${SLIDEGEN_PROJECT_NUMBER}.${REGION}.run.app"
printf 'MCP Server URL: %s/mcp\n' "$SLIDEGEN_MCP_ORIGIN"
```

Copy the printed URL for step 8. Use this default `.run.app` endpoint, not a
workstation proxy or custom domain. A browser visit to the private endpoint is
not a widget test.

## 7. Check organization policy

Ask the GE administrator to allow custom MCP connectors and egress to the MCP
hostname printed in step 6, `accounts.google.com`, and `oauth2.googleapis.com`.
If data-source types are restricted, allow `custom_mcp` through the approved
policy process. Do not make the service public to bypass policy.

Google's [custom MCP setup guide](https://docs.cloud.google.com/gemini/enterprise/docs/connectors/custom-mcp-server/set-up-custom-mcp-server)
describes these requirements and the separate service identity and user OAuth
headers. Pixelpitch requires both.

## 8. Create the Gemini Enterprise connector

In the intended GE app, create a new **Custom MCP Server** data store.
Choose **OAuth 2.0** and enter:

| Field | Value |
|---|---|
| MCP Server URL | The complete URL printed in step 6, ending in `/mcp` |
| Authorization URL | `https://accounts.google.com/o/oauth2/auth` |
| Authorization URL parameters | `&access_type=offline&prompt=consent` |
| Token URL | `https://oauth2.googleapis.com/token` |
| Client ID | The Web client ID from step 3 |
| Client Secret | Its matching secret from your private store |
| Scopes | `openid email profile` |
| PKCE | Enabled |

Do not paste shell variable names into console fields. The app uses its own
service accounts to call Vertex and Storage, so it does not need the user's
`cloud-platform` scope.

Choose **Verify Auth** or **Login**. Sign in with an account in an allowed domain.
Name the data store `Pixelpitch MCP`, finish creation, and wait for **Active**.
Keep any A2A entry unchanged.

## 9. Enable the widget actions

Open **Actions**. Reload custom actions if necessary, then enable:

```text
open_pixelpitch
deck_choices
start_deck
deck_status
deck_slide
cancel_deck
```

Only `open_pixelpitch` is model-facing. The other five are app-only. Confirm
that your tenant preserves this distinction before release. Workspace access
tokens must not become visible to the chat model.

Use this description if the connector asks for one:

```text
Pixelpitch creates editable PowerPoint presentations. Its interactive workspace
collects a brief, offers brand and template choices, shows slide drafts and
exported previews, and provides the completed PowerPoint download.
```

Use these agent instructions where an instruction field is available:

```text
When a user wants an interactive presentation-building workspace, call
open_pixelpitch with their topic. Ask them to review the brief and select
Generate deck in the widget. Opening the workspace does not start generation.
Do not claim that a PowerPoint exists until the app shows its download.
The remaining tools are for the widget, not chat commands.
```

## 10. Test in a signed-in conversation

Start a new GE conversation:

```text
Open the Pixelpitch MCP workspace. I want a one-slide presentation showing
the flow from a brief to live drafts to an editable PowerPoint.
```

Select a brand, set the slide count to one, and choose **Generate deck**. Check:

1. The editable brief opens before generation starts.
2. The request is saved and progress describes actual work.
3. A draft appears while the worker continues.
4. An available final preview is labelled **Actual PowerPoint export**.
5. **Download PowerPoint** opens a real `.pptx` with editable text.
6. Reopening the same widget reconnects to the saved job without resubmitting.

Next, upload an approved company template using the [template guide](template-operations.md).
Wait for readiness, request six slides, and inspect each exported slide.
Also test cancellation and two-user isolation. Local tests do not replace this host test.

## 11. Diagnose failures

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND (resource.labels.service_name="pixelpitch-mcp" OR resource.labels.service_name="pixelpitch-mcp-worker")' \
  --project "$PROJECT_ID" --freshness=30m --limit=60 \
  --format='table(timestamp,resource.labels.service_name,severity,textPayload)'
```

| Symptom | Check |
|---|---|
| 403 | Invoker access for the GE project's Discovery Engine service agent |
| 401 | OAuth client, approved user domain, token expiry, and consent |
| OAuth redirect mismatch | Exact redirect URI from step 3 |
| No widget | Tenant MCP Apps support, resource metadata, and enabled actions |
| Job stays queued | Worker startup, Firestore IAM, and always-allocated CPU |
| Template unavailable | Current file version's preparation status |
| Terraform resource already exists | Import into the correct state after reviewing ownership |

Drain active jobs before replacing the worker. Do not use `mise run destroy`
to retry setup. Keep logs, state, tokens, and generated customer decks private.
