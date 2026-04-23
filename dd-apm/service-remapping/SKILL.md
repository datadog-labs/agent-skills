---
name: service-remapping
description: Create and manage APM service remapping rules — rewrite service names at ingestion time to collapse noisy inferred entities, clean up auto-generated names, handle org renames, or normalize naming conventions. Use for any request involving service renaming, service mapping, inferred service cleanup, peer.service normalization, or collapsing fragmented service names.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,apm,service-remapping,service-naming,inferred-services,peer-service
  alwaysApply: "false"
  tools: pup,curl
---

# APM Service Remapping

> **Before doing anything else:** Fully resolve all variables in `## Context to resolve before acting`. Do not begin Step 0 until every variable has a concrete value.

---

## How Service Remapping Works — Domain Knowledge

Read this before building any rule. It gives you the mental model to construct the right filter and catch edge cases.

**What remapping does:** A rule intercepts telemetry at ingestion time and rewrites the service name before indexing. A rule says: "for any entity matching this filter, replace its service name with this new value."

**Two entity types — pick the right one:**

| Entity type | `rule_type` integer | What it targets |
|---|---|---|
| **SERVICE** | `0` | Instrumented services — have spans with an explicit `service` tag set by a tracer |
| **INFERRED_ENTITY** | `1` | Auto-detected from outbound calls — named from `peer.service`, `db.instance`, etc. |

**Filter syntax** — a standard Datadog event-grammar query string:

| Goal | Filter |
|---|---|
| Exact service match | `service:payments` |
| All services with a prefix | `service:deploy-test*` |
| All services with a suffix | `service:*.tropos` |
| All services containing a string | `service:*payments*` |
| All inferred services under a domain | `peer.service:*.shopify.com` |
| Service in one environment only | `service:payments AND env:prod` |

**New name syntax** — the `value` field in `rewrite_tag_rules`:

| Form | Example | Use for |
|---|---|---|
| Static string | `my-service` | Every matched entity gets exactly this name |
| Tag interpolation | `{{service}}` | Substitute the full value of a tag |
| Tag + regex capture | `{{service\|^(.+?)\..*$}}` | Extract part of a tag value (non-greedy capture) |

**Regex constraints for `{{tag\|regex}}`:**
- Maximum **1 capture group** per expression
- **No greedy quantifiers inside capture groups** — use non-greedy variants: `(.+?)` not `(.+)`, `(.*?)` not `(.*)`
- Quantifiers on capture groups themselves (e.g. `(foo)+`) are not allowed

**Five remapping patterns:**

| Pattern | User says… | Filter example | New name example |
|---|---|---|---|
| **N:1 group** | "These N services are all the same thing" | `peer.service:*.shopify.com` | `shopify` |
| **Strip suffix/prefix** | "The name has junk at the end/start" | `service:*.tropos` | `{{service\|^(.+?)\..*$}}` |
| **1:1 rename** | "We renamed this service and Datadog needs to match" | `service:old-auth-service` | `auth-service` |
| **Env split** | "I want separate services per env but they all have the same name" | `service:my-service AND env:prod` | `my-service-prod` |
| **Prefix normalization** | "All services should start with an env or team name" | `service:payments*` | `{{env}}-{{service}}` |

---

## Triggers

Invoke this skill when the user wants to:
- Rename a service in Datadog without re-instrumenting
- Collapse multiple inferred service names into one (e.g. many `api.shopify.com/*` variants → `shopify`)
- Strip environment suffixes, version tags, or deployment metadata baked into service names
- Normalize `peer.service` names to something meaningful
- Rename a service after an org change, product rebrand, or migration
- Split a single service into per-env variants (`my-service` + `env:prod` → `my-service-prod`)
- List, review, or delete existing service remapping rules

Do NOT invoke this skill if:
- The user wants to rename the service in their application code — that requires a tracer config change (`DD_SERVICE`), not a remapping rule
- The user wants to correlate telemetry across infrastructure tags — that is the "Correlate telemetry" action type in the UI, not remapping

---

## Prerequisites

### pup-cli: check, install, and authenticate

### Claude runs

```bash
pup --version
```

If not found:

### Claude runs

```bash
brew tap datadog-labs/pack
brew install pup
```

Check auth:
```bash
pup auth status
```

If not authenticated:

### Claude runs

```bash
pup auth login
```

> This opens a browser tab for OAuth. Complete the login there — Claude will continue once the command exits.

### Credentials for API calls

Service remapping rules are not yet supported by pup CLI — they require direct API calls. `DD_API_KEY`, `DD_APP_KEY`, and `DD_SITE` must be set.

### Claude runs

