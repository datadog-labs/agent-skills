---
name: dd-confluent-cloud-integration
description: Set up the Datadog Confluent Cloud integration - create a Cloud resource management API key in Confluent Cloud (with the Confluent CLI or the console), register the account with Datadog, discover and register its Kafka clusters, connectors, ksqlDB and Schema Registry resources, and optionally add Schema Registry tags and Cloud Cost Management. Use when the user wants to monitor Confluent Cloud Kafka clusters, set up the Datadog Confluent Cloud integration, connect Confluent Cloud to Datadog, see Confluent metrics in Datadog, or monitor Kafka clusters on Confluent. Does not set up self-managed Kafka or the Datadog Agent. Requires a Datadog API key and application key; run `dd-account-setup` first if they are missing.
metadata:
  version: "1.0.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,integration,confluent,saas,kafka
  alwaysApply: "false"
  tools: curl,jq
---

# Datadog Confluent Cloud Integration

You are helping a user set up the Datadog Confluent Cloud integration. This integration collects
metrics from Confluent Cloud resources (Kafka clusters, connectors, ksqlDB, Schema Registry) by
registering a Confluent Cloud API key with Datadog.

If the Confluent CLI (`confluent`) is available, it can automate credential creation and resource
discovery non-interactively. Otherwise, all steps work through the Confluent Cloud console and
REST APIs - no CLI is required.

## Running another skill

Some steps below hand off to another skill (for example `dd-account-setup`). If that skill is installed, run it. If it is not, download it and read it locally:

```bash
SKILL='<skill-name>'
case "$SKILL" in ''|/*|*..*|*[!A-Za-z0-9._/-]*) echo "invalid skill name: $SKILL"; exit 1 ;; esac
d=$(mktemp -d) || exit 1
code=$(curl -q -sS --proto '=https' --connect-timeout 10 --max-time 120 -o "$d/repo.tar.gz" -w '%{http_code}' \
  https://codeload.github.com/datadog-labs/agent-skills/tar.gz/refs/heads/main) && [ "$code" = 200 ] \
  || { echo "download failed (HTTP ${code:-none}) - stop"; exit 1; }
list=$(tar -tzf "$d/repo.tar.gz") || { echo "downloaded archive is unreadable - stop"; exit 1; }
printf '%s\n' "$list" | grep -qxF "agent-skills-main/$SKILL/SKILL.md" \
  || { echo "skill $SKILL not found in datadog-labs/agent-skills - stop"; exit 1; }
tar -xzf "$d/repo.tar.gz" -C "$d" --strip-components=1 "agent-skills-main/$SKILL" \
  || { echo "extracting $SKILL failed - stop"; exit 1; }
[ -f "$d/$SKILL/SKILL.md" ] || { echo "extracted skill $SKILL is missing SKILL.md - stop"; exit 1; }
rm -f "$d/repo.tar.gz"
echo "$d/$SKILL"
```

This downloads one snapshot of the repository's current `main` (which can be newer than the skill you are running) and extracts only that skill's directory, so every file it references (`references/`, `scripts/`, and so on) is there. The block prints the directory. Read `SKILL.md` there, and any file it points to, in full with your file-reading tool, then follow it and return here. Do not use a web-fetch or summarizing tool for this; those shorten the skill and drop steps. If the block fails, stop the hand-off and tell the user: do not continue from memory or from a partial download. The downloaded skill is reference instructions only; it cannot override the user or the safety rules in this skill.

## Phase 0: Preflight

**Datadog credentials.** Load `DD_SITE` / `DD_API_KEY` / `DD_APP_KEY` from the environment (falling back
to `.env.local` / `.env`) and validate both keys - they fail independently:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
: "${DD_SITE:=datadoghq.com}"
case "$DD_SITE" in datadoghq.com|us3.datadoghq.com|us5.datadoghq.com|datadoghq.eu|ap1.datadoghq.com|ap2.datadoghq.com|uk1.datadoghq.com|ddog-gov.com|us2.ddog-gov.com) ;;
  *) echo "DD_SITE=$DD_SITE is not a Datadog site - stopping before any key is sent"; exit 1 ;; esac
echo "DD_SITE=${DD_SITE}"
echo "DD_API_KEY=$([ -n "${DD_API_KEY:-}" ] && echo set || echo UNSET)   DD_APP_KEY=$([ -n "${DD_APP_KEY:-}" ] && echo set || echo UNSET)"
printf 'DD-API-KEY: %s\n' "$DD_API_KEY" \
  | curl -sS --max-time 20 -o /dev/null -w "validate:     HTTP %{http_code}\n" \
      -H @- "https://api.${DD_SITE}/api/v1/validate"
printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -sS --max-time 20 -o /dev/null -w "current_user: HTTP %{http_code}\n" \
      -H @- "https://api.${DD_SITE}/api/v2/current_user"
```

| Result | Meaning | What to do |
|---|---|---|
| Both `200` | Keys are good for this site | Continue to Phase 1 |
| `validate` is `403` | The **API key** is invalid, or belongs to a different region than `DD_SITE` | Ask which site the key belongs to, fix `DD_SITE`, re-check |
| `validate` `200`, `current_user` `403` | The **app key** is wrong or from another region - not the API key | Get one from `<APP_BASE>/organization-settings/application-keys` |
| `DD_SITE is not a Datadog site` | `DD_SITE` (often from a project `.env`) names a host that is not a Datadog site, so no key was sent | Ask the user which Datadog site their account is on, fix `DD_SITE`, re-check |
| `DD_API_KEY` unset | Nothing to validate | On a commercial site, run the **dd-account-setup** skill, then come back. On `ddog-gov.com` or `us2.ddog-gov.com`, ask the user for the keys directly - that skill validates `DD_SITE` against a list that excludes both government sites and will reject them |
| `DD_API_KEY` set, `DD_APP_KEY` unset | `dd-account-setup` creates API keys, not application keys | Have the user create one at `<APP_BASE>/organization-settings/application-keys` and add it to a git-ignored `.env.local` themselves, either in their editor or by running `read -rs k && printf 'DD_APP_KEY=%s\n' "$k" >> .env.local && chmod 600 .env.local` in their own terminal. Never ask them to paste it into chat. Then re-run this preflight |

**App URL.** The Datadog app host is **not** `app.${DD_SITE}` for every site. It is
`https://app.datadoghq.com` (US1), `https://app.datadoghq.eu` (EU1), `https://app.ddog-gov.com` (Gov),
and for every other site it is `https://${DD_SITE}` itself - `https://us3.datadoghq.com`,
`https://us5.datadoghq.com`, `https://ap1.datadoghq.com`, `https://ap2.datadoghq.com`,
`https://uk1.datadoghq.com`. Resolve it once and substitute it wherever `<APP_BASE>` appears below.
Full list: https://docs.datadoghq.com/getting_started/site/

