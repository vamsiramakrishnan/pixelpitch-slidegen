# Workstation preview and A2UI timing

Run the native A2A agent on port 18090 for local experiments. Keep the production Cloud Run service and Gemini Enterprise registration unchanged. This route shortens the edit, restart, and measurement cycle; it does not itself make deck authoring faster.

```text
Test caller --impersonation--> Preview service account
             --generateAccessToken(port=18090, ttl=900s)--> Workstations API
             --Bearer workstation token--> HTTPS workstation proxy
             --> localhost:18090 --> native ADK / A2A agent
                                      --> existing private renderer and GCS
```

The caller identity is separate from the agent's runtime identity. The preview service account needs no renderer or template-bucket access. The agent uses the workstation's existing credentials for those calls.

## Start the preview

From the repository root, load your approved deployment configuration
and discover the existing A2A origin:

```bash
eval "$(uv run python scripts/deploy_cli.py config --shell)"
PREVIEW_PUBLIC_BASE="$(gcloud run services describe slidegen-agent --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
```

Then start the local preview:

```bash
uv run python scripts/dev_local.py up --port 18090 --no-check \
  --public-base="$PREVIEW_PUBLIC_BASE"
```

The command prints the exact proxy URL and a laptop TCP-tunnel alternative. Open the `/dev-ui/?app=app` URL as a top-level browser navigation to establish the workstation login session for that port. An unauthenticated asset redirect is not an application CORS failure.

`--no-check` avoids the older lifecycle smoke test's deck-generation action. The timing probe below sends a plain intake request, reads the completed editable brief, and never sends `generate_deck`.

Restart only the API while a background deck is running:

```bash
uv run python scripts/dev_local.py down --port 18090 --keep-worker
uv run python scripts/dev_local.py up --port 18090 --no-check --no-sync \
  --public-base="$PREVIEW_PUBLIC_BASE"
```

The worker keeps its loaded code until it restarts. To update worker or authoring
code, first let active jobs finish or cancel them, then run `down` without
`--keep-worker` before `up`. Saved jobs and logs remain on disk in either case.

## Verify a saved background job

The [A2A background-job architecture](a2a-background-jobs.md) describes ownership,
duplicate-click handling, and restart limits. A job card is a snapshot, so click
**Check progress** for its current state. Type `status` in the same conversation
to retrieve the latest job if the card is no longer visible.

The following command starts one real deck and prints its context ID, job ID,
job status, and response time:

```bash
uv run python scripts/check_a2a_jobs.py \
  --base "$PREVIEW_PUBLIC_BASE" --auth \
  submit --topic "One-slide introduction to our team" --slides 1
```

Use the returned identifiers to check progress without starting another job:

```bash
uv run python scripts/check_a2a_jobs.py \
  --base "$PREVIEW_PUBLIC_BASE" --auth \
  status --context CONTEXT_ID --job JOB_ID
```

Replace `status` with `cancel` only to stop that job. `chat_turn_completed: true`
means the chat response ended. Delivery requires `job_status: completed` and the
result's PowerPoint link. The script measures wire arrival, not GE browser paint.

## Set up a keyless caller

Create a dedicated service account in the workstation project. On that account only, grant the identity running the probe `roles/iam.serviceAccountTokenCreator`. Do not create a service-account key or grant token-creator at project level.

Then grant the preview account `roles/workstations.user` on this workstation with `destination.port == 18090`:

```bash
uv run python scripts/workstation_preview.py authorize \
  --service-account=PREVIEW_ACCOUNT@PROJECT_ID.iam.gserviceaccount.com
```

