#!/usr/bin/env python3
"""Best-effort telemetry emitter for dd-orchestrator (v1).

One tiny emitter for the whole skill. Skills NEVER build their own HTTP request or
`curl` — they call this. It builds the setup-cli logs-intake log line and POSTs it to
Datadog logs intake, best-effort:

  * bounded 3s timeout,
  * retry ONLY transient 5xx (never 429),
  * a 1-strike per-process breaker so a down intake can't stall a multi-event run,
  * an append-only local debug log,
  * and it never raises or blocks the caller.

v1 uses the logs-intake route only (no signup route), so it has no cross-repo
dependency. It reuses setup-cli's publishable ``pub…`` client-token intake, which
lands events in the same internal ``service:setup-cli`` pipeline the onboarding-growth
team already consumes. It deliberately does NOT use the customer's DD_API_KEY: that
would write onboarding telemetry into the customer's own org, not Datadog's.

CLI (the runbook calls this at each dispatch boundary — see SKILL.md):
  python3 emit.py skill_run  --action started   --session-id <uuid> [--field k=v ...]
  python3 emit.py skill_step --action started   --session-id <uuid> --field skill_id=<id> ...
  python3 emit.py skill_run  --action finished  --session-id <uuid> --field result=success ...

The session id is minted and printed by resolve.py ("SESSION ID: <uuid>"); every
event in one DAG run must reuse it.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import quote

SCHEMA_VERSION = 1

# --- envelope the runtime owns (a caller can NEVER override these) -----------
RUNTIME_SOURCE = "setup-cli"   # reuse the existing pipeline -> service:setup-cli
FLOW = "agent"
PLATFORM = "cli"

EVENT_TYPES = {"skill_run", "skill_step"}
ACTIONS = {
    "skill_run": {"started", "plan_resolved", "finished"},
    "skill_step": {"planned", "started", "finished", "skipped"},
}

# Reconciled with catalog.json `kinds`: a `lifecycle` node CAN enter a plan, so it is
# a valid skill_kind (the proposal's enum omitted it). `internal` is filtered by
# resolve.py and never reaches a step, so it is intentionally absent here.
SKILL_KINDS = {"foundation", "platform-install", "cloud-connect",
               "product-enable", "verify-troubleshoot", "lifecycle"}

# --- client-side allow-list (mirrors the server-side allow-list intent) ------
# Any key not here is stripped before send (privacy + bounded cardinality). Grouped
# only for readability; the union is what gates.
_FACETS = {
    "event_type", "event_action", "result", "invocation_mode", "intent_mode", "entry_skill_id",
    "agent_name", "headless", "target_platform", "target_cloud", "step_kind",
    "skill_id", "skill_kind", "product", "source_repo", "source_mode", "error_code", "result_reported",
}
_MEASURES = {
    "duration_ms", "plan_position", "dependency_count",
    "planned_skill_count", "dead_end_count", "choice_count",
    "step_success_count", "step_failed_count", "step_skipped_count",
}
_ATTRIBUTES = {
    "schema_version", "session_id", "source", "flow", "platform",
    "agent_version", "recommended_products", "emitted_at", "depends_on", "event_seq",
    "skill_invoked", "instrumentation_invoked", "result_reconciled",
    "org_id",   # authenticated org's public_id, captured once at auth; attribute (not a facet)
}
ALLOWED_KEYS = _FACETS | _MEASURES | _ATTRIBUTES

_BOOL_KEYS = {"headless", "skill_invoked", "instrumentation_invoked", "result_reconciled"}

# setup-cli's publishable logs-intake client tokens (`pub…` = write-only, meant to
# ship in clients). Kept in sync with setup-cli/src/constants/credentials.ts. An env
# override always wins.
_CLIENT_TOKENS = {
    "datadoghq.com": "pub5b34c05a3abc178e4d2abddf11bb4f31",
    "us3.datadoghq.com": "pub82dbd99bf6299230cee3685535d4660d",
    "us5.datadoghq.com": "pubf9a4a5b93cc00e7f4d5628f05ba1e7f0",
    "datadoghq.eu": "pub471980dfdfb12f9cf2ffdf83551c9cb7",
    "ap1.datadoghq.com": "pubbbc10e19ab9fa2f7eb54250610e9bbad",
    "ap2.datadoghq.com": "pube391858a603cb55a0e72a8cb9f9fb627",
    "uk1.datadoghq.com": "pub90fe05f76003a728ed554865d39a88d1",
}
_TAGS = "service:setup-cli"

# Per-process breaker. Non-critical emits are 1-strike (a down intake can't add N*3s to a
# run). CRITICAL emits (resolve.py's plan-shape core) BYPASS an open breaker so a single
# transient blip cannot drop run:started + plan_resolved + planned×N; a critical failure
# trips the breaker only after 3 consecutive strikes (bounds the worst-case hang).
_BREAKER = {"open": False, "critical_fails": 0}


def _trip_breaker(critical):
    """Record one terminal failure (transport OR terminal HTTP) against the breaker.

    Non-critical: a single strike opens it. Critical (resolve-core): opens only after
    3 consecutive failures, so one blip cannot drop the plan shape — but once that
    bound is reached the breaker applies to critical events too (see ``emit``)."""
    if critical:
        _BREAKER["critical_fails"] += 1
        if _BREAKER["critical_fails"] >= 3:            # bound the worst-case hang
            _BREAKER["open"] = True
    else:
        _BREAKER["open"] = True                        # non-critical: 1-strike


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _is_disabled():
    if _truthy(os.environ.get("DD_ORCH_TELEMETRY_DISABLED", "")):
        return True
    # ponytail: never emit real telemetry from a test run — the pub token writes to the
    # internal setup-cli index. A genuine CLI / orchestrator run never imports these.
    if "unittest" in sys.modules or "pytest" in sys.modules:
        return True
    return False


def _debug_log_path():
    return os.environ.get(
        "DD_ORCH_TELEMETRY_LOG",
        os.path.join(tempfile.gettempdir(), "dd-orchestrator-telemetry.log"),
    )


def _debug(msg):
    """Append one line to the local debug log. Never raises."""
    try:
        with open(_debug_log_path(), "a") as handle:
            handle.write(msg.rstrip("\n") + "\n")
    except Exception:
        pass


def _coerce(key, value):
    if key in _BOOL_KEYS:
        return value if isinstance(value, bool) else _truthy(value)
    if key in _MEASURES:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None   # drop an unparseable measure rather than send a bad type
    return value


# --- run-scoped envelope: persisted ONCE by resolve.py, re-applied to every event ------
# Fixes F4 — SKILL.md-runbook emits (separate emit.py processes) dropped the envelope on
# started/finished/skill_run:finished. Write-once; read-only here; best-effort (never raises).
_ENVELOPE_KEYS = ("entry_skill_id", "invocation_mode", "intent_mode", "agent_name",
                  "target_platform", "target_cloud", "org_id")


def _session_state_path(session_id):
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return os.path.join(tempfile.gettempdir(), f"dd-orch-{safe}.json")


def _load_state(session_id):
    try:
        with open(_session_state_path(session_id)) as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def write_session_state(session_id, envelope, shape=None):
    """Persist the run's envelope (re-attached to every later emit) and, when given, the plan
    shape (`dead_end_count`, ...), so the terminal skill_run:finished can reconcile `result`
    against the real counts. Preserves the seq counter across re-writes. Never raises."""
    try:
        if not session_id:
            return
        state = _load_state(session_id)
        state["envelope"] = {k: (envelope or {})[k] for k in _ENVELOPE_KEYS
                             if (envelope or {}).get(k) not in (None, "")}
        if shape is not None:
            state["shape"] = {k: int(v) for k, v in shape.items()}
        state.setdefault("seq", 0)
        with open(_session_state_path(session_id), "w") as fh:
            json.dump(state, fh)
    except Exception:
        pass


def _read_session_envelope(session_id):
    env = _load_state(session_id).get("envelope", {})
    return {k: v for k, v in env.items() if k in _ENVELOPE_KEYS and v not in (None, "")}


def _next_seq(session_id):
    """Return the next per-session monotonic ordinal (1-based). A missing ordinal on the
    export means an event was dropped, not that the step never ran (gap detection). Never
    raises; returns None if state is unwritable (then event_seq is simply omitted).
    ponytail: no lock — dispatch is sequential (one emit.py process at a time)."""
    try:
        state = _load_state(session_id)
        seq = int(state.get("seq", 0)) + 1
        state["seq"] = seq
        state.setdefault("envelope", {})
        with open(_session_state_path(session_id), "w") as fh:
            json.dump(state, fh)
        return seq
    except Exception:
        return None


def _ndjson_path():
    return os.environ.get(
        "DD_ORCH_TELEMETRY_NDJSON",
        os.path.join(tempfile.gettempdir(), "dd-orchestrator-telemetry.ndjson"),
    )


def _record_ndjson(event, status):
    """Append one durable NDJSON line per ATTEMPTED event so drops are auditable/replayable
    offline. Never raises."""
    try:
        with open(_ndjson_path(), "a") as fh:
            fh.write(json.dumps({"status": status, "event": event}) + "\n")
    except Exception:
        pass


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def _read_run_shape(session_id):
    shape = _load_state(session_id).get("shape", {})
    return shape if isinstance(shape, dict) else {}


# Verdicts we re-check against the counts. An agent-reported `blocked` / `cancelled` is trusted
# and never overridden; but a reported success/partial/failed that contradicts a pure opt-out
# (only skips) or all-dead-end count-shape is corrected TO `cancelled` / `blocked` — so a user
# who declined is not lumped into the failure funnel (PR #194 review). Both are SKILL.md verdicts.
_RECONCILABLE_RESULTS = ("success", "partial_success", "failed")


def _reconcile_run_result(out, session_id):
    """Guard the terminal run `result` against the real counts, so an agent verdict that
    contradicts them (e.g. `partial_success` with 5 success / 0 failed / 0 skipped / 0 dead
    ends) cannot skew the funnel. On disagreement, keep the agent's value as `result_reported`
    and set `result` to the count-consistent verdict (`result_reconciled: true`)."""
    result = out.get("result")
    if result not in _RECONCILABLE_RESULTS:
        return
    if not any(k in out for k in ("step_success_count", "step_failed_count", "step_skipped_count")):
        return   # no counts on this event -> nothing to reconcile against
    s = int(out.get("step_success_count", 0) or 0)
    f = int(out.get("step_failed_count", 0) or 0)
    k = int(out.get("step_skipped_count", 0) or 0)
    d = int(_read_run_shape(session_id).get("dead_end_count", 0) or 0)
    if s >= 1 and f == 0 and k == 0:
        derived = "success"                # everything dispatched succeeded; dead-ends are coverage
                                           # gaps (a product with no skill yet), not partial failures
    elif s >= 1:
        derived = "partial_success"        # something succeeded, but a step failed or was skipped
    elif f == 0 and d == 0 and k >= 1:
        derived = "cancelled"              # nothing ran, nothing errored: user declined every step
    elif f == 0 and d >= 1:
        derived = "blocked"                # nothing errored, but dead-ends stopped all progress
    else:
        derived = "failed"                 # something actually failed
    if derived != result:
        out["result_reported"] = result    # preserve the agent's verdict
        out["result"] = derived            # count-consistent truth for the funnel
        out["result_reconciled"] = True


def build_event(event_type, event_action, session_id, fields=None):
    """Return the validated event dict.

    Caller `fields` are allow-list-stripped and type-coerced first; then the
    runtime-owned envelope is applied ON TOP, so a caller can never spoof
    session_id / source / event identity.
    """
    out = {}
    for key, value in (fields or {}).items():
        if key not in ALLOWED_KEYS or value is None or value == "":
            continue
        coerced = _coerce(key, value)
        if coerced is not None:
            out[key] = coerced
    # Re-attach the run-scoped envelope (persisted by resolve.py) so runbook emits that did
    # not re-pass it still carry agent/platform/entry (F4). Runtime-owned -> un-spoofable.
    out.update(_read_session_envelope(session_id))
    if event_type == "skill_run" and event_action == "finished":
        _reconcile_run_result(out, session_id)   # `result` must match the step counts
    out.update({
        "event_type": event_type,
        "event_action": event_action,
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "source": RUNTIME_SOURCE,
        "flow": FLOW,
        "platform": PLATFORM,
        "emitted_at": _now_iso(),   # F1: client ms timestamp -> reliable order + duration
    })
    return out


def _log_body(event):
    """The flat logs-intake log line (mirrors setup-cli's sendToIntake body)."""
    return json.dumps({
        "service": RUNTIME_SOURCE,
        "message": f"{event['event_type']}:{event['event_action']}",
        **event,
    })


def _resolve_token(site):
    override = os.environ.get("DD_ONBOARDING_CLIENT_TOKEN")
    if override:
        return override
    return _CLIENT_TOKENS.get(site)


def _intake_url(site, token):
    return (f"https://http-intake.logs.{site}/v1/input/{token}"
            f"?ddsource=dd-orchestrator&ddtags={quote(_TAGS)}")


def _default_post(url, body):
    """POST the JSON body with a 3s timeout. Returns the HTTP status (int) or None on a
    transport error / timeout. Never raises."""
    request = urllib.request.Request(
        url, data=body.encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.getcode()
    except urllib.error.HTTPError as err:
        return err.code
    except Exception:
        return None


def emit(event_type, event_action, session_id, fields=None,
         *, site=None, disabled=None, _post=None, critical=False):
    """Best-effort emit of one event. Returns True iff intake accepted it. Never raises,
    never blocks beyond the bounded timeout / retries.

    ``critical`` marks resolve.py's plan-shape core (run:started, plan_resolved, planned×N):
    it BYPASSES an open breaker — but only until 3 consecutive critical failures trip it —
    so one transient blip cannot drop the whole core, yet a persistently down intake still
    stops after the bound instead of adding a timeout per plan node.
    """
    try:
        if disabled is None:
            disabled = _is_disabled()
        if disabled:
            return False
        if event_type not in EVENT_TYPES or event_action not in ACTIONS.get(event_type, ()):
            _debug(f"drop: unknown event {event_type}:{event_action}")
            return False
        if not session_id:
            _debug(f"drop: missing session_id for {event_type}:{event_action}")
            return False

        site = site or os.environ.get("DD_SITE") or "datadoghq.com"
        token = _resolve_token(site)
        if not token:
            _debug(f"drop: no client token for site {site!r}")
            return False

        event = build_event(event_type, event_action, session_id, fields)
        seq = _next_seq(session_id)
        if seq is not None:
            event["event_seq"] = seq                           # gap-detection ordinal
        body = _log_body(event)

        # Softened breaker: a critical (resolve-core) emit bypasses an open breaker so a
        # single earlier blip cannot drop the plan shape — but only until the critical
        # failure bound is reached; past that the breaker applies to critical events too,
        # so a down intake cannot keep adding a timeout per plan node.
        if _BREAKER["open"] and (not critical or _BREAKER["critical_fails"] >= 3):
            _record_ndjson(event, "suppressed")
            return False

        url = _intake_url(site, token)
        post = _post or _default_post

        status = None
        for attempt in range(3):
            status = post(url, body)
            if status is not None and 500 <= status <= 599:   # retry transient 5xx only
                _debug(f"retry {event_type}:{event_action} attempt {attempt + 1} status {status}")
                time.sleep(0.2)
                continue
            break                                              # 2xx, 429, other 4xx, or None: stop

        _record_ndjson(event, status if status is not None else "transport_failed")

        if status is None:                                     # transport failure
            _trip_breaker(critical)
            _debug(f"transport-failed {event_type}:{event_action}")
            return False
        if status >= 400:                                      # terminal HTTP failure (4xx/5xx/429)
            _trip_breaker(critical)                            # count it toward the same bound
            _debug(f"dropped {event_type}:{event_action} status {status}")
            return False
        _BREAKER["critical_fails"] = 0                          # a success clears the streak
        return True
    except Exception as err:                                   # best-effort: NEVER propagate
        _debug(f"emit-exception {event_type}:{event_action}: {err!r}")
        return False


def _cli(argv=None):
    parser = argparse.ArgumentParser(
        description="best-effort dd-orchestrator telemetry emitter (v1)")
    parser.add_argument("event_type", choices=sorted(EVENT_TYPES))
    parser.add_argument("--action", required=True, help="event lifecycle action")
    parser.add_argument("--session-id", required=True,
                        help="the run's shared session id (from resolve.py 'SESSION ID:')")
    parser.add_argument("--field", action="append", default=[], metavar="KEY=VALUE",
                        help="event field (repeatable); non-allow-listed keys are dropped")
    args = parser.parse_args(argv)
    fields = {}
    for item in args.field:
        if "=" not in item:
            _debug(f"cli: ignoring malformed --field {item!r}")
            continue
        key, value = item.split("=", 1)
        fields[key.strip()] = value
    emit(args.event_type, args.action, args.session_id, fields)
    return 0   # ALWAYS succeed — telemetry must never fail the caller


if __name__ == "__main__":
    sys.exit(_cli())
