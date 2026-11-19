# Node.js (`dd-trace`)

Node.js 18+.

## Install

`dd-trace` **6.20.1+** or **5.131.1+**: the floors for agentless mode, log submission, and a security fix.

- Not installed: `npm install dd-trace@latest` (or the project's `yarn add`/`pnpm add`). It must land in `dependencies`, not `devDependencies`: production installs skip dev dependencies, so the deployed service would start without the tracer.
- Below the floor: upgrade within its major (`dd-trace@^5.131.1` or `^6.20.1`) and say so in the report.

## Load it first

Prefer a launch flag, on every launch command:

| Project | Command |
|---|---|
| CommonJS | `node --require dd-trace/init server.js` |
| ESM, Node 20.6+ | `node --import dd-trace/initialize.mjs server.js` |
| ESM, older Node | `node --loader dd-trace/loader-hook.mjs --require dd-trace/init server.js` |
| ts-node | `node --require dd-trace/init -r ts-node/register src/main.ts` |
| tsx | keep the runner: `NODE_OPTIONS="--require dd-trace/init" tsx src/main.ts` |
| Compiled or bundled | the flag on the output file, plus `--enable-source-maps` so logpoints map to `.ts` lines |

Launch command not editable: `NODE_OPTIONS="--require dd-trace/init"`. Bundlers must leave `dd-trace` external: it instruments libraries by hooking module loading at runtime, which bundled code skips.

## Logs

If the app logs through one of the common logging libraries (`winston`, `pino`, or `bunyan` in `package.json`), `dd-trace` sends those logs to Datadog for you. Set `DD_AGENTLESS_LOG_SUBMISSION_ENABLED=true` too: agentless mode already turns it on, but the setting makes it visible in the config.

If the app only uses `console.log`, those lines aren't sent. Don't add a logging library yourself, since that changes the app's own code; suggest Pino or Winston in the report.

## Launch command without deploy config

With no deploy config, put the settings in `scripts.start`, along with the Git variables `dd-git-tagging` adds:

```json
"start": "DD_SERVICE=<service> DD_ENV=<env> DD_SITE=<site> DD_AGENTLESS_ENABLED=true DD_DYNAMIC_INSTRUMENTATION_ENABLED=true DD_AGENTLESS_LOG_SUBMISSION_ENABLED=true node --env-file-if-exists=.env --require dd-trace/init server.js"
```

`dd-trace` doesn't read `.env` itself; `--env-file-if-exists` (Node 22.9+ or 20.19+) loads the key. On Node 20.6+ use `--env-file=.env` and note the file must exist. Node 18 has neither flag: drop it and load the file in the shell first (`key=$DD_API_KEY; [ -f .env ] && set -a && . ./.env && set +a; [ -n "$key" ] && export DD_API_KEY="$key"; node --require dd-trace/init server.js`), keeping a key already in the environment. If the key lives in another env file (step 2 of SKILL.md), load that file instead of `.env`. Don't add a `dotenv` import above the tracer: anything imported before it loads uninstrumented.

## Check

The startup `DATADOG TRACER CONFIGURATION` line shows `agent_url` even when agentless; ignore it and check `service`, `env`, `version`. `connect ECONNREFUSED 127.0.0.1:8126` means `DD_AGENTLESS_ENABLED` isn't reaching the process. A missing key doesn't stop startup, only sending.
