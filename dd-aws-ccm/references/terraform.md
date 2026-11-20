# CCM for AWS Terraform sections

The HCL sections that Phase 3 of `SKILL.md` assembles. Pick which ones to emit with the Scenario Matrix
in `SKILL.md`, and substitute the placeholders collected in Phases 1 and 2:

| Placeholder | Source |
|---|---|
| `<AWS_ACCOUNT_ID>` | Phase 1 (`aws_account_id`, the 12-digit AWS account number) |
| `<AWS_ACCOUNT_CONFIG_ID>` | Phase 1 (`id`, the Datadog account config UUID) |
| `<DATADOG_INTEGRATION_ROLE_NAME>` | Phase 1 (`auth_config.role_name`) |
| `<EXPORT_NAME>`, `<BUCKET_NAME>`, `<EXPORT_PATH_PREFIX>`, `<BUCKET_REGION>` | Phase 2 |

## Section 1a: S3 Bucket (only if creating a new bucket)

```hcl
# S3 bucket for CURs

resource "aws_s3_bucket" "cur_bucket" {
  bucket = "<BUCKET_NAME>"
}
```

## Section 1b: Bucket Policy (new bucket, or existing bucket without the delivery statement)

Both report services check this policy when the report is created and on every delivery. The first
statement lets them confirm the bucket is still owned by the account (Legacy CUR needs
`s3:GetBucketAcl` for that); the second lets them write report files. Both are limited to reports
owned by `<AWS_ACCOUNT_ID>`.

When `<BUCKET_REGION>` is not `us-east-1`, add `provider = aws.bucket_region` to the policy resource
emitted from this section (and to Section 1a), using the bucket-region provider from Phase 3 of
`SKILL.md`. This holds for a new bucket and an existing one.

If creating a new bucket:

```hcl
# Bucket policy to allow CUR and BCM Data Exports services to write reports
resource "aws_s3_bucket_policy" "cur_bucket" {
  bucket = aws_s3_bucket.cur_bucket.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = [
            "billingreports.amazonaws.com",
            "bcm-data-exports.amazonaws.com"
          ]
        }
        Action = [
          "s3:PutObject",
          "s3:GetBucketPolicy",
          "s3:GetBucketAcl"
        ]
        Resource = [
          aws_s3_bucket.cur_bucket.arn,
          "${aws_s3_bucket.cur_bucket.arn}/*"
        ]
        Condition = {
          StringLike = {
            "aws:SourceArn" = [
              "arn:*:cur:*:<AWS_ACCOUNT_ID>:definition/*",
              "arn:*:bcm-data-exports:*:<AWS_ACCOUNT_ID>:export/*"
            ]
            "aws:SourceAccount" = "<AWS_ACCOUNT_ID>"
          }
        }
      }
    ]
  })
}
```

If using an existing bucket, first check whether Terraform already manages its policy. The AWS
provider allows only one policy owner per bucket: with two, each apply overwrites the other's policy, so
a second `aws_s3_bucket_policy` would add the delivery statement once and then lose it. Phase 3 of
`SKILL.md` runs this check. If an `aws_s3_bucket_policy` for `<BUCKET_NAME>` already exists in the
project (or an `aws_s3_bucket` for it still sets the inline `policy` argument), do not emit the
resource below. Append the delivery statement from the block below to that resource's policy instead
(as a `statement` block if it is built from an `aws_iam_policy_document`), keeping its existing
statements, and point the report's `depends_on` (Sections 2a and 2b) at that resource rather than at
`aws_s3_bucket_policy.cur_bucket`.

Only when nothing in the project manages the policy, use the resource below. It must keep every
statement the bucket already has: an `aws_s3_bucket_policy` replaces the whole policy. Save the
policy recorded in Phase 2 as `existing-bucket-policy.json` next to the generated `.tf` file, byte for
byte. If the bucket had no
policy, write `{"Statement": []}` to that file. Terraform reads it with `file()` rather than having it
pasted into the HCL, because a pasted policy is parsed as a template and breaks on IAM policy
variables such as `${aws:username}`. AWS also allows `Statement` to be a single object instead of an
array, so the `try(...)` below wraps a lone statement in a list. Show the user the resulting policy
change in the plan and get their approval, as for any other change.

