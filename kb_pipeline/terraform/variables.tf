variable "project_id" {
  description = "GCP project ID that hosts the bucket and the data store."
  type        = string
}

variable "region" {
  description = "Default region for the GCS bucket."
  type        = string
  default     = "asia-northeast3"
}

variable "kb_bucket_name" {
  description = "Globally unique name of the private KB bucket."
  type        = string
}

variable "bucket_location" {
  description = "Bucket location. Defaults to var.region."
  type        = string
  default     = null
}

variable "datastore_location" {
  description = "Vertex AI Search location. CX Agent Studio only connects to us, eu or global, and it must match the CXAS app location."
  type        = string
  default     = "us"

  validation {
    condition     = contains(["us", "eu", "global"], var.datastore_location)
    error_message = "datastore_location must be one of: us, eu, global."
  }
}

variable "datastore_id" {
  description = "Vertex AI Search data store ID. Changing it creates a new, empty data store."
  type        = string
  default     = "coway-kb"
}

variable "datastore_display_name" {
  description = "Display name of the data store."
  type        = string
  default     = "Coway Knowledge Base"
}

variable "operator_members" {
  description = "IAM members who run sync_kb.py, e.g. [\"user:operator@example.com\"]."
  type        = list(string)

  validation {
    condition = alltrue([
      for m in var.operator_members :
      can(regex("^(user|group|serviceAccount|principal|principalSet):", m))
    ])
    error_message = "Each operator member needs an IAM prefix such as user:, group: or serviceAccount:."
  }
}
