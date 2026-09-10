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

# Generated decks. The renderer writes here and hands the agent a signed or
# gs:// reference; nothing else reads it.
resource "google_storage_bucket" "decks" {
  name                        = local.deck_bucket
  location                    = var.region
  project                     = var.project_id
  uniform_bucket_level_access = true

  # A deck is a delivered artifact, not a record. Without this a demo
  # tenancy accumulates every draft anyone ever generated.
  dynamic "lifecycle_rule" {
    for_each = var.deck_retention_days > 0 ? [1] : []
    content {
      condition {
        age = var.deck_retention_days
        matches_prefix = ["decks/"]
      }
      action {
        type = "Delete"
      }
    }
  }

  depends_on = [google_project_service.services]
}

resource "google_storage_bucket" "logs_data_bucket" {
  count = var.enable_telemetry ? 1 : 0

  name                        = "${var.project_id}-${var.project_name}-logs"
  location                    = var.region
  project                     = var.project_id
  uniform_bucket_level_access = true

  depends_on = [google_project_service.services]
}
