# Deployment inputs. `mise run infra` passes project_id, region, deck_bucket_name
# and artifact_repo from deploy.env, so those are only defaults for a bare
# `terraform apply`.

# Short base name for generated resource names (service accounts, dataset).
project_name = "slidegen"

# Your Google Cloud project id.
project_id = "your-gcp-project-id"

# Region for Cloud Run, Artifact Registry and the deck bucket.
region = "us-central1"

# Leave empty to default to <project_id>-slidegen-decks.
deck_bucket_name = ""

# Days a generated deck is kept. 0 keeps them forever.
deck_retention_days = 90

# BigQuery telemetry pipeline. Off by default; nothing on the deck path needs it.
enable_telemetry = false