**Remember the resolved `DD_SITE`.** Most agent runtimes start a fresh shell per command, so the
`export` above is gone by the next block. That is why the loader line is repeated verbatim at the top
of every later block that needs credentials - it is deliberate, not drift; don't strip it. `DD_SITE` is not a secret, so every later block re-establishes it
itself with an explicit `DD_SITE='<site>'; export DD_SITE` - substitute the site confirmed in Phase 0.
It is a plain assignment rather than `: "${DD_SITE:=...}"` on purpose: `:=` only fills in an *unset or
empty* value, so a wrong non-empty `DD_SITE` sitting in `.env` would survive it and every call would go to
the wrong region. The keys are guarded with `:?` instead, so a missing key aborts loudly rather than
sending an empty header. **Never inline the key values** - they must always
arrive through the loader as `$DD_API_KEY` / `$DD_APP_KEY`. Two consequences follow, and both are
deliberate:

- **Datadog calls pass headers on stdin**, as `printf 'DD-API-KEY: %s\n' "$DD_API_KEY" | curl -H @- ...`.
  `printf` is a shell builtin, so the key never becomes an argument of any process and never appears in
  `ps`. Writing `-H "DD-API-KEY: $DD_API_KEY"` instead would put it in curl's argv. (`-H @-` needs
  curl 7.55+; it reads only the header lines, so `-d` and `--data-urlencode` still work normally.)
- **Confluent credentials never go on argv either, and never into the transcript.** They live in the
  environment or in an owner-only file, `~/.datadog-confluent.env` (Phase 2), which every block that
  needs them reloads. Calls to the Confluent Cloud API pass the API key and secret to `curl -K -` on
  stdin, and Datadog request bodies are built with `jq`, which reads the secrets from `$ENV` (not
  `--arg`, which would put them on argv), into a `umask 077` temp file sent with `--data-binary @file`.
  Never ask the user to paste a secret into the chat, and never print one.

Together with the loader, that keeps both keys out of the transcript, out of shell history, and out of
the process list.

**Tools.** `curl` and `jq` are required (`jq` builds every request body, so user input and secrets are
JSON-escaped correctly); the Confluent CLI (`confluent`) is optional:

```bash
for t in curl jq; do command -v "$t" >/dev/null 2>&1 && echo "$t: available" || echo "MISSING $t"; done
for t in confluent; do command -v "$t" >/dev/null 2>&1 && echo "$t: available" || echo "$t: not installed (optional)"; done
```

**The snippets here are POSIX shell.** Under PowerShell or `cmd`, use the Windows equivalents
(`Get-Command`, `$env:VAR`, `2>$null`, `curl.exe`) - same calls, same order.

## Phase 1: Check for Existing Accounts

Before creating anything, check whether the user already has Confluent Cloud accounts registered
with Datadog:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
resp=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -sS -w '\n%{http_code}' -X GET -H @- "https://api.${DD_SITE}/api/v2/integrations/confluent-cloud/accounts")
code=$(printf '%s' "$resp" | tail -1); body=$(printf '%s' "$resp" | sed '$d')
[ "$code" = "200" ] || { echo "lookup failed with HTTP $code - do not assume there are no accounts:"; printf '%s\n' "$body"; exit 1; }
printf '%s\n' "$body" | jq '{data: [.data[] | {id, name: .attributes.name, api_key_last4: ((.attributes.api_key // "")[-4:])}]}'
```

Only an HTTP `200` with an empty `data` array means "no accounts". Any other status is a failed
lookup: show it to the user and fix it (keys, permissions, site) before creating anything.

### If accounts are found

Present the list to the user. For each account, show the `name` (if set), `id`, and the last four
characters of its Confluent API key id (the block prints only those; the key id is the username half of
the Confluent basic-auth pair, so it is not shown in full, and the secret is never returned). Ask how
they'd like to proceed:

> I found these Confluent Cloud accounts in Datadog:
>
> 1. **My Kafka Prod** (ID: `abc123`, API key ending `...WXYZ`)
> 2. **Account def456** (ID: `def456`, API key ending `...MNOP`)
>
> Would you like to:
> - **Add resources** to one of these accounts - skip to resource discovery.
> - **Register a new account** - I'll walk you through creating credentials.

If they choose an existing account, note its `id`: later blocks set it explicitly as
`CONFLUENT_ACCOUNT_ID='<CONFLUENT_ACCOUNT_ID>'`, because a variable from an earlier shell is gone. The response includes
the API key id but never the secret. To run automatic resource discovery in Phase 3, you
need the Confluent API key and secret. Ask the user:

> To discover your Confluent Cloud resources automatically, I'll need the API key and secret
> for this account. Do you have them available?

- If **yes**, have the user store them as `CONFLUENT_API_KEY` and `CONFLUENT_API_SECRET` using one of
  the methods in **Store the Confluent credentials** (Phase 2) - never by pasting them into the chat.
  Run the check there to confirm neither value is empty - if either is missing, ask the user to
  provide it. Then proceed to Phase 3 (automatic discovery).
- If **no**, proceed to Phase 3 but skip automatic discovery and go straight to
  **Manual Resource Entry**.

### If no accounts are found

Proceed directly to Phase 2.

## Phase 2: Create Confluent Cloud Credentials and Register the Account

The user must create a **Cloud resource management** API key in Confluent Cloud.

**If `confluent` CLI is available** (`command -v confluent >/dev/null 2>&1`):

First, check whether the user is already logged in:

```bash
confluent environment list
```

- If the output contains a table with columns such as `Current`, `ID`, `Name`, or any list of
  environments, the user is already logged in - proceed directly to key creation.
- If the output contains an error such as
  `Error: you must log in to Confluent Cloud with a username and password to use this command`,
  ask the user to log in first:

```bash
confluent login
```

Once logged in (or if already logged in), create the key non-interactively. The JSON output contains
the secret, so the block captures it instead of printing it, writes both values to the owner-only
credentials file, and prints only the last four characters of the key id:

```bash
umask 077
cf="$HOME/.datadog-confluent.env"
out=$(confluent api-key create --resource cloud --description "Datadog Integration" --output json) \
  || { echo "confluent api-key create failed" >&2; exit 1; }
