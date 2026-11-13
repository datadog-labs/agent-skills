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

CLI (the runbook calls `record` at each dispatch boundary — see SKILL.md). `record`
updates the run's trace file AND emits the matching event, deriving every field from
the plan resolve.py stored, so the agent passes only the skill id:
  python3 emit.py record confirmed | declined
  python3 emit.py record started  <skill_id> [--source-mode installed|fetched]
  python3 emit.py record finished <skill_id> --result success|failed
  python3 emit.py record skipped  <skill_id> [--result skipped_dependency]
  python3 emit.py record run-finished [--result blocked|cancelled]
The raw form stays for anything `record` does not cover:
  python3 emit.py skill_step --action started --session-id <uuid> --field skill_id=<id> ...

The session id is minted by resolve.py and saved to a state file, so a fresh shell
(no `$SID`) still lands on the current run. --session-id overrides it.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import os
import re
import stat
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
    "skill_run": {"started", "plan_resolved", "confirmed", "declined", "finished"},
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
    "agent_name", "run_context", "headless", "target_platform", "target_cloud", "step_kind",
    "skill_id", "skill_kind", "product", "source_repo", "source_mode", "error_code", "result_reported",
}
_MEASURES = {
    "duration_ms", "plan_position", "plan_revision", "dependency_count",
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
# override always wins; `datad0g.com` also honors the dogfood env var setup-cli uses.
_CLIENT_TOKENS = {
    "datad0g.com": "pub9a1f0104f5632c5840cca5a99a43c32a",
    "datadoghq.com": "pub5b34c05a3abc178e4d2abddf11bb4f31",
    "us3.datadoghq.com": "pub82dbd99bf6299230cee3685535d4660d",
    "us5.datadoghq.com": "pubf9a4a5b93cc00e7f4d5628f05ba1e7f0",
    "datadoghq.eu": "pub471980dfdfb12f9cf2ffdf83551c9cb7",
    "ap1.datadoghq.com": "pubbbc10e19ab9fa2f7eb54250610e9bbad",
    "ap2.datadoghq.com": "pube391858a603cb55a0e72a8cb9f9fb627",
    "uk1.datadoghq.com": "pub90fe05f76003a728ed554865d39a88d1",
}
_TAGS = "team:agentic-onboarding,service:setup-cli"

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


# --- run-scoped state: shared by sequential resolve.py and emit.py processes ----------
# Replans refresh the envelope and shape while preserving lifecycle state and event_seq.
_ENVELOPE_KEYS = ("entry_skill_id", "invocation_mode", "intent_mode", "agent_name",
                  "run_context", "target_platform", "target_cloud", "org_id")

# Env markers each agent sets in the shells it spawns. First match wins.
# ponytail: Codex/Cursor markers are best-known names, not verified against every
# version; --agent-name still overrides when a marker is missing.
_AGENT_MARKERS = (
    ("claude_code", ("CLAUDECODE",)),
    ("codex", ("CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_THREAD_ID")),
    ("cursor", ("CURSOR_AGENT", "CURSOR_TRACE_ID")),
)
_RUN_CONTEXTS = {"eval", "ci", "dev", "user"}


def detect_agent():
    for name, markers in _AGENT_MARKERS:
        if any(os.environ.get(m) for m in markers):
            return name
    return "unknown"


def run_context():
    """eval | ci | dev | user, from DD_ORCH_RUN_CONTEXT; else ci when CI is set."""
    ctx = os.environ.get("DD_ORCH_RUN_CONTEXT", "").strip().lower()
    if ctx in _RUN_CONTEXTS:
        return ctx
    return "ci" if _truthy(os.environ.get("CI", "")) else "user"


def _user_id():
    """Match `id -u` where possible, with a native-Windows-safe fallback."""
    try:
        return str(os.getuid())
    except (AttributeError, OSError):
        return os.environ.get("UID") or os.environ.get("USERNAME") or "user"


def _oauth_token_path():
    # Same path dd-account-setup writes: ${TMPDIR:-/tmp}/dd-oauth-$(id -u).token
    return os.path.join(os.environ.get("TMPDIR") or "/tmp", f"dd-oauth-{_user_id()}.token")


def _run_oauth_token_path():
    return os.path.join(_run_dir(), "oauth.token")


def _token_claim(token):
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    claim_dir = os.path.join(
        os.environ.get("TMPDIR") or tempfile.gettempdir(), "dd-orchestrator-token-claims", digest)
    marker = hashlib.sha256(_run_dir().encode("utf-8")).hexdigest()
    return claim_dir, os.path.join(claim_dir, marker)


@contextlib.contextmanager
def _token_claim_lock():
    root = os.path.join(
        os.environ.get("TMPDIR") or tempfile.gettempdir(), "dd-orchestrator-token-claims")
    lock = os.path.join(root, ".lock")
    acquired = False
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
        for _attempt in range(100):
            try:
                os.mkdir(lock, mode=0o700)
                acquired = True
                break
            except FileExistsError:
                try:
                    if time.time() - os.stat(lock).st_mtime > 30:
                        os.rmdir(lock)
                        continue
                except OSError:
                    pass
                time.sleep(0.01)
        yield acquired
    finally:
        if acquired:
            try:
                os.rmdir(lock)
            except OSError:
                pass


def claim_oauth_token():
    """Copy the per-user handoff token into this run and register ownership."""
    try:
        with _token_claim_lock() as acquired:
            if not acquired:
                return None
            with open(_oauth_token_path(), encoding="utf-8") as fh:
                token = fh.read().strip()
            if not token:
                return None
            _ensure_run_dir()
            path = _run_oauth_token_path()
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as fh:
                fh.write(token)
            claim_dir, marker = _token_claim(token)
            os.makedirs(claim_dir, mode=0o700, exist_ok=True)
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write(_run_dir())
            return token
    except (OSError, TypeError):
        return None


_CREDENTIAL_KEYS = ("DD_SITE", "DD_API_KEY", "DD_APP_KEY")


def _credential_context():
    """Load Datadog connection values with dd-account-setup's precedence.

    A shell environment wins, then `.env.local`, then `.env`. Values are parsed
    as data (never sourced), so a project file cannot execute code.
    """
    values = {key: os.environ.get(key) for key in _CREDENTIAL_KEYS}
    for filename in (".env.local", ".env"):
        try:
            with open(filename, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for line in lines:
            match = re.match(r"^\s*(DD_SITE|DD_API_KEY|DD_APP_KEY)\s*=(.*)$", line)
            if not match or values.get(match.group(1)):
                continue
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                values[match.group(1)] = value
    return values


def fetch_org_id(site=None):
    """The authenticated org's public_id, or None. Reads the OAuth token file
    dd-account-setup leaves in place (a file survives across the agent's fresh shells;
    an exported variable does not), else the API+APP key pair. An exported DD_ORG_ID is
    ignored: a leftover value can name another org and would skip the agreement check.
    Never raises."""
    try:
        token = claim_oauth_token()   # claim even when disabled: the run must own the shared token to clean it up
        if _is_disabled():
            return None
        credentials = _credential_context()
        site = site or credentials.get("DD_SITE") or "datadoghq.com"
        if site not in _CLIENT_TOKENS:
            _debug(f"org-id: refusing unrecognized Datadog site {site!r}")
            return None
        attempts = []
        if credentials.get("DD_API_KEY") and credentials.get("DD_APP_KEY"):
            attempts.append(("keys", {
                "DD-API-KEY": credentials["DD_API_KEY"],
                "DD-APPLICATION-KEY": credentials["DD_APP_KEY"],
            }))
        try:
            if not token:
                with open(_run_oauth_token_path(), encoding="utf-8") as fh:
                    token = fh.read().strip()
        except OSError:
            token = None
        if token:
            attempts.append(("oauth", {"Authorization": f"Bearer {token}"}))
        if not attempts:
            return None
        identities = {}
        for auth_source, headers in attempts:
            try:
                request = urllib.request.Request(
                    f"https://api.{site}/api/v2/current_user", headers=headers)
                with urllib.request.urlopen(request, timeout=5) as response:
                    data = json.load(response)
                org = data["data"]["relationships"]["org"]["data"]["id"]
                identities[auth_source] = next(
                    (item["attributes"]["public_id"] for item in data.get("included", [])
                     if item.get("type") == "orgs" and item.get("id") == org),
                    org,
                )
            except Exception:
                continue
        unique = set(identities.values())
        if len(unique) == 1:
            source = "both" if len(identities) == 2 else next(iter(identities), "none")
            try:
                _ensure_run_dir()
                with open(os.path.join(_run_dir(), "auth-source"), "w", encoding="utf-8") as fh:
                    fh.write(source)
            except OSError:
                pass
            return next(iter(unique))
        if len(unique) > 1:
            _debug("org-id: OAuth and API/app credentials identify different organizations")
        return None
    except Exception:
        return None


def _run_dir():
    explicit = os.environ.get("DD_ORCH_OUTPUT_DIR")
    if explicit:
        return explicit
    markers = (
        "DD_ORCH_RUN_ID",
        "CURSOR_CONVERSATION_ID",
        "CODEX_THREAD_ID",
        "CLAUDE_CODE_REMOTE_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODEREPL_ID",
    )
    scope = next((os.environ.get(name) for name in markers if os.environ.get(name)), None)
    scope = scope or os.path.abspath(os.getcwd())
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
    return os.path.join(
        os.environ.get("TMPDIR") or tempfile.gettempdir(), "dd-orchestrator", digest)


def _ensure_run_dir():
    """Create this run's directory private to the user; refuse one another user planted."""
    path = _run_dir()
    os.makedirs(path, mode=0o700, exist_ok=True)
    if not os.environ.get("DD_ORCH_OUTPUT_DIR") and hasattr(os, "getuid"):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
            raise OSError(f"untrusted run directory: {path}")
    return path


def _current_session_path():
    return os.path.join(_run_dir(), "session-id")


def trace_path():
    return os.path.join(_run_dir(), "trace.md")


def trace_session():
    """Return the session embedded in this run's trace, if one exists."""
    try:
        with open(trace_path(), encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("SESSION_ID:"):
                    return line.split(":", 1)[1].strip() or None
    except OSError:
        pass
    return None


def set_current_session(session_id):
    """Remember the run's session id on disk for later fresh-shell calls. Never raises."""
    try:
        _ensure_run_dir()
        with open(_current_session_path(), "w") as fh:
            fh.write(str(session_id))
    except Exception:
        pass


def current_session():
    traced = trace_session()
    if traced:
        return traced
    try:
        with open(_current_session_path()) as fh:
            return fh.read().strip() or None
    except Exception:
        return None


def _session_state_path(session_id):
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return os.path.join(_run_dir(), f"state-{safe}.json")


def _load_state(session_id):
    try:
        with open(_session_state_path(session_id)) as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def _save_state(session_id, state):
    """Atomically persist one run state. Return False rather than raising."""
    try:
        _ensure_run_dir()
        path = _session_state_path(session_id)
        temporary = f"{path}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(temporary, path)
        return True
    except Exception:
        return False


def write_session_state(session_id, envelope, shape=None, plan=None):
    """Persist the run's envelope (re-attached to every later emit) and, when given, the plan
    shape (`dead_end_count`, ...), so the terminal skill_run:finished can reconcile `result`
    against the real counts, and the plan nodes `record` derives step fields from.
    A replan merges into the envelope, so a value captured earlier (e.g. org_id) is kept.
    Preserves the seq counter across re-writes. Never raises."""
    try:
        if not session_id:
            return
        state = _load_state(session_id)
        merged = dict(state.get("envelope") or {})
        merged.update({k: v for k, v in (envelope or {}).items() if v not in (None, "")})
        state["envelope"] = {k: merged[k] for k in _ENVELOPE_KEYS if k in merged}
        if shape is not None:
            state["shape"] = {k: int(v) for k, v in shape.items()}
        if plan is not None:
            nodes = list(plan)
            if not any(node.get("id") == "dd-account-setup" for node in nodes):
                nodes.insert(0, {
                    "id": "dd-account-setup", "position": None, "kind": "foundation",
                    "product": "", "source_repo": "agent-skills", "preflight": True,
                })
            if state.get("plan") not in (None, nodes) and state.get("confirmed") is not None:
                state["confirmed"] = None
            state["plan"] = nodes
            state["plan_revision"] = int(state.get("plan_revision", 0)) + 1
        state.setdefault("seq", 0)
        return state.get("plan_revision") if _save_state(session_id, state) else None
    except Exception:
        return None


def session_org_id(session_id):
    """org_id already captured for this run (a replan need not fetch it again)."""
    return (_load_state(session_id).get("envelope") or {}).get("org_id")


def _read_session_envelope(session_id):
    env = _load_state(session_id).get("envelope", {})
    return {k: v for k, v in env.items() if k in _ENVELOPE_KEYS and v not in (None, "")}


def _claim_run_start(session_id):
    """Reserve one run-start attempt across sequential processes, before transport.

    Keep the marker even if transport fails: the local event log records that attempt.
    If state cannot be persisted, allow emission so telemetry remains best-effort.
    """
    try:
        state = _load_state(session_id)
        if state.get("run_started"):
            return False
        state["run_started"] = True
        return _save_state(session_id, state)
    except Exception:
        return True


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
        return seq if _save_state(session_id, state) else None
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


def _productive_success(state):
    """Whether anything beyond account preflight succeeded; None if unknown."""
    if "steps" not in state:
        return None
    return any(
        skill_id != "dd-account-setup" and step.get("status") == "success"
        for skill_id, step in state.get("steps", {}).items()
    )


def _scope_skips(state):
    """Steps removed because the developer's target is outside the skill's CAVEATS scope.
    They are coverage gaps, not failures, so they do not make a run `partial_success`."""
    return sum(1 for step in state.get("steps", {}).values()
               if step.get("skip_result") == "out_of_scope")


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
    shape = _read_run_shape(session_id)
    state = _load_state(session_id)
    derived = _derive_result(
        int(out.get("step_success_count", 0) or 0),
        int(out.get("step_failed_count", 0) or 0),
        int(out.get("step_skipped_count", 0) or 0) - _scope_skips(state),
        int(shape.get("dead_end_count", 0) or 0) + _scope_skips(state),   # scope gaps are dead ends
        _productive_success(state),
        int(shape.get("choice_count", 0) or 0))
    if derived != result:
        out["result_reported"] = result    # preserve the agent's verdict
        out["result"] = derived            # count-consistent truth for the funnel
        out["result_reconciled"] = True


def _derive_result(s, f, k, d, productive_success=None, choices=0):
    """The run verdict the step counts (success/failed/skipped) and dead-end count imply."""
    if choices >= 1 and productive_success is False and f == 0 and k == 0:
        derived = "blocked"                # unresolved structural choice prevented product work
    elif d >= 1 and productive_success is False and f == 0 and k == 0:
        derived = "blocked"                # only account preflight ran; every product dead-ended
    elif s >= 1 and f == 0 and k == 0:
        derived = "success"                # everything dispatched succeeded; dead-ends are coverage
                                           # gaps (a product with no skill yet), not partial failures
    elif productive_success is False and f >= 1:
        derived = "failed"                 # only the account preflight succeeded; product work failed
    elif s >= 1:
        derived = "partial_success"        # something succeeded, but a step failed or was skipped
    elif f == 0 and d == 0 and k >= 1:
        derived = "cancelled"              # nothing ran, nothing errored: user declined every step
    elif f == 0 and d >= 1:
        derived = "blocked"                # nothing errored, but dead-ends stopped all progress
    else:
        derived = "failed"                 # something actually failed
    return derived


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
    if site == "datad0g.com":
        dogfood = os.environ.get("SETUP_CLI_DOGFOOD_TELEMETRY_CLIENT_TOKEN")
        if dogfood:
            return dogfood
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


def _session_site(session_id):
    """The intake site for this run: the shell's DD_SITE, else the site the run first used.

    A site that only the project's .env files name would otherwise be re-read from whatever
    directory each later `record` runs in, splitting one run across two regions.
    """
    explicit = os.environ.get("DD_SITE")
    if explicit:
        return explicit
    path = os.path.join(_run_dir(), f"site-{re.sub(r'[^A-Za-z0-9_.-]', '_', str(session_id))}")
    try:
        with open(path, encoding="utf-8") as fh:
            saved = fh.read().strip()
        if saved in _CLIENT_TOKENS:
            return saved
    except OSError:
        pass
    site = _credential_context().get("DD_SITE") or "datadoghq.com"
    if site in _CLIENT_TOKENS:
        try:
            _ensure_run_dir()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(site)
        except OSError:
            pass
    return site


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

        site = site or _session_site(session_id)
        if site not in _CLIENT_TOKENS:
            _debug(f"drop: unrecognized Datadog site {site!r}")
            return False
        token = _resolve_token(site)
        if not token:
            _debug(f"drop: no client token for site {site!r}")
            return False

        if event_type == "skill_run" and event_action == "started":
            if not _claim_run_start(session_id):
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


# --- `record`: one call per dispatch boundary = trace update + event -------------------
# The agent must keep the trace current anyway. Routing that update through
# here makes the step events ride along instead of depending on a separate, skippable call.
_INSTRUMENTING_KINDS = {"platform-install", "cloud-connect", "product-enable"}


def _refuse(reason):
    """Reject a `record` transition. Log it and tell the agent why on stderr, so it can
    correct the call instead of guessing from exit code 2. Always returns False."""
    _debug(f"record: {reason}")
    print(f"emit.py record: {reason}", file=sys.stderr)
    return False


def _step_fields(node):
    """The plan-derived fields every skill_step event carries."""
    return {"skill_id": node.get("id"), "plan_position": node.get("position"),
            "skill_kind": node.get("kind"), "product": node.get("product"),
            "source_repo": node.get("source_repo")}


def trace_snapshot(session_id, plan_ids=None):
    """Return trace-owned lifecycle fields that a replan must render again."""
    state = _load_state(session_id)
    confirmed = state.get("confirmed")
    if plan_ids is not None:
        prior_ids = [node.get("id") for node in state.get("plan", [])]
        if prior_ids != list(plan_ids):
            confirmed = None
    dispatched = [
        skill_id for skill_id, step in state.get("steps", {}).items()
        if step.get("status") == "success"
    ]
    return {
        "confirmed": "yes" if confirmed is True else "no" if confirmed is False else "pending",
        "dispatched": dispatched,
    }


def _edit_trace(session_id, confirmed=None, dispatched=None):
    """Update one matching run trace atomically. Return False on any mismatch/error."""
    try:
        path = trace_path()
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        embedded = next(
            (line.split(":", 1)[1].strip() for line in lines if line.startswith("SESSION_ID:")),
            None,
        )
        if embedded != session_id:
            return _refuse(f"trace-session mismatch: trace={embedded!r} record={session_id!r}")
        out, in_dispatched, seen = [], False, False
        found_confirmed = found_dispatched = False
        for line in lines:
            if confirmed is not None and line.startswith("CONFIRMED:"):
                line = f"CONFIRMED: {confirmed}"
                found_confirmed = True
            if line.startswith("DISPATCHED:"):
                in_dispatched = True
                found_dispatched = True
                line = "DISPATCHED:"                  # always drop the fill-in placeholder
            elif in_dispatched and line.startswith("=== END"):
                if dispatched and not seen:
                    out.append(f"  {dispatched}")
                in_dispatched = False
            elif in_dispatched and dispatched:
                existing = line.strip().lstrip("-* ").split(" ", 1)[0]
                if existing == dispatched:
                    seen = True
            out.append(line)
        if (confirmed is not None and not found_confirmed) or not found_dispatched:
            return _refuse("trace-edit failed: required trace labels are missing")
        temporary = f"{path}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        os.replace(temporary, path)
        return True
    except Exception as err:
        return _refuse(f"trace-edit failed: {err!r}")


def _cleanup_oauth_token():
    try:
        with open(_run_oauth_token_path(), encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError:
        return
    try:
        os.remove(_run_oauth_token_path())      # this run's own copy: no lock needed
    except OSError:
        pass
    with _token_claim_lock() as acquired:
        if not acquired:
            return
        claim_dir, marker = _token_claim(token)
        try:
            os.remove(marker)
        except OSError:
            pass
        try:
            other_claims = bool(os.listdir(claim_dir))
        except OSError:
            other_claims = False
        if other_claims:
            return
        try:
            os.rmdir(claim_dir)
        except OSError:
            pass
        try:
            with open(_oauth_token_path(), encoding="utf-8") as fh:
                shared = fh.read().strip()
            if shared == token:
                os.remove(_oauth_token_path())
        except OSError:
            pass


def record(what, session_id, skill_id=None, result=None, source_mode=None):
    """Advance the run one step: update the trace and emit the matching event, with every
    field derived from the stored plan. Return whether the transition was accepted."""
    try:
        if not session_id or trace_session() != session_id:
            return _refuse(f"session {session_id!r} does not match this run's trace")
        state = _load_state(session_id)
        if state.get("run_finished"):
            return _refuse(f"run {session_id} is already terminal")
        steps = state.setdefault("steps", {})
        plan = state.get("plan") or []
        node = next((n for n in plan if n.get("id") == skill_id), None)
        step = _step_fields(node) if node else {}
        now = int(time.time() * 1000)

        if what in ("confirmed", "declined"):
            if state.get("confirmed") is not None:
                return _refuse(f"confirmation already recorded for {session_id}")
            if what == "confirmed" and int((state.get("shape") or {}).get("choice_count", 0)):
                return _refuse("unresolved choices block confirmation")
            if (steps.get("dd-account-setup") or {}).get("status") != "success":
                return _refuse("account preflight must succeed before confirmation")
            value = what == "confirmed"
            if not _edit_trace(session_id, confirmed="yes" if value else "no"):
                return False
            state["confirmed"] = value
            if not _save_state(session_id, state):
                return _refuse("could not save the run state")
            emit("skill_run", what, session_id)
        elif what in ("started", "finished", "skipped"):
            if not node:
                return _refuse(f"skill {skill_id!r} is not allowed by this run")
            entry = steps.get(skill_id)
            if what == "started":
                if entry:
                    return _refuse(f"duplicate/out-of-order start for {skill_id}")
                if any(
                        item.get("started_at") is not None and not item.get("status")
                        for item in steps.values()):
                    return _refuse(f"another skill is still active before {skill_id}")
                if skill_id != "dd-account-setup":
                    if state.get("confirmed") is not True:
                        return _refuse(f"{skill_id} cannot start before confirmation")
                    if (steps.get("dd-account-setup") or {}).get("status") != "success":
                        return _refuse(f"{skill_id} cannot start before account preflight")
                    if any(
                            (steps.get(required) or {}).get("status") != "success"
                            for required in node.get("requires", [])):
                        return _refuse(f"a prerequisite for {skill_id} did not succeed")
                    index = plan.index(node)
                    if any(
                            (steps.get(prior["id"]) or {}).get("status")
                            not in ("success", "failed", "skipped")
                            for prior in plan[:index]):
                        return _refuse(f"{skill_id} is out of plan order")
                steps[skill_id] = {"started_at": now, "source_mode": source_mode}
                if not _save_state(session_id, state):
                    return _refuse("could not save the run state")
                emit("skill_step", "started", session_id, {**step, "source_mode": source_mode})
            elif what == "finished":
                if result not in ("success", "failed") or not entry or "started_at" not in entry:
                    return _refuse(f"invalid finish for {skill_id} result={result!r}")
                if entry.get("status"):
                    return _refuse(f"duplicate finish for {skill_id}")
                if result == "success" and not _edit_trace(session_id, dispatched=skill_id):
                    return False
                entry["status"] = result
                if not _save_state(session_id, state):
                    return _refuse("could not save the run state")
                duration = None if skill_id == "dd-account-setup" else now - entry["started_at"]
                fields = {
                    **step, "result": result, "skill_invoked": True,
                    "instrumentation_invoked": node.get("kind") in _INSTRUMENTING_KINDS}
                if duration is not None:
                    fields["duration_ms"] = duration
                emit("skill_step", "finished", session_id, fields)
            else:
                result = result or (
                    "declined" if state.get("confirmed") is False else "skipped_dependency")
                if result not in ("skipped_dependency", "declined", "source_unreachable",
                                  "out_of_scope") or entry:
                    return _refuse(f"invalid skip for {skill_id} result={result!r}")
                if skill_id == "dd-account-setup":
                    return _refuse("account preflight cannot be skipped after resolve")
                # A node removed under the CAVEATS scope rule never runs. It is closed at the
                # confirm gate, in any plan position, so later nodes need not wait on it.
                scoped_out = result == "out_of_scope"
                if scoped_out:
                    if (steps.get("dd-account-setup") or {}).get("status") != "success":
                        return _refuse("account preflight must succeed before a scope decision")
                elif state.get("confirmed") not in (True, False):
                    return _refuse(f"{skill_id} cannot be skipped before a decision")
                if result == "declined" and state.get("confirmed") is not False:
                    return _refuse(f"{skill_id} is not part of a declined run")
                if result == "skipped_dependency" and not any(
                        (steps.get(required) or {}).get("status") in ("failed", "skipped")
                        for required in node.get("requires", [])):
                    return _refuse(f"{skill_id} has no failed/skipped prerequisite")
                index = plan.index(node)
                if not scoped_out and any(
                        (steps.get(prior["id"]) or {}).get("status")
                        not in ("success", "failed", "skipped")
                        for prior in plan[:index]):
                    return _refuse(f"{skill_id} skip is out of plan order")
                steps[skill_id] = {"status": "skipped", "skip_result": result}
                if not _save_state(session_id, state):
                    return _refuse("could not save the run state")
                emit("skill_step", "skipped", session_id, {**step, "result": result})
        elif what == "run-finished":
            # A declined plan runs nothing more, so close its open nodes here instead of
            # requiring one `record skipped` per node before the run can end.
            declined = []
            if state.get("confirmed") is False:
                declined = [item for item in plan if item["id"] not in steps]
                for item in declined:
                    steps[item["id"]] = {"status": "skipped", "skip_result": "declined"}
            missing = [
                item["id"] for item in plan
                if (steps.get(item["id"]) or {}).get("status")
                not in ("success", "failed", "skipped")
            ]
            if missing:
                return _refuse(f"run-finished before terminal steps: {', '.join(missing)}")
            statuses = [item.get("status") for item in steps.values()]
            s, f, k = (statuses.count(value) for value in ("success", "failed", "skipped"))
            if not result:
                if state.get("confirmed") is False:
                    result = "cancelled"
                elif not (s or f or k):
                    result = "blocked"
                else:
                    shape = state.get("shape") or {}
                    result = _derive_result(
                        s, f, k - _scope_skips(state),
                        int(shape.get("dead_end_count", 0)) + _scope_skips(state),   # scope gaps are dead ends
                        _productive_success(state), int(shape.get("choice_count", 0)))
            if result not in ("success", "partial_success", "failed", "blocked", "cancelled"):
                return _refuse(f"invalid terminal result {result!r}")
            state["run_finished"] = True
            state["result"] = result
            if not _save_state(session_id, state):
                return _refuse("could not save the run state")
            for item in declined:
                emit("skill_step", "skipped", session_id,
                     {**_step_fields(item), "result": "declined"})
            emit("skill_run", "finished", session_id, {
                "result": result, "step_success_count": s,
                "step_failed_count": f, "step_skipped_count": k})
            _cleanup_oauth_token()
        else:
            return _refuse(f"unknown step {what!r}")
        return True
    except Exception as err:
        return _refuse(f"unexpected error in {what}: {err!r}")


def _record_cli(argv):
    parser = argparse.ArgumentParser(prog="emit.py record",
                                     description="update the trace and emit one run event")
    parser.add_argument("what", choices=("confirmed", "declined", "started", "finished",
                                         "skipped", "run-finished"))
    parser.add_argument("skill_id", nargs="?")
    parser.add_argument("--result")
    parser.add_argument("--source-mode", choices=("installed", "fetched"))
    parser.add_argument("--session-id", default="")
    args = parser.parse_args(argv)
    session_id = args.session_id or current_session()
    if not session_id:
        parser.error(f"record {args.what} cannot find this run's session")
    if args.what in ("started", "finished", "skipped") and not args.skill_id:
        parser.error(f"record {args.what} needs a skill_id")
    if args.what not in ("started", "finished", "skipped") and args.skill_id:
        parser.error(f"record {args.what} does not take a skill_id")
    if args.what == "finished" and args.result not in ("success", "failed"):
        parser.error("record finished needs --result success|failed")
    if args.what == "skipped" and args.result not in (
            None, "skipped_dependency", "declined", "source_unreachable", "out_of_scope"):
        parser.error("record skipped result must be "
                     "skipped_dependency|declined|source_unreachable|out_of_scope")
    if args.what == "run-finished" and args.result not in (
            None, "success", "partial_success", "failed", "blocked", "cancelled"):
        parser.error("record run-finished has an invalid --result")
    if args.what in ("confirmed", "declined", "started") and args.result is not None:
        parser.error(f"record {args.what} does not take --result")
    if args.what != "started" and args.source_mode is not None:
        parser.error(f"record {args.what} does not take --source-mode")
    return 0 if record(
        args.what, session_id, args.skill_id, args.result, args.source_mode) else 2


def _cli(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["trace-path"]:
        print(trace_path())
        return 0
    if argv[:1] == ["record"]:
        return _record_cli(argv[1:])
    parser = argparse.ArgumentParser(
        description="best-effort dd-orchestrator telemetry emitter (v1)")
    parser.add_argument("event_type", choices=sorted(EVENT_TYPES))
    parser.add_argument("--action", required=True, help="event lifecycle action")
    parser.add_argument("--session-id", required=True,
                        help="the run's explicit session id")
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
