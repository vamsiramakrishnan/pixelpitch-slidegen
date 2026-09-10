terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google      = { source = "hashicorp/google", version = "~> 7.28.0" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 7.28.0" }
  }
}

variable "project_id" { type = string }
variable "region" { type = string }
variable "deck_bucket" { type = string }
variable "worker_image" { type = string }
variable "renderer_url" { type = string }
variable "agent_service_account" { type = string }
variable "additional_service_accounts" {
  type    = map(string)
  default = {}
}
variable "renderer_secret" {
  type    = string
  default = ""
}
variable "model_environment" {
  type    = map(string)
  default = {}
}
variable "template_prefix" {
  type    = string
  default = "templates/"
}

provider "google" {
  project = var.project_id
  region  = var.region
}
provider "google-beta" {
  project = var.project_id
  region  = var.region
}

data "google_project" "current" { project_id = var.project_id }
data "google_storage_bucket" "decks" { name = var.deck_bucket }
data "google_storage_project_service_account" "storage" { project = var.project_id }

locals {
  service    = "slidegen-template-worker"
  queue      = "projects/${var.project_id}/locations/${var.region}/queues/slidegen-templates"
  worker_url = "https://${local.service}-${data.google_project.current.number}.${var.region}.run.app"
  environment = merge(var.model_environment, {
    GOOGLE_GENAI_USE_VERTEXAI              = "true"
    GOOGLE_CLOUD_PROJECT                   = var.project_id
    SLIDEGEN_GCS_BUCKET                    = var.deck_bucket
    SLIDEGEN_TEMPLATE_PREFIX               = var.template_prefix
    SLIDEGEN_RENDERER_URL                  = var.renderer_url
    SLIDEGEN_TEMPLATE_QUEUE                = local.queue
    SLIDEGEN_TEMPLATE_WORKER_URL           = local.worker_url
    SLIDEGEN_TEMPLATE_TASK_SERVICE_ACCOUNT = google_service_account.tasks.email
    SLIDEGEN_AGY_TIMEOUT_SECONDS           = "1500"
  })
}

resource "google_project_service" "api" {
  for_each           = toset(["run.googleapis.com", "cloudtasks.googleapis.com", "eventarc.googleapis.com", "pubsub.googleapis.com", "iamcredentials.googleapis.com"])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_project_service_identity" "tasks" {
  provider   = google-beta
  project    = var.project_id
  service    = "cloudtasks.googleapis.com"
  depends_on = [google_project_service.api]
}

resource "google_service_account" "worker" {
  account_id   = "slidegen-template-worker"
  display_name = "Pixelpitch template preparation worker"
}
resource "google_service_account" "events" {
  account_id   = "slidegen-template-events"
  display_name = "Pixelpitch template upload event receiver"
}
resource "google_service_account" "tasks" {
  account_id   = "slidegen-template-tasks"
  display_name = "Pixelpitch template task dispatch"
}

resource "google_project_iam_member" "worker" {
  for_each = toset(["roles/aiplatform.user", "roles/logging.logWriter", "roles/serviceusage.serviceUsageConsumer"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.worker.email}"
}
resource "google_project_iam_member" "event_receiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${google_service_account.events.email}"
}
resource "google_project_iam_member" "storage_events" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.storage.email_address}"
}
resource "google_storage_bucket_iam_member" "read_source" {
  bucket = var.deck_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker.email}"
}
resource "google_storage_bucket_iam_member" "write_prepared" {
  bucket = var.deck_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.worker.email}"
  condition {
    title      = "template-output-only"
    expression = "resource.name.startsWith('projects/_/buckets/${var.deck_bucket}/objects/prepared-templates/') || resource.name.startsWith('projects/_/buckets/${var.deck_bucket}/objects/template-jobs/')"
  }
}
resource "google_secret_manager_secret_iam_member" "renderer_key" {
  count     = var.renderer_secret == "" ? 0 : 1
  project   = var.project_id
  secret_id = var.renderer_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}
resource "google_cloud_run_service_iam_member" "renderer_invoker" {
  project  = var.project_id
  location = var.region
  service  = "slidegen-renderer"
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_cloud_tasks_queue" "templates" {
  name     = "slidegen-templates"
  location = var.region
  rate_limits {
    max_concurrent_dispatches = 1
    max_dispatches_per_second = 1
  }
  retry_config {
    max_attempts  = 3
    min_backoff   = "60s"
    max_backoff   = "300s"
    max_doublings = 3
  }
  stackdriver_logging_config { sampling_ratio = 1 }
  depends_on = [google_project_service.api]
}
resource "google_cloud_tasks_queue_iam_member" "enqueue" {
  for_each = merge({ agent = var.agent_service_account, worker = google_service_account.worker.email }, var.additional_service_accounts)
  project  = var.project_id
  location = var.region
  name     = google_cloud_tasks_queue.templates.name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${each.value}"
}
resource "google_service_account_iam_member" "act_as_dispatcher" {
  for_each           = merge({ agent = var.agent_service_account, worker = google_service_account.worker.email }, var.additional_service_accounts)
  service_account_id = google_service_account.tasks.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${each.value}"
}
resource "google_service_account_iam_member" "dispatch_token" {
  service_account_id = google_service_account.tasks.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_project_service_identity.tasks.email}"
}

resource "google_cloud_run_v2_service" "worker" {
  name                = local.service
  location            = var.region
  deletion_protection = false
  template {
    service_account                  = google_service_account.worker.email
    timeout                          = "1800s"
    max_instance_request_concurrency = 4
    scaling {
      min_instance_count = 0
      max_instance_count = 4
    }
    containers {
      image = var.worker_image
      resources { limits = { cpu = "4", memory = "8Gi" } }
      dynamic "env" {
        for_each = local.environment
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = var.renderer_secret == "" ? [] : [var.renderer_secret]
        content {
          name = "SLIDEGEN_RENDERER_API_KEY"
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }
  }
  depends_on = [google_project_service.api, google_secret_manager_secret_iam_member.renderer_key]
}
resource "google_cloud_run_v2_service_iam_member" "invoke" {
  for_each = { events = google_service_account.events.email, tasks = google_service_account.tasks.email }
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${each.value}"
}

resource "google_eventarc_trigger" "template_upload" {
  name     = "slidegen-template-upload"
  location = lower(data.google_storage_bucket.decks.location)
  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }
  matching_criteria {
    attribute = "bucket"
    value     = var.deck_bucket
  }
  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.worker.name
      region  = var.region
      path    = "/events"
    }
  }
  service_account = google_service_account.events.email
  depends_on      = [google_cloud_run_v2_service_iam_member.invoke, google_project_iam_member.event_receiver, google_project_iam_member.storage_events, google_cloud_tasks_queue_iam_member.enqueue, google_service_account_iam_member.act_as_dispatcher, google_service_account_iam_member.dispatch_token]
}

output "worker_url" { value = local.worker_url }
output "queue" { value = local.queue }
output "task_service_account" { value = google_service_account.tasks.email }
output "trigger" { value = google_eventarc_trigger.template_upload.id }