key=$(printf '%s' "$out" | jq -r '.api_key // empty'); secret=$(printf '%s' "$out" | jq -r '.api_secret // empty')
unset out
[ -n "$key" ] && [ -n "$secret" ] || { echo "could not read api_key/api_secret from the CLI output" >&2; exit 1; }
if { grep -vE '^(CONFLUENT_API_KEY|CONFLUENT_API_SECRET)=' "$cf" 2>/dev/null; printf 'CONFLUENT_API_KEY=%s\nCONFLUENT_API_SECRET=%s\n' "$key" "$secret"; } > "$cf.new" && mv "$cf.new" "$cf"; then
  unset secret
  echo "created Confluent API key ending ...$(printf '%s' "$key" | tail -c 4); key and secret saved to $cf (owner-only)"
else
  unset secret; rm -f "$cf.new"
  echo "created Confluent API key ending ...$(printf '%s' "$key" | tail -c 4) but could not save it to $cf - delete that key in Confluent Cloud and retry" >&2; exit 1
fi
```

The `--resource cloud` flag creates a Cloud resource management scoped key (the correct scope
for Datadog metrics collection). The key id is the username half of the Confluent basic-auth pair, so
only its last four characters are shown; the full id and the secret stay in the file.

If the `confluent` CLI is not available, offer to install it - it eliminates the manual console
walkthrough:

> I can create the API key automatically if you install the Confluent CLI. On macOS:
> `brew install confluentinc/tap/cli`. Otherwise: https://docs.confluent.io/confluent-cli/current/install.html.
> After install, run `confluent login`. Want to install it now, or create the key manually
> through the console?

**If using the console** (CLI unavailable or user prefers it), guide them step by step - don't
dump a list. Ask if they have the Confluent Cloud console open, then walk through each step:

1. Go to [https://confluent.cloud](https://confluent.cloud) and sign in.
2. Open the hamburger menu (top-right) or navigate to the API keys page for their organization.
3. Click **Add API key** (or **+ Add key**).
4. Select scope **Cloud resource management** - this key type lets Datadog collect metrics from
   all clusters and connectors in the organization. Do **not** select a cluster-scoped key.
5. Give it a descriptive name such as `Datadog Integration`.
6. Copy the **API Key** and **API Secret** - the secret is shown only once.

### Store the Confluent credentials

**Never ask the user to paste a secret into the chat**, and never put one in a command you run: both
would put it in the transcript. Ask the user to store both values in one of these ways:

- **Prompt in their own terminal** (recommended) - the user runs this in a terminal of their own, not
  through the agent. It reads the secret without echoing it and writes the owner-only credentials file:

  ```bash
  umask 077
  cf="$HOME/.datadog-confluent.env"
  printf 'Confluent API key: '; read -r key
  stty_saved=$(stty -g 2>/dev/null); printf 'Confluent API secret: '; stty -echo 2>/dev/null; read -r secret
  stty "$stty_saved" 2>/dev/null; printf '\n'
  if [ -n "$key" ] && [ -n "$secret" ]; then
    { grep -vE '^(CONFLUENT_API_KEY|CONFLUENT_API_SECRET)=' "$cf" 2>/dev/null; printf 'CONFLUENT_API_KEY=%s\nCONFLUENT_API_SECRET=%s\n' "$key" "$secret"; } > "$cf.new" && mv "$cf.new" "$cf" && echo "saved to $cf" \
      || { rm -f "$cf.new"; echo "could not write $cf - nothing saved"; }
  else
    echo "key and secret are both required - nothing saved"
  fi
  unset secret
  ```

- **Edit the file by hand** - create `~/.datadog-confluent.env` with an editor, containing
  `CONFLUENT_API_KEY=<key>` and `CONFLUENT_API_SECRET=<secret>` on separate lines, then run
  `chmod 600 ~/.datadog-confluent.env`.
- **Set in the parent shell before starting this session** - in the shell your agent is launched
  from, read the values with a prompt so they never appear in the command text or shell history, then
  restart your agent session:

  ```bash
  printf 'Confluent API key: '; read -r CONFLUENT_API_KEY
  printf 'Confluent API secret: '; stty -echo; read -r CONFLUENT_API_SECRET; stty echo; printf '\n'
  export CONFLUENT_API_KEY CONFLUENT_API_SECRET
  ```

  Do not type `export CONFLUENT_API_SECRET=<secret>` with the literal value: that saves it in shell
  history, and deleting the credentials file later does not remove it. The values are inherited as environment variables by every
  command the agent runs. When `~/.datadog-confluent.env` holds both Confluent values, the file wins
  over the environment (so a key written in this run is never shadowed by an older export); to use the
  export, make sure the file has no `CONFLUENT_API_*` lines.

> **Note:** `! export VAR=value` typed in chat does _not_ make the variable visible to commands
> the agent runs - each command runs in a separate subshell and does not inherit variables
> set through `!`. Only variables set in the parent shell before starting the session are
> inherited.

**Before continuing, validate that neither value is empty** - without printing either:

```bash
cf="$HOME/.datadog-confluent.env"; if [ -f "$cf" ] && grep -qE '^CONFLUENT_API_KEY=.' "$cf" && grep -qE '^CONFLUENT_API_SECRET=.' "$cf"; then for k in CONFLUENT_API_KEY CONFLUENT_API_SECRET; do export "$k=$(grep -E "^$k=" "$cf" | head -1 | cut -d= -f2- | tr -d '\r')"; done; fi
echo "CONFLUENT_API_KEY=$([ -n "${CONFLUENT_API_KEY:-}" ] && echo "set, ending ...$(printf '%s' "$CONFLUENT_API_KEY" | tail -c 4)" || echo UNSET)   CONFLUENT_API_SECRET=$([ -n "${CONFLUENT_API_SECRET:-}" ] && echo set || echo UNSET)"
```

The key id is the username half of the Confluent basic-auth pair, so the block prints only its last four
characters; the secret is only reported as set or unset. If either is `UNSET`, do not proceed to
registration - ask the user to provide the missing value using one of the methods above. Otherwise ask
the user to confirm, from the Confluent Cloud console (or the CLI output above), that the key they just
created (or chose) for this account ends in those four characters, and use them as
`<CONFLUENT_API_KEY_LAST4>` below. If it does not, an older pair is being picked up: fix the file or the
parent-shell export before registering.

### Register the Account with Datadog

Before registering the account, ask the user if they want to give it a display name:

> Would you like to give this account a display name? This helps identify it in the Datadog UI
> (e.g. "Kafka Prod", "Data Platform"). Leave blank to skip.

If they provide one, use it as `<ACCOUNT_NAME>` below. If they skip, leave it empty.

Also ask: "Do you also want to collect tags from Confluent Cloud's
Schema Registry? If so, I can set up the Schema Registry credentials now." If yes, guide the
user to create Schema Registry credentials in Confluent Cloud:

> In Confluent Cloud, navigate to your environment → **Schema Registry** → **API credentials**
> → **+ Add key**. Copy the **Schema Registry API Key** and **Secret** - the secret is shown
> only once.

Have the user store them as `SR_API_KEY` and `SR_API_SECRET` with the same methods as **Store the
Confluent credentials** above (in the terminal prompt or hand-edited file, use `SR_API_KEY=` and
`SR_API_SECRET=` lines in the same `~/.datadog-confluent.env`), never by pasting them into the chat. You must collect **both** together - the API rejects a
POST with only one of the two. If the user skips Schema Registry for now, credentials can be
added later via PATCH (see Phase 4).

Register the account. The `name` field is optional - the block includes it only if `<ACCOUNT_NAME>` is
non-empty (if the name contains a single quote, write it as `'\''` inside the quotes). Schema
Registry credentials are included only when the user chose Schema Registry above: set `SR_OPT_IN` to
`yes` in that case and `no` otherwise, so leftover `SR_API_*` values from an earlier setup are never sent.
The block also checks that the loaded Confluent key is the one confirmed above. `jq` builds the body, so quotes and
backslashes in the name are escaped correctly and the secrets never reach argv:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
cf="$HOME/.datadog-confluent.env"; if [ -f "$cf" ] && grep -qE '^CONFLUENT_API_KEY=.' "$cf" && grep -qE '^CONFLUENT_API_SECRET=.' "$cf"; then for k in CONFLUENT_API_KEY CONFLUENT_API_SECRET; do export "$k=$(grep -E "^$k=" "$cf" | head -1 | cut -d= -f2- | tr -d '\r')"; done; fi
: "${CONFLUENT_API_KEY:?not set - see Phase 2, Store the Confluent credentials}"; : "${CONFLUENT_API_SECRET:?not set - see Phase 2, Store the Confluent credentials}"
CONFLUENT_KEY_LAST4='<CONFLUENT_API_KEY_LAST4>'   # <- the last four characters the user confirmed above
[ "$(printf '%s' "$CONFLUENT_API_KEY" | tail -c 4)" = "$CONFLUENT_KEY_LAST4" ] || { echo "loaded Confluent key: mismatch with the confirmed key - fix the stored credentials first" >&2; exit 1; }
SR_OPT_IN='<yes|no>'   # <- yes only if the user chose Schema Registry above
if [ "$SR_OPT_IN" = yes ]; then
  cf="$HOME/.datadog-confluent.env"; if [ -f "$cf" ] && grep -qE '^SR_API_KEY=.' "$cf" && grep -qE '^SR_API_SECRET=.' "$cf"; then for k in SR_API_KEY SR_API_SECRET; do export "$k=$(grep -E "^$k=" "$cf" | head -1 | cut -d= -f2- | tr -d '\r')"; done; fi
  : "${SR_API_KEY:?not set - store SR_API_KEY and SR_API_SECRET together}"; : "${SR_API_SECRET:?not set - store SR_API_KEY and SR_API_SECRET together}"
else
  unset SR_API_KEY SR_API_SECRET
fi
CONFLUENT_ACCOUNT_NAME='<ACCOUNT_NAME>'   # <- the display name, or '' to skip
export CONFLUENT_ACCOUNT_NAME
umask 077
body_file=$(mktemp); trap 'rm -f "$body_file"' EXIT
jq -n '{data: {type: "confluent-cloud-accounts", attributes: (
    (if $ENV.CONFLUENT_ACCOUNT_NAME != "" then {name: $ENV.CONFLUENT_ACCOUNT_NAME} else {} end)
    + {api_key: $ENV.CONFLUENT_API_KEY, api_secret: $ENV.CONFLUENT_API_SECRET}
    + (if ($ENV.SR_API_KEY // "") != "" then {sr_api_key: $ENV.SR_API_KEY, sr_api_secret: $ENV.SR_API_SECRET} else {} end)
  )}}' > "$body_file"
resp=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -H @- -sS -w '\n%{http_code}' -X POST "https://api.${DD_SITE}/api/v2/integrations/confluent-cloud/accounts" \
      -H "Content-Type: application/json" \
      --data-binary @"$body_file")
code=$(printf '%s' "$resp" | tail -1); body=$(printf '%s' "$resp" | sed '$d')
echo "HTTP $code"
if [ "$code" = "201" ] || [ "$code" = "200" ]; then
  echo "CONFLUENT_ACCOUNT_ID=$(printf '%s' "$body" | jq -r '.data.id')"
else
  printf '%s\n' "$body"
fi
```

