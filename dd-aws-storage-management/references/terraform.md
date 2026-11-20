# Storage Management for AWS Terraform pieces

The HCL that Phase 3 of `SKILL.md` assembles. Choose the pieces with the "Assemble per scenario" table and
the "Multiple Regions" rules in `SKILL.md`, and substitute the values collected in Phases 1 and 2. Section
names in quotes refer to sections of `SKILL.md`, except "SSE-KMS destination buckets", which is below.

| Placeholder | Source |
|---|---|
| `<DD_SITE>` | Phase 0 (`DD_SITE`) |
| `<AWS_ACCOUNT_ID>` | Phase 1 (`aws_account_id`) |
| `<DATADOG_INTEGRATION_ROLE_NAME>` | Phase 1 (`auth_config.role_name`) |
| `<DD_ORG_ID>` | Phase 1, "Fetch your Datadog org id" |
| `<SOURCE_BUCKET_NAME>`, `<DESTINATION_BUCKET_REGION>` | Phase 2 §1-§2 |
| `<DESTINATION_BUCKET_NAME>`, `<KMS_KEY_ARN>` | Phase 2 §3 |
| `<DATADOG_FORWARDER_ARN>`, `<ACCESS_LOG_BUCKET_NAME>` | Phase 2 §5 |

## Providers

**Providers** (`datadog` is required because the module registers the sync as a Terraform resource):

```hcl
terraform {
  required_providers {
    aws     = { source = "hashicorp/aws", version = ">= 5.0" }
    datadog = { source = "DataDog/datadog", version = ">= 3.85" }
  }
}

locals {
  # The DD_SITE env var value (e.g. datadoghq.com, datadoghq.eu, us3.datadoghq.com, ap1.datadoghq.com, ddog-gov.com).
  datadog_site = "<DD_SITE>"
}

# Single Region: one aws provider. Multi-Region: one aliased aws provider per Region (see "Multiple Regions").
provider "aws" {
  region              = "<DESTINATION_BUCKET_REGION>"
  allowed_account_ids = ["<AWS_ACCOUNT_ID>"]
}

# api_key / app_key come from DD_API_KEY / DD_APP_KEY. api_url MUST be derived from DD_SITE: the Datadog provider
# reads the site from DD_HOST / api_url (NOT DD_SITE) and otherwise defaults to US1 (https://api.datadoghq.com),
# which targets the wrong site and fails credential validation for EU / US3 / US5 / AP / Gov tenants.
provider "datadog" {
  api_url = "https://api.${local.datadog_site}"
}
```

## Destination bucket

**Destination bucket - only when creating a new one.** Set SSE-S3 explicitly so the bucket never silently
inherits a KMS account-default (which would break inventory delivery - see "SSE-KMS destination buckets"):

```hcl
resource "aws_s3_bucket" "inventory_destination" {
  bucket = "<DESTINATION_BUCKET_NAME>"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "inventory_destination" {
  bucket = aws_s3_bucket.inventory_destination.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
```

## The module

**The module** (one instance per Region):

```hcl
module "storage_management" {
  source  = "DataDog/storage-management-datadog/aws"
  version = "~> 1.0"

  name                                 = "dd-sm-<DD_ORG_ID>"   # org-scoped (Phase 1) so the S3 Inventory config id + module-managed resource names don't collide when one AWS account is connected to multiple Datadog orgs; also suffix the Region when multi-Region
  datadog_aws_integration_role_name    = "<DATADOG_INTEGRATION_ROLE_NAME>"
  source_bucket_names                  = ["<SOURCE_BUCKET_NAME>"]  # the source buckets in THIS Region
  destination_bucket_name              = "<DESTINATION_BUCKET_NAME>"
  destination_prefix                   = "datadog-inventories/"
  destination_bucket_policy_management = "create"       # "create" for a new/empty-policy bucket; "none" for an existing bucket that already has a policy (see "Existing destination bucket policy")

  # New-bucket case only: expire inventory reports after 2 days. Omit it for an existing bucket, because the module's lifecycle resource replaces the bucket's whole lifecycle configuration.
  manage_destination_lifecycle         = true

  # Access logs - include these lines only if the user opted in AND provided a Forwarder ARN in Phase 2 §5.
  # If they declined or have no Forwarder yet, leave these commented out entirely.
  # When enabled, also emit the dedicated access-log bucket + grant (aws_s3_bucket{,_policy}) shown below the module.
  # enable_access_logging  = true
  # access_log_prefix      = "s3-access-logs/"
  # datadog_forwarder_arn  = "<DATADOG_FORWARDER_ARN>"
  # access_log_bucket_name = "<ACCESS_LOG_BUCKET_NAME>"  # required when enable_access_logging = true (the module has no default). Must be a same-Region SSE-S3 (AES256) bucket with NO existing notifications - the module manages this bucket's notification config authoritatively (overwrites existing), and S3 won't deliver logs to an SSE-KMS bucket. A fresh dedicated bucket is safest (Phase 2 §5).

  # New-bucket case only - ensure the bucket + its encryption exist first.
  depends_on = [aws_s3_bucket.inventory_destination, aws_s3_bucket_server_side_encryption_configuration.inventory_destination]
}
```

