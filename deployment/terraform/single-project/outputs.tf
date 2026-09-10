output "project_number" {
  description = "Project number. Appears inside the Gemini Enterprise service agent address."
  value       = data.google_project.project.number
}

output "agent_service_account_email" {
  description = "Service account the slidegen-agent Cloud Run service runs as."
  value       = google_service_account.app_sa.email
}

output "renderer_service_account_email" {
  description = "Service account the slidegen-renderer Cloud Run service runs as."
  value       = google_service_account.renderer_sa.email
}

output "discovery_engine_service_agent" {
  description = "Identity Gemini Enterprise calls the agent as. Needs roles/run.invoker on slidegen-agent once that service exists."
  value       = google_project_service_identity.discovery_engine.email
}

output "deck_bucket" {
  description = "Bucket holding generated decks."
  value       = google_storage_bucket.decks.name
}

output "renderer_image" {
  description = "Image path the renderer build pushes to and Cloud Run pulls from."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.agents.repository_id}/slidegen-renderer"
}

output "renderer_key_secret" {
  description = "Secret Manager secret holding the agent-to-renderer shared key."
  value       = google_secret_manager_secret.renderer_key.secret_id
}