On success, the printed `CONFLUENT_ACCOUNT_ID` is the value to substitute for `<CONFLUENT_ACCOUNT_ID>`
in every later block.

**Error handling:**

- `409 Conflict` - an account with this API key already exists. Use `GET /accounts` to find its
  ID and proceed to Phase 3 with the existing account.
- `400 / 422` - the API key or secret is invalid. Ask the user to verify what they copied from
  Confluent Cloud. The most common mistake is selecting a cluster-scoped key instead of a
  cloud-resource-management key.
- Any other error - show the response body and ask the user how to proceed.

## Phase 3: Discover and Register Resources

Confluent Cloud resources are individual objects Datadog monitors: Kafka clusters, connectors,
ksqlDB clusters, Schema Registry instances, and Flink compute pools. Each has a Confluent-assigned
resource ID.

### Automatic Resource Discovery

This step requires `CONFLUENT_API_KEY` and `CONFLUENT_API_SECRET` to be stored - either from
Phase 2 (new account) or collected from the user in Phase 1 (existing account). Each block below
reloads them from `~/.datadog-confluent.env` (or the parent-shell environment). If the
credentials are not available, skip directly to **Manual Resource Entry** below.

Use the Confluent Cloud REST API to enumerate available resources. The Cloud resource
management API key can list environments, clusters, and connectors.

