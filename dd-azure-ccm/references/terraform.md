# Azure Cloud Cost Management Terraform template

The HCL for Phase 3 of `SKILL.md`. Emit only the sections that apply to the user's path (the table
under "Generate Terraform HCL" in `SKILL.md` says which), and split them across two files as described
in "Apply Terraform in Two Phases": Sections 1-4 in `azure-ccm.tf`, Section 5 in
`datadog-ccm-config.tf`. When the user already has a Terraform project, add these resources to it and
reuse the providers already there.

Placeholders to replace before writing the files:

| Placeholder | Source |
|---|---|
| `<STORAGE_ACCOUNT_NAME>`, `<CONTAINER_NAME>`, `<STORAGE_RESOURCE_GROUP>`, `<LOCATION>` | Phase 2 - Storage Configuration |
| `<STORAGE_SUBSCRIPTION_ID>` | Phase 2 - Storage Configuration, used in the `azurerm` provider block in `SKILL.md` |
| `<COST_RESOURCE_GROUP>` | Phase 2 - Scope Details, the Resource Group scope (not the storage resource group) |
| `<SCOPE>` | Phase 2 - `billing_account`, `subscription` or `resource_group` |
| `<SCOPE_ID_FIELD>`, `<SCOPE_ID_VALUE>`, `<SCOPE_VALUE>` | Phase 2 - scope details, formatted as shown in Sections 3 and 5 |
| `<BILLING_ACCOUNT_ID>`, `<SUBSCRIPTION_ID>` | Phase 2 - scope details |
| `<ACTUAL_EXPORT_NAME>`, `<ACTUAL_EXPORT_PATH>`, `<AMORTIZED_EXPORT_NAME>`, `<AMORTIZED_EXPORT_PATH>` | Phase 2 - Export Configuration |
| `<CURRENT_DATE_MIDNIGHT_UTC>` | Today's date in `YYYY-MM-DDT00:00:00Z` format |
| `<CONTAINER_ID_REF>` | `local.ccm_storage_container_id` if creating new storage (defined in Section 1), `local.storage_container_id` if using existing |
| `<TENANT_ID>` | Phase 1 - the selected integration's `tenant_name` |
| `<CLIENT_ID>`, `<EXISTING_CLIENT_ID>` | Phase 1 - the selected integration's `client_id` |

Build the configuration from the following sections.

**Section 1: Storage Account and Container** (only if creating new)

```hcl
resource "azurerm_storage_account" "ccm_storage_account" {
  name                            = "<STORAGE_ACCOUNT_NAME>"
  resource_group_name             = "<STORAGE_RESOURCE_GROUP>"
  location                        = "<LOCATION>"
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  allow_nested_items_to_be_public = false
}

resource "azurerm_storage_container" "ccm_storage_container" {
  name                  = "<CONTAINER_NAME>"
  storage_account_id    = azurerm_storage_account.ccm_storage_account.id
  container_access_type = "private"
}

locals {
  ccm_storage_container_id = "${azurerm_storage_account.ccm_storage_account.id}/blobServices/default/containers/${azurerm_storage_container.ccm_storage_container.name}"
}
```

The container uses `storage_account_id` (AzureRM 4.9.0+; `storage_account_name` was removed in 5.x).
The container's Resource Manager ID is built explicitly in `local.ccm_storage_container_id` so it does
not depend on the `resource_manager_id` attribute (removed in 5.x) or on the format of `id`, which
differs between 4.x and 5.x.

**Section 2: Existing Storage Reference** (only if using existing)

```hcl
data "azurerm_storage_account" "existing" {
  name                = "<STORAGE_ACCOUNT_NAME>"
  resource_group_name = "<STORAGE_RESOURCE_GROUP>"
}

locals {
  storage_container_id = "${data.azurerm_storage_account.existing.id}/blobServices/default/containers/<CONTAINER_NAME>"
}
```

**Section 3: Cost Exports** (only if creating new exports)

The resource type depends on the scope:
- Billing Account: `azurerm_billing_account_cost_management_export`
- Subscription: `azurerm_subscription_cost_management_export`
- Resource Group: `azurerm_resource_group_cost_management_export`

