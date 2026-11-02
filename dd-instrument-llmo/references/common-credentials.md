# Credentials and API keys

Used by the LLM Observability reference files (`llmobs-python.md` / `llmobs-nodejs.md`). Use only the local tool named below — do not invent tool names.

## API key (`CreateApiKey`)

`DD_API_KEY` is required for LLM Observability (dd-trace / ddtrace in agentless mode — see `llmobs-python.md` / `llmobs-nodejs.md`).

```
CreateApiKey(
  name: string  REQUIRED  — display name for the API key (e.g. "<project-folder-name> - LLM Observability")
)
```

On success it returns `{ apiKey, name }`.

Ask the user to confirm before creating a new org-level API key on their behalf. If the call fails (e.g. insufficient scope), ask the user to create a key manually at `https://app.datadoghq.com/organization-settings/api-keys` and paste it in. Do not proceed until they confirm.

Never log or print the key value in your final report.

## Provisioning `DD_API_KEY` into a local env file

### Identify the env file

Scan the project root for env files in this order and use the **first one found**:

`.env.local` → `.env.development.local` → `.env.development` → `.env`

If none exist, choose which file to **create** based on framework:
- Next.js or Create React App → create `.env.local`
- All other projects → create `.env`

### Check for an existing key

Read the chosen env file (if it exists) and check whether `DD_API_KEY` (or `DATADOG_API_KEY`) is already set to a non-empty value. If a non-empty key is already present, skip key creation and reuse it.

### Write the key

Upsert `DD_API_KEY=<key>` into the file identified above (create it if needed). Preserve all existing lines — only add or replace the `DD_API_KEY` line.

If a `.gitignore`/global-ignore rule blocks you from writing directly to an env file, use shell redirection (`echo "VAR=value" >> .env.local`) instead of a file-write tool that respects ignore rules.
