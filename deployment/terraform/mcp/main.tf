terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.28.0" }
  }
}

variable "project_id" { type = string }
variable "region" { type = string }
variable "deck_bucket" { type = string }
variable "renderer_secret" {
  type    = string
  default = ""
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "api" {
  for_each           = toset(["firestore.googleapis.com", "run.googleapis.com", "storage.googleapis.com", "aiplatform.googleapis.com"])
  service            = each.value
  disable_on_destroy = false
}

resource "google_firestore_database" "jobs" {
  project                           = var.project_id
  name                              = "pixelpitch-mcp"
  location_id                       = var.region
  type                              = "FIRESTORE_NATIVE"
  delete_protection_state           = "DELETE_PROTECTION_ENABLED"
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_ENABLED"
  depends_on                        = [google_project_service.api]
  lifecycle { prevent_destroy = true }
}

resource "google_firestore_field" "expiry" {
  for_each   = toset(["pixelpitch_mcp_jobs", "pixelpitch_mcp_workspaces", "pixelpitch_mcp_owners"])
  project    = var.project_id
  database   = google_firestore_database.jobs.name
  collection = each.value
  field      = "expires_at"
  ttl_config {}
}

resource "google_storage_bucket" "drafts" {
  name                        = "${var.project_id}-pixelpitch-mcp-drafts"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  soft_delete_policy { retention_duration_seconds = 604800 }
  lifecycle_rule {
    condition { age = 8 }
    action { type = "Delete" }
  }
  lifecycle { prevent_destroy = true }
}

resource "google_service_account" "runtime" {
  for_each     = toset(["api", "worker"])
  account_id   = "pixelpitch-mcp-${each.value}"
  display_name = "Pixelpitch MCP ${each.value}"
}

resource "google_project_iam_member" "database" {
  for_each = google_service_account.runtime
  project  = var.project_id
  member   = "serviceAccount:${each.value.email}"
  role     = "roles/datastore.user"
  condition {
    title      = "pixelpitch-mcp-database"
    expression = "resource.name=='projects/${var.project_id}/databases/${google_firestore_database.jobs.name}'"
  }
}

resource "google_cloud_run_service_iam_member" "renderer" {
  for_each = google_service_account.runtime
  project  = var.project_id
  location = var.region
  service  = "slidegen-renderer"
  role     = "roles/run.invoker"
  member   = "serviceAccount:${each.value.email}"
}

resource "google_storage_bucket_iam_member" "artifacts" {
  for_each = google_service_account.runtime
  bucket   = var.deck_bucket
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${each.value.email}"
}

resource "google_storage_bucket_iam_member" "drafts" {
  for_each = { api = "roles/storage.objectViewer", worker = "roles/storage.objectCreator" }
  bucket   = google_storage_bucket.drafts.name
  role     = each.value
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_storage_bucket_iam_member" "templates" {
  bucket = var.deck_bucket
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime["worker"].email}"
  condition {
    title      = "pixelpitch-template-preparation"
    expression = "resource.name.startsWith('projects/_/buckets/${var.deck_bucket}/objects/templates/') || resource.name.startsWith('projects/_/buckets/${var.deck_bucket}/objects/catalogs/')"
  }
}

resource "google_project_iam_member" "models" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.runtime["worker"].email}"
}

resource "google_secret_manager_secret_iam_member" "renderer_key" {
  for_each  = var.renderer_secret == "" ? toset([]) : toset(["api", "worker"])
  project   = var.project_id
  secret_id = var.renderer_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime[each.key].email}"
}
