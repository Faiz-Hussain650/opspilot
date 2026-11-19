variable "project_id" {
  description = "Your GCP project id"
  type        = string
}

variable "region" {
  description = "Cloud Run region. us-central1 / us-east1 / us-west1 get Tier 1 (cheapest) pricing."
  type        = string
  default     = "us-central1"
}

variable "api_image" {
  description = "linux/amd64 API image, e.g. docker.io/<user>/opspilot-api:latest"
  type        = string
}

variable "ui_image" {
  description = "linux/amd64 UI image, e.g. docker.io/<user>/opspilot-ui:latest"
  type        = string
}

variable "qdrant_url" {
  description = "Qdrant Cloud cluster URL"
  type        = string
}

variable "groq_model" {
  type    = string
  default = "llama-3.3-70b-versatile"
}

variable "groq_api_key" {
  type      = string
  sensitive = true
}

variable "qdrant_api_key" {
  type      = string
  sensitive = true
}

variable "langfuse_public_key" {
  type    = string
  default = ""
}

variable "langfuse_secret_key" {
  type      = string
  sensitive = true
  default   = ""
}
