---
name: k9-ownership-byod-setup
description: >
  Generate a BYOD ownership preferences reference table for a customer.
  Walks through preference types, generates CSV, and provides upload instructions (UI, API, cloud storage, or Terraform).
  Use when asked about BYOD setup, preferences reference table, k9_ownership_preferences, or ownership customization.
argument-hint: "[csv|api|help]"
model: sonnet
allowed-tools: Read, Bash
---

# BYOD Preferences Reference Table Setup

Help customers create and upload a `k9_ownership_preferences` reference table to customize how the Ownership Agent determines owners for their cloud resources.

## What This Skill Does

1. Asks which preference types the customer needs
2. Generates the CSV content
3. Provides upload instructions (CSV via UI, API, cloud storage sync, or Terraform)

## Overview

The Ownership Agent selects cloud resources with security findings and infers an owner for each one. By default, it uses cloud resource tags, service catalog data, and other data sources.

**Ownership preferences** let customers customize this process by providing their own rules stored in a Datadog reference table. The agent reads them automatically to enhance its results.

With preferences you can:
- **Map tags to owners**: Resources with specific tag values belong to a particular team or person
- **Exclude accounts**: Prevent bot accounts, service accounts, or shared infrastructure from appearing as owners
- **Provide custom guidance**: Give the AI engine organization-specific context to make better decisions

## Reference Table Details

- **Table name**: `k9_ownership_preferences` (exact name, must match)
- **Effect delay**: Changes take effect within 24 hours of upload

## Schema (12 columns, all STRING)

| Column | Used By | Required | Description |
|---|---|---|---|
| `id` | All | Yes | Unique row identifier (sequential integer) |
| `preference_type` | All | Yes | Row discriminator: `tag_mapping`, `exclusion`, or `prompt_text` |
| `tag_key` | tag_mapping | Yes | Tag key to match |
| `tag_value` | tag_mapping | No | Tag value to match. Empty = matches any value for that key (wildcard) |
| `owner` | tag_mapping | Yes | Owner handle to assign |
| `confidence` | tag_mapping | Yes | `high`, `medium`, or `low` |
| `owner_type` | tag_mapping | Yes | Owner type: `team`, `user`, or `service` |
| `handle` | exclusion | Yes | Owner handle to exclude |
| `exclusion_type` | exclusion | No | Owner type filter. Empty = all types |
| `exclusion_resource_type` | exclusion | No | Resource type filter. Empty = all resource types |
| `prompt_text` | prompt_text | Yes | Custom guidance text for the ownership engine |
| `priority` | prompt_text | No | Ordering: `high`, `medium`, or `low` |

## Preference Types

### Tag Mappings

A tag mapping says: _"When a resource has tag `X:Y`, it belongs to this owner."_

The agent checks cloud resource tags against your mappings. When a match is found, the specified owner is added as a candidate. Multiple mappings can match the same resource, producing multiple candidates ranked alongside other data sources.

Tag mappings complement existing data sources — they do not override a direct ownership tag (like `dd-team`) already on the resource.

**Columns**: `id` (required), `preference_type=tag_mapping`, `tag_key` (required), `tag_value` (optional, empty=wildcard), `owner` (required), `confidence` (required: `high`/`medium`/`low`), `owner_type` (required: `team`/`user`/`service`).

**Owner type guidance:**
| Value | When to use |
|---|---|
| `team` | The owner is a team handle (e.g., `team-platform`, `sre-team`) |
| `user` | The owner is an individual (e.g., `alice@example.com`) |
| `service` | The owner is a service or automation account (e.g., `payment-svc`) |

**Confidence guidance:**
| Level | When to use |
|---|---|
| `high` | The tag reliably identifies the owner. Example: a `cost-center` tag that maps 1:1 to a team |
| `medium` | The tag is a good indicator but may not always be correct. Example: a `project` tag shared across teams |
| `low` | The tag provides a hint but needs corroboration. Example: an `env` tag that loosely correlates with a team |

**Matching behavior:**
- Tag key and value matching is **case-insensitive**. `Cost-Center` matches `cost-center`.
- An empty `tag_value` matches **any value** for that tag key (wildcard).
- If multiple mappings match, all produce candidates. The agent ranks them by confidence.

**Examples:**
```csv
id,preference_type,tag_key,tag_value,owner,confidence,owner_type,handle,exclusion_type,exclusion_resource_type,prompt_text,priority
1,tag_mapping,cost-center,CC-100,team-platform,high,team,,,,,
2,tag_mapping,cost-center,CC-200,team-data-eng,high,team,,,,,
3,tag_mapping,project,atlas,team-atlas,medium,team,,,,,
4,tag_mapping,managed-by,,team-infra,low,team,,,,,
```

### Exclusions

An exclusion says: _"Never assign this handle as a resource owner."_

Bot accounts, CI runners, and shared service accounts often appear in cloud resource metadata. Exclusions remove these from ownership results.