```hcl
locals {
  existing_bucket_policy     = jsondecode(file("${path.module}/existing-bucket-policy.json"))
  existing_bucket_statements = try([merge(local.existing_bucket_policy.Statement)], local.existing_bucket_policy.Statement)
}

resource "aws_s3_bucket_policy" "cur_bucket" {
  bucket = "<BUCKET_NAME>"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      local.existing_bucket_statements,
      [
        {
          Effect = "Allow"
          Principal = {
            Service = [
              "billingreports.amazonaws.com",
              "bcm-data-exports.amazonaws.com"
            ]
          }
          Action = [
            "s3:PutObject",
            "s3:GetBucketPolicy",
            "s3:GetBucketAcl"
          ]
          Resource = [
            "arn:aws:s3:::<BUCKET_NAME>",
            "arn:aws:s3:::<BUCKET_NAME>/*"
          ]
          Condition = {
            StringLike = {
              "aws:SourceArn" = [
                "arn:*:cur:*:<AWS_ACCOUNT_ID>:definition/*",
                "arn:*:bcm-data-exports:*:<AWS_ACCOUNT_ID>:export/*"
              ]
              "aws:SourceAccount" = "<AWS_ACCOUNT_ID>"
            }
          }
        }
      ]
    )
  })
}
```

## Section 2a: Legacy CUR Definition (only if `<REPORT_TYPE>` is `legacy` and creating a new report)

If creating a new bucket:

```hcl
# CUR definition

resource "aws_cur_report_definition" "datadog_cur" {
  additional_artifacts       = []
  additional_schema_elements = ["RESOURCES", "SPLIT_COST_ALLOCATION_DATA"]
  compression                = "Parquet"
  format                     = "Parquet"
  refresh_closed_reports     = true
  report_name                = "<EXPORT_NAME>"
  report_versioning          = "CREATE_NEW_REPORT"
  s3_bucket                  = aws_s3_bucket.cur_bucket.bucket
  s3_prefix                  = "<EXPORT_PATH_PREFIX>"
  s3_region                  = "<BUCKET_REGION>"
  time_unit                  = "HOURLY"
  depends_on                 = [aws_s3_bucket_policy.cur_bucket]
  tags                       = {}
}
```

If using an existing bucket (add `depends_on = [aws_s3_bucket_policy.cur_bucket]` when Section 1b is
emitted for it, or a `depends_on` on the existing policy resource when the statement was added there):

```hcl
# CUR definition

resource "aws_cur_report_definition" "datadog_cur" {
  additional_artifacts       = []
  additional_schema_elements = ["RESOURCES", "SPLIT_COST_ALLOCATION_DATA"]
  compression                = "Parquet"
  format                     = "Parquet"
  refresh_closed_reports     = true
  report_name                = "<EXPORT_NAME>"
  report_versioning          = "CREATE_NEW_REPORT"
  s3_bucket                  = "<BUCKET_NAME>"
  s3_prefix                  = "<EXPORT_PATH_PREFIX>"
  s3_region                  = "<BUCKET_REGION>"
  time_unit                  = "HOURLY"
  tags                       = {}
}
```

## Section 2b: CUR 2.0 Export (only if `<REPORT_TYPE>` is `cur2` and creating a new report)

A CUR 2.0 export is defined like a SQL query: a `SELECT` of columns from the single fixed table
`COST_AND_USAGE_REPORT`, plus table configurations that toggle groups of columns on or off.

Datadog validates the export after registration and **rejects it** unless all of the following hold.
Do not vary them:

| Setting | Required value |
|---|---|
| `TIME_GRANULARITY` | `HOURLY` |
| `INCLUDE_RESOURCES` | `TRUE` |
| `refresh_cadence.frequency` | `SYNCHRONOUS` |
| `overwrite` | `CREATE_NEW_REPORT` |
| `format` / `compression` | `PARQUET` / `PARQUET`, or `TEXT_OR_CSV` / `GZIP` |
| `query_statement` | must select all 116 columns below from `COST_AND_USAGE_REPORT` |

Separately, the AWS provider requires that **if `table_configurations` is set at all, every
configuration value must be set**, and that `BILLING_VIEW_ARN` be included for the Cost and Usage
Report. Keep all five keys in the block below even though Datadog only validates two of them.

