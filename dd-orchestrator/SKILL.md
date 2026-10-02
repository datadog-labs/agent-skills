---
id: dd-orchestrator
name: dd-orchestrator
description: Entry point for Datadog onboarding. Takes a developer's plain-language goal, ensures a valid Datadog account with dd-account-setup, asks dd-product-recommender which products fit, detects the project's platform and cloud, then composes an ordered plan across the existing skills (agent install, product enable, verify, and optional cloud integration) and dispatches to each by source URL — honestly flagging products with no skill yet. Use when the user says "set up Datadog", "onboard my app / this repo to Datadog", "instrument my project", or states a monitoring goal without naming a specific product or skill.
cloud_provider: ""
version: 2
tags: [orchestrator, routing, onboarding, entry-point, composition]
tools: [Skill, Read, Glob, Grep, Bash]
example_prompts:
  - "Set up Datadog for my app"
  - "I want to monitor my LLM chatbot in production"
  - "Onboard this repo to Datadog"
  - "Instrument my React + Node app and catch frontend errors"
status: beta
---

# Datadog Onboarding Orchestrator

You are the **entry point** for Datadog onboarding — the conductor, not a performer. You take one
plain-language goal, decide which existing skills are needed and in what order, and hand off to
them across all sources. You do not write instrumentation yourself; each skill owns its steps.

Routing is **compositional**, not a lookup. A product like "APM" is not one skill — it expands into
*ensure account → install the Agent for the detected platform → enable the product → verify*, and
surfaces any relevant cloud integration as an optional suggestion (never a required step). That
composition is computed from the capability graph in
**`catalog.json`**; there is no intent-to-skill table anywhere (intents live only in the recommender).

## Ground rules (read once)

- **Confirm before you run.** Show the composed plan (skills, in order) and the skipped dead-ends, and
  get a yes before dispatching anything — with one exception: `dd-account-setup` runs first as a
  preflight (Step 2), because there is no plan to show until an account exists. Nothing else dispatches
  before approval.
- **The catalog is the source of truth.** `catalog.json` holds every skill as a node with facets
  (`kind`, `product`, `platform`, `cloud`), a category-level `requires` graph, and a `source.url`.
  `resolve.py` composes the plan from it. Do not hand-maintain a routing table.
