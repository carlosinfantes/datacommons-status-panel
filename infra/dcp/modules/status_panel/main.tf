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

locals {
  name_prefix   = var.instance_name != "" ? "${var.instance_name}-" : ""
  service_name  = "${local.name_prefix}dc-status"
  sa_account_id = "${local.name_prefix}dc-sts-sa"
  sa_email      = var.service_account_email != null ? var.service_account_email : google_service_account.status[0].email

  # The IAP service agent has to be able to invoke the service. There is no
  # google_project_service_identity in the GA provider, so the well-known address
  # is derived from the project number instead.
  iap_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-iap.iam.gserviceaccount.com"

  # Project scope is forced, not chosen: the google provider has no
  # google_workflows_workflow_iam_member resource (checked against 7.38.0, which
  # ships only google_workflows_workflow), so per-workflow IAM cannot be
  # expressed. Every other dependency below IS resource-scoped. The sibling
  # deployment repo documents the same limitation in its own iam.tf.
  #
  # roles/monitoring.viewer is project-scoped by nature: Cloud Monitoring has no
  # IAM below the project (no per-service or per-metric grants), so reading the
  # Cloud Run and Spanner time series behind the golden signals can only be
  # granted here. It is read-only and gives no access to the data itself.
  project_roles = [
    "roles/workflows.viewer",
    "roles/monitoring.viewer",
  ]

  ghcr_repository_id = var.ghcr_remote_repository_id != "" ? var.ghcr_remote_repository_id : "${local.name_prefix}ghcr"
}

data "google_project" "this" {
  project_id = var.project_id
}

# Created here unless the caller brings its own (service_account_email). A caller
# that grants its deployer actAs per service account, rather than project-wide,
# has to: Cloud Run checks actAs when the service is created, and a binding on an
# account this module creates cannot be ordered before that within one apply.
resource "google_service_account" "status" {
  count        = var.service_account_email == null ? 1 : 0
  project      = var.project_id
  account_id   = local.sa_account_id
  display_name = "Data Commons status panel"
}

resource "google_cloud_run_v2_service" "status" {
  project             = var.project_id
  name                = local.service_name
  location            = var.region
  ingress             = var.ingress
  iap_enabled         = var.enable_iap
  deletion_protection = var.stateless_deletion_protection

  template {
    service_account                  = local.sa_email
    timeout                          = "${var.request_timeout_seconds}s"
    max_instance_request_concurrency = var.max_request_concurrency

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "DCS_ENV_ID"
        value = var.env_id
      }
      env {
        name  = "DCS_ENV_LABEL"
        value = var.env_label != "" ? var.env_label : var.env_id
      }
      env {
        name  = "DCS_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "DCS_REGION"
        value = var.region
      }
      env {
        name  = "DCS_SPANNER_INSTANCE_ID"
        value = var.spanner_instance_id
      }
      env {
        name  = "DCS_SPANNER_DATABASE_ID"
        value = var.spanner_database_id
      }
      env {
        name  = "DCS_DATACOMMONS_SERVICE_NAME"
        value = var.datacommons_service_name
      }
      env {
        name  = "DCS_INGESTION_WORKFLOW_NAME"
        value = var.ingestion_workflow_name
      }
      env {
        name  = "DCS_ARTIFACTS_BUCKET_NAME"
        value = var.artifacts_bucket_name
      }
      env {
        name  = "DCS_PUBLIC_ENDPOINT_URL"
        value = var.public_endpoint_url
      }
      env {
        name  = "DCS_FRONTEND_URL"
        value = var.frontend_url
      }
      env {
        name  = "DCS_DATA_SOURCE_PREFIXES"
        value = join(",", var.data_source_prefixes)
      }
      env {
        name  = "DCS_INPUT_PREFIX"
        value = var.input_prefix
      }
      env {
        name  = "DCS_COUNTS_CACHE_TTL_SECONDS"
        value = tostring(var.counts_cache_ttl_seconds)
      }
      env {
        name  = "DCS_SCHEMA_CACHE_TTL_SECONDS"
        value = tostring(var.schema_cache_ttl_seconds)
      }
      env {
        name  = "DCS_CANARY_NODE"
        value = var.canary.node
      }
      env {
        name  = "DCS_CANARY_NAME"
        value = var.canary.name
      }
      env {
        name  = "DCS_SIGNALS_WINDOW_MINUTES"
        value = tostring(var.signals_window_minutes)
      }

      # Targets travel inside the document, so what the page draws is what the
      # collector judged against.
      env {
        name  = "DCS_TARGET_AVAILABILITY_PCT"
        value = tostring(var.targets.availability_pct)
      }
      env {
        name  = "DCS_TARGET_LATENCY_P95_MS"
        value = tostring(var.targets.latency_p95_ms)
      }
      env {
        name  = "DCS_TARGET_RUN_CPU_PCT"
        value = tostring(var.targets.run_cpu_pct)
      }
      env {
        name  = "DCS_TARGET_RUN_MEMORY_PCT"
        value = tostring(var.targets.run_memory_pct)
      }
      env {
        name  = "DCS_TARGET_SPANNER_CPU_PCT"
        value = tostring(var.targets.spanner_cpu_pct)
      }
      env {
        name  = "DCS_TARGET_MIN_REQUESTS_PER_HOUR"
        value = tostring(var.targets.min_requests_per_hour)
      }
      # Absent rather than empty when null: the age is then shown but not judged.
      dynamic "env" {
        for_each = var.targets.ingestion_max_age_hours == null ? [] : [var.targets.ingestion_max_age_hours]
        content {
          name  = "DCS_TARGET_INGESTION_MAX_AGE_HOURS"
          value = tostring(env.value)
        }
      }
      env {
        name  = "DCS_TARGET_MAX_ROW_DROP_PCT"
        value = tostring(var.targets.max_row_drop_pct)
      }

      # Defence in depth: IAP and the invoker binding are the control, these make
      # the collector refuse a request the perimeter should never have delivered.
      env {
        name  = "DCS_REQUIRE_AUTH"
        value = var.require_auth ? "true" : "false"
      }
      env {
        name  = "DCS_IAP_AUDIENCE"
        value = var.iap_audience
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 2
        period_seconds        = 5
        failure_threshold     = 6
      }
    }
  }

  # When the image is pulled through the GHCR remote repository created below,
  # the repository has to exist before the first revision is deployed.
  depends_on = [google_artifact_registry_repository.ghcr]
}

