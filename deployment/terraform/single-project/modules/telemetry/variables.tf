variable "project_id" {
  type        = string
  description = "Google Cloud project holding the telemetry dataset."
}

variable "region" {
  type        = string
  description = "Location for the BigQuery dataset, connection and logs bucket."
}

variable "project_name" {
  type        = string
  description = "Short base name for generated resource names."
}

variable "feedback_logs_filter" {
  type        = string
  description = "Log Sink filter selecting user feedback log entries."
}
