# A2A background deck jobs

The workstation preview separates a chat turn from deck generation. A submission
turn completes after SQLite saves the job. An independent process generates the
deck, records progress, and stores the result. A completed A2A submission task
therefore means **request saved**, not **PowerPoint delivered**.

```text
Gemini Enterprise: Generate deck
  -> existing Cloud Run relay
  -> workstation A2A agent
  -> save job and return a final A2UI progress card

Independent workstation worker
  -> claim saved job with a renewable lease
  -> native PixelpitchAgent in inline execution mode
  -> existing template, AGY authoring, and renderer pipeline
  -> save progress, result images, and download URL

Gemini Enterprise: Check progress
  -> same A2A agent and conversation
  -> read saved job
  -> return a current progress, failure, or download card
```

## Ownership and delivery

`app/a2a_jobs.py` binds each workspace to the invocation's app, user, and session.
Action payloads cannot choose that identity. Job IDs alone do not grant access.
The workstation deployment uses GE's existing A2A context; it does not introduce
end-user OAuth or change the registration.

Each intake card includes a submission ID. Repeated clicks reuse that ID, including
after completion or cancellation. A new brief card creates a new ID. Another
submission while a deck is active returns the existing job instead of starting a
second deck.

`app/a2a_worker.py` reuses the queue and native worker adapter already used by the
MCP implementation. No MCP server, widget, or registration is involved. The
worker explicitly disables queue submission in its internal agent, so it executes
the pipeline once rather than recursively queueing another job.

The job card shows recorded phases, elapsed time, and the last recorded activity.
It contains no estimated percentage or raw tool trace. **Check progress** requests
a new snapshot. The current implementation does not auto-refresh cards or push
completion into an idle Gemini conversation.

## Restart and cancellation behavior

The API and worker have separate process groups. Restarting only the API preserves
the worker, its lease, and the queued job. The new API can read saved results even
after losing its in-memory ADK session, provided the A2A context is unchanged.

The queue retains briefs, progress, final preview images, and download references
at `.tmp/dev-local/<port>/a2a/jobs.sqlite`. The lifecycle preserves this directory.
It does not migrate an older inline request into the queue.

A worker renews a 30-second lease while processing a job. An expired lease marks
the job interrupted and rejects late writes. Recovery preserves the recorded
state; it does not resume an interrupted AGY turn. **Review brief** creates a new
attempt only after the user submits again.

Cancelling a queued job prevents execution. Cancelling a running job signals the
native pipeline and prevents result publication, including a completion race.
The worker applies a 30-minute job limit. A failed quality check is a failed job,
not a completed deck with a missing download.

## Deployment boundary

`scripts/dev_local.py up` sets `SLIDEGEN_A2A_JOB_DB` and starts the independent
worker. The ordinary Cloud Run agent still uses its existing inline path when
this setting is absent. The SQLite adapter refuses to run under `K_SERVICE`:
Cloud Run's container filesystem is not durable job storage.

This implementation is suitable for the existing Cloud Run-to-workstation preview.
A direct Cloud Run release needs a shared durable store and a separately hosted
worker. The repository contains a Firestore job adapter for the MCP deployment,
but this change does not wire, provision, or authorize it for A2A.

The unchanged template pipeline still validates source generation and content
fidelity. Incremental template-slide authoring, automatic card refresh, and
cross-conversation job recovery are separate changes.

See [the workstation operations guide](workstation-preview.md) for startup,
restart, relay access, and the verification commands.
