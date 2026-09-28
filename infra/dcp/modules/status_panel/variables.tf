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
  description = "Whether Identity-Aware Proxy fronts the service."
  type        = bool
  default     = true
}

variable "iap_members" {
  description = "Members granted access through IAP."
  type        = list(string)
  default     = []
}

variable "invoker_members" {
  description = "Service accounts allowed to invoke the service directly, besides IAP. Machines only."
  type        = list(string)
  default     = []

  # This is the second door, and the weaker one: a principal here reaches Cloud
  # Run with an ID token, without passing the IAP consent screen or needing
  # roles/iap.httpsResourceAccessor. It exists for peer panels fanning in. A
  # human added here "just to curl it" would be a permanent, silent bypass, so
  # the type system refuses the shape of that mistake.
  validation {
    condition     = alltrue([for m in var.invoker_members : startswith(m, "serviceAccount:")])
    error_message = "invoker_members bypasses IAP, so only serviceAccount: principals are accepted — never user:, group:, domain:, allUsers or allAuthenticatedUsers. Put humans in iap_members instead."
  }

  validation {
    condition     = var.enable_iap || length(var.invoker_members) > 0
    error_message = "enable_iap is false and invoker_members is empty, so nothing could reach the panel. Populate invoker_members or leave IAP on."
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
    condition = !var.require_auth || var.iap_audience != "" || (
      length(var.allowed_callers) > 0 && var.self_url != ""
    )
    error_message = "require_auth is true but no credential would be accepted: set iap_audience for browser access through IAP, and/or allowed_callers with self_url for peer fan-in."
  }
}

variable "iap_audience" {
  description = <<-EOT
    Expected `aud` claim of IAP assertions, used by the collector to verify them.
    Required when require_auth is true unless allowed_callers covers every caller.

    This module does not assume the audience format: it differs between IAP
    fronted by a load balancer and IAP enabled natively on Cloud Run. Read it
    from a real assertion after the first deploy (decode the
    X-Goog-IAP-JWT-Assertion header) or from the IAP documentation, then pin it
    here. A wrong value fails closed — every browser request is refused.
  EOT
  type        = string
  default     = ""
}

variable "self_url" {
  description = <<-EOT
    This panel's own base URL, as its peers have it configured. Peers mint an ID
    token with that URL as the audience, so the value here is what the collector
    verifies incoming peer tokens against. Required when allowed_callers is set.

    Not derived from the service's own uri attribute: that would be a
    self-reference cycle. It is the same string the other panels carry in their
    `peers` list, so it is already known at plan time.
  EOT
  type        = string
  default     = ""
}

variable "allowed_callers" {
  description = "Service account emails allowed to fetch the document with an ID token. The peer panels, and nothing else."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for m in var.allowed_callers : can(regex("^[^@]+@[^@]+\\.iam\\.gserviceaccount\\.com$", m))])
    error_message = "allowed_callers takes bare service account emails, not IAM member strings — 'panel@project.iam.gserviceaccount.com', not 'serviceAccount:panel@…'."
  }

  validation {
    condition     = length(var.allowed_callers) == 0 || var.self_url != ""
    error_message = "allowed_callers needs self_url: with no audience to check against, a peer's ID token would be accepted whichever service it was minted for."
  }
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

variable "peers" {
  description = "Other status panels to aggregate. Empty means this panel only reports itself."
  type = list(object({
    id    = string
    label = string
    url   = string
  }))
  default = []
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