```bash
echo "DD_API_KEY set: $([ -n "${DD_API_KEY:-}" ] && echo yes || echo no)"
echo "DD_APP_KEY set: $([ -n "${DD_APP_KEY:-}" ] && echo yes || echo no)"
echo "DD_SITE: ${DD_SITE:-not set (defaulting to datadoghq.com)}"
```

If any are missing:

### What you need to do in a terminal

```bash
export DD_API_KEY=<your-api-key>
export DD_APP_KEY=<your-app-key>
export DD_SITE=datadoghq.com   # adjust for your site
```

> Common sites: `datadoghq.com` (US1), `datadoghq.eu` (EU1), `us3.datadoghq.com`, `us5.datadoghq.com`, `ap1.datadoghq.com`

Wait for the user to set credentials, then re-run the check above before continuing.

---

## Context to resolve before acting

| Variable | How to resolve |
|---|---|
| `ENV` | Ask the user which environment to target. Do NOT assume `prod`. |
| `ORIGINAL_SERVICE` | Current service name(s) to remap — discover with `pup apm services list` or ask the user |
| `ENTITY_TYPE` | Instrumented service (`rule_type: 0`) or inferred entity (`rule_type: 1`)? Ask if unclear — see Domain Knowledge |
| `TARGET_NAME` | The desired new service name — ask the user |
| `PATTERN` | Which pattern applies — identify from the user's description (see Domain Knowledge above) |

---

## Step 0: Discover Current Service Names

If the user hasn't specified exact names to remap, discover what exists first:

### Claude runs

```bash
pup apm services list --env <ENV> --from 1h
pup traces search --query "service:<PARTIAL_NAME>" --from 1h --limit 20
```

Use the output to help the user identify exact service names. Ask the user to confirm which names they want remapped before proceeding.

---

## Step 1: Build the Rule

Work through each component before writing any JSON.

### 1. Entity type

[DECISION: entity type — ask the user if unclear]
- Does the service appear because a tracer explicitly set its `service` tag? → `rule_type: 0` (SERVICE)
- Does it appear in the service map from outbound calls (e.g. a database, queue, or external API)? → `rule_type: 1` (INFERRED_ENTITY)

### 2. Filter

Write a single event-grammar query string targeting the service(s) to remap. Use the filter syntax and pattern table in Domain Knowledge to pick the right form.

### 3. New name (`value`)

Use the new name syntax and regex table in Domain Knowledge to pick the right form. For regex values, apply the constraints listed there.

### 4. Rule name

Suggest a descriptive name. Examples:
- `collapse-shopify-inferred-services`
- `strip-tropos-suffix`
- `rename-old-auth-to-auth-service`
- `env-split-my-service-prod`

---

## Step 2: Preview Impact

Before constructing the JSON, check what will be affected:

### Claude runs

```bash
# Confirm telemetry exists for the targeted service (zero spans = wrong query or wrong env)
pup traces search --query "service:<ORIGINAL_SERVICE>" --from 15m --limit 5

# Check for monitors referencing the old service name
pup monitors list | grep -i "<ORIGINAL_SERVICE>"

# List existing service remapping rules that may conflict
curl -s "https://api.${DD_SITE:-datadoghq.com}/api/v2/service-naming-rules" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -H "DD-APPLICATION-KEY: ${DD_APP_KEY}" | jq .
```

Report to the user:

| Item | What to surface |
|---|---|
| **Telemetry volume** | Non-zero spans confirm the filter will match real data. Zero = likely wrong service name or env. |
| **Monitors** | Any monitor referencing the old service name will silently break after remapping. List them and offer to update. |
| **Conflicting rules** | Existing rules targeting the same service may be overridden. Show conflicts and ask the user to confirm. |

If monitors reference the old service name, ask:
> *"I found `<N>` monitor(s) referencing `<ORIGINAL_SERVICE>`. After remapping, they'll need to be updated to use `<TARGET_NAME>`. Want me to update them now?"*

---

## Step 3: Construct the Rule JSON

Write the rule as `rule.json`. The `destination_tag_name` is always `"service"` for service remapping.

**1:1 rename — base service:**
```json
{
  "name": "rename-old-auth-to-auth-service",
  "filter": "service:old-auth-service",
  "rule_type": 0,
  "rewrite_tag_rules": [
    {"destination_tag_name": "service", "value": "auth-service"}
  ]
}
```

**N:1 group — inferred services (collapse all Shopify calls):**
```json
{
  "name": "collapse-shopify-inferred-services",
  "filter": "peer.service:*.shopify.com",
  "rule_type": 1,
  "rewrite_tag_rules": [
    {"destination_tag_name": "service", "value": "shopify"}
  ]
}
```

