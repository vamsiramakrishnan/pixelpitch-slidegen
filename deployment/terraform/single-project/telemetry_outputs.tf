output "telemetry_dataset_id" {
  description = "BigQuery dataset holding telemetry, when enable_telemetry is set."
  value       = one(module.telemetry[*].telemetry_dataset_id)
}

output "telemetry_logs_bucket" {
  description = "Bucket the agent writes GenAI completions to, when enable_telemetry is set."
  value       = one(module.telemetry[*].logs_bucket_name)
}
