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

locals {
  # Serving. Every one of these is reached on an ordinary deck request or on
  # the deploy that produces it.
  core_services = [
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudtrace.googleapis.com",
    "discoveryengine.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
  ]

  telemetry_services = [
    "bigquery.googleapis.com",
    "telemetry.googleapis.com",
  ]

  services = concat(
    local.core_services,
    var.enable_telemetry ? local.telemetry_services : [],
  )
}

resource "google_project_service" "services" {
  for_each = toset(local.services)

  project = var.project_id
  service = each.value

  # Turning an API off underneath a running service is a far worse outcome
  # than leaving one enabled after a teardown.
  disable_on_destroy = false
}

# Gemini Enterprise calls the agent as this Google-managed identity. Creating
# it explicitly means the run.invoker binding after the agent deploy has
# something to bind to; otherwise the identity is created lazily on first use
# and the grant fails with "service account does not exist".
resource "google_project_service_identity" "discovery_engine" {
  provider = google-beta

  project = var.project_id
  service = "discoveryengine.googleapis.com"

  depends_on = [google_project_service.services]
}

resource "google_project_service_identity" "vertex" {
  provider = google-beta

  project = var.project_id
  service = "aiplatform.googleapis.com"

  depends_on = [google_project_service.services]
}