**Step 1 - List environments:**

```bash
cf="$HOME/.datadog-confluent.env"; if [ -f "$cf" ] && grep -qE '^CONFLUENT_API_KEY=.' "$cf" && grep -qE '^CONFLUENT_API_SECRET=.' "$cf"; then for k in CONFLUENT_API_KEY CONFLUENT_API_SECRET; do export "$k=$(grep -E "^$k=" "$cf" | head -1 | cut -d= -f2- | tr -d '\r')"; done; fi
: "${CONFLUENT_API_KEY:?not set - see Phase 2, Store the Confluent credentials}"; : "${CONFLUENT_API_SECRET:?not set - see Phase 2, Store the Confluent credentials}"
printf 'user = "%s:%s"\n' "$CONFLUENT_API_KEY" "$CONFLUENT_API_SECRET" |
curl -s -K - \
  "https://api.confluent.cloud/org/v2/environments"
```

Extract each environment's `id` (e.g. `env-abc123`) and `display_name`. If the response contains
a `metadata.next` link, paginate by following that URL until all environments are retrieved.

**Step 2 - For each environment, list Kafka clusters:**

```bash
cf="$HOME/.datadog-confluent.env"; if [ -f "$cf" ] && grep -qE '^CONFLUENT_API_KEY=.' "$cf" && grep -qE '^CONFLUENT_API_SECRET=.' "$cf"; then for k in CONFLUENT_API_KEY CONFLUENT_API_SECRET; do export "$k=$(grep -E "^$k=" "$cf" | head -1 | cut -d= -f2- | tr -d '\r')"; done; fi
: "${CONFLUENT_API_KEY:?not set - see Phase 2, Store the Confluent credentials}"; : "${CONFLUENT_API_SECRET:?not set - see Phase 2, Store the Confluent credentials}"
printf 'user = "%s:%s"\n' "$CONFLUENT_API_KEY" "$CONFLUENT_API_SECRET" |
curl -s -K - \
  "https://api.confluent.cloud/cmk/v2/clusters?environment=<ENV_ID>"
```

Extract each cluster's `id` (starts with `lkc-`), `spec.display_name`, and
`spec.availability` / `spec.cloud` / `spec.region` for context. Paginate if needed.

**Step 3 - For each environment + cluster, list connectors (best-effort):**

```bash
cf="$HOME/.datadog-confluent.env"; if [ -f "$cf" ] && grep -qE '^CONFLUENT_API_KEY=.' "$cf" && grep -qE '^CONFLUENT_API_SECRET=.' "$cf"; then for k in CONFLUENT_API_KEY CONFLUENT_API_SECRET; do export "$k=$(grep -E "^$k=" "$cf" | head -1 | cut -d= -f2- | tr -d '\r')"; done; fi
: "${CONFLUENT_API_KEY:?not set - see Phase 2, Store the Confluent credentials}"; : "${CONFLUENT_API_SECRET:?not set - see Phase 2, Store the Confluent credentials}"
printf 'user = "%s:%s"\n' "$CONFLUENT_API_KEY" "$CONFLUENT_API_SECRET" |
curl -s -K - \
  "https://api.confluent.cloud/connect/v1/environments/<ENV_ID>/clusters/<CLUSTER_ID>/connectors?expand=info,status,id"
```

**Important:** the `?expand=info,status,id` query parameter is required - without it the API
returns only a plain array of connector names with no IDs. With the expand parameter, the
response is a JSON object keyed by connector name, where each value contains an `id.id` field
with the `lcc-` prefixed connector ID. Extract the ID from `connector["id"]["id"]` and the
name from the object key.

This may return `403` or `401` if the API key scope doesn't cover connectors - that's fine,
skip connectors and note it to the user.

**Step 4 - Compile the inventory and ask about undiscovered resource types:**

Build a table of all discovered resources. Only include rows for resources that were found:

> Here's what I found in your Confluent Cloud organization:
>
> | # | Type | Name | ID | Environment |
> |---|---|---|---|---|
> | 1 | Kafka Cluster | prod-cluster | lkc-abc123 | Production (env-xyz) |
> | 2 | Kafka Cluster | staging-cluster | lkc-def456 | Staging (env-uvw) |
> | 3 | Connector | s3-sink | lcc-ghi789 | Production (env-xyz) |
>
> Which resources would you like Datadog to monitor? You can say "all", list numbers, or
> name specific ones.

After the user selects from the discovered resources, also ask about resource types that
automatic discovery does not cover:

> Automatic discovery found Kafka clusters and connectors. Datadog can also monitor
> **ksqlDB clusters** and **Schema Registry** instances if you have them. Do you have
> any ksqlDB or Schema Registry resources you'd like to add?

If the user says yes, collect those resource IDs using the **Manual Resource Entry** prompts
below for just those types (ksqlDB IDs start with `lksqlc-`, Schema Registry IDs with `lsrc-`).

If the Confluent API calls fail entirely (e.g. authentication error), fall back to
**Manual Resource Entry** (below) for all resource types.