This command reads the current IAM policy at version 3, preserves its etag and existing bindings, and adds one conditional grant. A concurrent policy change fails instead of being overwritten. See [Google's port-sharing instructions](https://docs.cloud.google.com/workstations/docs/configure-port-access).

### Runtime service-account permission can be an additional gate

If the workstation configuration has explicit service-account scopes, the Workstations API can also require `iam.serviceAccounts.actAs` on its runtime service account.

The script does **not** grant this permission. It is broader than preview-port access and needs separate approval. An administrator can grant `roles/iam.serviceAccountUser` on that specific runtime account to the preview account after reviewing its privileges. Do not grant it project-wide or remove workstation security settings to make the test pass.

## Measure localhost and proxy separately

```bash
uv run python scripts/workstation_preview.py probe \
  --service-account=PREVIEW_ACCOUNT@PROJECT_ID.iam.gserviceaccount.com \
  --runs=3
```

The probe checks that unauthenticated access fails and that the preview account cannot mint a token for another port. It then checks the current Dev UI assets, composite-catalog agent card, and real streaming A2A intake on localhost and through the proxy.

Results distinguish response headers, first SSE event, first text, first A2UI surface, first component update, and completed turn. These are wire-arrival timings, not browser interactivity or slide-generation timings. `first_event_ms` alone is not a useful-response metric. The probe requires a completed turn with A2UI components.

Use `--browser` with an interpreter that already has Playwright and Chromium installed, such as the renderer environment. It captures a Dev UI screenshot, paint times, browser errors, and failed responses. Browser authentication headers are attached only to the exact preview origin. Tokens stay in memory and expire after 15 minutes. They never go into report files, URLs, or screenshots. [Workstation token API](https://docs.cloud.google.com/workstations/docs/reference/rest/v1/projects.locations.workstationClusters.workstationConfigs.workstations/generateAccessToken)

Evidence lives under `.tmp/workstation-preview/18090/`. Treat this as local diagnostic data, not source content. The probe creates a few intake sessions in the local agent's session store. It does not start an authoring job.

`--current-identity` can test an already-authorized ADC identity instead of impersonating the preview account. It explicitly skips the port-boundary assertion and is not proof of the dedicated service-account setup.

## What to optimize and how to prove it

Measure one change at a time against the same prompt and source generation:

| Question | Evidence |
| --- | --- |
| Does the proxy delay or buffer progress? | Compare first-text and inter-event arrival locally and through the proxy |
| Is intake waiting on template browsing? | Compare first text with the completed brief; trace renderer listing and GCS gallery lookups |
| Does feedback appear before expensive work? | Observe progress while the task is running, not only in the final artifact |
| Are warmed templates useful? | Compare admitted-bundle deck runs with first-use preparation separately |
| Does the UI actually feel faster in GE? | Run the same action in Gemini Enterprise and inspect the rendered form and progress |

The native agent already emits an intake status before catalog work and loads brands, styles, and templates concurrently. Preserve source-generation checks and template admission when testing caches or prewarming. Do not replace them with stale template content to improve a timing number.

## Gemini Enterprise remains a separate release gate

The workstation probe negotiates the same A2UI extension and composite catalog as the A2A service. The ADK Dev UI is not the Gemini Enterprise renderer. A passing probe cannot establish GE widget fidelity or perceived latency.

A Cloud Run IAM identity token is not a workstation proxy access token. Merely granting the Discovery Engine service agent workstation access and changing the agent-card URL is not a verified integration.

Record and reuse your approved A2A registration and agent-card URL. Preserve
its composite catalog and authentication settings. Do not copy another tenant's
registration ID or repoint a production entry during a local-only test.

The relay in `workstation-relay/relay.py` keeps that registered Cloud Run entry point. It exchanges workload credentials for a port-scoped workstation token, preserves A2A extension headers, forwards response chunks as received, and returns an error on workstation outages. It never retries an agent request or silently switches runtimes mid-task. Only the A2A RPC and agent-card paths are exposed; the Dev UI remains on the workstation URL.

The relay image inherits the prior agent image by digest and adds only the relay module and startup command. This avoids releasing unrelated local agent changes to Cloud Run. The workstation itself runs the current checkout. The runtime identity, private Cloud Run invoker policy, model settings, and existing GE registration are retained.

The relay is experimental. Validate buffering, authentication, and actual GE
rendering in your own environment. A successful localhost test is not proof of
end-to-end perceived latency through the workstation proxy.

### Release and rollback

Build from the repository root using `workstation-relay/cloudbuild.yaml` and its `gcloudignore`. Supply `_AGENT_IMAGE` as the prior agent's immutable image digest and a unique `_TAG`. Deploy the resulting relay image to `slidegen-agent` with `--no-traffic --tag=workstation-preview`, retaining the existing service settings and adding these variables:

- `WORKSTATION_RELAY_RESOURCE`: the full workstation resource name.
- `WORKSTATION_RELAY_ORIGIN`: the exact port-18090 HTTPS workstation origin.
- `WORKSTATION_RELAY_PORT`: `18090`.
- `WORKSTATION_RELAY_CALLER_SA`: the approved preview service account email.

Test the tagged URL with Cloud Run authentication before switching traffic. Check the agent-card URL, confirmed A2UI extension, complete form, and execution header. Do not mistake `/healthz` for an upstream health check; it explicitly reports `upstream_checked: false`.

Before switching traffic, record the existing normal-agent revision. To roll
back after approval, replace `YOUR_KNOWN_GOOD_REVISION` below with that value:

```bash
gcloud run services update-traffic slidegen-agent \
  --project="$PROJECT_ID" --region="$REGION" \
  --to-revisions=YOUR_KNOWN_GOOD_REVISION=100
```

This restores normal execution without changing the GE registration. Future ordinary agent deploys should remove the four relay variables and use the normal agent image. Start a new GE conversation after switching execution modes: workstation sessions are local and are not the normal Cloud Run session store. Saved background jobs survive an API-only restart. Stopping the worker or workstation can interrupt active generation; the saved job records that interruption after its lease expires.

## Stop and revoke

Stop the experiment with `uv run python scripts/dev_local.py down --port 18090`.

To revoke access, remove only the `pixelpitch-preview-18090` conditional binding for the preview service account from this workstation's current IAM policy, preserving its etag and all other grants. Remove the probe runner's token-creator binding on the preview account if no longer needed. If an administrator approved an additional runtime-account `serviceAccountUser` grant, remove that exact binding as well.

Already-issued workstation tokens cannot be revoked individually. Let the 15-minute token expire or stop the workstation. Do not stop a shared workstation solely to clean up this preview without the owner's approval.
