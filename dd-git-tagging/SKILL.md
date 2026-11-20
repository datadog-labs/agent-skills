---
name: dd-git-tagging
description: Datadog Git metadata tagging for one service. Checks whether the service's traces already carry the Git commit and repository URL, and if not, sets DD_GIT_COMMIT_SHA and DD_GIT_REPOSITORY_URL where the service is built and launched (plus matching DD_TAGS entries only for services that send traces without an Agent), then confirms tagged traces arrive. This is the telemetry half of Datadog source code integration, which links traces, errors, and profiles to the exact code that ran, and which services built on it, such as Live Debugger, rely on. Works for any language Datadog's tracers support. Use when the user's Git provider is connected to Datadog but code links, inline source, or "view in GitHub" don't show up; when Datadog's source code setup page says a service isn't tagged; when asked to add Git commit or repository info to traces; or when another skill needs telemetry tagged with Git metadata. Not for connecting GitHub to Datadog (use dd-github-onboarding) or installing a tracer.
metadata:
  version: "0.1.0"
  author: datadog-labs
  repository: https://github.com/datadog-labs/agent-skills
  tags: datadog,source-code-integration,git,apm,tagging
  alwaysApply: "false"
---

# Datadog Git metadata tagging

[Source code integration](https://docs.datadoghq.com/source_code/) has two halves: connecting the Git provider to Datadog, and tagging telemetry with the commit and repository it came from. This skill does the second half for one service. A service counts as tagged when its spans carry both `git.commit.sha` and `git.repository_url`.

**Inputs:** the service name and, optionally, the env. Use them if the caller passed them. Otherwise read `DD_SERVICE` and `DD_ENV` from the project's config; with several services and no name given, ask which one.

## Rules

- Change only how the two Git values reach the service. No unrelated edits, and no notes or docs files: explanations go in the report.
- Compute both values when the service builds or launches, never as literals: a committed SHA is wrong after the next commit.
- Strip credentials from the repository URL so a `user:token@` never lands in an image or in telemetry.
- Done means the check below passes. If you can't run or deploy the service, report the changes and how to confirm them.

## 1. Check

```bash
python3 -I -B <this-skill-dir>/scripts/check_git_tags.py --service <service> [--env <env>] --json
```

It authenticates with the signed-in `pup` session, or with `DD_API_KEY`, `DD_APP_KEY`, and `DD_SITE` when pup isn't signed in. Without either, run the same search with the Datadog MCP `aggregate_spans` tool (count, last 15 minutes; widen the window only if that finds nothing, since older spans are searchable only when indexed), using the `query` the script would print:

```
@_top_level:1 service:<service> [env:<env>] (git.commit.sha:* OR @git.commit.sha:* OR @_dd.git.commit.sha:*) (git.repository_url:* OR @git.repository_url:* OR @_dd.git.repository_url:*)
```

A non-zero count means tagged; zero means search again without the two Git clauses to tell **untagged** from **no spans**.

| Exit | Status | Do |
|---|---|---|
| 0 | `tagged` | Nothing to change. Report the commit and repository it found, and stop. If `source` is `tracer`, the service sends both values but Datadog hasn't turned them into `git.*` tags, which happens when traces skip the Agent: add the agentless `DD_TAGS` entries from step 3. |
| 2 | `untagged` | Spans arrive without one or both values (`has_commit_sha`, `has_repository_url`). Go to step 2. |
| 3 | `no_spans` | Nothing from this service in the last 15 minutes, nor in a day of indexed spans. If the service isn't instrumented or running yet (for example, another skill is setting it up), go to step 2 and check again once it runs. Otherwise confirm the service name and env with the user before editing. |
| 1 | error | Report the message. A 403 means the key or session can't read APM data. |

## 2. Inspect

Find every place the service is built and started: Dockerfile `ARG`/`ENV`/`CMD`, compose, Kubernetes or Helm manifests, platform config (Procfile, `app.yaml`, `fly.toml`, `render.yaml`), CI workflows that build images or deploy, and launch scripts (`package.json` `scripts.start`, a gunicorn or uvicorn command).

Look for existing `DD_GIT_COMMIT_SHA` and `DD_GIT_REPOSITORY_URL`. If they exist but the check says untagged, they aren't reaching the running process. Common causes:

- A Dockerfile `ARG` with no matching `ENV`, so the value exists at build time only.
- CI sets the variable in the build job but the deploy doesn't pass it to the container.
- A hardcoded value or a placeholder like `<repo-url>`.
- The `.git` directory is excluded from the build (`.dockerignore`), so a `git` call inside the image returns nothing.
- The service sends traces without an Agent (`DD_AGENTLESS_ENABLED=true`) and sets only `DD_GIT_*`. Without an Agent, Datadog keeps the tracer's raw values but never turns them into `git.*` tags, so the values must also be in `DD_TAGS`.

If every build and launch path already sets both `DD_GIT_*` variables correctly (and, without an Agent, their `DD_TAGS` entries), change nothing: the running service was built or started another way, or before this config existed. Skip to step 5 and tell the user to rebuild or redeploy through that path.

## 3. Configure

Get the values from the checkout:

```bash
git rev-parse HEAD
git config --get remote.origin.url | sed -E 's#://[^/@]*@#://#'
```

Then set both in the environment of the running process, wherever the project defines it, matching what the project already uses. Have the build or deploy compute them each time; never paste the values in by hand:

- **Host or VM:** the service's systemd unit (`Environment=`, or an `EnvironmentFile=` the deploy writes), or the env file its process manager loads.
- **Kubernetes or Helm:** the container's `env` in the manifest or chart values, filled by the CI step that deploys.
- **Docker:** an `ARG` and an `ENV` for each in the final (runtime) stage, so the build value carries into the running container:
  ```dockerfile
  ARG DD_GIT_REPOSITORY_URL
  ARG DD_GIT_COMMIT_SHA
  ENV DD_GIT_REPOSITORY_URL=${DD_GIT_REPOSITORY_URL} DD_GIT_COMMIT_SHA=${DD_GIT_COMMIT_SHA}
  ```
  Pass them at build time: `--build-arg DD_GIT_COMMIT_SHA=$(git rev-parse HEAD) --build-arg DD_GIT_REPOSITORY_URL=$(git config --get remote.origin.url | sed -E 's#://[^/@]*@#://#')`, and update every script, compose `build.args`, or CI step that builds the image.
- **CI builds:** use the provider's variables instead of `git`, which may be a shallow or detached checkout. GitHub Actions: `${{ github.sha }}` and `${{ github.server_url }}/${{ github.repository }}`. GitLab CI: `$CI_COMMIT_SHA` and `$CI_PROJECT_URL`. CircleCI: `$CIRCLE_SHA1` and `$CIRCLE_REPOSITORY_URL`.
- **Platforms that inject the commit:** map their variable, for example Vercel `VERCEL_GIT_COMMIT_SHA` or Render `RENDER_GIT_COMMIT`; set the repository URL as a plain config value since it doesn't change.
- **No build or deploy config:** compute both in the launch command itself:
  ```bash
  sha="$(git rev-parse HEAD)" url="$(git config --get remote.origin.url | sed -E 's#://[^/@]*@#://#')"; DD_GIT_COMMIT_SHA="$sha" DD_GIT_REPOSITORY_URL="$url" <existing start command>
  ```

No code change is needed. Set `DD_TAGS` only when the service sends traces without an Agent (`DD_AGENTLESS_ENABLED=true`, see step 2): there, add `git.commit.sha:<sha>,git.repository_url:<url>` from the same values, appending to an existing `DD_TAGS` rather than replacing it. With an Agent, leave `DD_TAGS` alone.

## 4. Confirm

Restart the service with the new config (or rebuild the image), send it a few requests, and wait for tagged spans:

```bash
python3 -I -B <this-skill-dir>/scripts/check_git_tags.py --service <service> [--env <env>] --from now-15m --wait 300 --json
```

If the service only runs through a deploy you can't trigger, skip this and tell the user the change applies from the next deploy, with the check command to run afterward.

## 5. Report

1. One line first: `Tagged: <service> (env <env>) spans carry commit <sha> from <repository_url>.`, or `Already tagged: …`, or `Not verified: <reason>. <What to do>.`
2. Each changed file, with why it's needed.
3. If the Git provider isn't connected to Datadog yet, say that code links also need that half, and suggest `dd-github-onboarding` for GitHub.
