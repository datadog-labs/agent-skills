# k9_ownership_preferences Schema Reference

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

## CSV Header

```csv
id,preference_type,tag_key,tag_value,owner,confidence,owner_type,handle,exclusion_type,exclusion_resource_type,prompt_text,priority
```

Each row gets a unique sequential `id` and fills columns relevant to its `preference_type`, leaving the rest empty.

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

**Example rows:**
```csv
1,tag_mapping,cost-center,CC-100,team-platform,high,team,,,,,
2,tag_mapping,managed-by,,team-infra,low,team,,,,,
```

### Exclusions

An exclusion says: _"Never assign this handle as a resource owner."_

Bot accounts, CI runners, and shared service accounts often appear in cloud resource metadata. Exclusions remove these from ownership results.

**Columns**: `id` (required), `preference_type=exclusion`, `handle` (required), `exclusion_type` (optional), `exclusion_resource_type` (optional).

**Matching behavior:**
- The `handle` is matched **case-insensitively**.
- Optional filters use **AND logic**. All non-empty fields must match for the exclusion to apply.
- Leave `exclusion_type` and `exclusion_resource_type` empty to exclude from all results (most common).

**Example rows:**
```csv
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

**Example rows:**
```csv
1,prompt_text,,,,,,,,,Our organization assigns ownership by cost center.,high
2,prompt_text,,,,,,,,,Shared infrastructure accounts should never be resource owners.,medium
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
