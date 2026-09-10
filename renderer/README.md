# Pixelpitch Slidegen renderer

FastAPI service that wraps the in-repo `slidify` HTML-to-PPTX converter and
stores results in Google Cloud Storage.

The slidegen ADK agent in `app/` calls this service;
Playwright, Chromium, LibreOffice, and tesseract live here instead of inside
the Cloud Run A2A agent.

## API

- `GET /healthz` -> `{"ok": true}`
- `POST /render`

```json
{
  "deck_title": "Q3 Board Update",
  "brand_id": "stripe",
  "slides": [{"title": "Title", "html": "<!doctype html>..."}]
}
```

Response:

```json
{
  "ok": true,
  "gs_uri": "gs://bucket/decks/2026/08/26/<id>-q3-board-update.pptx",
  "https_url": "https://storage.googleapis.com/... (signed, 7 days, or null)",
  "slide_count": 6,
  "native_area_ratio": 0.87
}
```

## Environment

| Variable | Purpose |
|----------|---------|
| `SLIDEGEN_GCS_BUCKET` | Bucket for rendered PPTX files (required) |
| `SLIDIFY_PROFILE` | `full` (visual measurement without raster correction; default), `balanced` (no oracle), or `fast` (no oracle or LLM adjudicator) |
| `SLIDIFY_TIMEOUT_SECONDS` | Per-attempt conversion timeout (default 600) |
| `PORT` | Listen port (default 8080; Cloud Run compatible) |

## Export quality

The `full` profile passes `--low-memory` to Slidify. This keeps its visual
comparison against browser screenshots but disables automatic raster
correction. That correction can replace a native title with a picture to
improve its visual score. Slidify then checks editability against the revised
plan, so its editability result alone does not detect the lost text.

The renderer checks authored words against text in the actual PowerPoint
before upload. Missing words and failed editability admission reject the
export. Visual discrepancies remain in `quality.fidelity_failures`; they are
diagnostics, not proof of missing text or a claim of pixel-identical output.

`scripts/check_template_fidelity.py` uses this same conversion path and
defaults to `--profile full`. It requires Chromium, LibreOffice, tesseract,
and the source fonts for a representative full-profile check. Its local
`conversion.json` records visual results separately from content admission.

## Run locally

From the repository root:

```bash
uv run --project renderer \
  uvicorn renderer.main:app --port 8080
```

## Build the container

From the repository root:

```bash
docker build -f renderer/Dockerfile \
  -t pixelpitch-slidegen-renderer .
```

For Cloud Build, keep the same root context but use the renderer-specific
ignore file to upload the renderer, local `vendor/slidify` package, and required
template extraction helper:

```bash
gcloud builds submit . \
  --config renderer/cloudbuild.yaml \
  --ignore-file renderer/gcloudignore
```
