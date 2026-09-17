# LLM Observability: Node.js

Provision `DD_API_KEY` first via `common-credentials.md` if you haven't already.

Install — and **persist `dd-trace` into the dependency manifest the deploy installs from** (the one identified in SKILL.md Phase 1e, normally `package.json`). `npm install` / `yarn add` / `pnpm add` / `bun add` all write it to `package.json` by default, which is what you want:
```sh
npm install dd-trace
```
(substitute `yarn add` / `pnpm add` / `bun add` per the detected package manager). Do not pass `--no-save`, and confirm `dd-trace` appears in `package.json` afterward — a package present only in `node_modules` is missing from a clean deploy install and the deploy will crash with `Cannot find module 'dd-trace'`.

## In-code init (non-Next.js)

> **Copy the package name and `.init({...})` option keys verbatim** — the package is `dd-trace` and the options are exactly `llmobs: { mlApp, agentlessEnabled }` as shown below. Don't paraphrase the package name or invent option keys from memory. Adapt only the module *mechanism* to the project's module system: the CommonJS `require('dd-trace').init(...)` form is below; a project with `"type": "module"` in `package.json` or an `.mjs` entrypoint must use an ESM form instead (Node's `--import dd-trace/initialize.mjs` via `NODE_OPTIONS`, or `import tracer from 'dd-trace'` — both shown later in this file), because `require` is undefined in an ES module and forcing it there crashes at startup with `require is not defined`.

Must run before any other application code, typically as the very first line of the entry file or in a separate `tracer.js` required first:
```javascript
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

Env vars (`.env`/`.env.local`):
```
DD_API_KEY=<key from common-credentials.md>
DD_LLMOBS_ML_APP=<suitable app name, e.g. project folder name>
DD_SITE=<site, default datadoghq.com>
```

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
Also add `serverExternalPackages: ["<llm-sdk-package>"]` (e.g. `"@anthropic-ai/sdk"`, `"openai"`) to `next.config.js`. Additionally set `DD_LLMOBS_ENABLED=true` and `DD_LLMOBS_AGENTLESS_ENABLED=true` in the env file. After making these changes, run `npx tsc --noEmit` and add `// @ts-ignore` only for genuinely new errors introduced by the dd-trace types.

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

## Alternative: no code change

Use `NODE_OPTIONS` in `package.json` scripts:
```json
{ "scripts": { "start": "NODE_OPTIONS='--import dd-trace/initialize.mjs' node app.js" } }
```
For Next.js: `"start": "NODE_OPTIONS='--import dd-trace/initialize.mjs' next start"`, `"dev": "NODE_OPTIONS='--import dd-trace/initialize.mjs' next dev"`.

Note: when running an app inline for a one-off manual test (not via its normal start script), `.env` values won't be picked up automatically — pass them on the command line directly:
```sh
DD_LLMOBS_ENABLED=1 DD_LLMOBS_ML_APP=<app-name> DD_API_KEY=<key> DD_LLMOBS_AGENTLESS_ENABLED=1 DD_SITE=datadoghq.com pnpm dev
```

## Beyond SDK init: a well-formed trace

**Run this section even if `dd-trace`'s `init()` already exists in the project.** SDK init alone only turns on auto-instrumented `llm` spans — it does not by itself produce a session ID, a root span, or a RUM/APM link. Don't let Phase 1d's "SDK already present" finding stand in for this checklist.

Enabling the SDK produces auto-instrumented `llm` spans for supported providers, but a useful trace also needs a root span, session ID, and annotations. Work toward these four goals; the first two are always achievable, the last two depend on the environment:

| # | Goal | Always achievable? | What it requires |
|---|------|-------------------|-------------------|
| 1 | Well-formed LLMObs trace | Yes | SDK initialized; a root span with the right kind; nested child spans; `inputData`/`outputData` annotated |
| 2 | Agent Session ID | Yes | A stable `sessionId` on the root span — it propagates to children automatically |
| 3 | RUM session linking | Only with RUM SDK | RUM session ID (`datadogRum.getInternalContext().session_id`) passed to the backend as the LLMObs `sessionId`. Without RUM, fall back to a UUID — linking can be added later without restructuring |
| 4 | APM trace linking | Partial — always | `apm_trace_id` is set on every span automatically. Full navigable linking in the UI needs a dd-agent; without one the tag is present but the APM trace won't exist |

Detect what's achievable before instrumenting: check for `@datadog/browser-rum` on the frontend (goal 3), and for `DD_AGENT_HOST`/`DD_TRACE_AGENT_URL`/`NODE_OPTIONS=--require dd-trace/init`/an agent sidecar (goal 4). Never promise RUM or navigable APM linking that the detected environment doesn't support — state the gap and the upgrade path instead.

**Goal 4 config knob:** the init snippets above hardcode `agentlessEnabled: true`. Flip it to `false` whenever a dd-agent is detected — leave it `true` otherwise (the `apm_trace_id` tag is still set either way, but only becomes a navigable APM trace with an agent present).

## Adding spans

Obtain `llmobs` from the already-initialized tracer (the `require('dd-trace').init(...)` from "In-code init" above) — don't call `.init()` again:
```typescript
import tracer from 'dd-trace';
const { llmobs } = tracer;
```
(CommonJS: same tracer instance returned by the `require('dd-trace').init(...)` call above — `const { llmobs } = require('dd-trace');`)

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

**RUM present** — default the LLMO session ID to the RUM session ID. The frontend forwards it to the backend alongside the rest of the request payload, under whatever key name fits this project's existing request-body naming convention; read that value on the backend using the same request-parsing accessor the project already uses for the rest of that request's fields — do not assume Express's `req.body` (Fastify uses `request.body`, Next.js Route Handlers / Hono use `await req.json()`, etc.; see Framework-specific notes below for the accessor per framework):
```typescript
// Shown with Express's req.body — substitute the accessor your framework/route actually uses.
const sessionId = req.body.sessionId;
const result = await llmobs.trace({ kind: 'agent', name: 'my_agent', sessionId }, async (span) => {
  llmobs.annotate(span, { inputData: req.body.message });
  const res = await doWork(req.body.message);
  llmobs.annotate(span, { outputData: res });
  return res;
});
```
Frontend side (show as instructions, never edit frontend files yourself in this reference — setting up the RUM SDK itself is out of scope for this LLM Observability skill):
```javascript
body: JSON.stringify({
  message: userMessage,
  sessionId: datadogRum.getInternalContext()?.session_id ?? crypto.randomUUID(),
})
```

**Web framework, no RUM** — accept from caller, UUID fallback (same caveat: use the project's actual request accessor, not necessarily `req.body`):
```typescript
const sessionId = req.body.sessionId ?? req.headers['x-session-id'] ?? crypto.randomUUID();
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
- **Next.js App Router:** session ID from the request body in Route Handlers/API routes; the SDK itself still initializes in `instrumentation.ts` as shown above. **Pages Router:** same intake, init via `NODE_OPTIONS` — never in `_app.tsx`, which is a browser bundle.
- **CLI / background job:** prefer an existing per-operation ID if the code already tracks one (`const sessionId = jobId ?? crypto.randomUUID()`); otherwise `crypto.randomUUID()` per run. RUM not applicable.

## Reporting goal status

Alongside the standard report shape in `common-verify-report.md`, summarize goal status in the human-readable message, e.g.:
```
✓ Well-formed LLMObs trace
✓ Agent Session ID (UUID fallback — RUM upgrade path noted)
~ RUM session linking: not available (no RUM SDK detected)
~ APM trace linking: apm_trace_id set, but not navigable (no dd-agent detected)
```
