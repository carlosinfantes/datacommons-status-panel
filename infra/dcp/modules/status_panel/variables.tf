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

variable "project_id" {
  description = "Project that hosts the status panel."
  type        = string
}

variable "region" {
  description = "Region for the Cloud Run service."
  type        = string
}

variable "instance_name" {
  description = "Prefix applied to every resource name. Empty means no prefix."
  type        = string
  default     = ""
}

variable "stateless_deletion_protection" {
  description = "Whether stateless resources refuse to be destroyed."
  type        = bool
  default     = false
}

variable "image" {
  description = "Container image for the collector, pinned by digest."
  type        = string
  nullable    = false
}

variable "cpu" {
  description = "CPU limit for the collector container."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Memory limit for the collector container."
  type        = string
  default     = "512Mi"
}

variable "min_instances" {
  description = "Minimum number of instances. Zero lets the panel cost nothing at rest."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum number of instances."
  type        = number
  default     = 2
}

variable "ingress" {
  description = "Cloud Run ingress setting."
  type        = string
  default     = "INGRESS_TRAFFIC_ALL"
}

variable "enable_iap" {
  description = <<-EOT
    Whether IAP is enabled natively on the Cloud Run service. Set it to false only
    when IAP is enforced by a load balancer you manage in front of the service,
    and grant access on that backend service, because iap_members is then not
    applied. Either way the only principal granted roles/run.invoker is IAP's
    service agent, so turning this off without such a load balancer leaves the
    panel closed, not open.
  EOT
  type        = bool
  default     = true
}

variable "iap_members" {
  description = "Principals granted roles/iap.httpsResourceAccessor on the service. Empty means nobody; prefer a group."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for m in var.iap_members : !contains(["allUsers", "allAuthenticatedUsers"], m)])
    error_message = "iap_members must name principals; allUsers or allAuthenticatedUsers would publish the panel."
  }
}

variable "require_auth" {
  description = "Whether the collector verifies every request itself, on top of IAP."
  type        = bool
  default     = true

  # The collector raises the same error on its first request. Checking it here
  # means a policy nobody can satisfy fails at plan time — with no credentials
  # needed, so CI catches it — instead of becoming a revision that refuses every
  # admin. A validation rather than a lifecycle precondition for exactly that
  # reason: preconditions are not reached until the provider has authenticated.
  validation {
    condition     = !var.require_auth || var.iap_audience != ""
    error_message = "require_auth is true but iap_audience is empty, so no request would be accepted. Set iap_audience to the aud claim of this service's IAP assertions."
  }
}

variable "iap_audience" {
  description = <<-EOT
    Expected `aud` claim of IAP assertions, used by the collector to verify them.
    Required when require_auth is true.

    This module does not assume the audience format: it differs between IAP
    fronted by a load balancer and IAP enabled natively on Cloud Run. Read it
    from a real assertion after the first deploy (decode the
    X-Goog-IAP-JWT-Assertion header) or from the IAP documentation, then pin it
    here. A wrong value fails closed — every browser request is refused.
  EOT
  type        = string
  default     = ""
}

variable "env_id" {
  description = "Short identifier for this environment, shown on the page."
  type        = string
}

variable "env_label" {
  description = "Human-readable name for this environment."
  type        = string
  default     = ""
}

variable "spanner_instance_id" {
  description = "Spanner instance the platform serves from."
  type        = string
}

variable "spanner_database_id" {
  description = "Spanner database the platform serves from."
  type        = string
}

variable "datacommons_service_name" {
  description = "Name of the Data Commons Cloud Run service to inspect."
  type        = string
}

variable "ingestion_workflow_name" {
  description = "Name of the ingestion workflow to inspect."
  type        = string
}

variable "artifacts_bucket_name" {
  description = "Bucket holding ingestion input and artifacts."
  type        = string
}

variable "public_endpoint_url" {
  description = "Public base URL of the platform API."
  type        = string
}

variable "frontend_url" {
  description = "Public base URL of the frontend."
  type        = string
}

variable "data_source_prefixes" {
  description = "Prefixes under the input path, one per data source."
  type        = list(string)
  default     = []
}

variable "input_prefix" {
  description = "Path inside the bucket where per-source input lives."
  type        = string
  default     = "ingestion/input/"
}

variable "counts_cache_ttl_seconds" {
  description = "How long row counts are cached in process."
  type        = number
  default     = 300
}

variable "schema_cache_ttl_seconds" {
  description = "How long the discovered schema is cached in process."
  type        = number
  default     = 3600
}

variable "request_timeout_seconds" {
  description = "How long a single request may run before Cloud Run terminates it."
  type        = number
  default     = 120
}

variable "max_request_concurrency" {
  description = "Concurrent requests per instance. Match the server's worker/thread product."
  type        = number
  default     = 8
}

variable "targets" {
  description = <<-EOT
    Targets the collector judges against. They travel inside the document, so the
    page draws the same target the backend judged with. Unset fields keep their
    defaults. ingestion_max_age_hours = null shows the age without judging it.
  EOT
  type = object({
    availability_pct        = optional(number, 99.5)
    latency_p95_ms          = optional(number, 1000)
    run_cpu_pct             = optional(number, 80)
    run_memory_pct          = optional(number, 80)
    spanner_cpu_pct         = optional(number, 65)
    min_requests_per_hour   = optional(number, 100)
    ingestion_max_age_hours = optional(number)
    max_row_drop_pct        = optional(number, 10)
  })
  default  = {}
  nullable = false

  validation {
    condition = alltrue([
      for pct in [
        var.targets.availability_pct,
        var.targets.run_cpu_pct,
        var.targets.run_memory_pct,
        var.targets.spanner_cpu_pct,
        var.targets.max_row_drop_pct,
      ] : pct != null && pct >= 0 && pct <= 100
    ])
    error_message = "targets: availability_pct, run_cpu_pct, run_memory_pct, spanner_cpu_pct and max_row_drop_pct are percentages between 0 and 100."
  }

  validation {
    condition = alltrue([
      for value in [var.targets.latency_p95_ms, var.targets.min_requests_per_hour] : value != null && value >= 0
    ])
    error_message = "targets: latency_p95_ms and min_requests_per_hour must not be negative."
  }

  validation {
    condition     = var.targets.ingestion_max_age_hours == null ? true : var.targets.ingestion_max_age_hours >= 0
    error_message = "targets: ingestion_max_age_hours must not be negative. Leave it null to show the age without judging it."
  }
}

variable "canary" {
  description = "The node the dc_api probe resolves, and the name it must resolve to. Pick a node your deployment serves."
  type = object({
    node = optional(string, "country/GTM")
    name = optional(string, "Guatemala")
  })
  default  = {}
  nullable = false

  validation {
    condition     = try(trimspace(var.canary.node), "") != "" && try(trimspace(var.canary.name), "") != ""
    error_message = "canary.node and canary.name must not be empty."
  }
}

variable "signals_window_minutes" {
  description = "Window for the golden signals read from Cloud Monitoring, one point per minute."
  type        = number
  default     = 60
  nullable    = false

  validation {
    condition     = var.signals_window_minutes >= 5 && var.signals_window_minutes <= 1440 && floor(var.signals_window_minutes) == var.signals_window_minutes
    error_message = "signals_window_minutes must be a whole number between 5 and 1440."
  }
}
