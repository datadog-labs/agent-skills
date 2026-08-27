# GCP integration Terraform template

The standalone configuration to emit when the user has no existing Terraform project. When they do,
take only the resources below the `provider` blocks and add them to what they already have.

Placeholders to replace before writing the file:

| Placeholder | Source |
|---|---|
| `<USER_FOLDER_IDS>` | Phase 2 - a **complete HCL list**, e.g. `["123456789"]`, or `[]` when none. The placeholder replaces the whole expression, brackets included |
| `<USER_PROJECT_IDS>` | Phase 2 - a complete HCL list, e.g. `["my-proj"]`, or `[]` when none |
| `<HOST_PROJECT_ID>` | Phase 2 - the project that holds the service account |
| `<DATADOG_PRINCIPAL_ID>` | Phase 1 - Datadog's delegate service-account email |
| `<DD_SITE>` | Phase 0 (`DD_SITE`) |

```hcl
# No root variables hold the Datadog keys. The provider reads them from DD_API_KEY and DD_APP_KEY in the
# environment, so there is nothing for Terraform to record: no variable value in state, none in a saved
# plan, and no `-var=` on any command line. This also means an existing project needs no variable changes.

locals {
  folder_ids  = <USER_FOLDER_IDS>
  project_ids = <USER_PROJECT_IDS>
  project_id  = "<HOST_PROJECT_ID>"
  # roles/browser is deliberately NOT here: Datadog requires it only in the service account's own
  # (host) project, and it is granted separately below. Granting it to every monitored scope both
  # over-permissions them and misses the host project when the host is out of monitoring scope.
  roles_to_assign = [
    "roles/cloudasset.viewer",
    "roles/compute.viewer",
    "roles/monitoring.viewer",
    "roles/serviceusage.serviceUsageConsumer",
  ]
  apis_to_enable = [
    "cloudasset.googleapis.com",
    "compute.googleapis.com",
    "monitoring.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ]
  datadog_site = "<DD_SITE>"
}

terraform {
  required_providers {
    datadog = {
      source  = "DataDog/datadog"
      version = "~> 3.85.0"
    }
    google = {
      source = "hashicorp/google"
    }
  }
}

provider "datadog" {
  # api_key and app_key are intentionally omitted: the provider picks them up from DD_API_KEY and
  # DD_APP_KEY. api_url is not a secret, so it stays explicit.
  api_url = "https://api.${local.datadog_site}"
}

# Direct child projects of each folder. This filter matches on immediate parent, so projects nested in
# sub-folders are NOT returned: they still inherit the folder-level IAM grants below, but they do not get
# API enablement from this config. List nested folders explicitly, or name their projects in project_ids.
#
# This is the Cloud Resource Manager v1 projects.list grammar, NOT the gcloud --filter grammar used during
# discovery: it runs server-side, has no AND/OR/NOT keywords (terms are space-separated and implicitly
# ANDed), requires parent.type alongside parent.id, and has no projectId field. Google-managed sys-*
# projects are therefore excluded client-side, in all_project_ids below.
data "google_projects" "folder_projects" {
  for_each = toset(local.folder_ids)
  filter   = "parent.type:folder parent.id:${each.value} lifecycleState:ACTIVE"
}

# The host project needs the IAM APIs regardless of whether it is monitored, because the service account
# below is created in it and Datadog impersonates that account.
resource "google_project_service" "host_project_iam_apis" {
  for_each = toset(["iam.googleapis.com", "iamcredentials.googleapis.com"])

  project = local.project_id
  service = each.value
}

# Combine explicit projects and folder projects into a single set
locals {
  all_project_ids = toset(
    concat(
      local.project_ids,
      flatten([
        for f in data.google_projects.folder_projects :
        [for p in f.projects : p.project_id if !startswith(p.project_id, "sys-")]
      ])
    )
  )
}

# Enable required GCP APIs for all projects
resource "google_project_service" "enabled_apis" {
  for_each = {
    for combo in setproduct(local.all_project_ids, local.apis_to_enable) :
    "${combo[0]}-${combo[1]}" => { project_id = combo[0], api = combo[1] }
  }

  project = each.value.project_id
  service = each.value.api
}

# Create the service account in the host project
resource "google_service_account" "datadog_gcp_service_account" {
  account_id   = "datadog-integration"
  display_name = "Datadog Service Account"
  project      = local.project_id

  depends_on = [
    google_project_service.enabled_apis,
    google_project_service.host_project_iam_apis,
  ]
}

# Browser in the host project only - the project the service account itself lives in.
resource "google_project_iam_member" "datadog_host_project_browser" {
  project = local.project_id
  role    = "roles/browser"
  member  = "serviceAccount:${google_service_account.datadog_gcp_service_account.email}"

  depends_on = [google_service_account.datadog_gcp_service_account]
}

# Grant the Datadog delegate principal the ability to impersonate this service account
resource "google_service_account_iam_member" "datadog_gcp_service_account_token_creator" {
  service_account_id = google_service_account.datadog_gcp_service_account.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:<DATADOG_PRINCIPAL_ID>"

  depends_on = [
    google_project_service.enabled_apis,
    google_service_account.datadog_gcp_service_account,
  ]
}

# Assign roles to the service account in all explicit projects
resource "google_project_iam_member" "datadog_gcp_service_account_project_roles" {
  for_each = {
    for combo in setproduct(local.project_ids, local.roles_to_assign) :
    "${combo[0]}-${combo[1]}" => { project_id = combo[0], role = combo[1] }
  }

  project = each.value.project_id
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.datadog_gcp_service_account.email}"

  depends_on = [
    google_project_service.enabled_apis,
    google_service_account.datadog_gcp_service_account,
  ]
}

# Assign roles to the service account in all folders
resource "google_folder_iam_member" "datadog_gcp_service_account_folder_roles" {
  for_each = {
    for combo in setproduct(local.folder_ids, local.roles_to_assign) :
    "${combo[0]}-${combo[1]}" => { folder_id = combo[0], role = combo[1] }
  }

  folder = each.value.folder_id
  role   = each.value.role
  member = "serviceAccount:${google_service_account.datadog_gcp_service_account.email}"

  depends_on = [
    google_project_service.enabled_apis,
    google_service_account.datadog_gcp_service_account,
  ]
}

# Register the integration with Datadog
resource "datadog_integration_gcp_sts" "datadog_integration" {
  depends_on = [
    google_project_service.enabled_apis,
    google_service_account.datadog_gcp_service_account,
    google_service_account_iam_member.datadog_gcp_service_account_token_creator,
    google_project_iam_member.datadog_gcp_service_account_project_roles,
    google_folder_iam_member.datadog_gcp_service_account_folder_roles,
  ]

  client_email                 = google_service_account.datadog_gcp_service_account.email
  automute                     = true
  resource_collection_enabled  = true
  is_per_project_quota_enabled = true
  is_global_location_enabled   = false
  metric_namespace_configs     = []
  monitored_resource_configs   = []
  account_tags                 = []
  region_filter_configs        = []
}
```

