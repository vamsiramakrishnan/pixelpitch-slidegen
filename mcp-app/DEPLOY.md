# MCP deployment reference

For the numbered, user-executed setup, follow [Set up the MCP App](../docs/mcp-setup.md).

The setup guide distinguishes new infrastructure from adding MCP to an existing
renderer. The commands below are the manual provisioning path. Use one method
per installation. Do not mix manually created resources with Terraform without
reviewed imports into the correct state.

Production entrypoints and local verification are implemented. This reference
does not establish that your cloud resources or GE connector are deployed.
The existing `slidegen-agent` A2A service and registration remain separate.

These commands create billable resources and change IAM. Run them only after
approval for the target project, OAuth client, allowed email domains and costs.
The initial configuration keeps one API instance and one 4-CPU worker warm.
The worker requires instance-based billing because jobs run outside HTTP requests.

## 1. Choose the identity and target

Create a Google OAuth **Web application** client, with authorized redirect URI
`https://vertexaisearch.cloud.google.com/oauth-redirect`. Configure its consent
screen and audience for the intended users. The client secret belongs only in
the Gemini Enterprise connector configuration, never in this image, repository,
or browser. The server only needs the public client ID and exact allowed domains.

From the repository root, set these reviewed values in your shell:

```bash
export MCP_PROJECT='YOUR_PROJECT_ID'
export MCP_PROJECT_NUMBER='YOUR_PROJECT_NUMBER'
export MCP_GE_PROJECT_NUMBER='YOUR_GEMINI_ENTERPRISE_PROJECT_NUMBER'
export MCP_REGION='us-central1'
export MCP_CLIENT_ID='YOUR_OAUTH_CLIENT_ID.apps.googleusercontent.com'
export MCP_DOMAINS='YOUR_COMPANY_DOMAIN'
export MCP_RENDERER_SERVICE='slidegen-renderer'
export MCP_RENDERER_URL='YOUR_EXISTING_RENDERER_HTTPS_URL'
export MCP_ARTIFACT_BUCKET='YOUR_EXISTING_RENDERER_ARTIFACT_BUCKET'
export MCP_DATABASE='pixelpitch-mcp'
export MCP_BUCKET="${MCP_PROJECT}-pixelpitch-mcp-drafts"
export MCP_API_SA="pixelpitch-mcp-api@${MCP_PROJECT}.iam.gserviceaccount.com"
export MCP_WORKER_SA="pixelpitch-mcp-worker@${MCP_PROJECT}.iam.gserviceaccount.com"
export MCP_URL="https://pixelpitch-mcp-${MCP_PROJECT_NUMBER}.${MCP_REGION}.run.app"
export MCP_IMAGE="${MCP_REGION}-docker.pkg.dev/${MCP_PROJECT}/pixelpitch-mcp/app:$(date -u +%Y%m%d-%H%M%S)"
```

This recipe assumes the renderer is in the same project and region. If it is
elsewhere, change only the renderer IAM command's project/region. Verify the
project number with `gcloud projects describe`. Preserve the existing authoring
model and model location. The recipe uses the existing code defaults with
`GOOGLE_CLOUD_LOCATION=global`; carry over any explicit model overrides from
the approved A2A configuration to the MCP worker, without changing their values.

## 2. Provision dedicated storage and identities

For existing resources, inspect their configuration and IAM instead of blindly
recreating them. Never delete a resource to work around an “already exists” error.