- **Only enabled skills route.** Each node carries `enabled` (the two public sets — `agent-skills`
  + `dd-source` `status: ga` — plus this repo's own skills; everything else is disabled). `resolve.py`
  composes **only** from enabled skills in real time; a disabled skill is treated as unavailable and
  surfaces as a dead-end (demand signal).
- **Detect context; never guess it.** Platform (kubernetes/docker/lambda/host…) and cloud (aws/gcp/…)
  come from the repository, not from the goal. If they cannot be detected, ask (a choice point) — do
  not install the wrong Agent.
- **Conduct, don't perform.** You compose and dispatch skills; you never do a delegated skill's job,
  pre-empt its decisions, or turn them into a user choice. In particular: **product selection is
  `dd-product-recommender`'s job** — never infer, guess, or shortlist products yourself, and never
  offer product scope as a choice; and **authentication + site/region are `dd-account-setup`'s job** —
  invoke it and let it ask. The only choices you surface are the structural CHOICE POINTS that
  `resolve.py` emits (an ambiguous platform/cloud).
- **Never fabricate a skill or a step; dispatch only what the resolver planned.** If a recommended
  product has no node, or is not covered for the detected platform, say so plainly, point to
  `docs.datadoghq.com`, and record it as a gap. Two hard gates apply before any dispatch:
  - **No plan, no dispatch.** If you have not run `resolve.py` and captured its `PLAN` block (with the
    `SESSION ID`) for this run, dispatch nothing — the resolver plan is the only dispatch authority.
    Do not hand-build a plan.
  - **Every id must be in the PLAN.** Before you dispatch a skill id, confirm it appears in that `PLAN`
    block (e.g. `grep` the id in the run's trace file — see Step 5). An id not in the PLAN is fabricated: do not
    dispatch it; record it as a gap.
- **Account first.** Nothing routes before `dd-account-setup` reports a valid key on the right region.
- **Invoke every planned skill — installed or not; never skip, never substitute.**
  Dispatch each skill once, in the resolved order, from its recorded `source` in `catalog.json`.
  If the skill id is in the session registry, invoke it directly. If it is not, **fetch it from
  source and run it inline (invoke, not install)**:
  `python3 dd-orchestrator/scripts/fetch_skill.py <id>` materializes the skill (its
  `SKILL.md` plus any `references/` and `scripts/`) into a temp dir from the newest public source,
  and you then execute that `SKILL.md`. Sources always track the newest version (`main` / the live
  onboarding-API render) — nothing is pinned. You may NOT infer, summarize, or hand-author a
  skill's result in its place. The only permitted non-execution is a hard failure of the fetch or
  the skill itself, which stops that dependency chain and is reported (see *Sequential dispatch*
  below) — never a silent skip.
- **Sequential dispatch — chains first, stop on failure.** Run the plan strictly in `resolve.py`'s
  order (it is deterministic and independent of the product request order, and keeps each dependency
  chain contiguous). Execute one skill at a time; do not start a skill until every hard prerequisite
  has **succeeded**, not merely been dispatched. Each skill runs at most once. If a skill fails, stop
  that chain: skip its transitive dependents and report them as not-run — an independent branch is
  unaffected.
- **Checklist discipline.** Post a checklist up front and tick items as you go (per this repo's CLAUDE.md).
- **Output discipline — quiet by default.** The only user-facing output is: the checklist (post it once,
  then tick items in place — do not reprint it), the **PLAN block**, any choice questions, and the
  **SUMMARY block**. Between the PLAN block and the SUMMARY block, write at most **one short status line
  per plan node** (e.g. `-> dd-account-setup ...` then `done: dd-account-setup`). Do not narrate your
  reasoning, read files aloud, restate the goal or the plan, or re-explain these rules. The audit detail
  belongs in the run's trace file (Step 5) — point to it; do not reprint it. Keep your reasoning internal; do not
  think out loud. **Setup and telemetry plumbing** — capturing the org id, running preflight shell
  commands, emitting `emit.py` events — is internal: run it silently and never announce it or report its
  outcome (e.g. "capturing the org id", "org id not captured"), **unless the context explicitly asks for
  debug info**. Fewer words, no lost meaning (this pairs with Simplified Technical English below).
- **Correctness first; styling and telemetry are best-effort.** The load-bearing path is: account
  first → compose with `resolve.py` → confirm → dispatch each planned skill once, in order. Per-step
  telemetry (`emit.py`) and the Simplified Technical English styling below are best-effort. If you are
  under load, on a tight budget, or a telemetry call is failing, skip them and keep going — they never
  change the plan, the dispatch, or the grade.
- **Prefer Simplified Technical English (ASD-STE100)** (best-effort — see *Correctness first* above).
  All user-facing output — the checklist, the plan, choice questions, gaps, and the summary — uses
  Simplified Technical English (ASD-STE100). Apply its core rules:
  - Keep sentences short: at most 20 words for an instruction, 25 for a description. Write one
    instruction per sentence.
  - Use the active voice, the imperative for instructions, and simple verb tenses. Avoid `-ing` forms
    where a simpler verb works.
  - Keep the articles ("a", "the") and write complete sentences. Do not use a telegraphic or headline style.
  - Use one word for one meaning, and keep the same term for the same thing. Avoid slang, jargon, and
    undefined abbreviations.
  - Prefer short, common words. Use lists for steps, and keep paragraphs short.
  The result is short but complete: fewer words, no lost meaning.

## The flow

```
developer goal
      │
      ├─►  dd-account-setup            precondition: valid key, right region
      ├─►  dd-product-recommender      goal + codebase → ranked PRODUCTS  (skipped if the intent names products)
      ├─►  detect context              platform + cloud from the repo
      │
      ▼
  scripts/resolve.py --products "<recommended>" --platform <detected> --cloud <detected>
      │        (reads catalog.json: binds category requires to detected context,
      │         orders hard edges depth-first for dependency-chain locality
      │         (kind breaks ties), dedupes, appends verify)
      ▼
  PLAN (ordered skills) + DEAD-ENDS (recorded) + CHOICE POINTS (ask)
      │
      ▼
  confirm → dispatch each plan node by source.url → summarize (incl. the skipped gaps)
```

## Steps

1. **Post the checklist.**
2. **Ensure the account** — invoke `dd-account-setup` (installed → invoke directly; not installed →
   fetch it from source and run it inline, per *Ground rules*); stop if it cannot produce a validated
   key. `dd-account-setup` owns the site/region and authentication prompts — do not pre-empt them;
   invoke it and let it ask.
   Then capture the authenticated org id for telemetry (best-effort — any failure just leaves it unset
   and the field is omitted). This works on **any** validated path: prefer the OAuth Bearer token that
   `dd-account-setup` leaves in place, and fall back to the API+APP key pair — so an OAuth sign-in that
   has no app key still resolves the org (do not try to mint an app key just for this). Run once,
   before Step 5, and **silently** — do not announce the capture or its outcome (it is best-effort
   telemetry); surface it only if the context asks for debug info:

   ```bash
   tf="${TMPDIR:-/tmp}/dd-oauth-$(id -u).token"
   if   [ -s "$tf" ];        then hdr=(-H "Authorization: Bearer $(cat "$tf")")
   elif [ -n "$DD_APP_KEY" ]; then hdr=(-H "DD-API-KEY: $DD_API_KEY" -H "DD-APPLICATION-KEY: $DD_APP_KEY")
   else hdr=(); fi
   [ ${#hdr[@]} -gt 0 ] && export DD_ORG_ID="$(curl -sf -m 5 "${hdr[@]}" \
     "https://api.${DD_SITE:-datadoghq.com}/api/v2/current_user" \
     | python3 -c 'import sys,json; d=json.load(sys.stdin); o=d["data"]["relationships"]["org"]["data"]["id"]; print(next((x["attributes"]["public_id"] for x in d.get("included",[]) if x.get("type")=="orgs" and x.get("id")==o), o))' 2>/dev/null || true)"
   ```

   `resolve.py` (Step 5) reads `DD_ORG_ID` into the run envelope, so every event carries the org id.
3. **Products — shortcut or recommend.** First check whether the intent already names products:
   `python3 dd-orchestrator/scripts/resolve.py --detect-products "<intent>"`.
   - If it prints one or more product tokens, the user named the products: use that list and **skip
     the recommender**. (e.g. "Instrument RUM and LLMO" → `rum,llm-obs`.)
   - If it prints nothing, the intent describes a goal: **you MUST invoke `dd-product-recommender`** and
     use its ranked product list (installed → invoke directly; not installed → fetch it from source and
     run it inline, per *Ground rules*). **Never infer, guess, or shortlist products yourself** from the
     stack/framework, and never offer product scope as a user choice — product selection is the
     recommender's job, and running it is mandatory here. (e.g. "Help me track user actions" → recommend.)
   The shortcut skips only the recommendation. Account setup (Step 2), context detection (Step 4), the
   confirm gate, and `resolve.py` still run — the shortcut is never a bypass of a safety gate.
   **No legitimate products, no `resolve.py`.** The product list passed to Step 5 MUST come from exactly
   one of: the `--detect-products` shortcut (→ `--intent-mode explicit`) or `dd-product-recommender`
   (→ `--intent-mode recommended`). You may not compose or preview a plan from products you authored
   yourself; `resolve.py --trace` refuses to run without a declared `--intent-mode`.
4. **Detect context** — read the repo for platform (k8s manifests, Dockerfile, `serverless.yml`,
   host) and cloud (Terraform/SDK/provider signals). Leave unknowns unset.
5. **Compose the plan and seed the trace.** The trace is a run-scoped scratch file the orchestrator owns —
   `${DD_ORCH_OUTPUT_DIR:-${TMPDIR:-/tmp}/dd-orchestrator}/trace.md` — **not** a bare `output/` in the
   user's project (a relative path is cwd-dependent and could overwrite the user's own files). The default
   is under `TMPDIR`, so it never litters the repo and is cleaned automatically; set `DD_ORCH_OUTPUT_DIR`
   to override it (the eval points it at its workspace). Reset the scratch — safe, because the path is the
   orchestrator's own namespace, not a guessed `output/` — then run:
   `TRACE="${DD_ORCH_OUTPUT_DIR:-${TMPDIR:-/tmp}/dd-orchestrator}/trace.md"; mkdir -p "$(dirname "$TRACE")" && rm -f "$TRACE"`
   `python3 dd-orchestrator/scripts/resolve.py --trace --products "<products>" --platform <platform> --cloud <cloud> --intent-mode <explicit|recommended> | tee "$TRACE"`
   Pass `--intent-mode explicit` when Step 3's shortcut named the products, or `--intent-mode recommended`
   when `dd-product-recommender` produced them. It is **required** — `resolve.py --trace` refuses to
   compose a plan without it (the products must trace to the shortcut or the recommender, never to your
   own inference) — but it never changes the plan itself.
   The `--trace` flag prints one stable, machine-readable block — `SESSION_ID`, `STOP_REASON`, `PLAN`,
   `DEAD_ENDS`, `CHOICE_POINTS`, `SUGGESTED`, `CONFIRMED`, `DISPATCHED` — and `tee` saves it verbatim to
   `$TRACE`. **That deterministic block IS your dispatch trace; never re-narrate the plan by
   hand.** Read the same output for the ordered plan, the dead-ends, and any choice points. **Capture the
   `SESSION_ID:` value from that block** — every telemetry call in this run reuses it. In **debug
   mode** (only when the context explicitly asks for it), add `--debug` to also render the ASCII DAG
   (indent = dependency depth, `<-` = direct prerequisites). `resolve.py` also emits the reliable
   telemetry core here (see **Telemetry** below).
6. **Resolve choices** — for each choice point (e.g. "pick a platform: kubernetes, linux"), ask the
   developer and re-run, or proceed with the confirmed value.
7. **Confirm — render the PLAN block, then dispatch.** Before any dispatch, render the **PLAN block**
   (see *Output templates* below) verbatim: fill the slots, add no extra prose, keep the exact section
   order and headers. The Plan table's Source column is the skill's source URL, as `resolve.py` prints
   it (the newest public source; `self` for this repo). On approval, run each plan node in dispatch
   order, one at a time — invoking each skill (installed → directly; not installed → fetched from
   source and run inline, per *Ground rules*) — and ticking the
   checklist. Whenever the plan begins with `dd-account-setup` (every plan that needs an account —
   i.e. any non-empty onboarding plan), Step 2 already ran it: do **not** invoke it a second time;
   tick that node and emit its `skill_step:started`/`finished` from the preflight result, then continue
   with the next node. Do not start a node until its prerequisites succeeded; if one fails, skip its dependents.
   As you dispatch, emit best-effort per-step telemetry (**Telemetry** below): `skill_step:started`
   before a node, then `skill_step:finished` (with `result` + `duration_ms`) or `skill_step:skipped`.
   **Keep the trace file (`$TRACE`, Step 5) current** (it is the graded artifact): set `CONFIRMED: yes` on approval —
   or `CONFIRMED: no` if the user declines — and add each dispatched `skill_id` on its own line under
   `DISPATCHED`. If the plan is a choice point or a dead-end (`STOP_REASON` ≠ `none`), leave
   `DISPATCHED` empty — that zero-dispatch state is the correct, recorded outcome.
8. **Summarize — render the SUMMARY block.** As the terminal output, render the **SUMMARY block** (see
   *Output templates* below) verbatim, in the exact section order. The skipped / gaps list is a real
   output — the coverage-gap / demand signal for what to automate next; state it, do not hide it. Emit
   one terminal `skill_run:finished` (**Telemetry**).

## Output templates

Render two fixed blocks so every run reads the same: the **PLAN block** at Step 7 (the confirm gate)
and the **SUMMARY block** at Step 8 (terminal output). Fill the slots and add no extra prose.

**Rendering rules (both blocks).** Use the given section order and the exact headers. Prefer tables to
prose; one line per row; do not editorialize or restate the goal. Every dispatched step shows its
source (the skill's public source URL — newest; `self` for this repo). Action items are imperative and
carry the exact command or file path. Always end with the telemetry `session_id` so the output and the
events join. Omit a section only by its stated omission rule. Marker legend: `✓` done/verified · `◑`
partial or wired-not-verified · `⚠` needs action / mutating · `✗` failed/blocked · `⊘`
skipped/not-covered.

### PLAN block — Step 7 (before any dispatch)

```
# Datadog Onboarding — Plan · session {{session_id}}

## Detected context
- Platform: {{platform}}  ·  Cloud: {{cloud|none}}  ·  Stack: {{stack_summary}}
- Existing Datadog: {{existing|none}}

## Recommended products
{{i}}. {{product}} · {{priority}} · {{one-line why, names a file/lib}}

## Plan — {{k}} step(s), in dispatch order
| # | Skill | Kind | Product | Source |
|---|-------|------|---------|--------|
| {{n}} | {{skill_id}} | {{kind}} | {{product}} | {{source url (newest) or self}} |
Dependencies: {{root}} → {{chain / branches, one line}}

## Not automated ({{dead_end_count}})
- {{product}} — {{why}} → {{docs URL}}
> Show "None — every recommended product is covered." when dead_end_count = 0.

## Decisions needed ({{choice_count}})
- {{choice}}: {{optionA}} / {{optionB}} / {{optionC}}
> Show "None." when choice_count = 0.

## Before you approve — effects
- ⚠ {{step}} {{mutating / outward-facing effect}}
- {{step}} {{non-mutating effect}}

Approve?  [Proceed — all {{k}}]  ·  [Cancel]
```

### SUMMARY block — Step 8 (terminal output)

```
# Datadog Onboarding — Summary · session {{session_id}} · {{result}}

## Checklist
- [{{x|.}}] #{{n}} {{skill_id}} — {{one-line outcome}}

## Products set up
| Product | Delivered by | Status | Evidence |
|---------|--------------|--------|----------|
| {{product}} | {{mechanism, source}} | {{✓|◑|✗}} | {{proof or "pending {{blocker}}"}} |

## Changed
- Cluster: {{namespaces/resources}}
- App: {{files/manifests}}
- Creds: {{where, gitignored?}}

## Action items — do next
1. [ ] {{imperative}} — `{{exact command / path}}`
> Show "None — setup is complete." when there are no follow-ups.

## Issues & deviations
| What | Cause | Resolution / impact |
|------|-------|---------------------|
| {{issue}} | {{cause}} | {{how resolved / residual impact}} |
> Show "None." when the run was clean.

## Gaps / demand signals
- {{skipped product or catalog/orchestrator gap}}

## Verify in Datadog
- {{product}}: {{deep link}}

## Telemetry
session {{session_id}} · {{event_count}} events · result {{result}} ({{s}}✓ / {{f}}✗ / {{k}}⊘)
```

> **Run verdict** — the overall `{{result}}`: use `success` when every **dispatched** skill succeeded,
> even if some recommended products are **dead-ends / "Not automated"** (no skill yet) — those are
> coverage gaps, not partial failures. Reserve `partial_success` for when a dispatched skill **failed**
> or was **skipped**; `blocked` when nothing ran because every product dead-ended; `cancelled` when the
> user declined every step. (`emit.py` reconciles the telemetry verdict the same way.)

## Telemetry (best-effort — never blocks onboarding)

All telemetry goes through `emit.py`; **never build your own HTTP request or `curl`.** It is
best-effort by construction (bounded timeout, local debug log, never throws) and emits to the
logs-intake route only. Turn it off with `DD_ORCH_TELEMETRY_DISABLED=1`. Reuse the single
`SESSION ID:` from Step 5 on every call so the whole run stitches together.

`resolve.py` already emits the reliable core: `skill_run:started`, `skill_run:plan_resolved`, and
one `skill_step:planned` per plan node and per dead-end (each `skill_step` also carries `depends_on`
— the CSV of prerequisite plan positions — so the DAG edges are reconstructable). `resolve.py`
persists the run envelope (agent, platform, cloud, entry, intent mode, org id) and `emit.py` re-attaches it plus an
`emitted_at` (ms) timestamp to **every** event automatically — so you need not re-pass the envelope;
send only the per-step fields below. During dispatch you add the per-step lifecycle and the terminal
run event:

```
SID=<the SESSION ID printed by resolve.py>

# before invoking a plan node (source_mode records how it ran: installed vs fetched-from-source)
python3 dd-orchestrator/scripts/emit.py skill_step --action started --session-id "$SID" \
  --field plan_position=<n> --field skill_id=<id> --field skill_kind=<kind> \
  --field product=<product> --field source_repo=<repo> --field source_mode=<installed|fetched>

# after it returns
python3 dd-orchestrator/scripts/emit.py skill_step --action finished --session-id "$SID" \
  --field plan_position=<n> --field skill_id=<id> --field result=success \
  --field duration_ms=<ms> --field skill_invoked=true \
  --field instrumentation_invoked=<true if it was an install/connect/enable skill>

# if a node is NOT run (failed prerequisite, user declined, no automation, source unreachable)
python3 dd-orchestrator/scripts/emit.py skill_step --action skipped --session-id "$SID" \
  --field plan_position=<n> --field skill_id=<id> --field result=skipped_dependency

# once, when the run reaches a terminal state
python3 dd-orchestrator/scripts/emit.py skill_run --action finished --session-id "$SID" \
  --field result=<success|partial_success|failed|blocked|cancelled> \
  --field step_success_count=<n> --field step_failed_count=<n> --field step_skipped_count=<n>
```

Field values are bounded enums / ids / counts only — never send goal text, paths, keys, URLs, or
model output (the emitter also strips anything not on its allow-list).

**Reliability and reconciliation.** Every event carries a per-session `event_seq` (a monotonic
ordinal): a gap in `event_seq` means an event was dropped, not that the step never ran. `resolve.py`
emits its plan-shape core (`skill_run:started`, `skill_run:plan_resolved`, one `skill_step:planned`
per node) as **critical** — one transport blip cannot drop the whole core. `emit.py` also appends
every attempted event to a durable local NDJSON log for offline audit. When you analyze a run, treat
`step_success_count` / `step_failed_count` / `step_skipped_count` on `skill_run:finished` as the
**source of truth** for how many steps ran; reconcile the per-step events against it. Do not assume a
missing per-step event means the step did not run. `emit.py` also reconciles the terminal
`skill_run:finished` `result` against those counts (and the dead-end count): if the reported verdict
contradicts them, it keeps the reported value as `result_reported` and sets `result` to the
count-consistent verdict (`result_reconciled: true`). So report the honest per-step results and let the
guard settle the run verdict.

## Worked example

Goal *"monitor my Node service on Kubernetes"* → recommender `[APM, Infrastructure Monitoring]`,
detected `platform=kubernetes`:

```
1. dd-account-setup            (foundation)          [self]
2. apm-agent-install-kubernetes (platform-install)   [agent-skills]
3. apm-enable-kubernetes       (product-enable/apm)  [agent-skills]
4. apm-verify-ssi-kubernetes   (verify)              [agent-skills]
```

One product plus a detected platform became a four-skill plan drawn from two repos, correctly
ordered, with Infrastructure Monitoring delivered by the same SSI Agent install. The intent never entered
the resolver — only the products and the detected platform did.