The scope ID field also depends on the scope:
- Billing Account: `billing_account_id = "/providers/Microsoft.Billing/billingAccounts/<BILLING_ACCOUNT_ID>"`
- Subscription: `subscription_id = "/subscriptions/<SUBSCRIPTION_ID>"`
- Resource Group: `resource_group_id = "/subscriptions/<SUBSCRIPTION_ID>/resourceGroups/<COST_RESOURCE_GROUP>"`

Generate two exports - one for actual cost, one for amortized:

```hcl
resource "azurerm_<SCOPE>_cost_management_export" "actual_cost" {
  name                         = "<ACTUAL_EXPORT_NAME>"
  <SCOPE_ID_FIELD>             = "<SCOPE_ID_VALUE>"
  recurrence_type              = "Daily"
  recurrence_period_start_date = "<CURRENT_DATE_MIDNIGHT_UTC>"
  recurrence_period_end_date   = "2124-01-01T00:00:00Z"

  export_data_storage_location {
    container_id     = <CONTAINER_ID_REF>
    root_folder_path = "/<ACTUAL_EXPORT_PATH>"
  }

  export_data_options {
    type       = "ActualCost"
    time_frame = "MonthToDate"
  }

  lifecycle {
    ignore_changes = all
  }
}

resource "azurerm_<SCOPE>_cost_management_export" "amortized_cost" {
  name                         = "<AMORTIZED_EXPORT_NAME>"
  <SCOPE_ID_FIELD>             = "<SCOPE_ID_VALUE>"
  recurrence_type              = "Daily"
  recurrence_period_start_date = "<CURRENT_DATE_MIDNIGHT_UTC>"
  recurrence_period_end_date   = "2124-01-01T00:00:00Z"

  export_data_storage_location {
    container_id     = <CONTAINER_ID_REF>
    root_folder_path = "/<AMORTIZED_EXPORT_PATH>"
  }

  export_data_options {
    type       = "AmortizedCost"
    time_frame = "MonthToDate"
  }

  lifecycle {
    ignore_changes = all
  }
}
```

Replace `<CONTAINER_ID_REF>` with:
- `local.ccm_storage_container_id` if creating new
- `local.storage_container_id` if using existing

Replace `<CURRENT_DATE_MIDNIGHT_UTC>` with today's date in `YYYY-MM-DDT00:00:00Z` format.

**Section 4: Role Assignments** (always included)

```hcl
data "azuread_service_principal" "datadog" {
  client_id = "<CLIENT_ID>"
}

resource "azurerm_role_assignment" "datadog_storage_reader" {
  scope                = <CONTAINER_ID_REF>
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = data.azuread_service_principal.datadog.object_id
}
```

For **Subscription** and **Resource Group** scopes, also add:

```hcl
resource "azurerm_role_assignment" "datadog_cost_reader" {
  scope                = "/subscriptions/<SUBSCRIPTION_ID>"
  role_definition_name = "Cost Management Reader"
  principal_id         = data.azuread_service_principal.datadog.object_id
}
```

This is **not needed** for Billing Account scope.

**Section 5: Datadog Azure UC Config** (always included)

The `scope` value depends on the scope type:
- Billing Account: `"/providers/Microsoft.Billing/billingAccounts/<BILLING_ACCOUNT_ID>"`
- Subscription: `"/subscriptions/<SUBSCRIPTION_ID>"`
- Resource Group: `"/subscriptions/<SUBSCRIPTION_ID>/resourceGroups/<COST_RESOURCE_GROUP>"`

Declare `depends_on` referencing the role assignments and exports so Terraform has an explicit
ordering. On the second apply those resources already exist in state, so this just makes the
dependency graph explicit and keeps the config honest if someone ever collapses the flow into a
single apply.

| Scope | depends_on contents |
|---|---|
| Billing Account | `datadog_storage_reader`, actual + amortized exports (omit `datadog_cost_reader` - not used) |
| Subscription | `datadog_storage_reader`, `datadog_cost_reader`, actual + amortized exports |
| Resource Group | `datadog_storage_reader`, `datadog_cost_reader`, actual + amortized exports |

For the existing-exports case, drop the export references and keep just the role assignments.

Pick the variant that matches the path the user is on. The `client_id`, `storage_account`, and `storage_container` fields are written DIFFERENTLY for the resource-reference case (unquoted) vs the literal case (quoted) - do not mix the two.

