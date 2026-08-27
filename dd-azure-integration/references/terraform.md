# Azure integration Terraform template

The standalone configuration to emit when the user has no existing Terraform project. When they do,
take only the resources below the `provider` blocks and add them to what they already have.

Placeholders to replace before writing the file:

| Placeholder | Source |
|---|---|
| `<USER_SUBSCRIPTION_IDS>` | Phase 1 - a **complete HCL list**, e.g. `["sub-a", "sub-b"]`. Never a bare comma-separated fragment: the placeholder replaces the whole expression, brackets included |
| `<USER_MANAGEMENT_GROUP_NAMES>` | Phase 1 - a complete HCL list, e.g. `["mg-root"]`, or `[]` when none |
| `<TENANT_ID>` | Phase 1 - the Entra tenant ID. Both providers are pinned to it so a guest/multi-tenant login cannot create the app in a different tenant than the one you checked for duplicates |
| `<DD_SITE>` | Phase 0 (`DD_SITE`) |

```hcl
# No root variables hold the Datadog keys. The provider reads them from DD_API_KEY and DD_APP_KEY in the
# environment, so there is nothing for Terraform to record: no variable value in state, none in a saved
# plan, and no `-var=` on any command line. This also means an existing project needs no variable changes.

locals {
  tenant_id              = "<TENANT_ID>"
  azure_subscription_ids = <USER_SUBSCRIPTION_IDS>
  management_group_names = <USER_MANAGEMENT_GROUP_NAMES>
  # The full set Datadog requires; do not trim it. Source of truth:
  # https://docs.datadoghq.com/integrations/guide/azure-graph-api-permissions/
  graph_api_permissions = [
    "AdministrativeUnit.Read.All",
    "Application.Read.All",
    "AuditLog.Read.All",
    "Directory.Read.All",
    "Domain.Read.All",
    "Group.Read.All",
    "Policy.Read.All",
    "PrivilegedAssignmentSchedule.Read.AzureADGroup",
    "PrivilegedEligibilitySchedule.Read.AzureADGroup",
    "RoleManagement.Read.All",
    "User.Read.All",
  ]
  datadog_site = "<DD_SITE>"
}

terraform {
  required_providers {
    datadog = { source = "DataDog/datadog", version = ">=3.61.0" }
    azurerm = { source = "hashicorp/azurerm" }
    azuread = { source = "hashicorp/azuread" }
    time    = { source = "hashicorp/time" }
  }
}

# Both providers are pinned to the tenant gathered in Phase 1. Without this, the az CLI's *current*
# tenant wins, which for a guest or multi-tenant login is not necessarily the tenant you checked.
provider "azurerm" {
  features {}
  tenant_id       = local.tenant_id
  subscription_id = local.azure_subscription_ids[0]
}

provider "azuread" {
  tenant_id = local.tenant_id
}

provider "datadog" {
  # api_key and app_key are intentionally omitted: the provider picks them up from DD_API_KEY and
  # DD_APP_KEY. api_url is not a secret, so it stays explicit.
  api_url = "https://api.${local.datadog_site}"
}

# Create the App Registration
resource "azuread_application" "datadog_app" {
  display_name = "datadog-azure-integration-tf"

  # Required when API access is managed by the separate azuread_application_api_access resource below:
  # without it, later plans fight over required_resource_access and can revoke the Graph permissions.
  # https://registry.terraform.io/providers/hashicorp/azuread/latest/docs/resources/application_api_access
  lifecycle {
    ignore_changes = [required_resource_access]
  }
}

resource "time_rotating" "secret_rotation_duration" {
  rotation_days = 365
}

resource "azuread_application_password" "datadog_app_password" {
  application_id = azuread_application.datadog_app.id
  display_name   = "rbac"
  start_date     = time_rotating.secret_rotation_duration.id
  end_date       = timeadd(time_rotating.secret_rotation_duration.id, "8760h") # 1 year
}

resource "azuread_service_principal" "datadog_sp" {
  client_id = azuread_application.datadog_app.client_id
}

# Assign the Monitoring Reader role to each subscription
resource "azurerm_role_assignment" "monitoring_reader_subscription" {
  for_each             = toset(local.azure_subscription_ids)
  scope                = "/subscriptions/${each.value}"
  role_definition_name = "Monitoring Reader"
  principal_id         = azuread_service_principal.datadog_sp.object_id
}

# Assign the Monitoring Reader role to each management group
resource "azurerm_role_assignment" "monitoring_reader_management_group" {
  for_each             = toset(local.management_group_names)
  scope                = "/providers/Microsoft.Management/managementGroups/${each.value}"
  role_definition_name = "Monitoring Reader"
  principal_id         = azuread_service_principal.datadog_sp.object_id
}

# Graph API Permissions
data "azuread_application_published_app_ids" "well_known" {}

data "azuread_service_principal" "msgraph" {
  client_id = data.azuread_application_published_app_ids.well_known.result.MicrosoftGraph
}

resource "azuread_application_api_access" "msgraph_api_access" {
  application_id = azuread_application.datadog_app.id
  api_client_id  = data.azuread_application_published_app_ids.well_known.result.MicrosoftGraph
  role_ids       = [for permission in local.graph_api_permissions : data.azuread_service_principal.msgraph.app_role_ids[permission]]
}

resource "azuread_app_role_assignment" "grant_entra_consent" {
  for_each            = toset(local.graph_api_permissions)
  app_role_id         = data.azuread_service_principal.msgraph.app_role_ids[each.value]
  principal_object_id = azuread_service_principal.datadog_sp.object_id
  resource_object_id  = data.azuread_service_principal.msgraph.object_id
}

# Datadog Azure Integration
resource "datadog_integration_azure" "datadog_integration" {
  depends_on = [
    azurerm_role_assignment.monitoring_reader_subscription,
    azurerm_role_assignment.monitoring_reader_management_group,
    azuread_application_api_access.msgraph_api_access,
    azuread_app_role_assignment.grant_entra_consent,
  ]

  tenant_name   = local.tenant_id
  client_id     = azuread_application.datadog_app.client_id
  client_secret = azuread_application_password.datadog_app_password.value

  automute                    = true
  metrics_enabled             = true
  custom_metrics_enabled      = true
  usage_metrics_enabled       = true
  metrics_enabled_default     = true
  resource_collection_enabled = true
  cspm_enabled                = false
  resource_provider_configs   = []
  host_filters                = ""
  app_service_plan_filters    = ""
  container_app_filters       = ""
}
```

