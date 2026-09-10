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

# A BigQuery dataset, two log sinks, an external table and a view. Useful,
# and none of it is on the path a deck request takes, so it is one gate
# rather than ten `count` expressions threaded through cross-references.
module "telemetry" {
  count  = var.enable_telemetry ? 1 : 0
  source = "./modules/telemetry"

  project_id           = var.project_id
  region               = var.region
  project_name         = var.project_name
  feedback_logs_filter = var.feedback_logs_filter

  depends_on = [google_project_service.services]
}
