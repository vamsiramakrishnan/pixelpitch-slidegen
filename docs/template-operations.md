# Prepare templates after upload

Install the upload pipeline once. After that, adding or replacing a `.pptx`
starts preparation without a chat request or agent redeployment. Read
[how Eventarc and the worker fit together](template-architecture.md#in-plain-language)
for the plain-language explanation.

## 1. Configure the deployment

From the repository root, run:

```bash
mise run configure
mise run config
mise run plan
```

Use your project, region, and deck bucket. Choose `automatic` template
preparation and the interfaces you want connected: `a2a`, `mcp`, or `both`.
For a new MCP installation, complete [MCP setup](mcp-setup.md) first.

The source bucket must be in the deployment project. The Eventarc trigger uses
the bucket's location, which can differ from the worker's Cloud Run region.
The deployment discovers that location rather than assuming `us-central1`.

## 2. Install the pipeline

For a new installation, `mise run deploy` includes this pipeline when the
configuration selects `automatic`. Do not run a full deployment against
existing resources outside this checkout's Terraform state.

For an existing installation, first confirm that the renderer and all selected
interfaces are deployed from a compatible release. Then run:

```bash
mise run deploy-templates
```

This command changes Cloud Run, Eventarc, Cloud Tasks, and IAM. Obtain approval
for the target first. The template script prints a Terraform plan and applies
that saved plan without a second confirmation prompt.

The command:

1. Builds the template worker from the renderer's immutable image.
2. Creates the private `slidegen-template-worker` service.
3. Creates the `slidegen-templates` Cloud Tasks queue.
4. Creates the `slidegen-template-upload` Eventarc trigger.
5. Grants the selected runtime identities access to enqueue preparation tasks.
6. Updates those services with queue settings and enqueues existing templates.

Mode `mcp` connects the MCP API and worker without updating A2A. Mode `both`
updates all selected services. It does not change GE registrations.

The module at `deployment/terraform/template-preparation` has its own Terraform
state. Keep it. If resources already exist, review/import them into that state.
Never delete the worker or source bucket to bypass an ownership conflict.

Expected result: deployment prints `Template trigger deployed; backfill enqueued`.
This confirms setup, not successful parsing. Check readiness in step 4.

## 3. Upload or replace a source

Load your validated configuration into the shell:

```bash
eval "$(uv run python scripts/deploy_cli.py config --shell)"
```

Place your approved template at `./company-template.pptx`, then upload it:

```bash
gcloud storage cp ./company-template.pptx \
  "gs://${DECK_BUCKET}/templates/company-template.pptx" --project "$PROJECT_ID"
```

The default source prefix is `templates/`. If your installation overrides
`SLIDEGEN_TEMPLATE_PREFIX`, use that prefix instead. Put the `.pptx` directly
under it. `templates/team/company.pptx` is nested and is not a source this
pipeline accepts. PDFs and generated files are ignored by the receiver.

To update a template, run the same upload command with the replacement file.
The URI stays the same, but the Storage generation changes. Updating only
metadata does not trigger parsing. An upload event can be delivered more than
once; named tasks and ready markers prevent duplicate publication.

## 4. Wait for the current version to be ready

```bash
mise run templates-status
```

Each JSON line includes the source URI, generation, worker status, and `ready`.
Wait for `ready: true` for the current generation. The statuses mean:

| Status | Meaning |
|---|---|
| `not_started_or_queued` | No worker status yet; inspect the trigger and queue |
| `running` | The worker is parsing or validating the source |
| `ready` | A complete bundle has been published |
| `failed` | Inspect the error; automatic retries may still be pending |
| `superseded` | A newer source version replaced this job's input |

Refresh a previously opened brief after replacing a template. The old pinned
selection is rejected; it is not silently replaced with different content.
PowerPoints already generated are not modified.

## 5. Check a delayed or failed job

Repeat the configuration load from step 3 if you opened a new shell. Derive
the bucket location, then inspect the trigger, tasks, and worker:

```bash
SLIDEGEN_BUCKET_LOCATION="$(gcloud storage buckets describe "gs://${DECK_BUCKET}" --project "$PROJECT_ID" --format='value(location)')"
SLIDEGEN_EVENTARC_LOCATION="${SLIDEGEN_BUCKET_LOCATION,,}"
gcloud eventarc triggers describe slidegen-template-upload \
  --location "$SLIDEGEN_EVENTARC_LOCATION" --project "$PROJECT_ID"
gcloud tasks list --queue slidegen-templates \
  --location "$REGION" --project "$PROJECT_ID"
gcloud run services describe slidegen-template-worker \
  --region "$REGION" --project "$PROJECT_ID"
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="slidegen-template-worker" AND severity>=ERROR' \
  --project "$PROJECT_ID" --freshness=1h --limit=30
```

Cloud Tasks dispatches one preparation attempt at a time, with up to three
attempts. One attempt can run for up to 30 minutes. These are limits, not
expected completion times. A queue behind another template is not necessarily stuck.

To recover missed upload events or sources uploaded before deployment, run:

```bash
mise run templates-backfill
```

Backfill enqueues current sources that lack a ready bundle. Repeating it does
not reparse ready versions. It cannot bypass Cloud Tasks' retention of an
exhausted task name.

After fixing a terminal failure, request a new attempt:

```bash
uv run python scripts/deploy_templates.py retry \
  --gs-uri "gs://${DECK_BUCKET}/templates/company-template.pptx" \
  --retry-id incident-123-attempt-2
```

Choose a distinct retry ID for each deliberate retry. Uploading corrected bytes
also creates a fresh generation and task. Missing source fonts require a renderer
image update followed by a template-worker rebuild, not silent font substitution.

## 6. Verify the upload-to-deck path

1. Upload an approved test template under a distinct source name.
2. Record its generation and wait for that version's `ready: true`.
3. Select it in GE and generate a small deck with a body slide and a table.
4. Download the PowerPoint. Inspect artwork, fonts, editable text, table content,
   and page numbers. A preview or health check is insufficient.
5. Replace the same source URI. Confirm a new generation is prepared and an old
   pinned selection is rejected.
6. Repeat backfill and confirm the ready version does not start another authoring run.

Retain the task ID, source generation, bundle ID, PowerPoint, and rendered previews
in private release evidence. Do not claim the trigger works until this path passes.

## Pause or resume delivery

To stop new task dispatches during an incident:

```bash
gcloud tasks queues pause slidegen-templates --location "$REGION" --project "$PROJECT_ID"
```

An already-running attempt can continue. After repair:

```bash
gcloud tasks queues resume slidegen-templates --location "$REGION" --project "$PROJECT_ID"
```

Keep source files and committed bundles during recovery. Deleting storage,
the worker, or Terraform state is not a retry procedure.
