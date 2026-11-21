# LLM Observability: Node.js

Pick the env file first with `common-credentials.md`. Add the variables below to it, and write `DD_API_KEY` last, as that file describes.

Install — and **persist `dd-trace` into the dependency manifest the deploy installs from** (the one identified in SKILL.md Phase 1e, normally `package.json`). `npm install` / `yarn add` / `pnpm add` / `bun add` all write it to `package.json` by default, which is what you want:
```sh
npm install dd-trace
```
(substitute `yarn add` / `pnpm add` / `bun add` per the detected package manager). Do not pass `--no-save`, and confirm `dd-trace` appears in `package.json` afterward — a package present only in `node_modules` is missing from a clean deploy install and the deploy will crash with `Cannot find module 'dd-trace'`.

## Start the tracer (non-Next.js)

The tracer must load before any LLM library. Otherwise it cannot patch the LLM client, and the app sends no LLM spans.

### Tracer already started

If Phase 1d found that a preload or an existing `dd-trace` `.init(...)` already starts the tracer (for APM, for example), keep it unchanged and add no second start. A second `init()` is ignored, and a preload added in front of the app's own `init()` would run first and drop that call's options, such as its `service` name. Instead, add the missing variables from the next section to what the start path loads, then the key: `dd-trace` turns LLM Observability on when `DD_LLMOBS_ML_APP` or `DD_LLMOBS_AGENTLESS_ENABLED` is set. For an existing in-code init, you can instead add the `llmobs` option from the fallback below to that call. Either way, the variables must load before that init runs. An existing APM setup usually means a Datadog Agent exists, so check goal 4 below before you set agentless mode.

### Preferred: a preload in the start command

Add the preload to the persisted start command, wherever it lives: `package.json` scripts, `Dockerfile` `CMD`, compose `command` or `Procfile`. The preload starts the tracer before any app code runs, so import order does not matter, and no init code is needed. Pick the flag by module system:

- ESM (`"type": "module"` in `package.json`, an `.mjs` entry, or an entry that uses `import`): `node --import dd-trace/initialize.mjs server.js`. This flag starts the tracer and registers the loader that `dd-trace` needs to patch ES modules. `dd-trace/register.js` alone only registers the loader and never starts the tracer.
- CommonJS: `node --require dd-trace/init server.js`.

When the start command runs a wrapper instead of `node` (for example `tsx`, `ts-node` or `nodemon`), pass the same flag through `NODE_OPTIONS`:
```json
{ "scripts": { "start": "NODE_OPTIONS='--import dd-trace/initialize.mjs' tsx server.ts" } }
```

LLM Observability then starts from these variables, set in the env file (`common-credentials.md`) or the service's environment:
```
DD_LLMOBS_ENABLED=1
DD_LLMOBS_AGENTLESS_ENABLED=1
DD_LLMOBS_ML_APP=<suitable app name, e.g. project folder name>
DD_SITE=<site, default datadoghq.com>
DD_API_KEY=<key from common-credentials.md>
```

### Fallback: in-code init (CommonJS only)

Use this only when the start command cannot be edited.

> **Copy the package name and `.init({...})` option keys verbatim** — the package is `dd-trace` and the options are exactly `llmobs: { mlApp, agentlessEnabled }` as shown below. Don't paraphrase the package name or invent option keys from memory.

Never use in-code init in an ES module (`"type": "module"` or an `.mjs` entry). `require` is undefined there, imports are hoisted above any init statement, and `dd-trace` patches ES modules only through the `--import` loader. If an ESM app's start command cannot be edited, `NODE_OPTIONS='--import dd-trace/initialize.mjs'` must be set in the host's environment settings. Set it if you can; otherwise tell the user to set it, and report the setup as incomplete.

The init must run before any other application code, above every LLM import: as the very first line of the entry file, or in a separate `tracer.js` that the entry file requires first. It reads the same variables as the preload:
```javascript
require('dotenv').config(); // only when the settings come from an env file; must run before init

require('dd-trace').init({
    llmobs: {
        mlApp: process.env.DD_LLMOBS_ML_APP,
        agentlessEnabled: true,
    },
    site: process.env.DD_SITE,
    env: process.env.DD_ENV || "dev",
});
// ... then require everything else
```
If you add `dotenv`, persist it into `package.json` the same way as `dd-trace`.

