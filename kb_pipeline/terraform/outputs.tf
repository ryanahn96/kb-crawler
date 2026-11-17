output "kb_bucket_name" {
  description = "KB bucket name. Pass it to sync_kb.py as --bucket."
  value       = google_storage_bucket.kb.name
}

output "datastore_id" {
  description = "Vertex AI Search data store ID. Pass it to sync_kb.py as --datastore-id."
  value       = google_discovery_engine_data_store.kb.data_store_id
}

output "datastore_location" {
  description = "Vertex AI Search data store location. Pass it to sync_kb.py as --datastore-location."
  value       = google_discovery_engine_data_store.kb.location
}

output "datastore_name" {
  description = "Full resource name of the data store, for the CXAS DataStoreTool."
  value       = google_discovery_engine_data_store.kb.name
}
