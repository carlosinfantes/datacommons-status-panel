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
  description = "Members allowed to invoke the service directly, besides IAP."
  type        = list(string)
  default     = []
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

variable "preprocessing_job_name" {
  description = "Name of the preprocessing Cloud Run job to inspect."
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