`BILLING_VIEW_ARN` is the easy one to talk yourself out of, because Datadog never reads it. Omit it
and the export is still created in AWS and still passes Datadog's validation - but AWS returns the
field anyway, and Terraform then fails with `Provider produced inconsistent result after apply: new
element "BILLING_VIEW_ARN" has appeared` and **cannot save state**. The user is left with a real
export that Terraform does not track, so the next apply tries to create it again. The symptom is
easy to misread as success, since the Datadog side completes normally.

Set it to the account's primary billing view, as shown below. Never set it to an empty string -
that makes AWS reject `bcm-data-exports:CreateExport` with `AccessDeniedException` even for an
administrator. `<AWS_ACCOUNT_ID>` in the ARN is the 12-digit AWS account number.

Keep the `depends_on` line whenever Section 1b is emitted. If the delivery statement was added to a
policy resource the project already had, point `depends_on` at that resource instead. Drop it only when
using an existing bucket whose policy already allows delivery:

```hcl
# CUR 2.0 export via BCM Data Exports

resource "aws_bcmdataexports_export" "datadog_cur" {
  export {
    name = "<EXPORT_NAME>"

    data_query {
      query_statement = <<-EOT
      SELECT bill_bill_type, bill_billing_entity, bill_billing_period_end_date, bill_billing_period_start_date, bill_invoice_id, bill_invoicing_entity, bill_payer_account_id, bill_payer_account_name, cost_category, discount, discount_bundled_discount, discount_total_discount, identity_line_item_id, identity_time_interval, line_item_availability_zone, line_item_blended_cost, line_item_blended_rate, line_item_currency_code, line_item_legal_entity, line_item_line_item_description, line_item_line_item_type, line_item_net_unblended_cost, line_item_net_unblended_rate, line_item_normalization_factor, line_item_normalized_usage_amount, line_item_operation, line_item_product_code, line_item_resource_id, line_item_tax_type, line_item_unblended_cost, line_item_unblended_rate, line_item_usage_account_id, line_item_usage_account_name, line_item_usage_amount, line_item_usage_end_date, line_item_usage_start_date, line_item_usage_type, line_item_user_identifier, pricing_currency, pricing_lease_contract_length, pricing_offering_class, pricing_public_on_demand_cost, pricing_public_on_demand_rate, pricing_purchase_option, pricing_rate_code, pricing_rate_id, pricing_term, pricing_unit, product, product_comment, product_fee_code, product_fee_description, product_from_location, product_from_location_type, product_from_region_code, product_instance_family, product_instance_type, product_instancesku, product_location, product_location_type, product_operation, product_pricing_unit, product_product_family, product_region_code, product_servicecode, product_sku, product_to_location, product_to_location_type, product_to_region_code, product_usagetype, reservation_amortized_upfront_cost_for_usage, reservation_amortized_upfront_fee_for_billing_period, reservation_availability_zone, reservation_effective_cost, reservation_end_time, reservation_modification_status, reservation_net_amortized_upfront_cost_for_usage, reservation_net_amortized_upfront_fee_for_billing_period, reservation_net_effective_cost, reservation_net_recurring_fee_for_usage, reservation_net_unused_amortized_upfront_fee_for_billing_period, reservation_net_unused_recurring_fee, reservation_net_upfront_value, reservation_normalized_units_per_reservation, reservation_number_of_reservations, reservation_recurring_fee_for_usage, reservation_reservation_a_r_n, reservation_start_time, reservation_subscription_id, reservation_total_reserved_normalized_units, reservation_total_reserved_units, reservation_units_per_reservation, reservation_unused_amortized_upfront_fee_for_billing_period, reservation_unused_normalized_unit_quantity, reservation_unused_quantity, reservation_unused_recurring_fee, reservation_upfront_value, resource_tags, savings_plan_amortized_upfront_commitment_for_billing_period, savings_plan_end_time, savings_plan_instance_type_family, savings_plan_net_amortized_upfront_commitment_for_billing_period, savings_plan_net_recurring_commitment_for_billing_period, savings_plan_net_savings_plan_effective_cost, savings_plan_offering_type, savings_plan_payment_option, savings_plan_purchase_term, savings_plan_recurring_commitment_for_billing_period, savings_plan_region, savings_plan_savings_plan_a_r_n, savings_plan_savings_plan_effective_cost, savings_plan_savings_plan_rate, savings_plan_start_time, savings_plan_total_commitment_to_date, savings_plan_used_commitment, tags FROM COST_AND_USAGE_REPORT
      EOT
      table_configurations = {
        COST_AND_USAGE_REPORT = {
          BILLING_VIEW_ARN                      = "arn:aws:billing::<AWS_ACCOUNT_ID>:billingview/primary"
          TIME_GRANULARITY                      = "HOURLY"
          INCLUDE_RESOURCES                     = "TRUE"
          INCLUDE_SPLIT_COST_ALLOCATION_DATA    = "FALSE"
          INCLUDE_MANUAL_DISCOUNT_COMPATIBILITY = "FALSE"
        }
      }
    }

    destination_configurations {
      s3_destination {
        s3_bucket = "<BUCKET_NAME>"
        s3_prefix = "<EXPORT_PATH_PREFIX>"
        s3_region = "<BUCKET_REGION>"
        s3_output_configurations {
          overwrite   = "CREATE_NEW_REPORT"
          format      = "PARQUET"
          compression = "PARQUET"
          output_type = "CUSTOM"
        }
      }
    }

    refresh_cadence {
      frequency = "SYNCHRONOUS"
    }
  }

  depends_on = [aws_s3_bucket_policy.cur_bucket]
}
```