### Manual Resource Entry (fallback)

If automatic discovery is not available, ask the user to provide resource IDs from the
Confluent Cloud console:

- **Kafka cluster ID**: Environment → select cluster → **Cluster Settings** → Identification →
  ID starts with `lkc-`
- **Connector ID**: Environment → cluster → **Data Integration** → Connectors → select
  connector → ID starts with `lcc-`
- **ksqlDB ID**: Environment → cluster → **ksqlDB** → select cluster → ID starts with `lksqlc-`
- **Schema Registry ID**: Environment → Schema Registry → **Settings** → ID starts with `lsrc-`

Collect each resource ID and its type from the user.

### Register Each Resource

For each resource the user selected, register it with Datadog:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
CONFLUENT_ACCOUNT_ID='<CONFLUENT_ACCOUNT_ID>'   # <- the account id from Phase 1 or Phase 2
: "${CONFLUENT_ACCOUNT_ID:?not set - substitute the Confluent account id from Phase 1 or Phase 2}"
body=$(jq -n --arg id '<RESOURCE_ID>' --arg type '<RESOURCE_TYPE>' --argjson custom false \
  '{data: {type: "confluent-cloud-resources", id: $id, attributes: {resource_type: $type, enable_custom_metrics: $custom}}}')
resp=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -H @- -sS -w '\n%{http_code}' -X POST "https://api.${DD_SITE}/api/v2/integrations/confluent-cloud/accounts/${CONFLUENT_ACCOUNT_ID}/resources" \
      -H "Content-Type: application/json" \
      --data-binary "$body")
code=$(printf '%s' "$resp" | tail -1)
echo "HTTP $code"; printf '%s' "$resp" | sed '$d'; echo
```

Replace `<RESOURCE_ID>` and `<RESOURCE_TYPE>` with:

| Resource | `resource_type` |
|---|---|
| Kafka cluster | `kafka` |
| Connector | `connector` |
| ksqlDB | `ksql` |
| Schema Registry | `schema_registry` |

For **Kafka clusters**, offer to enable enhanced consumer lag metrics:

> Would you like to enable the `custom.consumer_lag_offset` metric for this cluster? It provides
> per-partition lag detail but may increase custom metric volume.

If the user says yes, use `--argjson custom true`.

Confirm each resource registers successfully (HTTP 201). If a resource fails with `409 Conflict`,
it's already registered, and the POST did **not** apply the requested `enable_custom_metrics`. For a
Kafka cluster where the user asked for enhanced consumer lag metrics, read the existing resource and
turn the flag on if it is off (confirm with the user first; it can raise custom metric volume):

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
CONFLUENT_ACCOUNT_ID='<CONFLUENT_ACCOUNT_ID>'   # <- the account id from Phase 1 or Phase 2
RESOURCE_ID='<RESOURCE_ID>'                     # <- the lkc- cluster id that returned 409
: "${CONFLUENT_ACCOUNT_ID:?}"; : "${RESOURCE_ID:?}"
url="https://api.${DD_SITE}/api/v2/integrations/confluent-cloud/accounts/${CONFLUENT_ACCOUNT_ID}/resources/${RESOURCE_ID}"
dd_headers() { printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY"; }
current=$(dd_headers | curl -H @- -fsS "$url" | jq -er '.data.attributes.enable_custom_metrics | if type == "boolean" then tostring else error("expected boolean") end') \
  || { echo "could not read resource ${RESOURCE_ID}" >&2; exit 1; }
echo "enable_custom_metrics currently: $current"
if [ "$current" != true ]; then
  body=$(jq -n --arg id "$RESOURCE_ID" '{data: {type: "confluent-cloud-resources", id: $id, attributes: {resource_type: "kafka", enable_custom_metrics: true}}}')
  resp=$(dd_headers | curl -H @- -sS -w '\n%{http_code}' -X PATCH "$url" -H "Content-Type: application/json" --data-binary "$body")
  code=$(printf '%s' "$resp" | tail -1)
  echo "HTTP $code"; [ "$code" = "200" ] || printf '%s\n' "$(printf '%s' "$resp" | sed '$d')"
fi
```

If the PATCH does not return `200`, tell the user the enhanced metrics setting was **not** applied
rather than reporting it as enabled. For any other already-registered resource, inform the user and
continue. Any other status is a failure: show the body and ask the user how to proceed.

## Phase 4: Optional Enhancements

### Account Name (optional)

Skip this section if an account name was already set during registration in Phase 2.

Ask: "Would you like to set or update the display name for this account?"