## Variants

- **Projects only (no folders).** Delete the `folder_ids` local, the `google_projects.folder_projects`
  data source, `google_folder_iam_member.datadog_gcp_service_account_folder_roles`, and its `depends_on`
  entry; simplify `all_project_ids` to `toset(local.project_ids)`.
- **Folders only.** Keep `folder_ids` populated *and* set `project_ids` to the expanded list of descendant
  projects gathered during discovery. Do not leave `project_ids = []`: folder-scope roles are inherited by
  every current and future project, but `google_project_service` only enables APIs for projects it is told
  about, so an empty list silently ships an integration that collects nothing.
- **The host project is not in scope.** The host project only needs to hold the service account, so it
  can stay out of `project_ids` and out of the monitored folders if the user doesn't want its metrics
  collected. Keep `google_project_service.host_project_iam_apis` in that case - it is what enables
  `iam.googleapis.com` / `iamcredentials.googleapis.com` there, and without it service-account creation or
  impersonation fails in a project that no other resource touches. Keep
  `google_project_iam_member.datadog_host_project_browser` for the same reason - `roles/browser` is required
  in the service account's own project regardless of whether that project is monitored.
- **Nested folders.** `data.google_projects` matches on immediate parent only, so a project inside a
  sub-folder inherits the folder IAM grants but never gets API enablement. Either list each nested folder
  in `folder_ids` or name those projects in `project_ids` - do not assume one top-level folder covers a
  deep hierarchy.
- **Explicit `google` provider config.** The template relies on ambient application-default credentials.
  Add `provider "google" { project = local.project_id }` if the user needs a pinned project or an
  impersonated identity.