**Reproduce `query_statement` exactly as written above.** It is 116 comma-separated column names on a
single line, and Datadog's validation fails the export if any one of them is missing. Copy it
verbatim - do not reformat it, wrap it across lines, reorder it, abbreviate it, or replace it with
`SELECT *`. In particular the ` FROM COST_AND_USAGE_REPORT` must stay on the same line as the last
column (`tags`), separated by a single space. Some column names look like typos but are correct:
`product_instancesku`, `reservation_reservation_a_r_n`, `savings_plan_savings_plan_a_r_n`.

If you cannot reproduce the list exactly, do not hand-write a shortened version. Instead tell the
user to create the export in the AWS console under **Data Exports** with the *Standard data export*
default column selection plus **Include resource IDs**, which produces this same set, and then rerun
this skill choosing "use an existing report".

**Optional additions from Phase 2, question 1.** Each one flips a table configuration *and* adds its
columns to the `SELECT` - a table configuration alone exports nothing new, so both edits are needed:

- If `<INCLUDE_SPLIT_COST_ALLOCATION>` is true: set `INCLUDE_SPLIT_COST_ALLOCATION_DATA = "TRUE"`
  and append these 11 columns before ` FROM`:
  `split_line_item_actual_usage`, `split_line_item_net_split_cost`, `split_line_item_net_unused_cost`,
  `split_line_item_parent_resource_id`, `split_line_item_public_on_demand_split_cost`,
  `split_line_item_public_on_demand_unused_cost`, `split_line_item_reserved_usage`,
  `split_line_item_split_cost`, `split_line_item_split_usage`, `split_line_item_split_usage_ratio`,
  `split_line_item_unused_cost`.
  Also remind the user to opt in to AWS Split Cost Allocation in Cost Explorer preferences -
  without that, the columns stay empty.
- If `<INCLUDE_IAM_PRINCIPAL_DATA>` is true: add `INCLUDE_IAM_PRINCIPAL_DATA = "TRUE"` and append
  the single column `line_item_iam_principal`. Note that AWS only populates it from 2026-04-08 onward.