# Cloud Run pulls only from Artifact Registry (and Docker Hub), not from ghcr.io.
# A remote repository with ghcr.io upstream lets it pull the public release image
# by digest and caches it in the project. The image is public, so no upstream
# credentials are configured, and Cloud Run's service agent can read a
# repository in its own project without an extra grant.
resource "google_artifact_registry_repository" "ghcr" {
  count = var.create_ghcr_remote ? 1 : 0

  project       = var.project_id
  location      = var.region
  repository_id = local.ghcr_repository_id
  description   = "Remote repository for ghcr.io, used to pull the Data Commons status panel image."
  format        = "DOCKER"
  mode          = "REMOTE_REPOSITORY"

  remote_repository_config {
    description = "ghcr.io"

    common_repository {
      uri = "https://ghcr.io"
    }
  }
}

# IAP's service agent is the only invoker, whether IAP is native (enable_iap) or
# on a load balancer in front. Nobody else holds roles/run.invoker and allUsers
# is never granted, so a request that did not come through IAP cannot reach the
# container at all.
#
# Still keyed by member under the old address: renaming it would make Terraform
# create and delete the same binding in one apply, and whichever lands last wins.
resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset([local.iap_agent])

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.status.name
  role     = "roles/run.invoker"
  member   = each.value
}

resource "google_iap_web_cloud_run_service_iam_member" "accessors" {
  for_each = var.enable_iap ? toset(var.iap_members) : toset([])

  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.status.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}

resource "google_project_iam_member" "project_roles" {
  for_each = toset(local.project_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${local.sa_email}"
}

resource "google_spanner_database_iam_member" "reader" {
  project  = var.project_id
  instance = var.spanner_instance_id
  database = var.spanner_database_id
  role     = "roles/spanner.databaseReader"
  member   = "serviceAccount:${local.sa_email}"
}

# databaseReader does not include instances.get or databases.get.
resource "google_spanner_instance_iam_member" "viewer" {
  project  = var.project_id
  instance = var.spanner_instance_id
  role     = "roles/spanner.viewer"
  member   = "serviceAccount:${local.sa_email}"
}

resource "google_cloud_run_v2_service_iam_member" "datacommons_viewer" {
  project  = var.project_id
  location = var.region
  name     = var.datacommons_service_name
  role     = "roles/run.viewer"
  member   = "serviceAccount:${local.sa_email}"
}

# Lists object names and sizes; deliberately not objectViewer, so the panel can
# see that input files exist without being able to read their contents.
resource "google_storage_bucket_iam_member" "bucket_reader" {
  bucket = var.artifacts_bucket_name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${local.sa_email}"
}
