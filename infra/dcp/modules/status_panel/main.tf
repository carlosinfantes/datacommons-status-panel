locals {
  name_prefix   = var.instance_name != "" ? "${var.instance_name}-" : ""
  service_name  = "${local.name_prefix}dc-status"
  sa_account_id = "${local.name_prefix}dc-sts-sa"

  # The IAP service agent has to be able to invoke the service. There is no
  # google_project_service_identity in the GA provider, so the well-known address
  # is derived from the project number instead.
  iap_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-iap.iam.gserviceaccount.com"

  # Project scope is forced, not chosen: the google provider has no
  # google_workflows_workflow_iam_member resource (checked against 7.38.0, which
  # ships only google_workflows_workflow), so per-workflow IAM cannot be
  # expressed. Every other dependency below IS resource-scoped. The sibling
  # deployment repo documents the same limitation in its own iam.tf.
  project_roles = [
    "roles/workflows.viewer",
  ]

  # With enable_iap = false and invoker_members left empty, nothing can invoke the
  # service. That is deliberate — it fails closed rather than open, and allUsers is
  # never granted — but an operator turning IAP off must populate invoker_members
  # or the panel becomes unreachable.
  invokers = var.enable_iap ? concat([local.iap_agent], var.invoker_members) : var.invoker_members
}

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_service_account" "status" {
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
    service_account                  = google_service_account.status.email
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
        name  = "DCS_PEERS"
        value = jsonencode(var.peers)
      }
      env {
        name  = "DCS_COUNTS_CACHE_TTL_SECONDS"
        value = tostring(var.counts_cache_ttl_seconds)
      }
      env {
        name  = "DCS_SCHEMA_CACHE_TTL_SECONDS"
        value = tostring(var.schema_cache_ttl_seconds)
      }

      # Defence in depth: IAP and the invoker bindings are the control, these make
      # the collector refuse a request the perimeter should never have delivered.
      env {
        name  = "DCS_REQUIRE_AUTH"
        value = var.require_auth ? "true" : "false"
      }
      env {
        name  = "DCS_IAP_AUDIENCE"
        value = var.iap_audience
      }
      env {
        name  = "DCS_SELF_AUDIENCE"
        value = var.self_url
      }
      env {
        name  = "DCS_ALLOWED_CALLERS"
        value = join(",", var.allowed_callers)
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
}

resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset(local.invokers)

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
  member  = "serviceAccount:${google_service_account.status.email}"
}

resource "google_spanner_database_iam_member" "reader" {
  project  = var.project_id
  instance = var.spanner_instance_id
  database = var.spanner_database_id
  role     = "roles/spanner.databaseReader"
  member   = "serviceAccount:${google_service_account.status.email}"
}

# databaseReader does not include instances.get or databases.get.
resource "google_spanner_instance_iam_member" "viewer" {
  project  = var.project_id
  instance = var.spanner_instance_id
  role     = "roles/spanner.viewer"
  member   = "serviceAccount:${google_service_account.status.email}"
}

resource "google_cloud_run_v2_service_iam_member" "datacommons_viewer" {
  project  = var.project_id
  location = var.region
  name     = var.datacommons_service_name
  role     = "roles/run.viewer"
  member   = "serviceAccount:${google_service_account.status.email}"
}

# Lists object names and sizes; deliberately not objectViewer, so the panel can
# see that input files exist without being able to read their contents.
resource "google_storage_bucket_iam_member" "bucket_reader" {
  bucket = var.artifacts_bucket_name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.status.email}"
}