**Columns**: `id` (required), `preference_type=exclusion`, `handle` (required), `exclusion_type` (optional), `exclusion_resource_type` (optional).

**Matching behavior:**
- The `handle` is matched **case-insensitively**.
- Optional filters use **AND logic**. All non-empty fields must match for the exclusion to apply.
- Leave `exclusion_type` and `exclusion_resource_type` empty to exclude from all results (most common).

**Examples:**
```csv
id,preference_type,tag_key,tag_value,owner,confidence,owner_type,handle,exclusion_type,exclusion_resource_type,prompt_text,priority
1,exclusion,,,,,,deploy-bot,,,,
2,exclusion,,,,,,ci-runner,service,,,
3,exclusion,,,,,,k8s-node-controller,service,aws_ec2_instance,,
```

### Custom Prompt Text

Custom prompt text provides free-form guidance to the AI inference engine. Use it to share organizational context: naming conventions, team structures, which data sources to prioritize.

Up to **3** entries, one per priority level (`high`, `medium`, `low`). Entries with the same priority are concatenated.

**Columns**: `id` (required), `preference_type=prompt_text`, `prompt_text` (required, up to 4096 bytes), `priority` (optional, default: `low`).

**Tips for effective guidance:**
- Be specific and actionable: "The cost-center tag is our most reliable ownership signal" > "Use tags"
- Use plain, declarative sentences — describe facts, not instructions to the AI
- Avoid special formatting: Markdown, HTML, XML tags are stripped during processing

**Examples:**
```csv
id,preference_type,tag_key,tag_value,owner,confidence,owner_type,handle,exclusion_type,exclusion_resource_type,prompt_text,priority
1,prompt_text,,,,,,,,,Our organization assigns ownership by cost center. The cost-center tag is the primary ownership signal. Team identifiers always use the team- prefix.,high
2,prompt_text,,,,,,,,,Shared infrastructure accounts (deploy-bot ci-runner github-actions) are automation and should never be resource owners.,medium
3,prompt_text,,,,,,,,,For container images the repository owner in GitHub is a reliable secondary signal when cost-center tags are missing.,low
```

## Validation Rules

**All-or-nothing**: If **any** row fails validation, the **entire** preference set is rejected for that sync cycle. Preferences are left empty until a valid set is uploaded.

### Allowed Characters

| Field type | Allowed characters | Applies to |
|---|---|---|
| Structured fields | Letters, digits, `- _ . : / @` | `tag_key`, `owner`, `handle`, `exclusion_type`, `exclusion_resource_type`, `owner_type`, `confidence`, `priority` |
| Tag values | Same as structured fields, plus spaces | `tag_value` |
| Prompt text | Same as above, plus `# , ; ! ? ( ) ' "` backticks, spaces, tabs, newlines | `prompt_text` |

**Not allowed in any field**: Angle brackets (`<` `>`), curly braces (`{` `}`), pipe characters (`|`).

### Size Limits

| Limit | Value |
|---|---|
| Max tag mappings | 50 rows |
| Max exclusions | 20 rows |
| Max prompt text entries | 3 (one per priority: high, medium, low) |
| Max field length | 1,024 bytes |
| Max prompt text per entry | 4,096 bytes |

### Duplicate Detection

The agent rejects the entire set if it contains conflicts:
- **Tag mappings**: Same `tag_key`+`tag_value` with different `owner` = conflict. Same key+value+owner with different `confidence` = conflict. Exact duplicates are allowed.
- **Exclusions**: Same `handle`+`exclusion_type`+`exclusion_resource_type` = duplicate. Case-insensitive.

## Workflow

### Step 1: Determine Needs

Ask the customer:
- **Tag mappings**: "Do you have tags on your cloud resources that indicate ownership? (e.g., `cost-center`, `team`, `project`)"
- **Exclusions**: "Are there bot accounts, service accounts, or shared accounts that should never appear as owners?"
- **Prompt text**: "Any organization-specific context that would help determine ownership? (e.g., naming conventions, team structure)"

### Step 2: Generate CSV

Build a CSV with all 12 column headers:

```csv
id,preference_type,tag_key,tag_value,owner,confidence,owner_type,handle,exclusion_type,exclusion_resource_type,prompt_text,priority
```

Each row gets a unique sequential `id` and fills columns relevant to its `preference_type`, leaving the rest empty.

### Step 3: Upload Instructions

**Option A — CSV Upload (UI):**
1. Go to **Integrations > Reference Tables** in Datadog
2. Click **New Reference Table**
3. Upload the CSV
4. Set table name to `k9_ownership_preferences`
5. Choose primary key: `preference_type, tag_key, tag_value, handle`
6. Save

Manual uploads support files up to 4 MB.

