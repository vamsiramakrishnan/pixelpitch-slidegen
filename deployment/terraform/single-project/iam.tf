# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

data "google_project" "project" {
  project_id = var.project_id
}

# Cloud Build runs as the default compute service account and needs to push
# to Artifact Registry and write build logs.
resource "google_project_iam_member" "cloudbuild_builder" {
  project    = var.project_id
  role       = "roles/cloudbuild.builds.builder"
  member     = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
  depends_on = [google_project_service.services]
}

# ---------------------------------------------------------------- identities

# Two service accounts rather than one, so the blast radius of the
# internet-adjacent renderer stops short of the agent's Vertex and session
# access, and so revoking one does not take the other down.
resource "google_service_account" "app_sa" {
  account_id   = "${var.project_name}-agent"
  display_name = "Slidegen agent (A2A / Gemini Enterprise)"
  project      = var.project_id
  depends_on   = [google_project_service.services]
}

resource "google_service_account" "renderer_sa" {
  account_id   = "${var.project_name}-renderer"
  display_name = "Slidegen renderer (HTML to PPTX)"
  project      = var.project_id
  depends_on   = [google_project_service.services]
}

# ------------------------------------------------------------ project roles

resource "google_project_iam_member" "app_sa_roles" {
  for_each = toset(var.app_sa_roles)

  project    = var.project_id
  role       = each.value
  member     = "serviceAccount:${google_service_account.app_sa.email}"
  depends_on = [google_project_service.services]
}

resource "google_project_iam_member" "renderer_sa_roles" {
  for_each = toset(var.renderer_sa_roles)

  project    = var.project_id
  role       = each.value
  member     = "serviceAccount:${google_service_account.renderer_sa.email}"
  depends_on = [google_project_service.services]
}

# ------------------------------------------------------------ bucket access

# Scoped to the deck bucket. The scaffolded module granted project-wide
# roles/storage.admin for what is one bucket's worth of object writes.
resource "google_storage_bucket_iam_member" "renderer_writes_decks" {
  bucket = google_storage_bucket.decks.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.renderer_sa.email}"
}

resource "google_storage_bucket_iam_member" "agent_reads_decks" {
  bucket = google_storage_bucket.decks.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.app_sa.email}"
}

# ------------------------------------------------------------ shared secret

resource "google_secret_manager_secret_iam_member" "renderer_reads_key" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.renderer_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.renderer_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "agent_reads_key" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.renderer_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app_sa.email}"
}

# The run.invoker bindings are not here. Both of them name a Cloud Run
# service that does not exist until `mise run deploy` creates it, so they are
# applied by the deploy tasks that follow it. See mise.toml.
