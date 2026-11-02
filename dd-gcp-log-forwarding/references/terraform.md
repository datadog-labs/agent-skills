# GCP log forwarding Terraform template

The full configuration for the Dataflow log-forwarding pipeline. Emit it with every `<PLACEHOLDER>`
filled in as listed under "Applying the Terraform" in `SKILL.md`, and trim the project or folder sink
resources as that section describes.

If the directory already holds Terraform (for example from `dd-gcp-integration`), follow "Existing
Terraform project" in `SKILL.md` before emitting: it merges the provider requirements into the
project's existing `required_providers` block, reuses existing `provider` blocks and renames colliding
locals.

Declare no variables for the Datadog credentials. The provider reads `DD_API_KEY` and `DD_APP_KEY`
from the environment, which keeps them out of command lines, shell history and `.tfvars` files.

```hcl
terraform {
  required_providers {
    google = {
      source = "hashicorp/google"
    }
    datadog = {
      source  = "DataDog/datadog"
      version = "~> 3.79.0"
    }
  }
}

provider "datadog" {
  api_url = "https://api.<DD_SITE>"
}

resource "datadog_api_key" "logs_export" {
  name = "export-logs-to-datadog-api-key"
}

locals {
  project_id         = "<HOST_PROJECT_ID>"
  region             = "<REGION>"
  datadog_site       = "<DD_SITE>"

  folder_ids  = [<USER_FOLDER_IDS>]
  project_ids = [<USER_PROJECT_IDS>]

  resource_name_prefix = "export-logs-to-datadog"
  bucket_prefix        = "dataflow-temp"

  max_workers             = <MAX_WORKERS>
  batch_size              = <BATCH_SIZE>
  parallelism             = <PARALLELISM>
  machine_type            = <MACHINE_TYPE>
  enable_dataflow_prime   = <DATAFLOW_PRIME>
  enable_streaming_engine = <STREAMING_ENGINE>
  network                 = <NETWORK>
  subnetwork              = <SUBNETWORK>
  ip_configuration        = <IP_CONFIGURATION>

  inclusion_filter  = <INCLUSION_FILTER>
  exclusion_filters = <EXCLUSION_FILTERS>

  bucket_name                 = "${local.bucket_prefix}-${local.project_id}"
  pubsub_topic_id             = "${local.resource_name_prefix}-topic"
  pubsub_dead_letter_topic_id = "${local.resource_name_prefix}-dlq"
  secret_manager_name         = "${local.resource_name_prefix}-api-key"
  dataflow_job_name           = "${local.resource_name_prefix}-job"
  log_sink_name               = "${local.resource_name_prefix}-log-sink"
  datadog_intake_url          = "https://http-intake.logs.${local.datadog_site}"
}

resource "google_project_service" "iam" {
  project            = local.project_id
  service            = "iam.googleapis.com"
  disable_on_destroy = false
}

# Service account for Dataflow
resource "google_service_account" "dataflow_service_account" {
  account_id   = "export-logs-to-datadog-sa"
  display_name = "Datadog Dataflow Service Account"
  project      = local.project_id

  depends_on = [google_project_service.iam]
}

# Enable Dataflow API
resource "google_project_service" "dataflow" {
  project            = local.project_id
  service            = "dataflow.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "pubsub" {
  project            = local.project_id
  service            = "pubsub.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secretmanager" {
  project            = local.project_id
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

# GCS bucket for Dataflow temporary files
resource "google_storage_bucket" "dataflow_temp" {
  project  = local.project_id
  name     = local.bucket_name
  location = local.region

  soft_delete_policy {
    retention_duration_seconds = 0
  }
}

resource "google_storage_bucket_iam_member" "dataflow_temp_admin" {
  bucket = google_storage_bucket.dataflow_temp.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.dataflow_service_account.email}"
}

# Pub/Sub topic and subscription for logs
resource "google_pubsub_topic" "logs" {
  project = local.project_id
  name    = local.pubsub_topic_id

  depends_on = [google_project_service.pubsub]
}

resource "google_pubsub_subscription" "logs" {
  project = local.project_id
  name    = "${local.pubsub_topic_id}-subscription"
  topic   = google_pubsub_topic.logs.name
}

resource "google_pubsub_subscription_iam_member" "subscriber" {
  project      = local.project_id
  subscription = google_pubsub_subscription.logs.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.dataflow_service_account.email}"
}

resource "google_pubsub_subscription_iam_member" "viewer" {
  project      = local.project_id
  subscription = google_pubsub_subscription.logs.name
  role         = "roles/pubsub.viewer"
  member       = "serviceAccount:${google_service_account.dataflow_service_account.email}"
}

# Dead letter topic and subscription
resource "google_pubsub_topic" "dead_letter" {
  project = local.project_id
  name    = local.pubsub_dead_letter_topic_id

  depends_on = [google_project_service.pubsub]
}

resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  project = local.project_id
  topic   = google_pubsub_topic.dead_letter.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.dataflow_service_account.email}"
}

resource "google_pubsub_subscription" "dead_letter" {
  project = local.project_id
  name    = "${local.pubsub_dead_letter_topic_id}-subscription"
  topic   = google_pubsub_topic.dead_letter.name

  # Nothing consumes this subscription and publishing does not count as activity, so without this the
  # default 31-day inactivity expiry deletes it and later failed logs are dropped.
  expiration_policy {
    ttl = ""
  }
}

resource "google_pubsub_subscription_iam_member" "dead_letter_subscriber" {
  project      = local.project_id
  subscription = google_pubsub_subscription.dead_letter.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.dataflow_service_account.email}"
}

resource "google_pubsub_subscription_iam_member" "dead_letter_viewer" {
  project      = local.project_id
  subscription = google_pubsub_subscription.dead_letter.name
  role         = "roles/pubsub.viewer"
  member       = "serviceAccount:${google_service_account.dataflow_service_account.email}"
}

# Secret Manager for Datadog API key
resource "google_secret_manager_secret" "datadog_api_key" {
  project   = local.project_id
  secret_id = local.secret_manager_name

  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager]
}

# This puts the key in state (see "Important Notes" in SKILL.md for why this differs from the AWS skill's
# out-of-band seeding).
resource "google_secret_manager_secret_version" "datadog_api_key" {
  secret      = google_secret_manager_secret.datadog_api_key.id
  secret_data = datadog_api_key.logs_export.key
}

resource "google_secret_manager_secret_iam_member" "datadog_api_key_accessor" {
  project   = local.project_id
  secret_id = google_secret_manager_secret.datadog_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.dataflow_service_account.email}"
}

# Dataflow worker role
resource "google_project_iam_member" "dataflow_worker" {
  project = local.project_id
  role    = "roles/dataflow.worker"
  member  = "serviceAccount:${google_service_account.dataflow_service_account.email}"
}

# Log sinks for projects
resource "google_logging_project_sink" "datadog" {
  for_each = toset(local.project_ids)

  project     = each.value
  name        = local.log_sink_name
  destination = "pubsub.googleapis.com/${google_pubsub_topic.logs.id}"

  filter = local.inclusion_filter

  dynamic "exclusions" {
    for_each = local.exclusion_filters
    content {
      name   = exclusions.value.name
      filter = exclusions.value.filter
    }
  }

  unique_writer_identity = true
}

resource "google_pubsub_topic_iam_member" "log_sink_publisher_project" {
  for_each = toset(local.project_ids)

  project = local.project_id
  topic   = google_pubsub_topic.logs.name
  role    = "roles/pubsub.publisher"
  member  = google_logging_project_sink.datadog[each.key].writer_identity
}

# Log sinks for folders
resource "google_logging_folder_sink" "datadog" {
  for_each = toset(local.folder_ids)

  folder           = each.value
  name             = local.log_sink_name
  destination      = "pubsub.googleapis.com/${google_pubsub_topic.logs.id}"
  include_children = true

  filter = local.inclusion_filter

  dynamic "exclusions" {
    for_each = local.exclusion_filters
    content {
      name   = exclusions.value.name
      filter = exclusions.value.filter
    }
  }
}

resource "google_pubsub_topic_iam_member" "log_sink_publisher_folder" {
  for_each = toset(local.folder_ids)

  project = local.project_id
  topic   = google_pubsub_topic.logs.name
  role    = "roles/pubsub.publisher"
  member  = google_logging_folder_sink.datadog[each.key].writer_identity
}

# Dataflow job
resource "google_dataflow_job" "datadog_export" {
  project               = local.project_id
  name                  = local.dataflow_job_name
  template_gcs_path     = "gs://dataflow-templates-${local.region}/latest/Cloud_PubSub_to_Datadog"
  temp_gcs_location     = "gs://${google_storage_bucket.dataflow_temp.name}"
  region                = local.region
  service_account_email = google_service_account.dataflow_service_account.email
  max_workers           = local.max_workers
  network               = local.network
  subnetwork            = local.subnetwork
  ip_configuration      = local.ip_configuration

  parameters = {
    inputSubscription      = google_pubsub_subscription.logs.id
    url                    = local.datadog_intake_url
    apiKeySource           = "SECRET_MANAGER"
    apiKeySecretId         = "${google_secret_manager_secret.datadog_api_key.id}/versions/latest"
    outputDeadletterTopic  = google_pubsub_topic.dead_letter.id
    batchCount             = local.batch_size
    parallelism            = local.parallelism
  }

  additional_experiments = concat(
    local.enable_dataflow_prime ? ["enable_prime"] : [],
    local.enable_streaming_engine ? ["enable_streaming_engine"] : []
  )

  machine_type = local.enable_dataflow_prime ? null : local.machine_type

  depends_on = [
    google_project_service.dataflow,
    google_storage_bucket_iam_member.dataflow_temp_admin,
    google_pubsub_subscription_iam_member.subscriber,
    google_pubsub_subscription_iam_member.viewer,
    google_pubsub_topic_iam_member.dead_letter_publisher,
    google_secret_manager_secret_version.datadog_api_key,
    google_secret_manager_secret_iam_member.datadog_api_key_accessor,
    google_project_iam_member.dataflow_worker,
  ]
}
```
