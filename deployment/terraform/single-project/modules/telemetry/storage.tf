# The GenAI completions the agent writes. The external table below reads it,
# so it belongs to the telemetry stack rather than to serving.
resource "google_storage_bucket" "logs" {
  name                        = "${var.project_id}-${var.project_name}-logs"
  location                    = var.region
  project                     = var.project_id
  uniform_bucket_level_access = true
}