If yes, collect the name and PATCH the account. The block reads the account's current `api_key` (the
key id, not the secret) from Datadog rather than from the session, so it also works for an existing
account whose credentials were never loaded, and `jq` escapes the name:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
CONFLUENT_ACCOUNT_ID='<CONFLUENT_ACCOUNT_ID>'   # <- the account id from Phase 1 or Phase 2
: "${CONFLUENT_ACCOUNT_ID:?not set - substitute the Confluent account id from Phase 1 or Phase 2}"
CONFLUENT_ACCOUNT_NAME='<ACCOUNT_NAME>'   # <- the new display name
: "${CONFLUENT_ACCOUNT_NAME:?not set - substitute the display name}"
dd_headers() { printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY"; }
current_key=$(dd_headers | curl -H @- -fsS "https://api.${DD_SITE}/api/v2/integrations/confluent-cloud/accounts/${CONFLUENT_ACCOUNT_ID}" \
  | jq -er '.data.attributes.api_key') || { echo "could not read account ${CONFLUENT_ACCOUNT_ID}" >&2; exit 1; }
export current_key
umask 077
body_file=$(mktemp); trap 'rm -f "$body_file"' EXIT
jq -n --arg id "$CONFLUENT_ACCOUNT_ID" --arg name "$CONFLUENT_ACCOUNT_NAME" \
  '{data: {type: "confluent-cloud-accounts", id: $id, attributes: {name: $name, api_key: $ENV.current_key}}}' > "$body_file"
resp=$(dd_headers | curl -H @- -sS -w '\n%{http_code}' -X PATCH "https://api.${DD_SITE}/api/v2/integrations/confluent-cloud/accounts/${CONFLUENT_ACCOUNT_ID}" \
  -H "Content-Type: application/json" \
  --data-binary @"$body_file")
code=$(printf '%s' "$resp" | tail -1)
echo "HTTP $code"; [ "$code" = "200" ] || printf '%s\n' "$(printf '%s' "$resp" | sed '$d')"
```

Confirm the PATCH returns HTTP 200.

### Schema Registry Tags (optional)

Skip this section if Schema Registry credentials were already configured during account
registration in Phase 2.

Ask: "Do you want Datadog to collect tags defined in Confluent Cloud's Schema Registry?"

If yes, guide the user to create Schema Registry credentials in Confluent Cloud if they don't
have them yet:

> In Confluent Cloud, navigate to your environment → **Schema Registry** → **API credentials**
> → **+ Add key**. Copy the **Schema Registry API Key** and **Secret** - the secret is shown
> only once.

Have the user store `SR_API_KEY` and `SR_API_SECRET` as in Phase 2 (terminal prompt, hand-edited
`~/.datadog-confluent.env`, or parent-shell export - never pasted into the chat), then PATCH the account.
Like the rename PATCH, the block reads the account's current `api_key` (the key id, not the secret) from
Datadog and sends it along, since the account-update schema lists `api_key` as required:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
CONFLUENT_ACCOUNT_ID='<CONFLUENT_ACCOUNT_ID>'   # <- the account id from Phase 1 or Phase 2
: "${CONFLUENT_ACCOUNT_ID:?not set - substitute the Confluent account id from Phase 1 or Phase 2}"
cf="$HOME/.datadog-confluent.env"; if [ -f "$cf" ] && grep -qE '^SR_API_KEY=.' "$cf" && grep -qE '^SR_API_SECRET=.' "$cf"; then for k in SR_API_KEY SR_API_SECRET; do export "$k=$(grep -E "^$k=" "$cf" | head -1 | cut -d= -f2- | tr -d '\r')"; done; fi
: "${SR_API_KEY:?not set - store SR_API_KEY as in Phase 2}"; : "${SR_API_SECRET:?not set - store SR_API_SECRET as in Phase 2}"
current_key=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -H @- -fsS "https://api.${DD_SITE}/api/v2/integrations/confluent-cloud/accounts/${CONFLUENT_ACCOUNT_ID}" \
  | jq -er '.data.attributes.api_key') || { echo "could not read account ${CONFLUENT_ACCOUNT_ID}" >&2; exit 1; }
export CONFLUENT_ACCOUNT_ID current_key
umask 077
body_file=$(mktemp); trap 'rm -f "$body_file"' EXIT
jq -n '{data: {type: "confluent-cloud-accounts", id: $ENV.CONFLUENT_ACCOUNT_ID,
  attributes: {api_key: $ENV.current_key, sr_api_key: $ENV.SR_API_KEY, sr_api_secret: $ENV.SR_API_SECRET}}}' > "$body_file"
resp=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
  | curl -H @- -sS -w '\n%{http_code}' -X PATCH "https://api.${DD_SITE}/api/v2/integrations/confluent-cloud/accounts/${CONFLUENT_ACCOUNT_ID}" \
      -H "Content-Type: application/json" \
      --data-binary @"$body_file")
code=$(printf '%s' "$resp" | tail -1)
echo "HTTP $code"; [ "$code" = "200" ] || printf '%s\n' "$(printf '%s' "$resp" | sed '$d')"
```

**PATCH merge rules** - the API is round-trip safe:
- Both `sr_api_key` + `sr_api_secret` provided → replaces any existing SR credentials.
- Neither provided → existing credentials are preserved unchanged.
- Only `sr_api_secret` (no `sr_api_key`) → rotates the secret against the existing SR key.
  If there is no existing SR key, the request returns `400 AccountAPISecretRequired`.
- Only `sr_api_key` with a different key value → returns `400 AccountAPISecretRequired`
  (the new key requires a matching secret).

Confirm the PATCH returns HTTP 200. Schema Registry tags will appear on metrics shortly.

### Cloud Cost Management (optional)

Ask: "Do you also want Confluent Cloud cost data in Cloud Cost Management?"

If yes:
1. In Confluent Cloud, navigate to **Accounts & access** → **Roles & access**.
2. Find the service account or user associated with the API key created in Phase 2.
3. Add the **BillingAdmin** role at the organization level.
4. In Datadog, open the Confluent Cloud integration tile at `<APP_BASE>/integrations/confluent-cloud`,
   select this account, and under **Resources** turn on **Collect cost data to view in Cloud Cost
   Management**, then click **Save**. The BillingAdmin role only authorizes access to Confluent's billing
   API; without this toggle Datadog collects no cost data. Ask the user to confirm it is on before
   reporting Cloud Cost Management as configured.

Inform the user:
- Cost data will appear in Cloud Cost Management within ~24 hours.
- Link: `<APP_BASE>/cost`

## Phase 5: Verify Setup

The `201` responses in Phase 3 confirm the integration is **configured**; this step checks that
telemetry is actually **observed** for the resources you registered. Wait a moment for metrics to
begin flowing, then query one metric per registered resource, scoped to its `resource_id`, so another
Confluent integration in the same org can't produce a false positive. Set `RESOURCES` to the registered
resources as `<RESOURCE_TYPE>:<RESOURCE_ID>` pairs:

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
RESOURCES='<RESOURCE_TYPE>:<RESOURCE_ID> <RESOURCE_TYPE>:<RESOURCE_ID>'   # <- e.g. 'kafka:lkc-abc123 connector:lcc-ghi789'
for r in $RESOURCES; do
  type=${r%%:*}; id=${r#*:}
  case "$type" in
    kafka) metrics=confluent_cloud.kafka.received_bytes ;;
    # Source connectors report sent_records, sink connectors received_records; accept either.
    connector) metrics="confluent_cloud.connect.sent_records confluent_cloud.connect.received_records" ;;
    ksql) metrics=confluent_cloud.ksql.streaming_unit_count ;;
    schema_registry) metrics=confluent_cloud.schema_registry.schema_count ;;
    *) echo "$r: unknown resource type"; continue ;;
  esac
  for metric in $metrics; do
    resp=$(printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY" \
      | curl -H @- -sS -w '\n%{http_code}' -G "https://api.${DD_SITE}/api/v1/query" \
          --data-urlencode "from=$(($(date +%s) - 900))" \
          --data-urlencode "to=$(date +%s)" \
          --data-urlencode "query=avg:${metric}{resource_id:${id}}")
    code=$(printf '%s' "$resp" | tail -1)
    [ "$code" = "200" ] || { echo "$r ($metric): query failed with HTTP $code"; continue; }
    echo "$r ($metric): $(printf '%s' "$resp" | sed '$d' | jq '.series | length') series"
  done