`<DATADOG_INTEGRATION_ROLE_NAME>` is the `role_name` from the account selected in Phase 1 (typically
`DatadogIntegrationRole`). The module configures the inventory (`DatadogStorageManagement` name, CSV, daily,
`included_object_versions = All`), the destination bucket policy, the Datadog read IAM policy, **and** the sync -
no separate API call. For an **existing** destination bucket, drop the `aws_s3_bucket` /
`aws_s3_bucket_server_side_encryption_configuration` / `depends_on` / `manage_destination_lifecycle`, set `destination_bucket_name` to the literal
name, and choose `none` vs `create` per Phase 2 §3.

## Existing destination bucket policy

Only for an **existing** destination bucket that already has a policy, or whose policy a resource in the project
already manages. Do not use the module's `"merge"` mode:
it reads the live policy on every plan and appends its statement again, so after the first apply each plan adds
a duplicate `S3InventoryWrite-<name>` statement, and grants for removed source buckets are never dropped. Set the
module's `destination_bucket_policy_management = "none"` and manage the merged policy here instead.

First check whether Terraform already manages this bucket's policy (Phase 3 of `SKILL.md`, "Check for Existing
Terraform", runs the search). The AWS provider allows only one policy owner per bucket: with two, each apply
overwrites the other's policy, so the original owner could drop the inventory grant and the resource below could
overwrite later changes with its saved snapshot. If an `aws_s3_bucket_policy` for `<DESTINATION_BUCKET_NAME>`
already exists in the project (or an `aws_s3_bucket` for it still sets the inline `policy` argument), do not emit
the file or resource below. Append the `S3InventoryWrite-<MODULE_NAME>` statement from the block below to that
resource's policy instead (as a `statement` block if it is built from an `aws_iam_policy_document`), keeping its
existing statements, and add that resource to the module's `depends_on`. This also applies when the live bucket
has no policy yet: the module stays on `"none"` so it does not become a second owner.

Only when nothing in the project manages the policy, save the policy recorded in Phase 2 §3 as `existing-destination-policy.json` next to the generated `.tf` file,
byte for byte. Terraform reads it with `file()` rather than having it pasted into the HCL, because a pasted policy
is parsed as a template and breaks on IAM policy variables such as `${aws:username}`. The `try(...)` wraps a
single-object `Statement` in a list, and the `for` drops any earlier copy of this module's statement, so the
result is the same on every plan. `<MODULE_NAME>` is the module's `name` (`dd-sm-<DD_ORG_ID>`, plus the Region
suffix when multi-Region), and the prefix in `Resource` must match `destination_prefix`:

```hcl
locals {
  existing_destination_policy = jsondecode(file("${path.module}/existing-destination-policy.json"))
  existing_destination_statements = [
    for statement in try([merge(local.existing_destination_policy.Statement)], local.existing_destination_policy.Statement) :
    statement if try(statement.Sid, "") != "S3InventoryWrite-<MODULE_NAME>"
  ]
}

resource "aws_s3_bucket_policy" "inventory_destination" {
  bucket = "<DESTINATION_BUCKET_NAME>"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.existing_destination_statements, [
      {
        Sid       = "S3InventoryWrite-<MODULE_NAME>"
        Effect    = "Allow"
        Principal = { Service = "s3.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "arn:aws:s3:::<DESTINATION_BUCKET_NAME>/datadog-inventories/*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = "<AWS_ACCOUNT_ID>" }
          ArnLike      = { "aws:SourceArn" = ["arn:aws:s3:::<SOURCE_BUCKET_NAME>"] }
        }
      }
    ])
  })
}
```

