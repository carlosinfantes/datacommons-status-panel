output "service_name" {
  description = "Name of the status panel Cloud Run service."
  value       = google_cloud_run_v2_service.status.name
}

output "service_uri" {
  description = "URL of the status panel."
  value       = google_cloud_run_v2_service.status.uri
}

output "service_account_email" {
  description = "Service account the collector runs as."
  value       = google_service_account.status.email
}
