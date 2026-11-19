output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "ui_url" {
  value = google_cloud_run_v2_service.ui.uri
}