List every source bucket in this Region in `aws:SourceArn` (the same list as the module's `source_bucket_names`),
and add `aws_s3_bucket_policy.inventory_destination` to the module's `depends_on`. Multi-Region: one file, `locals`
block and policy resource per Region, with Region-suffixed names (for example
`existing-destination-policy-eu-west-1.json`) and `provider = aws.<alias>` on the resource.

## Access-log bucket

**Access-log bucket - create it + grant log delivery** (only when `enable_access_logging = true` above). By default
the skill provisions a fresh, dedicated SSE-S3 bucket for access logs and attaches the delivery grant the module
omits - the same shape `dd-aws-ccm` uses for the CUR bucket. S3's log-delivery service can't write until the
bucket policy allows it, and a fresh bucket has ACLs disabled, so the bucket policy is the only mechanism - without
it the apply succeeds but no log is ever delivered:

```hcl
resource "aws_s3_bucket" "access_logs" {
  bucket = "<ACCESS_LOG_BUCKET_NAME>"   # dedicated; name it like the destination, e.g. dd-s3-access-logs-<AWS_ACCOUNT_ID>-<REGION>
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"   # SSE-S3 - server access logs are never delivered to an SSE-KMS bucket
    }
  }
}

resource "aws_s3_bucket_policy" "access_log_delivery" {
  bucket = aws_s3_bucket.access_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "S3ServerAccessLogsPolicy"
        Effect    = "Allow"
        Principal = { Service = "logging.s3.amazonaws.com" }
        Action    = ["s3:PutObject"]
        Resource  = "${aws_s3_bucket.access_logs.arn}/s3-access-logs/*"   # prefix must match access_log_prefix
        Condition = {
          # Every source bucket in THIS Region logs here - list them all (matches the module's source_bucket_names).
          # aws:SourceAccount is the source buckets' account (AWS_ACCOUNT_ID from Phase 1).
          ArnLike      = { "aws:SourceArn" = ["arn:aws:s3:::<SOURCE_BUCKET_NAME>"] }
          StringEquals = { "aws:SourceAccount" = "<AWS_ACCOUNT_ID>" }
        }
      }
    ]
  })
}
```

Set the module's `access_log_bucket_name = "<ACCESS_LOG_BUCKET_NAME>"` and add `aws_s3_bucket.access_logs`,
`aws_s3_bucket_server_side_encryption_configuration.access_logs` and `aws_s3_bucket_policy.access_log_delivery` to
the module's `depends_on` so the bucket, its encryption, **and its delivery grant**
exist before the module wires up source-bucket logging (the module takes only the bucket name, so there's no
implicit edge to the grant otherwise). **Bring-your-own:** if the user supplies
an existing dedicated log bucket, drop the `aws_s3_bucket` / `aws_s3_bucket_server_side_encryption_configuration`,
point the policy's `bucket` at its name, replace `${aws_s3_bucket.access_logs.arn}` in the statement's `Resource`
with `arn:aws:s3:::<ACCESS_LOG_BUCKET_NAME>`, keep only the policy resource that ends up holding the statement in
the module's `depends_on` (drop `aws_s3_bucket.access_logs` and
`aws_s3_bucket_server_side_encryption_configuration.access_logs`), and **merge** this `S3ServerAccessLogsPolicy` statement into its current
policy rather than emitting a standalone resource (a second `aws_s3_bucket_policy` would silently clobber the
existing one). Multi-Region: one access-log bucket + grant per Region, each scoped to that Region's source buckets
and using its aliased provider.

## SSE-KMS destination buckets

**SSE-KMS destination buckets** (only if Phase 2 §3 found the destination bucket is SSE-KMS-encrypted). The module
does **not** handle KMS, so add the two grants it omits - otherwise S3 can't write the encrypted report and Datadog
can't read it (silent failure). `<KMS_KEY_ARN>` is the `KMSMasterKeyID` from Phase 2; the AWS-managed `aws/s3` key
is **not** supported by S3 Inventory, so this only applies to a customer-managed key (CMK).

