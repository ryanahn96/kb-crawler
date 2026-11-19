# IAM follows the table in kb_pipeline/CONTRACT.md. Every binding is additive
# (iam_member), so nothing here removes access granted elsewhere.

# ---------------------------------------------------------------------------
# Operator (sync script): writes snapshots, processed blobs, manifests, and
# state in this bucket, and imports into Vertex AI Search.
# ---------------------------------------------------------------------------

resource "google_storage_bucket_iam_member" "operator_objects" {
  for_each = toset(var.operator_members)
  bucket   = google_storage_bucket.kb.name
  role     = "roles/storage.objectUser"
  member   = each.value
}

resource "google_project_iam_custom_role" "kb_importer" {
  project     = var.project_id
  role_id     = "kbSyncImporter"
  title       = "KB Sync Importer"
  description = "ImportDocuments into Vertex AI Search and poll import operations."
  permissions = [
    "discoveryengine.dataStores.get",
    "discoveryengine.documents.get",
    "discoveryengine.documents.import",
    "discoveryengine.documents.list",
    "discoveryengine.operations.get",
  ]
  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "operator_importer" {
  for_each = toset(var.operator_members)
  project  = var.project_id
  role     = google_project_iam_custom_role.kb_importer.id
  member   = each.value
}

# ---------------------------------------------------------------------------
# Readers of the data store: CX Agent Studio and the Companion Agent.
# ---------------------------------------------------------------------------

resource "google_project_service_identity" "ces" {
  provider   = google-beta
  project    = var.project_id
  service    = "ces.googleapis.com"
  depends_on = [google_project_service.apis]
}

resource "google_project_service_identity" "dialogflow" {
  provider   = google-beta
  project    = var.project_id
  service    = "dialogflow.googleapis.com"
  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "ces_datastore_viewer" {
  project    = var.project_id
  role       = "roles/discoveryengine.viewer"
  member     = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-ces.iam.gserviceaccount.com"
  depends_on = [google_project_service_identity.ces]
}

resource "google_project_iam_member" "dialogflow_datastore_viewer" {
  project    = var.project_id
  role       = "roles/discoveryengine.viewer"
  member     = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-dialogflow.iam.gserviceaccount.com"
  depends_on = [google_project_service_identity.dialogflow]
}