**Variant A - New storage (Section 1), and `azuread_application.datadog_app` is declared in this root module:** all three are HCL resource references, written unquoted. If that resource is not in the current module, use Variant B.

```hcl
resource "datadog_azure_uc_config" "azure_uc_config" {
  account_id = "<TENANT_ID>"
  client_id  = azuread_application.datadog_app.client_id
  scope      = "<SCOPE_VALUE>"

  actual_bill_config {
    export_name       = "<ACTUAL_EXPORT_NAME>"
    export_path       = "/<ACTUAL_EXPORT_PATH>"
    storage_account   = azurerm_storage_account.ccm_storage_account.name
    storage_container = azurerm_storage_container.ccm_storage_container.name
  }

  amortized_bill_config {
    export_name       = "<AMORTIZED_EXPORT_NAME>"
    export_path       = "/<AMORTIZED_EXPORT_PATH>"
    storage_account   = azurerm_storage_account.ccm_storage_account.name
    storage_container = azurerm_storage_container.ccm_storage_container.name
  }

  depends_on = [
    azurerm_role_assignment.datadog_storage_reader,
    # azurerm_role_assignment.datadog_cost_reader,  # omit for Billing Account scope
    azurerm_<SCOPE>_cost_management_export.actual_cost,
    azurerm_<SCOPE>_cost_management_export.amortized_cost,
  ]
}
```

**Variant B - Existing integration + new storage (Section 1):** storage fields are still HCL references (storage was created in this apply), but `client_id` is the literal client_id of the user's existing Datadog Azure app registration. Fetch it via `GET /api/v1/integration/azure` and inline as a quoted string.

```hcl
resource "datadog_azure_uc_config" "azure_uc_config" {
  account_id = "<TENANT_ID>"
  client_id  = "<EXISTING_CLIENT_ID>"
  scope      = "<SCOPE_VALUE>"

  actual_bill_config {
    export_name       = "<ACTUAL_EXPORT_NAME>"
    export_path       = "/<ACTUAL_EXPORT_PATH>"
    storage_account   = azurerm_storage_account.ccm_storage_account.name
    storage_container = azurerm_storage_container.ccm_storage_container.name
  }

  amortized_bill_config {
    export_name       = "<AMORTIZED_EXPORT_NAME>"
    export_path       = "/<AMORTIZED_EXPORT_PATH>"
    storage_account   = azurerm_storage_account.ccm_storage_account.name
    storage_container = azurerm_storage_container.ccm_storage_container.name
  }

  depends_on = [
    azurerm_role_assignment.datadog_storage_reader,
    # azurerm_role_assignment.datadog_cost_reader,  # omit for Billing Account scope
    azurerm_<SCOPE>_cost_management_export.actual_cost,
    azurerm_<SCOPE>_cost_management_export.amortized_cost,
  ]
}
```

**Variant C - Existing integration + existing storage (Section 2):** `client_id` is the literal existing value as in Variant B; `storage_account` is the data-source `.name` (HCL reference, unquoted); `storage_container` is the literal container name string the user provided.

```hcl
resource "datadog_azure_uc_config" "azure_uc_config" {
  account_id = "<TENANT_ID>"
  client_id  = "<EXISTING_CLIENT_ID>"
  scope      = "<SCOPE_VALUE>"

  actual_bill_config {
    export_name       = "<ACTUAL_EXPORT_NAME>"
    export_path       = "/<ACTUAL_EXPORT_PATH>"
    storage_account   = data.azurerm_storage_account.existing.name
    storage_container = "<CONTAINER_NAME>"
  }

  amortized_bill_config {
    export_name       = "<AMORTIZED_EXPORT_NAME>"
    export_path       = "/<AMORTIZED_EXPORT_PATH>"
    storage_account   = data.azurerm_storage_account.existing.name
    storage_container = "<CONTAINER_NAME>"
  }

  depends_on = [
    azurerm_role_assignment.datadog_storage_reader,
    # azurerm_role_assignment.datadog_cost_reader,  # omit for Billing Account scope
    azurerm_<SCOPE>_cost_management_export.actual_cost,
    azurerm_<SCOPE>_cost_management_export.amortized_cost,
  ]
}
```
