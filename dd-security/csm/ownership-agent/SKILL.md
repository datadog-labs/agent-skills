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

Help customers create and upload a `k9_ownership_preferences` reference table to customize how the Ownership Agent determines resource owners.

## Read first

- `references/schema.md` — full schema, column details, validation rules, and per-type examples
- `assets/example.csv` — complete working CSV with all three preference types

## Overview

The Ownership Agent infers owners for cloud resources with security findings. **Ownership preferences** let customers customize this by providing rules in a Datadog reference table. The agent reads them automatically.

With preferences you can:
- **Map tags to owners**: Resources with specific tag values belong to a particular team or person
- **Exclude accounts**: Prevent bot accounts or shared infrastructure from appearing as owners
- **Provide custom guidance**: Give the AI engine organization-specific context

## Reference Table Details

- **Table name**: `k9_ownership_preferences` (exact name, must match)
- **Effect delay**: Changes take effect within 24 hours of upload
- **Schema**: 12 columns, all STRING — see `references/schema.md` for details

## Workflow

### Step 1: Determine Needs

Ask the customer:
- **Tag mappings**: "Do you have tags on your cloud resources that indicate ownership? (e.g., `cost-center`, `team`, `project`)"
- **Exclusions**: "Are there bot accounts, service accounts, or shared accounts that should never appear as owners?"
- **Prompt text**: "Any organization-specific context that would help determine ownership? (e.g., naming conventions, team structure)"

### Step 2: Generate CSV

Read `references/schema.md` for the full column spec and `assets/example.csv` for a working template. Build a CSV with all 12 column headers. Each row gets a unique sequential `id` and fills columns relevant to its `preference_type`, leaving the rest empty.

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
| All preferences rejected | Invalid characters | See `references/schema.md` Allowed Characters. No angle brackets, curly braces, or pipes |
| All preferences rejected | Missing required field | Check required fields for each preference type in `references/schema.md` |
| All preferences rejected | Duplicate or conflicting rows | See Duplicate Detection in `references/schema.md` |
| All preferences rejected | Size limit exceeded | 50 tag mappings, 20 exclusions, 3 prompt texts. 1024 bytes/field, 4096/prompt |
| Tag mapping not matching | Spelling mismatch | Matching is case-insensitive but verify exact tag key/value on resource |
| Exclusion not applying | Scoping too narrow | All non-empty fields must match (AND). Leave filters empty for broad exclusions |
| Preferences cleared unexpectedly | Table emptied or deleted | Both cause cached preferences to expire. Upload a valid CSV to restore |