These columns come from the AWS split line item and table configuration references
(https://docs.aws.amazon.com/cur/latest/userguide/split-line-item-columns.html and
https://docs.aws.amazon.com/cur/latest/userguide/table-dictionary-cur2.html). If `terraform plan`
rejects a column name, re-derive it from those pages rather than guessing - and note that the
optional blocks are never required, so dropping back to the default 116-column export is always a
valid outcome.

## Section 3: IAM Policy (always included)

The `bcm-data-exports` actions are only used by CUR 2.0 and the `cur` action only by Legacy CUR, but
this single policy covers both - emit it unchanged for either report type. If a `DatadogCURAccess`
policy already exists in the account (for instance from an earlier legacy setup), do not create a
second one with the same name: Terraform will fail on the collision. Update the existing policy to
add the missing actions, or pick a distinct name.

```hcl
# IAM policy for Datadog to access CUR data

resource "aws_iam_policy" "datadog_cur_access" {
  name        = "DatadogCURAccess"
  description = "Allow Datadog to access Cost and Usage Reports"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DDCloudCostReadBucket"
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = "arn:aws:s3:::<BUCKET_NAME>"
      },
      {
        Sid    = "DDCloudCostGetBill"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = "arn:aws:s3:::<BUCKET_NAME>/<EXPORT_PATH_PREFIX>/<EXPORT_NAME>/*"
      },
      {
        Sid    = "DDCloudCostCheckAccuracy"
        Effect = "Allow"
        Action = [
          "ce:Get*"
        ]
        Resource = "*"
      },
      {
        Sid    = "DDCloudCostListCURs"
        Effect = "Allow"
        Action = [
          "cur:DescribeReportDefinitions",
          "bcm-data-exports:GetExport",
          "bcm-data-exports:ListExports"
        ]
        Resource = "*"
      },
      {
        Sid    = "DDCloudCostListOrganizations"
        Effect = "Allow"
        Action = [
          "organizations:Describe*",
          "organizations:List*"
        ]
        Resource = "*"
      }
    ]
  })
}
```

## Section 4: Policy Attachment (always included)

```hcl
# Attach the CUR access policy to the existing Datadog integration role

resource "aws_iam_role_policy_attachment" "datadog_cur_access" {
  role       = "<DATADOG_INTEGRATION_ROLE_NAME>"
  policy_arn = aws_iam_policy.datadog_cur_access.arn
}
```

Replace `<DATADOG_INTEGRATION_ROLE_NAME>` with the `auth_config.role_name` value read from the
account selected in Phase 1. It is commonly `DatadogIntegrationRole`, but orgs rename it, so use
what the API returned rather than the common default. If you did not get a `role_name` from the
response, go back to Phase 1 - do not fill this in with a guess.

## Section 5a: Datadog Legacy CUR Config (only if `<REPORT_TYPE>` is `legacy`)

If this configuration creates the report (Section 2a):

```hcl
# Register the CUR config with Datadog

resource "datadog_aws_cur_config" "aws_cur" {
  account_id    = "<AWS_ACCOUNT_ID>"
  bucket_name   = "<BUCKET_NAME>"
  bucket_region = "<BUCKET_REGION>"
  report_name   = "<EXPORT_NAME>"
  report_prefix = "<EXPORT_PATH_PREFIX>"

  depends_on = [aws_cur_report_definition.datadog_cur, aws_iam_role_policy_attachment.datadog_cur_access]
}
```

If using an existing report:

```hcl
# Register the CUR config with Datadog

resource "datadog_aws_cur_config" "aws_cur" {
  account_id    = "<AWS_ACCOUNT_ID>"
  bucket_name   = "<BUCKET_NAME>"
  bucket_region = "<BUCKET_REGION>"
  report_name   = "<EXPORT_NAME>"
  report_prefix = "<EXPORT_PATH_PREFIX>"

  depends_on = [aws_iam_role_policy_attachment.datadog_cur_access]
}
```

## Section 5b: Datadog CUR 2.0 Config (only if `<REPORT_TYPE>` is `cur2`)

```hcl
# Register the CUR 2.0 config with Datadog

resource "datadog_integration_aws_account_ccm_config" "aws_cur_2" {
  aws_account_config_id = "<AWS_ACCOUNT_CONFIG_ID>"

  ccm_config {
    data_export_configs {
      report_name   = "<EXPORT_NAME>"
      report_prefix = "<EXPORT_PATH_PREFIX>"
      report_type   = "CUR2.0"
      bucket_name   = "<BUCKET_NAME>"
      bucket_region = "<BUCKET_REGION>"
    }
  }

  depends_on = [aws_iam_role_policy_attachment.datadog_cur_access]
}
```

Two things to get right here:

- `aws_account_config_id` is the Datadog account config **UUID** from Phase 1 (the `id` field), not
  the 12-digit AWS account number. Using the AWS account ID fails.
- `report_type` must be the literal string `"CUR2.0"`.

When you also created the export in Section 2b, add `aws_bcmdataexports_export.datadog_cur` to the
`depends_on` list so the export exists before Datadog validates it.
