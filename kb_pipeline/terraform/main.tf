locals {
  required_apis = toset([
    "ces.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "dialogflow.googleapis.com",
    "discoveryengine.googleapis.com",
    "iam.googleapis.com",
    "storage.googleapis.com",
  ])

  bucket_location = coalesce(var.bucket_location, var.region)

  # Object name prefixes used in IAM conditions (bucket layout in CONTRACT.md).
  bucket_object_prefix = "projects/_/buckets/${google_storage_bucket.kb.name}/objects/"
}

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_project_service" "apis" {
  for_each           = local.required_apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# 1. Private KB bucket
#    Snapshots are kept forever: no lifecycle rule, and Terraform refuses to
#    destroy the bucket unless prevent_destroy is changed.
# ---------------------------------------------------------------------------

resource "google_storage_bucket" "kb" {
  name                        = var.kb_bucket_name
  project                     = var.project_id
  location                    = local.bucket_location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# 2. Vertex AI Search data store
#    Chunking and parsing are fixed at creation. Changing anything in
#    document_processing_config replaces the data store and empties the index.
# ---------------------------------------------------------------------------

resource "google_discovery_engine_data_store" "kb" {
  project                     = var.project_id
  location                    = var.datastore_location
  data_store_id               = var.datastore_id
  display_name                = var.datastore_display_name
  industry_vertical           = "GENERIC"
  content_config              = "CONTENT_REQUIRED"
  solution_types              = ["SOLUTION_TYPE_SEARCH"]
  create_advanced_site_search = false

  document_processing_config {
    chunking_config {
      layout_based_chunking_config {
        chunk_size                = 500
        include_ancestor_headings = true
      }
    }

    # HTML Knowledge Pages (with inline Base64 images and <figcaption> URLs)
    # and Manual Documents all use Layout Parser with table + image annotation.
    default_parsing_config {
      layout_parsing_config {
        enable_table_annotation = true
        enable_image_annotation = true
      }
    }

    dynamic "parsing_config_overrides" {
      for_each = toset(["html", "pdf", "docx", "pptx", "xlsx"])
      content {
        file_type = parsing_config_overrides.value
        layout_parsing_config {
          enable_table_annotation = true
          enable_image_annotation = true
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}