```bash
gcloud services enable firestore.googleapis.com storage.googleapis.com \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com --project="$MCP_PROJECT"

gcloud firestore databases create --database="$MCP_DATABASE" \
  --location="$MCP_REGION" --type=firestore-native --delete-protection \
  --project="$MCP_PROJECT"

gcloud storage buckets create "gs://${MCP_BUCKET}" --location="$MCP_REGION" \
  --uniform-bucket-level-access --public-access-prevention \
  --soft-delete-duration=7d --project="$MCP_PROJECT"
gcloud storage buckets update "gs://${MCP_BUCKET}" \
  --lifecycle-file=mcp-app/drafts-lifecycle.json --project="$MCP_PROJECT"

gcloud iam service-accounts create pixelpitch-mcp-api --project="$MCP_PROJECT"
gcloud iam service-accounts create pixelpitch-mcp-worker --project="$MCP_PROJECT"

for MCP_ACCOUNT in "$MCP_API_SA" "$MCP_WORKER_SA"; do
  gcloud projects add-iam-policy-binding "$MCP_PROJECT" \
    --member="serviceAccount:${MCP_ACCOUNT}" --role=roles/datastore.user \
    --condition="expression=resource.name=='projects/${MCP_PROJECT}/databases/${MCP_DATABASE}',title=pixelpitch-mcp-database"
  # The API loads template choices; the worker renders and prepares templates.
  gcloud run services add-iam-policy-binding "$MCP_RENDERER_SERVICE" \
    --region="$MCP_REGION" --project="$MCP_PROJECT" \
    --member="serviceAccount:${MCP_ACCOUNT}" --role=roles/run.invoker
  gcloud storage buckets add-iam-policy-binding "gs://${MCP_ARTIFACT_BUCKET}" \
    --member="serviceAccount:${MCP_ACCOUNT}" --role=roles/storage.objectViewer
done

gcloud storage buckets add-iam-policy-binding "gs://${MCP_BUCKET}" \
  --member="serviceAccount:${MCP_API_SA}" --role=roles/storage.objectViewer
gcloud storage buckets add-iam-policy-binding "gs://${MCP_BUCKET}" \
  --member="serviceAccount:${MCP_WORKER_SA}" --role=roles/storage.objectCreator
gcloud storage buckets add-iam-policy-binding "gs://${MCP_ARTIFACT_BUCKET}" \
  --member="serviceAccount:${MCP_WORKER_SA}" --role=roles/storage.objectUser \
  --condition="expression=resource.name.startsWith('projects/_/buckets/${MCP_ARTIFACT_BUCKET}/objects/templates/') || resource.name.startsWith('projects/_/buckets/${MCP_ARTIFACT_BUCKET}/objects/catalogs/'),title=pixelpitch-template-preparation"
gcloud projects add-iam-policy-binding "$MCP_PROJECT" \
  --member="serviceAccount:${MCP_WORKER_SA}" --role=roles/aiplatform.user \
  --condition=None

for MCP_COLLECTION in pixelpitch_mcp_jobs pixelpitch_mcp_workspaces pixelpitch_mcp_owners; do
  gcloud firestore fields ttls update expires_at --collection-group="$MCP_COLLECTION" \
    --database="$MCP_DATABASE" --enable-ttl --project="$MCP_PROJECT"
done

gcloud artifacts repositories create pixelpitch-mcp --repository-format=docker \
  --location="$MCP_REGION" --project="$MCP_PROJECT"
```

Active jobs deliberately have no Firestore TTL: deleting them would lose their
queue quota bookkeeping. The worker expires abandoned queued jobs after one
hour and interrupts stale running jobs after their 30-second lease. Terminal
jobs and idle ownership records expire after seven days; draft objects become
eligible for deletion after eight days, then have a seven-day soft-delete window.
These policies do not change the existing renderer's final PPTX retention.
The worker's write permission on the existing artifact bucket is limited to
template preparation and catalog prefixes. Templates sourced from other buckets
need separate, explicitly approved read access; do not add project-wide Storage
Admin to make them work.

## 3. Build the isolated image

```bash
uv sync --extra mcp-app --extra lint
uv run python scripts/prepare_agy_skills.py
uv run python scripts/prepare_agy_skills.py --check
uv run --extra mcp-app python scripts/check_mcp.py context

gcloud builds submit . --project="$MCP_PROJECT" --region="$MCP_REGION" \
  --config=mcp-app/cloudbuild.yaml \
  --ignore-file=mcp-app/Dockerfile.dockerignore \
  --substitutions="_IMAGE=${MCP_IMAGE}"
```

Review `.tmp/mcp-checks/upload-files.json` before uploading. The shared ignore
file is used by Cloud Build upload and the Docker build. It excludes local
credentials, databases, virtual environments and generated preview state.
The image includes Chromium and the locked Python visual-check dependencies,
so the authoring worker can inspect slides without runtime package downloads.
Do not use the agent's A2A `.gcloudignore`: it intentionally excludes MCP code.
The build identity needs Artifact Registry write access and its normal source
bucket/logging permissions; do not grant those permissions to the runtime users.

After the build, resolve its image digest and deploy that immutable reference:

```bash
export MCP_DIGEST="$(gcloud artifacts docker images describe "$MCP_IMAGE" \
  --project="$MCP_PROJECT" --format='value(image_summary.digest)')"
test -n "$MCP_DIGEST"
export MCP_RELEASE="${MCP_IMAGE%:*}@${MCP_DIGEST}"
```

## 4. Start private API and worker services

The `|` delimiter allows a comma-separated domain allowlist. No OAuth client
secret belongs in the services. Renderer calls use the workload's own
short-lived Google ID token and its service-scoped invoker permission. If the
renderer also requires `RENDERER_API_KEY` (the default customer mise deployment),
grant both MCP service accounts Secret Accessor on that specific secret and
mount it as `SLIDEGEN_RENDERER_API_KEY` in both services. The guided
`scripts/deploy_mcp.py` path detects and configures this automatically; the manual
commands below assume an IAM-only renderer and need that addition otherwise.

