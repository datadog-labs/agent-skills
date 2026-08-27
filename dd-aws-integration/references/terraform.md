# AWS integration Terraform template

The standalone configuration to emit when the user has no existing Terraform project. When they do,
take only the resources below the `provider` blocks and add them to what they already have.

Placeholders to replace before writing the file:

| Placeholder | Source |
|---|---|
| `<DD_SITE>` | Phase 0 (`DD_SITE`) |
| `<DATADOG_TRUSTED_ACCOUNT_ID>` | The trusted-account table in Phase 1, keyed by `DD_SITE` |
| `<AWS_ACCOUNT_ID>` | Phase 1 (the user's 12-digit account ID) |

```hcl
# No root variables hold the Datadog keys. The provider reads them from DD_API_KEY and DD_APP_KEY in the
# environment, so there is nothing for Terraform to record: no variable value in state, none in a saved
# plan, and no `-var=` on any command line. This also means an existing project needs no variable changes.

locals {
  datadog_site = "<DD_SITE>"
}

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
    datadog = {
      source = "DataDog/datadog"
    }
  }
}

provider "aws" {}

provider "datadog" {
  # api_key and app_key are intentionally omitted: the provider picks them up from DD_API_KEY and
  # DD_APP_KEY. api_url is not a secret, so it stays explicit.
  api_url = "https://api.${local.datadog_site}"
}

# Block the apply if the credentials Terraform is using are not for the account being registered - otherwise
# the role is created in one account while a different one is registered with Datadog.
# This is a lifecycle precondition, NOT a `check` block: check failures are warnings and do not stop an apply.
data "aws_caller_identity" "current" {}

# Trust policy: allow Datadog's AWS account to assume this role
data "aws_iam_policy_document" "datadog_aws_integration_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::<DATADOG_TRUSTED_ACCOUNT_ID>:root"]
    }
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values = [
        datadog_integration_aws_account.datadog_integration.auth_config.aws_auth_config_role.external_id
      ]
    }
  }
}

# Fetch the required IAM permissions from Datadog
data "datadog_integration_aws_iam_permissions" "datadog_permissions" {}

# Split permissions into chunks to stay under the 6144 character IAM policy limit
locals {
  all_permissions = data.datadog_integration_aws_iam_permissions.datadog_permissions.iam_permissions

  max_policy_size   = 6144
  target_chunk_size = 5900

  permission_sizes = [
    for perm in local.all_permissions :
    length(perm) + 3
  ]
  cumulative_sizes = [
    for i in range(length(local.permission_sizes)) :
    sum(slice(local.permission_sizes, 0, i + 1))
  ]

  chunk_assignments = [
    for cumulative_size in local.cumulative_sizes :
    floor(cumulative_size / local.target_chunk_size)
  ]
  chunk_numbers = distinct(local.chunk_assignments)
  permission_chunks = [
    for chunk_num in local.chunk_numbers : [
      for i, perm in local.all_permissions :
      perm if local.chunk_assignments[i] == chunk_num
    ]
  ]
}

data "aws_iam_policy_document" "datadog_aws_integration" {
  count = length(local.permission_chunks)

  statement {
    actions   = local.permission_chunks[count.index]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "datadog_aws_integration" {
  count = length(local.permission_chunks)

  name   = "DatadogAWSIntegrationPolicy-${count.index + 1}"
  policy = data.aws_iam_policy_document.datadog_aws_integration[count.index].json
}

resource "aws_iam_role" "datadog_aws_integration" {
  name               = "DatadogIntegrationRole"
  description        = "Role for Datadog AWS Integration"
  assume_role_policy = data.aws_iam_policy_document.datadog_aws_integration_assume_role.json

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == "<AWS_ACCOUNT_ID>"
      error_message = "AWS credentials are for account ${data.aws_caller_identity.current.account_id}, but this configuration registers <AWS_ACCOUNT_ID> with Datadog. Fix the credentials or the account id before applying."
    }
  }
}

resource "aws_iam_role_policy_attachment" "datadog_aws_integration" {
  count = length(local.permission_chunks)

  role       = aws_iam_role.datadog_aws_integration.name
  policy_arn = aws_iam_policy.datadog_aws_integration[count.index].arn
}

resource "aws_iam_role_policy_attachment" "datadog_aws_integration_security_audit" {
  role       = aws_iam_role.datadog_aws_integration.name
  policy_arn = "arn:aws:iam::aws:policy/SecurityAudit"
}

# Register the integration with Datadog
resource "datadog_integration_aws_account" "datadog_integration" {
  account_tags   = []
  aws_account_id = "<AWS_ACCOUNT_ID>"
  aws_partition  = "aws"
  aws_regions {
    include_all = true
  }
  auth_config {
    aws_auth_config_role {
      role_name = "DatadogIntegrationRole"
    }
  }
  resources_config {
    cloud_security_posture_management_collection = true
    extended_collection                          = true
  }
  traces_config {
    xray_services {
    }
  }
  logs_config {
    lambda_forwarder {
    }
  }
  metrics_config {
    namespace_filters {
    }
  }
}
```

## Variants

- **Specific regions instead of all.** Replace the `aws_regions` block's `include_all = true` with
  `include_only = ["us-east-1", "eu-west-1"]` using the regions the user named in Phase 1.
- **GovCloud or China partitions.** Do **not** reach for this template by flipping `aws_partition`. The
  trust policy above delegates to a commercial-partition Datadog account, and a partition swap alone leaves
  it trusting a principal that cannot assume the role. AWS China has no role-delegation path to Datadog at
  all. For GovCloud, the trusted account differs from the commercial one **and** differs depending on
  whether the monitored AWS account is itself in the GovCloud partition - this file deliberately carries no
  literal for it. Read the value from
  [Datadog's AWS manual setup guide](https://docs.datadoghq.com/integrations/guide/aws-manual-setup/) with
  the **DATADOG SITE** selector set to the user's site, exactly as the skill's Phase 1 says, and never
  reuse the commercial id.
- **Log forwarding.** `logs_config.lambda_forwarder` is intentionally empty. It is filled in by the
  separate forwarder setup (https://docs.datadoghq.com/logs/guide/forwarder/), which adds the
  forwarder Lambda's ARN to `lambdas` and the log sources to `sources`.