## Variants

- **Subscriptions only (no management groups).** Set `management_group_names = []` and delete the
  `azurerm_role_assignment.monitoring_reader_management_group` resource and its `depends_on` entry.
- **Management groups only.** Keep `azure_subscription_ids` populated with at least one subscription
  from inside one of those groups - the `azurerm` provider requires a subscription ID even when every
  role assignment is at group scope. Datadog discovers the rest of the group's subscriptions itself.
- **Cloud Security posture.** Set `cspm_enabled = true` to add security posture scanning. It needs the
  `Security Reader` role in addition to `Monitoring Reader`; add a second `azurerm_role_assignment`
  per scope if the user wants it.
- **Host filters.** `host_filters` takes a comma-separated tag list (e.g. `"env:prod,team:core"`) to
  restrict which VMs are monitored. Leave `""` to monitor everything.
- **Secretless auth (Preview) - the direction to prefer, but not shippable from this template yet.**
  This is Azure's equivalent of the keyless models AWS and GCP use (role assumption and service-account
  impersonation): with it, no secret is minted at all, so nothing secret reaches state.
  As written today it cannot be used here.
  `datadog_integration_azure` accepts `secretless_auth_enabled = true`, where Datadog authenticates with
  federated workload identity instead of a client secret, and nothing secret reaches state. The catch: the
  federated credential must exist **on the app registration being registered**, and this template creates a
  brand-new `azuread_application` that has none - so flipping the flag alone yields an integration that
  cannot authenticate. Making it work means either provisioning
  `azuread_application_federated_identity_credential` with Datadog's issuer and subject values (this skill
  does not carry them), or replacing the new application and service-principal resources with references to
  an existing app that already holds the credential. Both are beyond this template: tell the user secretless
  exists and is the better end state, point at
  [the provider docs](https://registry.terraform.io/providers/DataDog/datadog/latest/docs/resources/integration_azure),
  and do not half-apply it. The specific gap is the federated credential's **issuer, subject, and audience**:
  those are not published in the provider docs, the Azure integration docs, or the Azure portal setup guide
  as of this writing, so they have to come from Datadog. If the user already has secretless enabled on an
  existing app registration, prefer that app over creating a new one - that is the one case where this path
  works today.