**Option B — Cloud Storage Sync (S3, Azure Blob, GCS):**
Best for automated, recurring updates. Store your CSV in a cloud storage bucket and Datadog periodically imports it.
1. Upload CSV to S3 / Azure Blob / GCS
2. In Datadog, go to **Integrations > Reference Tables**
3. Click **New Reference Table**, select **Cloud Storage** as source
4. Provide storage path and credentials
5. Set table name to `k9_ownership_preferences`
6. Datadog re-imports the file periodically

Cloud storage uploads support files up to 200 MB.

**Option C — Terraform:**
Use the `datadog_reference_table` resource in the Datadog Terraform provider to manage the table as infrastructure-as-code.

**Option D — API:**
You can manage reference tables programmatically through the Reference Tables API. See the [API documentation](https://docs.datadoghq.com/api/latest/reference-tables/) for available endpoints. Replace the API domain with your Datadog site URL if applicable.

### Step 4: Verify

Changes take effect within 24 hours. To verify:
1. Identify a resource that matches one of your tag mappings
2. After 24 hours, check the ownership suggestion for that resource in the Datadog UI
3. The suggested owner should reflect your configured mapping

## When Preferences Take Effect

1. Customer uploads or updates their reference table
2. The Ownership Agent reads the table periodically (~once per day per org)
3. Preferences are validated. If valid, they replace the previous set
4. On the next inference run for each resource:
   - **Tag mappings** add ownership candidates
   - **Exclusions** remove unwanted handles
   - **Custom prompt text** guides the AI engine
5. Updated results appear in the Cloud Security posture management UI

## Key Behaviors

- **Case-insensitive matching**: Tag keys, tag values, handles, exclusion types, and resource types are all matched case-insensitively
- **AND-logic exclusions**: All non-empty exclusion fields must match. Empty fields act as wildcards
- **Tag mappings complement, not override**: Direct ownership indicators (like `team:` or `service:` tags) take precedence. Tag mappings augment, not replace
- **Graceful degradation**: If the table doesn't exist or is empty, ownership detection works normally without preferences
- **Empty table clears preferences**: Deleting all rows or deleting the table causes cached preferences to expire and be left empty
- **All-or-nothing validation**: Any validation failure rejects the entire preference set for that cycle

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| Preferences not taking effect after 24h | Table name is wrong | Must be exactly `k9_ownership_preferences` |
| Preferences not taking effect after 24h | Missing column headers | All 12 columns must exist as CSV headers |
| Preferences not taking effect after 24h | Feature not enabled for org | Contact support to enable ownership preferences |
| All preferences rejected | Invalid characters | See Allowed Characters. No angle brackets, curly braces, or pipes |
| All preferences rejected | Missing required field | Check required fields for each preference type |
| All preferences rejected | Duplicate or conflicting rows | See Duplicate Detection above |
| All preferences rejected | Invalid enum value | `confidence`: `high`/`medium`/`low`. `owner_type`: `team`/`user`/`service` |
| All preferences rejected | Size limit exceeded | 50 tag mappings, 20 exclusions, 3 prompt texts. 1024 bytes/field, 4096/prompt |
| Tag mapping not matching | Spelling mismatch | Matching is case-insensitive but verify exact tag key/value on resource |
| Exclusion not applying | Scoping too narrow | All non-empty fields must match (AND). Leave filters empty for broad exclusions |
| Preferences cleared unexpectedly | Table emptied or deleted | Both cause cached preferences to expire. Upload a valid CSV to restore |

## Complete Example

```csv
id,preference_type,tag_key,tag_value,owner,confidence,owner_type,handle,exclusion_type,exclusion_resource_type,prompt_text,priority
1,tag_mapping,cost-center,CC-100,team-platform,high,team,,,,,
2,tag_mapping,cost-center,CC-200,team-data-eng,high,team,,,,,
3,tag_mapping,cost-center,CC-300,team-security,high,team,,,,,
4,tag_mapping,project,atlas,team-atlas,medium,team,,,,,
5,tag_mapping,project,hermes,alice@example.com,medium,user,,,,,
6,tag_mapping,env,production,sre-team,low,team,,,,,
7,tag_mapping,managed-by,,team-infra,low,team,,,,,
8,exclusion,,,,,,deploy-bot,,,,
9,exclusion,,,,,,ci-runner,service,,,
10,exclusion,,,,,,github-actions,service,,,
11,exclusion,,,,,,legacy-ops,team,aws_ec2_instance,,
12,prompt_text,,,,,,,,,Our organization assigns ownership by cost center. The cost-center tag is the primary ownership signal for all cloud resources. Team identifiers always use the team- prefix followed by the team name (e.g. team-platform team-data-eng).,high
13,prompt_text,,,,,,,,,Shared infrastructure accounts (deploy-bot ci-runner github-actions) are automation accounts and should never be assigned as resource owners. Look for the human or team that configured the automation instead.,medium
14,prompt_text,,,,,,,,,For container images the repository owner in GitHub is a reliable secondary signal when cost-center tags are missing.,low
```
