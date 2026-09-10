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

# Holds the renderer image. Cloud Build pushes here and Cloud Run pulls from
# here; the repository must exist before the first build rather than being
# created by it.
resource "google_artifact_registry_repository" "agents" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_repo
  format        = "DOCKER"
  description   = "Pixelpitch agent container images"

  depends_on = [google_project_service.services]
}

# The agent authenticates to the renderer with this shared secret, sent as
# X-API-Key, on top of Cloud Run IAM. Generated rather than supplied so that
# no operator ever has a reason to paste a credential into a config file.
resource "random_password" "renderer_key" {
  length  = 48
  special = false
}

resource "google_secret_manager_secret" "renderer_key" {
  project   = var.project_id
  secret_id = "slidegen-renderer-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "renderer_key" {
  secret      = google_secret_manager_secret.renderer_key.id
  secret_data = random_password.renderer_key.result

  # Rotating on every apply would invalidate the running agent's copy until
  # both services redeploy. Rotation is a deliberate act: destroy this
  # version, or add a new one and redeploy both.
  lifecycle {
    ignore_changes = [secret_data]
  }
}