### Load the env file before the tracer starts

`dd-trace` does not read `.env` files, and a preload reads its settings when the process starts, before a `require('dotenv').config()` in the app runs. Load the file in the start command, before the preload:
```sh
node --env-file-if-exists=.env --import dd-trace/initialize.mjs server.js
```
`--env-file-if-exists` skips a missing file, so the same command still starts in a deploy that sets the variables in the service's environment. `--env-file` stops Node.js when the file is missing. `--env-file-if-exists` needs Node.js 20.19+ or 22.9+, and an older Node.js refuses to start with it as a bad option. Read the version from `engines` in `package.json`, `.nvmrc`/`.node-version` or the `Dockerfile` base image. On an older or unknown version, preload `dotenv` instead and persist it into `package.json`: `node --require dotenv/config --import dd-trace/initialize.mjs server.js`. `dotenv/config` reads `.env`; set `DOTENV_CONFIG_PATH` for another file. Node.js rejects both env-file flags in `NODE_OPTIONS`, so with a wrapper, use the wrapper's own env-file option, or preload `dotenv` first: `NODE_OPTIONS='--require dotenv/config --import dd-trace/initialize.mjs'`. In container and hosted deploys, set the variables in the service's environment, for example with compose `env_file:` or the hosting platform's settings.

## Next.js

Look for (or create) `instrumentation.ts`/`.js` at the project root and add:
```typescript
export async function register() {
    if (process.env.NEXT_RUNTIME === 'nodejs') {
        const initializeImportName = 'dd-trace/initialize.mjs';
        await import(/* webpackIgnore: true */ initializeImportName as 'dd-trace/initialize.mjs')
    }
}
```
Also add `serverExternalPackages: ["<llm-sdk-package>"]` (e.g. `"@anthropic-ai/sdk"`, `"openai"`) to `next.config.js`. Set the five variables from the preload section above in the env file (`.env.local`). `register()` runs after Next.js loads its `.env*` files, so the tracer sees them with no extra loader. Don't start the tracer with a `NODE_OPTIONS` preload in Next.js: the preload starts before Next.js loads those files, so their settings never reach the tracer.

Check the `next` version in `package.json`. The snippet and config above are for Next.js 15 and later. Before Next.js 15 (13.x and 14.x), `register()` runs only when `next.config.js` sets `experimental: { instrumentationHook: true }`, so add it; otherwise the tracer never starts. On those versions the externals list is `experimental.serverComponentsExternalPackages` instead of `serverExternalPackages`, including for `"ai"` below.

After making these changes, run `npx tsc --noEmit` and add `// @ts-ignore` only for genuinely new errors introduced by the dd-trace types.

If the project already defines a `register()` export in `instrumentation.ts` (e.g. for other instrumentation), extend it rather than adding a second, competing `register()` export.

## Vercel AI SDK (`ai` package)

The LLM Observability integration requires `experimental_telemetry.isEnabled = true` to be explicitly set (not omitted) on every call to `generateText`, `streamText`, `generateObject`, `streamObject`, `embed`, `embedMany`, and any `tool.execute`. Grep for these call sites and fix each one; if `experimental_telemetry` is missing entirely, add `experimental_telemetry: { isEnabled: true }`. For Next.js apps using this SDK, also add `"ai"` to `serverExternalPackages`.