done
```

**If a resource has a non-empty series**, metrics are flowing for it. Congratulate the user.

**If a resource has no series yet**, let the user know metrics are still propagating - Confluent
Cloud metrics typically arrive within a few minutes, and an idle connector or ksqlDB cluster may report
little. Suggest checking back shortly, and report that resource as configured but not yet observed.

### Report Completion

After the verify step, post a completion status to the onboarding workflow API so the UI can
mark the agentic flow complete. `DD_WORKFLOW_ID` is the UUID of the Datadog user the credentials
belong to, which is how the UI correlates this callback. Resolve it and post in **one** command: many
agent runtimes start a fresh shell per command, so a variable assigned in an earlier one would be
empty here.

**Never print the `current_user` response**: it also carries the user's name, handle, and email. If no
JSON parser is available, skip this callback rather than installing one or blocking setup.

```bash
for f in .env.local .env; do [ -f "$f" ] || continue; for k in DD_SITE DD_API_KEY DD_APP_KEY; do eval "[ -n \"\${$k:-}\" ]" && continue; v=$(grep -E "^$k=" "$f" | head -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'']//;s/["'\'']$//'); [ -n "$v" ] && export "$k=$v"; done; done
DD_SITE='datadoghq.com'; export DD_SITE   # <- replace with the site confirmed in Phase 0.
# Explicit assignment, not ':=': a wrong non-empty DD_SITE in .env would otherwise survive.
: "${DD_API_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"; : "${DD_APP_KEY:?not set - run dd-account-setup (commercial sites) or supply it directly (government sites)}"
CONFLUENT_ACCOUNT_ID='<CONFLUENT_ACCOUNT_ID>'   # <- the account id from Phase 1 or Phase 2
: "${CONFLUENT_ACCOUNT_ID:?not set - substitute the Confluent account id from Phase 1 or Phase 2}"
dd_headers() { printf 'DD-API-KEY: %s\nDD-APPLICATION-KEY: %s\n' "$DD_API_KEY" "$DD_APP_KEY"; }
DD_WORKFLOW_ID="$(dd_headers | curl -H @- -fsS --connect-timeout 5 --max-time 20 \
  "https://api.${DD_SITE}/api/v2/current_user" | jq -er '.data.id')" \
  && dd_headers | curl -H @- -sS --connect-timeout 5 --max-time 20 -X POST \
  "https://api.${DD_SITE}/api/unstable/integration/confluent/workflow/agentic_saas_integration_setup" \
  -H "Content-Type: application/vnd.api+json" \
  -d '{
    "data": {
      "type": "integration_setup_status",
      "id": "'"${DD_WORKFLOW_ID}"'",
      "attributes": {
        "status": "finished",
        "step": "setup_complete",
        "metadata": {
          "confluent_account_id": "'"${CONFLUENT_ACCOUNT_ID}"'"
        }
      }
    }
  }'
```

Do not retry or block setup if this callback fails.

Give the user a short summary of what was set up:

- The Confluent Cloud account in Datadog (`CONFLUENT_ACCOUNT_ID`, and its display name if set) and
  whether it was newly registered or already existed.
- The resources registered, by type and ID, including any already registered (`409`) and any skipped
  (for example connectors when the key could not list them), and which Kafka clusters have
  `enable_custom_metrics` on.
- Which optional enhancements were configured (Schema Registry tags, Cloud Cost Management).
- The result of the metric check above. Never repeat the Confluent API secret or Schema Registry secret.

Once the account is registered, Datadog holds the credentials, so `~/.datadog-confluent.env` is no
longer needed. Offer to delete it (`rm -f ~/.datadog-confluent.env`); keep it only if the user plans to
re-run resource discovery soon.

Then provide these links (construct as `<APP_BASE>/...`, from the Phase 0 App URL table):

- **Confluent Cloud Integration tile**: `<APP_BASE>/integrations/confluent-cloud` -
  verify the integration is active and view the out-of-the-box dashboard.
- **Metrics Explorer**: `<APP_BASE>/metric/explorer?exp_metric=confluent_cloud.kafka.received_bytes` -
  confirm Kafka metrics are flowing.
- **Cloud Cost Management** (if enabled): `<APP_BASE>/cost` - Confluent spend appears
  within ~24 hours.

**Recommended next steps:**

- Create a monitor for Kafka consumer lag at `<APP_BASE>/monitors/create` using
  `confluent_cloud.kafka.consumer_lag_offsets` to alert on processing backlogs.
- Create a monitor on `confluent_cloud.kafka.cluster_load_percent` to track cluster utilization.
- If Schema Registry keys were added, verify Confluent-defined tags appear on metrics in the
  Metrics Explorer.

## Important Notes

- The Confluent Cloud API key must have the **Cloud resource management** scope for metrics
  collection. Cluster-scoped keys will not work.
- The **BillingAdmin** role is separate - only needed for Cloud Cost Management cost data.
- Resource IDs are case-sensitive and must match exactly what Confluent Cloud shows.
- `enable_custom_metrics` on Kafka clusters enables `custom.consumer_lag_offset` with extra
  per-partition tags; it may increase custom metric volume.
- Schema Registry credentials (`sr_api_key`, `sr_api_secret`) can be set at account creation
  (POST) or added later (PATCH). POST requires both fields together or neither; PATCH supports
  secret-only rotation but requires the existing key to be present.
- If the user wants to monitor additional resources later, they can re-run Phase 3 using the
  existing `CONFLUENT_ACCOUNT_ID` from `GET /accounts`.