**Strip suffix — base service with regex:**
```json
{
  "name": "strip-tropos-suffix",
  "filter": "service:*.tropos",
  "rule_type": 0,
  "rewrite_tag_rules": [
    {"destination_tag_name": "service", "value": "{{service|^(.+?)\\..*$}}"}
  ]
}
```

**Env split — restrict to one environment:**
```json
{
  "name": "env-split-my-service-prod",
  "filter": "service:my-service AND env:prod",
  "rule_type": 0,
  "rewrite_tag_rules": [
    {"destination_tag_name": "service", "value": "my-service-prod"}
  ]
}
```

**Prefix normalization:**
```json
{
  "name": "prepend-env-to-payments",
  "filter": "service:payments*",
  "rule_type": 0,
  "rewrite_tag_rules": [
    {"destination_tag_name": "service", "value": "{{env}}-{{service}}"}
  ]
}
```

### Claude runs

```bash
cat > rule.json << 'EOF'
<RULE_JSON>
EOF
cat rule.json
```

---

## Step 4: Create the Rule

Show the user the rule and confirm before sending:

> *"I'm going to create a service remapping rule named `<RULE_NAME>` that maps `<ORIGINAL_SERVICE>` → `<TARGET_NAME>`. Here's the rule: [show rule.json contents]. Ready to proceed?"*

Wait for confirmation, then:

### Claude runs

```bash
curl -s -X POST "https://api.${DD_SITE:-datadoghq.com}/api/v2/service-naming-rules" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -H "DD-APPLICATION-KEY: ${DD_APP_KEY}" \
  -H "Content-Type: application/json" \
  -d @rule.json
```

If the response contains an `id` field — creation succeeded. Record the `id` and `version` values from the response.

ERROR: `400 Bad Request` with "Filter expression has invalid syntax" — the filter query is malformed. Check glob syntax and boolean operators.

ERROR: `400 Bad Request` with "Template value in target name is invalid" — the `value` regex is invalid. Check: max 1 capture group, non-greedy quantifiers inside groups (`(.+?)` not `(.+)`).

ERROR: `401 Unauthorized` — credentials are invalid or expired. Re-check `DD_API_KEY` and `DD_APP_KEY`.

ERROR: `403 Forbidden` — the API key lacks `apm_service_renaming_write` permission.

---

## Step 5: Verify

Allow 2–5 minutes for the rule to propagate, then confirm it is active:

### Claude runs

```bash
# Confirm new service name appears in APM
pup apm services list --env <ENV> --from 5m

# Confirm traces are arriving under the new name
pup traces search --query "service:<TARGET_NAME>" --from 5m --limit 5
```

If `<TARGET_NAME>` appears — rule is active.

ERROR: New name not appearing after 5 minutes:
- Confirm old service is still sending traces: `pup traces search --query "service:<ORIGINAL_SERVICE>" --from 5m`
- If old name still appears, propagation may still be in progress — wait 2 more minutes and retry
- If neither name appears, recheck that the filter matches actual tag values in the traces (re-run Step 0)

---

## Managing Existing Rules

### List all rules

### Claude runs

```bash
curl -s "https://api.${DD_SITE:-datadoghq.com}/api/v2/service-naming-rules" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -H "DD-APPLICATION-KEY: ${DD_APP_KEY}" | jq .
```

### Delete a rule

Show the user the rule's name and filter first, then ask for confirmation. Delete requires both the rule `id` and `version` from the list response:

### Claude runs

```bash
curl -s -X DELETE "https://api.${DD_SITE:-datadoghq.com}/api/v2/service-naming-rules/<RULE_ID>/<RULE_VERSION>" \
  -H "DD-API-KEY: ${DD_API_KEY}" \
  -H "DD-APPLICATION-KEY: ${DD_APP_KEY}"
```

ERROR: `409 Conflict` — the rule was modified since you fetched it. Re-list rules to get the current version and retry.

---

## Done

Exit when ALL of the following are true:
- [ ] Rule JSON shown to user and confirmed before creation
- [ ] Rule created and `id` returned in response
- [ ] New service name visible in `pup apm services list`
- [ ] Impacted monitors identified and offered for update
- [ ] User confirmed the remapping matches their intent

---

## Security constraints

- Never write a raw API key into any file or chat message — always use `$DD_API_KEY` and `$DD_APP_KEY`
- Never create or delete a rule without explicit user confirmation — show the full rule before creating
- Never assume `prod` as the environment — always confirm with the user
- Never run DELETE without showing the user the rule's name and filter first