Follow-up reading for the user: [Vercel AI SDK integration docs](https://docs.datadoghq.com/integrations/vercel-ai-sdk/).

## Vercel-hosted projects: env var placement

On Vercel, add these to the project's env vars — Vercel dashboard for deployed environments, `.env.local` for local dev:

| Variable | Value |
|---|---|
| `DD_API_KEY` | From `common-credentials.md` |
| `DD_SITE` | e.g. `datadoghq.com` |
| `DD_LLMOBS_ENABLED` | `true` |
| `DD_LLMOBS_AGENTLESS_ENABLED` | `true` |
| `DD_LLMOBS_ML_APP` | A suitable ML app name (e.g. the Vercel project name) |

## Beyond SDK init: a well-formed trace

**Run this section even if `dd-trace`'s `init()` already exists in the project.** SDK init alone only turns on auto-instrumented `llm` spans — it does not by itself produce a session ID, a root span, or a RUM/APM link. Don't let Phase 1d's "SDK already present" finding stand in for this checklist.

Enabling the SDK produces auto-instrumented `llm` spans for supported providers, but a useful trace also needs a root span, session ID, and annotations. Work toward these four goals; the first two are always achievable, the last two depend on the environment:

| # | Goal | Always achievable? | What it requires |
|---|------|-------------------|-------------------|
| 1 | Well-formed LLMObs trace | Yes | SDK initialized; a root span with the right kind; nested child spans; `inputData`/`outputData` annotated |
| 2 | Agent Session ID | Yes | A stable `sessionId` on the root span, scoped to one conversation/operation — it propagates to children automatically |
| 3 | RUM session linking | Opt-in, needs RUM SDK | Correlating a RUM session with LLMObs works by forwarding the RUM session ID (`datadogRum.getInternalContext().session_id`) to the backend and using it *as* the `sessionId`. That is the only documented link, so it makes the LLMObs session span the whole browser session — several conversations in one browse collapse into one. Prefer the conversation-scoped `sessionId` from goal 2 by default; opt in only when that whole-browser grouping is acceptable |
| 4 | APM trace linking | Partial — always | `apm_trace_id` is set on every span automatically. Full navigable linking in the UI needs a dd-agent; without one the tag is present but the APM trace won't exist |

Detect what's achievable before instrumenting: check for `@datadog/browser-rum` on the frontend (goal 3, opt-in), and for `DD_AGENT_HOST`/`DD_TRACE_AGENT_URL`/an agent sidecar (goal 4). A `dd-trace` preload alone does not mean an agent exists. Never promise RUM or navigable APM linking that the detected environment doesn't support — state the gap and the upgrade path instead.

**Goal 4 config knob:** the setup above hardcodes agentless mode (`DD_LLMOBS_AGENTLESS_ENABLED=1`, or `agentlessEnabled: true` in code). Flip it to `0`/`false` whenever a dd-agent is detected — leave it `true` otherwise (the `apm_trace_id` tag is still set either way, but only becomes a navigable APM trace with an agent present).

## Adding spans

Obtain `llmobs` from the tracer that the preload, `instrumentation.ts` or the fallback init above already started — don't call `.init()` again:
```typescript
import tracer from 'dd-trace';
const { llmobs } = tracer;
```
(CommonJS: `const { llmobs } = require('dd-trace');` returns the same tracer instance.)

**`llmobs.trace()`** (preferred):
```typescript
const result = await llmobs.trace(
  { kind: 'agent', name: 'my_agent', sessionId },   // set sessionId here only
  async (span) => {
    llmobs.annotate(span, { inputData: userMessage });
    const res = await doWork(userMessage);
    llmobs.annotate(span, { outputData: res });
    return res;
  }
);
```

**`llmobs.wrap()`** for named functions:
```typescript
const instrumentedFn = llmobs.wrap({ kind: 'tool', name: 'my_tool' }, myFn);
```

**Annotation:**
```typescript
llmobs.annotate(span, {
  inputData: 'user message',   // string or messages array
  outputData: 'agent response',
  metadata: { key: 'value' },
});
```

**Session ID rule:** pass `sessionId` only to whichever `llmobs.trace()`/`llmobs.wrap()` call is the *root* span of the trace — typically `kind: 'agent'`, but `kind: 'workflow'` can be the root if the entry point has no direct LLM orchestration (see the LangChain.js/Vercel AI SDK notes below). Child spans created in the same async context inherit it automatically — never pass `sessionId` to a non-root span.

## Session name (optional display title)

`sessionId` has no companion "display name" field in the SDK — the UI otherwise shows a truncated session ID. If the application already has (or can cheaply derive) a human-readable label for the operation — a job type, a conversation topic, a ticket title — set it as a `session_name` tag on the same root span that carries `sessionId`, via `llmobs.annotate(span, { tags })`:

```typescript
const result = await llmobs.trace(
  { kind: 'agent', name: 'my_agent', sessionId },
  async (span) => {
    llmobs.annotate(span, { tags: { session_name: 'Playlist for Milo (cat)' } });
    return await doWork();
  }
);
```

Only set it once, on the root span — same rule as `sessionId`. Skip this if there's no natural label available; don't fabricate one from generic defaults (e.g. `` `session-${sessionId}` ``) since that's no more useful than the raw ID.

## Session ID intake by environment

Scope the `sessionId` to **one conversation/operation** — that is the unit the LLM Obs UI groups on. Don't default it to the browser/RUM session (see the opt-in below for why).

**Default — web app (conversation-scoped)** — the frontend owns a conversation ID: mint one when a conversation starts (a chat thread, a task), reuse it across that conversation's turns, and start a fresh one when the user begins a new conversation. Send it with each request under whatever key fits this project's existing request-body naming; read it on the backend with the same request-parsing accessor the project already uses for its other fields — do not assume Express's `req.body` (Fastify uses `request.body`, Next.js Route Handlers / Hono use `await req.json()`, etc.; see Framework-specific notes below for the accessor per framework):
```typescript
// Shown with Express's req.body — substitute the accessor your framework/route actually uses.
const sessionId = req.body.sessionId ?? req.headers['x-session-id'] ?? crypto.randomUUID();
const result = await llmobs.trace({ kind: 'agent', name: 'my_agent', sessionId }, async (span) => {
  llmobs.annotate(span, { inputData: req.body.message });
  const res = await doWork(req.body.message);
  llmobs.annotate(span, { outputData: res });
  return res;
});
```
Frontend side (show as instructions, never edit frontend files yourself in this reference):
```javascript
body: JSON.stringify({
  message: userMessage,
  // one ID per conversation, regenerated when the user starts a new one — NOT the RUM/browser session ID
  sessionId: conversationId,
})
```

**Opt-in — reuse the RUM session ID (enables the RUM↔LLMObs pivot)** — the only documented way to pivot between a RUM session and its LLMObs traces is to forward the RUM session ID and use it *as* the `sessionId`. The trade-off: the LLMObs session then spans the whole browser session, so multiple conversations in one browse collapse into a single LLMObs session. Choose this only when that grouping is acceptable (setting up the RUM SDK itself is out of scope for this skill):
```javascript
body: JSON.stringify({
  message: userMessage,
  sessionId: datadogRum.getInternalContext()?.session_id ?? crypto.randomUUID(),
})
```

**No web frontend (CLI / background job)** — reuse an existing per-operation identifier if the code already generates one before the pipeline runs (`jobId`, `requestId`, `taskId`, etc.) instead of minting a second ID; otherwise one UUID per invocation:
```typescript
const sessionId = jobId ?? crypto.randomUUID();
```

## Multi-agent pipelines and sub-agent orchestration

When one function invokes multiple distinct agents (or the same agent multiple times) as sequential stages of a single logical operation — a background job that runs a profiler agent, then a theme agent, then a scout agent, then a curator agent, for example — wrap the **entire orchestrating function once**. One root span per logical operation, not one per agent-run call:

```typescript
async function runPipeline(jobId: string) {
  return llmobs.trace({ kind: 'workflow', name: 'playlist_pipeline', sessionId: jobId }, async (span) => {
    const vibe = await profilerAgent.run(...);
    const theme = await themeAgent.run(...);
    const candidates = await scoutAgent.run(...);
    const selections = await curatorAgent.run(...);
    llmobs.annotate(span, { inputData: jobId, outputData: selections });
    return selections;
  });
}
```

Use `kind: 'workflow'` here, not `'agent'` — `runPipeline` doesn't call an LLM directly, it orchestrates other agents that do (see the decision table below). Each agent-run call auto-instruments as a nested `agent` span under this one root; all four land on a single trace and inherit `sessionId` through the same async context. This applies to any auto-instrumented agent framework (LangChain.js/LangGraph.js, Vercel AI SDK, OpenAI Agents JS) — the rule is about *where* you put the one enclosing span, not which framework is calling the LLM.

**Anti-pattern — produces one trace/session per stage instead of one per operation:** calling each stage's agent-run function directly inside the pipeline function with no enclosing `llmobs.trace()`/`llmobs.wrap()`. Without an active span in scope, there's nothing for the auto-instrumentation to attach each call to, so every stage gets its own root span — a separate trace, and (with no explicit `sessionId`) a separate session in the UI. This is easy to miss because the SDK is "enabled" and traces do show up — they're just fragmented one-per-agent instead of grouped one-per-operation. Symptom in the trace list: what should be one conversation instead shows up as N separate single-agent conversations (e.g. `curator_agent`, `scout_agent`, `theme_agent`, `profiler_agent` each listed on their own, instead of one session containing all four).

**Detecting this while verifying:** if a single logical operation (one job, one request, one pipeline run) produces more than one root span/trace in the LLM Obs UI, the fix is to add one enclosing `kind: 'workflow'`/`'agent'` span around the whole orchestrating function — not to sprinkle the same `sessionId` across each stage's own root span. Matching `sessionId`s across separate traces groups them into one *session*, but a single trace with nested children is the stronger, simpler, and intended fix; prefer it whenever the stages share one caller.

**A deliberate split is fine — the anti-pattern is the *accidental* one.** The failure above is unintentional fragmentation: no enclosing span *and* no shared `sessionId`, so the trace splits and the pieces never regroup. When the agents are genuinely independent or long-lived — not tight sequential stages of one operation — a separate trace per agent is a legitimate call; put the *same* `sessionId` on each root span and they still read as one session in the UI. One combined trace stays the default — it's simpler, and it's usually what a single operation *is* — so reach for per-agent traces when you actually want them, not because the enclosing span got dropped.

## Span kind decision table

| Code pattern | Span kind |
|---|---|
| Top-level function orchestrating LLM calls and tools | `agent` |
| Multi-step pipeline not directly calling LLMs | `workflow` |
| Function calling an LLM directly (only if not auto-instrumented) | `llm` (manual) |
| Function wrapping a capability (web search, calculator, DB) | `tool` |
| Pure data transformation / business logic | `task` |
| RAG / vector search | `retrieval` |
| Text embedding generation | `embedding` |

**Auto-instrumented providers** (no manual `llm` span needed): OpenAI JS SDK, Anthropic JS SDK, LangChain.js/LangGraph.js, Vercel AI SDK. Wrap the *calling* function with `kind: 'tool'` or `kind: 'agent'` instead of the provider call itself.

**Never add spans to:** web routing/middleware, data model/config classes, DB/cache clients, logging/metrics utilities, test files, pure utility functions.

## Framework-specific notes

- **LangChain.js / LangGraph.js:** auto-instrumented; wrap the top-level invoking function with `llmobs.trace({ kind: 'workflow', sessionId })` — `sessionId` is valid here only because this call *is* the root span; don't add it to any nested `kind: 'workflow'` call.
- **Vercel AI SDK:** auto-instrumented; wrap the calling function with `llmobs.trace({ kind: 'workflow', sessionId })` (same root-span caveat) — do not wrap individual `generateText()`/`streamText()` calls, they're already traced. Also requires `experimental_telemetry.isEnabled = true` per call — see the Vercel AI SDK section above.
- **OpenAI Agents JS:** auto-instrumented; wrap the function calling `Runner.run()` with `llmobs.trace({ kind: 'agent', sessionId })`. If that function calls more than one agent/`Runner.run()` (a multi-stage pipeline), wrap it **once**, enclosing every stage — see "Multi-agent pipelines and sub-agent orchestration" above; don't wrap each stage's call separately. Use `kind: 'workflow'` instead of `'agent'` when the wrapper orchestrates multiple agents without calling an LLM itself.
- **Express / Fastify / Hono / Koa:** session ID from `req.body.sessionId` or `req.headers['x-session-id']` with `crypto.randomUUID()` fallback.
- **Next.js App Router:** session ID from the request body in Route Handlers/API routes; the SDK itself still initializes in `instrumentation.ts` as shown above. **Pages Router:** same intake, and init in `instrumentation.ts` as well — never in `_app.tsx`, which is a browser bundle.
- **CLI / background job:** prefer an existing per-operation ID if the code already tracks one (`const sessionId = jobId ?? crypto.randomUUID()`); otherwise `crypto.randomUUID()` per run. RUM not applicable.

## Reporting goal status

Alongside the standard report shape in `common-verify-report.md`, summarize goal status in the human-readable message, e.g.:
```
✓ Well-formed LLMObs trace
✓ Agent Session ID (conversation-scoped; UUID fallback)
~ RUM session linking: not applicable (no RUM SDK detected)
~ APM trace linking: apm_trace_id set, but not navigable (no dd-agent detected)
```
