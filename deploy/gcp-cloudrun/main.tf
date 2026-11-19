# OPTIONAL bonus: deploy OpsPilot (cloud mode) to Google Cloud Run with Terraform.
# Needs a GCP billing account (credit card). Within the Cloud Run free tier this
# demo costs $0 - but set a budget alert anyway. See README.md in this folder.

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  services = [
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ]

  secret_values = {
    GROQ_API_KEY        = var.groq_api_key
    QDRANT_API_KEY      = var.qdrant_api_key
    LANGFUSE_SECRET_KEY = var.langfuse_secret_key
  }
  # Only create secrets for values that were provided. for_each can't iterate
  # over sensitive values, so we take just the (non-secret) key names.
  secrets = toset(nonsensitive([for k, v in local.secret_values : k if v != ""]))
}

resource "google_project_service" "enabled" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

# --------------------------------------------------------------- identity
resource "google_service_account" "run" {
  account_id   = "opspilot-run"
  display_name = "OpsPilot Cloud Run runtime"
}

# --------------------------------------------------------------- secrets
resource "google_secret_manager_secret" "this" {
  for_each  = local.secrets
  secret_id = "opspilot-${lower(replace(each.value, "_", "-"))}"

  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "this" {
  for_each    = local.secrets
  secret      = google_secret_manager_secret.this[each.key].id
  secret_data = local.secret_values[each.key]
}

resource "google_secret_manager_secret_iam_member" "run_can_read" {
  for_each  = local.secrets
  secret_id = google_secret_manager_secret.this[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run.email}"
}

# --------------------------------------------------------------- API service
resource "google_cloud_run_v2_service" "api" {
  name                = "opspilot-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.run.email

    # Scale to zero when idle = no cost. One instance max = no surprise bills.
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = var.api_image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "OPSPILOT_MODE"
        value = "cloud"
      }
      env {
        name  = "QDRANT_URL"
        value = var.qdrant_url
      }
      env {
        name  = "GROQ_MODEL"
        value = var.groq_model
      }
      env {
        # The knowledge base is loaded once from your laptop (make ingest-cloud).
        name  = "AUTO_INGEST"
        value = "false"
      }
      env {
        name  = "LANGFUSE_PUBLIC_KEY"
        value = var.langfuse_public_key
      }

      dynamic "env" {
        for_each = local.secrets
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this[env.key].secret_id
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        http_get {
          path = "/health"
        }
        initial_delay_seconds = 10
        period_seconds        = 10
        timeout_seconds       = 5
        failure_threshold     = 18
      }
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_secret_manager_secret_iam_member.run_can_read,
    google_secret_manager_secret_version.this,
  ]
}

# --------------------------------------------------------------- UI service
resource "google_cloud_run_v2_service" "ui" {
  name                = "opspilot-ui"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.run.email

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    # Streamlit keeps a websocket open per browser tab.
    session_affinity = true

    containers {
      image = var.ui_image

      ports {
        container_port = 8501
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }

      env {
        name  = "UI_BACKEND"
        value = "api"
      }
      env {
        name  = "API_URL"
        value = google_cloud_run_v2_service.api.uri
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

# --------------------------------------------------------------- public access
resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = {
    api = google_cloud_run_v2_service.api.name
    ui  = google_cloud_run_v2_service.ui.name
  }
  location = var.region
  name     = each.value
  role     = "roles/run.invoker"
  member   = "allUsers"
}
