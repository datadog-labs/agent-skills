# Credentials and API keys

Used by the LLM Observability reference files (`llmobs-python.md` / `llmobs-nodejs.md`). `DD_API_KEY` is required for LLM Observability in agentless mode. Never print the key value, never put it in code, and never write it to a file that git tracks, because a tracked file puts the key in version control.

Order of work: pick the env file, add the other variables, and write the key last.

## Use the environment's tools when it has them

Your environment may provide a tool that scans env files and a tool that writes `DD_API_KEY` into an env file. When it does, use them instead of the git commands below. The scan tool reports, for each env file, whether git tracks it and which variables have a value; the writer checks the tracked status again before it writes. If a git command is denied, use those tools; a denied command does not mean "git cannot tell".

## Identify the env file

Scan the project root for env files in this order:

`.env.local` → `.env.development.local` → `.env.development` → `.env`

If one of them already has a non-empty `DD_API_KEY` (or `DATADOG_API_KEY`), for example the file that `dd-account-setup` wrote, use that file. Otherwise use the first one found. If none exist, choose which file to **create** based on framework:
- Next.js or Create React App → create `.env.local`
- All other projects → create `.env`

This must be the file that the start path loads before the tracer starts (see "Load the env file before the tracer starts" in the runtime reference).

## Check that git does not track the file

Without the environment's tools, run this before you write to the file:

```sh
git ls-files --error-unmatch -- <env file>
```

- Exit status 1 means git does not track the file. Outside a git repository the command fails with "not a git repository"; treat that as untracked too.
- Exit status 0 means git tracks the file. Any other failure means git cannot tell. In both cases, use or create another env file that the start path loads and git does not track, for example `.env.local` for Next.js. If there is no such file, tell the user to set the key in the service's environment, and write neither a key nor a placeholder into the tracked file.

If `git check-ignore -q -- <env file>` exits 1, the file is not ignored: add it to `.gitignore` before you write the key.

## Add the other variables without reading the file

The env file may already hold a key, so don't open it with a read or edit tool. Check which names it sets with the scan tool, or with `grep -c '^NAME=.' <env file>`, which prints a count and not the value. Append only the missing names:

```sh
[ -s <env file> ] && [ -n "$(tail -c1 <env file>)" ] && echo >> <env file>
printf '%s\n' 'DD_LLMOBS_ENABLED=1' 'DD_LLMOBS_ML_APP=<app name>' >> <env file>
```

The first line adds a newline when the file does not end with one. Without it, the first appended line joins the file's last line.

## Write the key

Use the first source that applies:

1. **The env file already has a non-empty `DD_API_KEY` (or `DATADOG_API_KEY`).** Keep it as is. Do not replace it.
2. **Your environment provides a tool that writes `DD_API_KEY` into an env file.** Call it now, after the other variables are in the file, and follow its result. Don't read or edit the file after it holds the key.
3. **The task environment has `DD_API_KEY` (or `DATADOG_API_KEY`).** Run the newline check above, then append it with `printf 'DD_API_KEY=%s\n' "${DD_API_KEY:-$DATADOG_API_KEY}" >> <env file>`, so the value never appears in your output. If the file lacks `DD_SITE`, append it the same way from `${DD_SITE:-datadoghq.com}`.
4. **Otherwise**, append the placeholder `DD_API_KEY=<your-api-key>` to the untracked env file. Tell the user to replace it with a key from `https://<app host>/organization-settings/api-keys`, or to run `dd-account-setup` and then rerun this skill. Report the setup as incomplete until the key is set.

The app host is `app.datadoghq.com` for `datadoghq.com` (US1), `app.datadoghq.eu` for `datadoghq.eu` (EU1), `app.ddog-gov.com` for `ddog-gov.com` (US1-FED), and the site itself for every other site (for example `us5.datadoghq.com`).

Change nothing else in the file. If `grep -c '^DD_API_KEY=$' <env file>` prints a number above 0, the file has an empty `DD_API_KEY=` line. Let the writer tool fill it, or remove that one line before you append, so the file ends up with one `DD_API_KEY` line.

If a `.gitignore`/global-ignore rule blocks you from writing directly to an env file, use shell redirection (`printf … >> <env file>`) instead of a file-write tool that respects ignore rules.