```bash
export MCP_ENV="^|^GOOGLE_CLOUD_PROJECT=${MCP_PROJECT}|GOOGLE_CLOUD_LOCATION=global|GOOGLE_GENAI_USE_VERTEXAI=true|MCP_DATABASE=${MCP_DATABASE}|MCP_DRAFT_BUCKET=${MCP_BUCKET}|MCP_PUBLIC_URL=${MCP_URL}|MCP_OAUTH_CLIENT_ID=${MCP_CLIENT_ID}|MCP_ALLOWED_EMAIL_DOMAINS=${MCP_DOMAINS}|SLIDEGEN_RENDERER_URL=${MCP_RENDERER_URL}"
export MCP_ENV="${MCP_ENV}|SLIDEGEN_GCS_BUCKET=${MCP_ARTIFACT_BUCKET}"

gcloud run deploy pixelpitch-mcp-worker --image="$MCP_RELEASE" \
  --project="$MCP_PROJECT" --region="$MCP_REGION" \
  --service-account="$MCP_WORKER_SA" --no-allow-unauthenticated \
  --cpu=4 --memory=8Gi --min-instances=1 --max-instances=1 \
  --concurrency=1 --no-cpu-throttling --timeout=60 \
  --set-env-vars="${MCP_ENV}|MCP_ROLE=worker|MCP_JOB_TIMEOUT_SECONDS=1800"

gcloud run deploy pixelpitch-mcp --image="$MCP_RELEASE" \
  --project="$MCP_PROJECT" --region="$MCP_REGION" \
  --service-account="$MCP_API_SA" --no-allow-unauthenticated \
  --cpu=1 --memory=2Gi --min-instances=1 --max-instances=3 \
  --concurrency=40 --timeout=60 \
  --set-env-vars="${MCP_ENV}|MCP_ROLE=api"

gcloud run services add-iam-policy-binding pixelpitch-mcp \
  --project="$MCP_PROJECT" --region="$MCP_REGION" \
  --member="serviceAccount:service-${MCP_GE_PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role=roles/run.invoker
```

Record both revision names and digests. Verify `MCP_URL` is an assigned default
Cloud Run URL, both services are private, and the worker has always-allocated
CPU. Worker replacement can interrupt a deck, but never silently regenerate it.
An already-started remote renderer request may continue after cancellation.
Do not scale the worker to zero while accepting new jobs. A future scaling or
Cloud Tasks design should be an explicit change, not a request-lifecycle task.

## 5. Connect Gemini Enterprise

Create a **new Custom MCP Server data store** in the intended Gemini Enterprise
app. Do not edit the A2A agent registration. Confirm the organization permits
custom MCP connectors and the necessary server/OAuth domains.

| Field | Value |
|---|---|
| Server URL | `${MCP_URL}/mcp` |
| Authorization URL | `https://accounts.google.com/o/oauth2/auth` |
| Authorization URL parameters | `&access_type=offline&prompt=consent` |
| Token URL | `https://oauth2.googleapis.com/token` |
| Client ID / secret | The approved Google Web OAuth client |
| Scopes | `openid email profile` |
| PKCE | Enabled |

Cloud Run validates GE's service ID token from `X-Serverless-Authorization`.
The app independently verifies the user access token from `Authorization`,
checking the configured OAuth client, subject, expiry, verified email and domain.
The shared service agent is never treated as the end user. Successful token
checks are cached for at most 60 seconds, so revocation is not instantaneous.

Suggested agent instructions:

> When someone wants to build a presentation, call `open_pixelpitch` with their
> topic. Ask them to review the brief and select Generate deck in the widget.
> The widget shows progress and real slide drafts, then a download when ready.
> Do not claim that a finished deck exists just because the widget has opened.

After OAuth login succeeds and the store is Active, reload and enable its
custom actions. Only `open_pixelpitch` is model-facing; the other five tools
must remain app-only. Verify the tenant supports that distinction before release.

Google's [connector setup guide](https://docs.cloud.google.com/gemini/enterprise/docs/connectors/custom-mcp-server/set-up-custom-mcp-server)
describes the connector and dual-header authentication path. Its setup and your
organization's OAuth policy must both permit the intended accounts.

## 6. Release gate and rollback

- Confirm unauthenticated access fails at Cloud Run, and a service token alone
  cannot invoke an MCP tool without a valid user token.
- In signed-in GE chat, open the widget and submit a small real deck. Inspect
  draft previews under the host's CSP and download/open the actual PPTX.
- Test reconnect, cancellation, expiration and two-user isolation in that host.
  Verify the host preserves tool-result `_meta` for reconnection.
- Measure chat-to-editable-widget and click-to-first-draft there. Emulator and
  local SDK-host timings do not include GE auth, rendering or cloud cold starts.
- Keep both service revision/digest pairs and the verification results. For an
  update, drain the queue before replacing its sole worker. For rollback, first
  disable the MCP actions, then restore both prior revisions with Cloud Run
  traffic controls. Do not delete Firestore, the draft bucket or the A2A service.

Complete this gate in each target environment. Local tests use Google's Firestore emulator, fake
Google token responses, and an in-memory object adapter. The browser tests use
synthetic authoring/rendering. They are not proof of live OAuth, GCS IAM, the
container build, a real exported deck, or Gemini Enterprise iframe compatibility.
