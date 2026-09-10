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

variable "project_name" {
  type        = string
  description = "Short base name for generated resource names."
  default     = "slidegen"

  # This value is interpolated into a service account id, a Cloud Storage
  # bucket name and a BigQuery dataset id, none of which accept a slash, and
  # a service account id caps at 30 characters. The scaffolded default was a
  # directory path, so every one of those names was invalid and this module
  # could never have applied. Rejecting it here names the field instead of
  # failing three resources deep.
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}[a-z0-9]$", var.project_name))
    error_message = "project_name must be 3-22 chars, lowercase letters, digits and hyphens, starting with a letter. It is used verbatim in service account, bucket and dataset names."
  }
}

variable "project_id" {
  type        = string
  description = "Google Cloud Project ID for resource deployment."
}

variable "region" {
  type        = string
  description = "Google Cloud region for resource deployment."
  default     = "us-central1"
}

variable "deck_bucket_name" {
  type        = string
  description = "Bucket for generated PPTX decks. Defaults to <project_id>-slidegen-decks. Must match DECK_BUCKET in deploy.env."
  default     = ""
}

variable "artifact_repo" {
  type        = string
  description = "Artifact Registry repository holding the renderer image."
  default     = "pixelpitch-agents"
}

variable "deck_retention_days" {
  type        = number
  description = "Days before a generated deck is deleted. Set to 0 to keep decks forever."
  default     = 90
}

variable "enable_telemetry" {
  type        = bool
  description = "Create the BigQuery telemetry dataset, log sinks and completions views. Off by default so a first deploy provisions only what serving requires."
  default     = false
}

variable "telemetry_logs_filter" {
  type        = string
  description = "Log Sink filter for capturing telemetry data."
  default     = "labels.service_name=\"slidegen-agent\" labels.type=\"agent_telemetry\""
}

variable "feedback_logs_filter" {
  type        = string
  description = "Log Sink filter for capturing feedback data."
  default     = "jsonPayload.log_type=\"feedback\" jsonPayload.service_name=\"slidegen-agent\""
}

variable "app_sa_roles" {
  description = "Project-level roles held by the agent service account."
  type        = list(string)
  default = [
    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
    "roles/serviceusage.serviceUsageConsumer",
  ]
}

variable "renderer_sa_roles" {
  description = "Project-level roles held by the renderer service account. The renderer calls Vertex for slidify's LLM tiering and writes decks to the bucket."
  type        = list(string)
  default = [
    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
    "roles/serviceusage.serviceUsageConsumer",
  ]
}

locals {
  # Bucket names are a global namespace, so the project id is the only
  # prefix available by construction. Kept identical to the default in
  # scripts/deploy_cli.py; the deploy passes DECK_BUCKET explicitly, and
  # this default only matters for a bare `terraform apply`.
  deck_bucket = var.deck_bucket_name != "" ? var.deck_bucket_name : "${var.project_id}-slidegen-decks"
}
