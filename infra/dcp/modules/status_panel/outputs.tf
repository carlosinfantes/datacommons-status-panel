# Copyright 2026 Carlos Infantes
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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

output "ghcr_remote_image" {
  description = "Image path through the GHCR remote repository, without tag or digest. Append :<version>@sha256:<digest> and pass it as image. Null unless create_ghcr_remote is true."
  value       = var.create_ghcr_remote ? "${var.region}-docker.pkg.dev/${var.project_id}/${local.ghcr_repository_id}/${var.ghcr_image_path}" : null
}