1. **Read side** - Datadog's role needs `kms:Decrypt` (the module's read policy only grants `s3:GetObject`). Add an
   inline policy:

   ```hcl
   resource "aws_iam_role_policy" "datadog_storage_management_kms" {
     role = "<DATADOG_INTEGRATION_ROLE_NAME>"
     name = "DatadogStorageManagementKMSDecrypt-<DD_ORG_ID>-<DESTINATION_BUCKET_REGION>"

     policy = jsonencode({
       Version = "2012-10-17"
       Statement = [
         {
           Sid      = "DDStorageKMSDecrypt"
           Effect   = "Allow"
           Action   = ["kms:Decrypt"]
           Resource = "<KMS_KEY_ARN>"
         }
       ]
     })
   }
   ```

   IAM identifies an inline policy by role and `name`, so with more than one SSE-KMS destination, emit one of
   these per destination with a Region-unique Terraform label and the Region in `name`; two resources with the
   same `name` would keep overwriting each other.

2. **Key-policy grants** - the CMK's key policy must let the S3 service generate a data key (`kms:GenerateDataKey`)
   and, for locked-down keys, let the Datadog role decrypt (`kms:Decrypt`). The inline IAM grant in step 1 only
   takes effect if the key policy **delegates to IAM** (the default `Enable IAM User Permissions` statement); a
   hardened key that removed it makes the IAM grant inert, so the key policy must name the role directly. A key
   policy is a whole-policy resource the module doesn't own. Add both grants to the Terraform resource that owns
   the key policy if the project has one, otherwise merge them out-of-band (see "Merge the KMS key policy" and
   "Check for Existing Terraform" in `SKILL.md`).

## Multiple Regions

The HCL for the "Multiple Regions" case in `SKILL.md`: one Region-pinned provider alias per Region:

```hcl
provider "aws" {
  alias               = "us_east_1"
  region              = "us-east-1"
  allowed_account_ids = ["<AWS_ACCOUNT_ID>"]
}

provider "aws" {
  alias               = "eu_west_1"
  region              = "eu-west-1"
  allowed_account_ids = ["<AWS_ACCOUNT_ID>"]
}
```

Then, per Region, the destination bucket and module instance (this example is the `eu-west-1` one):

```hcl
resource "aws_s3_bucket" "inventory_destination_eu_west_1" {
  provider = aws.eu_west_1
  bucket   = "<EU_DESTINATION_BUCKET>"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "inventory_destination_eu_west_1" {
  provider = aws.eu_west_1
  bucket   = aws_s3_bucket.inventory_destination_eu_west_1.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

module "storage_management_eu_west_1" {
  source    = "DataDog/storage-management-datadog/aws"
  version   = "~> 1.0"
  providers = { aws = aws.eu_west_1 }

  name                                 = "dd-sm-<DD_ORG_ID>-eu-west-1"
  datadog_aws_integration_role_name    = "<DATADOG_INTEGRATION_ROLE_NAME>"
  source_bucket_names                  = ["<EU_SOURCE_BUCKET>"]
  destination_bucket_name              = "<EU_DESTINATION_BUCKET>"
  destination_prefix                   = "datadog-inventories/"
  destination_bucket_policy_management = "create"
  manage_destination_lifecycle         = true  # new-bucket case only (see the single-Region block)

  # Access logs - include only if opted in with a same-Region Forwarder ARN (Phase 2 §5).
  # When enabled, also emit a same-Region dedicated access-log bucket + grant (aws_s3_bucket{,_policy}) for this
  # Region, and add them to depends_on below alongside the destination bucket.
  # enable_access_logging  = true
  # access_log_prefix      = "s3-access-logs/"
  # datadog_forwarder_arn  = "<EU_DATADOG_FORWARDER_ARN>"  # the Forwarder Lambda must be in THIS Region - S3 invokes only same-Region Lambdas
  # access_log_bucket_name = "<EU_ACCESS_LOG_BUCKET>"  # same-Region SSE-S3 (AES256) bucket with NO existing notifications - the module manages this bucket's notification config authoritatively (overwrites existing). A fresh dedicated bucket is safest (Phase 2 §5).

  # New-bucket case only - gate the module on the per-Region destination bucket (mirrors the single-Region block).
  depends_on = [aws_s3_bucket.inventory_destination_eu_west_1, aws_s3_bucket_server_side_encryption_configuration.inventory_destination_eu_west_1]
}
```
