# GCP Cloud Cost Management Terraform template

The HCL for Phase 3, Step 3 of `SKILL.md`. Emit only the sections that apply to the user's path (the
table in Step 3 says which), together with the provider block from Step 4. When the user already has a
Terraform project, add these resources to it and reuse the providers already there.

Placeholders to replace before writing the file:

| Placeholder | Source |
|---|---|
| `<BUCKET_NAME>` | Phase 2 - GCS bucket (new or existing) |
| `<REGION>` | Phase 2 - GCS bucket region, colocated with the BigQuery export dataset |
| `<BUCKET_REF>` | `google_storage_bucket.billing_bucket.name` if creating new, `"<BUCKET_NAME>"` if using existing |
| `<SERVICE_ACCOUNT_EMAIL>` | Phase 1 - the Datadog service account `client_email` |
| `<EXISTING_DATADOG_CLIENT_EMAIL>` | Phase 1 - the same `client_email`, as a quoted literal (Variants B and C) |
| `<EXPORT_PROJECT_ID>` | Phase 2 - project hosting the BigQuery dataset |
| `<DATASET_NAME>` | Phase 2 - BigQuery dataset for billing data |
| `<BILLING_ACCOUNT_ID>` | Phase 2 - GCP Billing Account ID |

**Section 1: GCS Bucket** (only if creating new)

```hcl
resource "google_storage_bucket" "billing_bucket" {
  project  = "<EXPORT_PROJECT_ID>"
  name     = "<BUCKET_NAME>"
  location = "<REGION>"

  public_access_prevention = "enforced"

  soft_delete_policy {
    retention_duration_seconds = 0
  }
}
```

`public_access_prevention = "enforced"` blocks all public access via IAM or ACLs on the bucket.
Setting `soft_delete_policy.retention_duration_seconds = 0` disables soft delete, matching the
CCM settings UI. `project` is set explicitly so the bucket lands in the export project even when an
existing `google` provider defaults to a different one.

**Section 2: IAM Bindings**

These are the predefined roles Datadog documents for GCP Cloud Cost Management
(https://docs.datadoghq.com/cloud_cost_management/setup/google_cloud/). `roles/bigquery.admin` is broad:
it grants full BigQuery control over the whole export project, so tell the user that before they approve
the plan.

```hcl
resource "google_project_iam_member" "ccm_export_project_role_binding" {
  project = "<EXPORT_PROJECT_ID>"
  role    = "roles/bigquery.admin"
  member  = "serviceAccount:<SERVICE_ACCOUNT_EMAIL>"
}

resource "google_bigquery_dataset_access" "ccm_export_dataset_role_binding" {
  project       = "<EXPORT_PROJECT_ID>"
  dataset_id    = "<DATASET_NAME>"
  role          = "roles/bigquery.dataEditor"
  user_by_email = "<SERVICE_ACCOUNT_EMAIL>"
}

resource "google_storage_bucket_iam_member" "ccm_bucket_reader_binding" {
  bucket = <BUCKET_REF>
  role   = "roles/storage.legacyObjectReader"
  member = "serviceAccount:<SERVICE_ACCOUNT_EMAIL>"
}

resource "google_storage_bucket_iam_member" "ccm_bucket_writer_binding" {
  bucket = <BUCKET_REF>
  role   = "roles/storage.legacyBucketWriter"
  member = "serviceAccount:<SERVICE_ACCOUNT_EMAIL>"
}
```

Replace `<BUCKET_REF>` with:
- `google_storage_bucket.billing_bucket.name` if creating new
- `"<BUCKET_NAME>"` if using existing

The project and dataset grants set `project` explicitly so they land in the export project even when an
existing `google` provider defaults to a different one. Do not change a shared `google` provider's default
project to fix this.

The dataset grant uses `google_bigquery_dataset_access`, which adds one access entry and keeps the
dataset's others. Do not swap it for `google_bigquery_dataset_iam_member`: the Google provider docs warn
that the `google_bigquery_dataset_iam_*` resources remove authorized view permissions from the dataset,
which breaks existing consumers of a reused billing-export dataset. If the project already manages this
dataset's access through `access` blocks on a `google_bigquery_dataset` resource, add the grant as
another `access` block there instead (the two approaches overwrite each other), unless that resource
sets `lifecycle { ignore_changes = [access] }`. In that case, do not emit `google_bigquery_dataset_access.ccm_export_dataset_role_binding`, and in every
Section 3 variant replace it in `depends_on` with that existing `google_bigquery_dataset` resource.

**Section 3: Register GCP cost config with Datadog**

Pick the variant that matches the path the user is on. `bucket_name` and `service_account` are written DIFFERENTLY for the resource-reference case (unquoted) vs the literal case (quoted) - do not mix the two.

**Variant A - New bucket, and `google_service_account.datadog_gcp_service_account` is declared in this root module with an email equal to the Phase 1 `client_email`:** both are HCL resource references, written unquoted. If that resource is not in the current module, or its email is not the selected `client_email`, use Variant B.

```hcl
resource "datadog_gcp_uc_config" "gcp_uc_config" {
  billing_account_id  = "<BILLING_ACCOUNT_ID>"
  bucket_name         = google_storage_bucket.billing_bucket.name
  export_dataset_name = "<DATASET_NAME>"
  export_project_name = "<EXPORT_PROJECT_ID>"
  service_account     = google_service_account.datadog_gcp_service_account.email

  depends_on = [
    google_project_iam_member.ccm_export_project_role_binding,
    google_bigquery_dataset_access.ccm_export_dataset_role_binding,
    google_storage_bucket_iam_member.ccm_bucket_reader_binding,
    google_storage_bucket_iam_member.ccm_bucket_writer_binding,
  ]
}
```

**Variant B - Existing integration + new bucket (or a service account in this module that is not the selected one):** `bucket_name` is still the HCL reference (bucket was created in this apply); `service_account` is the literal Datadog `client_email` from the existing integration. Fetch via `GET /api/v2/integration/gcp/accounts` and inline as a quoted string.

```hcl
resource "datadog_gcp_uc_config" "gcp_uc_config" {
  billing_account_id  = "<BILLING_ACCOUNT_ID>"
  bucket_name         = google_storage_bucket.billing_bucket.name
  export_dataset_name = "<DATASET_NAME>"
  export_project_name = "<EXPORT_PROJECT_ID>"
  service_account     = "<EXISTING_DATADOG_CLIENT_EMAIL>"

  depends_on = [
    google_project_iam_member.ccm_export_project_role_binding,
    google_bigquery_dataset_access.ccm_export_dataset_role_binding,
    google_storage_bucket_iam_member.ccm_bucket_reader_binding,
    google_storage_bucket_iam_member.ccm_bucket_writer_binding,
  ]
}
```

**Variant C - Existing integration + existing bucket:** both are literals - use the bucket name and the Datadog `client_email` collected in Phase 1.

```hcl
resource "datadog_gcp_uc_config" "gcp_uc_config" {
  billing_account_id  = "<BILLING_ACCOUNT_ID>"
  bucket_name         = "<BUCKET_NAME>"
  export_dataset_name = "<DATASET_NAME>"
  export_project_name = "<EXPORT_PROJECT_ID>"
  service_account     = "<EXISTING_DATADOG_CLIENT_EMAIL>"

  depends_on = [
    google_project_iam_member.ccm_export_project_role_binding,
    google_bigquery_dataset_access.ccm_export_dataset_role_binding,
    google_storage_bucket_iam_member.ccm_bucket_reader_binding,
    google_storage_bucket_iam_member.ccm_bucket_writer_binding,
  ]
}
```
