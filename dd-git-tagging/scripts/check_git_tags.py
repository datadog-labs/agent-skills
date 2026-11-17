#!/usr/bin/env python3
"""Check whether a service's spans carry Git metadata (read-only).

Datadog links telemetry to source code when spans carry both `git.commit.sha`
and `git.repository_url`. This searches the service's top-level spans for
them, the same fields Datadog's source code setup page and Live Debugger query,
plus the tracer's raw `_dd.git.*` fields, in case an intake path keeps them
without promoting them. `source` in the result says which form was found.

Authentication: `pup api` with the user's session when pup is signed in,
otherwise `DD_API_KEY` + `DD_APP_KEY` against `api.<DD_SITE>`.

Search covers every span for the last 15 minutes but only indexed spans beyond that, so
without `--from` it checks the last 15 minutes and falls back to a day of indexed spans.

Exit codes: 0 tagged, 2 spans arrive but lack Git metadata, 3 no spans in the
window, 1 error. `--wait` polls until tagged or the timeout passes.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

SEARCH_PATH = "/api/v2/spans/events/search"
SHA = "(git.commit.sha:* OR @git.commit.sha:* OR @_dd.git.commit.sha:*)"
URL = "(git.repository_url:* OR @git.repository_url:* OR @_dd.git.repository_url:*)"

TAGGED, UNTAGGED, NO_SPANS = "tagged", "untagged", "no_spans"
LIVE_WINDOW, INDEXED_WINDOW = "now-15m", "now-1d"
EXIT = {TAGGED: 0, UNTAGGED: 2, NO_SPANS: 3}


class CheckError(Exception):
    pass


def quote(value):
    return f'"{value}"' if any(c in value for c in ' :()"') else value


def base_query(service, env=None):
    q = f"@_top_level:1 service:{quote(service)}"
    return f"{q} env:{quote(env)}" if env else q


def search_body(query, window):
    return {"data": {"type": "search_request", "attributes": {
        "filter": {"query": query, "from": window, "to": "now"},
        "page": {"limit": 1},
    }}}


def first_json(text):
    """The first JSON value in pup's output, which can start with warning lines."""
    start = text.find("{")
    try:
        return json.JSONDecoder().raw_decode(text, start)[0] if start >= 0 else None
    except json.JSONDecodeError:
        return None


def pup_ready(pup):
    path = shutil.which(pup)
    if not path or os.path.splitext(os.path.basename(path))[0].lower() != "pup":
        return False
    try:
        # no-dd-sa
        proc = subprocess.run([path, "auth", "status"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    status = first_json(proc.stdout or "")
    return isinstance(status, dict) and bool(status.get("authenticated"))


def search_pup(pup, body):
    cmd = [pup, "api", "--no-agent", "--method", "POST", SEARCH_PATH, "--input", "-"]
    try:
        # no-dd-sa
        proc = subprocess.run(cmd, input=json.dumps(body), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CheckError(f"could not run pup api: {e}")
    out = proc.stdout or ""
    payload = first_json(out)
    if proc.returncode != 0 or not isinstance(payload, dict) or payload.get("errors"):
        detail = (payload or {}).get("errors") or (out + (proc.stderr or "")).strip()[:200]
        raise CheckError(f"span search failed: {detail}")
    return payload


def search_keys(site, body):
    api_key, app_key = os.environ.get("DD_API_KEY"), os.environ.get("DD_APP_KEY")
    if not (api_key and app_key and site):
        raise CheckError("no pup session and DD_API_KEY, DD_APP_KEY, DD_SITE are not all set")
    req = urllib.request.Request(
        f"https://api.{site}{SEARCH_PATH}", data=json.dumps(body).encode(), method="POST",
        headers={"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise CheckError(f"span search failed: HTTP {e.code} {e.read()[:200]!r}")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise CheckError(f"span search failed: {e}")


def flatten(value, prefix=""):
    """Custom attributes as dotted keys; the API nests them on some sites and flattens them on others."""
    if not isinstance(value, dict):
        return {prefix: value}
    out = {}
    for k, v in value.items():
        out.update(flatten(v, f"{prefix}{k.lstrip('@')}."))
    return {k.rstrip("."): v for k, v in out.items()}


def git_values(span):
    """(sha, repository_url, source) from a span's tags or custom attributes."""
    attrs = span.get("attributes") or {}
    fields = {**flatten(attrs.get("custom") or {}),
              **dict(t.split(":", 1) for t in attrs.get("tags") or [] if ":" in t)}
    sha, url = fields.get("git.commit.sha"), fields.get("git.repository_url")
    source = "promoted" if sha and url else "tracer"
    return sha or fields.get("_dd.git.commit.sha"), url or fields.get("_dd.git.repository_url"), source


def check(search, service, env=None, window=None):
    if window is None:
        result = check(search, service, env, LIVE_WINDOW)
        return result if result["status"] != NO_SPANS else check(search, service, env, INDEXED_WINDOW)
    base = base_query(service, env)

    def first(query):
        data = search(search_body(query, window)).get("data") or []
        return data[0] if data else None

    result = {"service": service, "env": env, "window": window, "query": f"{base} {SHA} {URL}"}
    span = first(result["query"])
    if span:
        sha, url, source = git_values(span)
        return {**result, "status": TAGGED, "commit_sha": sha, "repository_url": url, "source": source}
    if not first(base):
        return {**result, "status": NO_SPANS}
    return {**result, "status": UNTAGGED,
            "has_commit_sha": bool(first(f"{base} {SHA}")),
            "has_repository_url": bool(first(f"{base} {URL}"))}


def describe(r):
    where = r["service"] + (f" (env {r['env']})" if r["env"] else "")
    if r["status"] == TAGGED:
        note = "" if r.get("source") == "promoted" else " (sent by the tracer; Datadog hasn't promoted it to git.* tags yet)"
        return f"Tagged: {where} spans carry commit {r['commit_sha']} from {r['repository_url']}{note}."
    if r["status"] == NO_SPANS:
        return f"No spans from {where} since {r['window']}: check the service name and that it is running."
    missing = [name for name, ok in (("git.commit.sha", r["has_commit_sha"]),
                                     ("git.repository_url", r["has_repository_url"])) if not ok]
    if not missing:
        return f"Not tagged: {where} spans carry the commit and the repository, but never both on the same span."
    return f"Not tagged: {where} spans arrive without {' and '.join(missing)}."


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--service", required=True)
    p.add_argument("--env")
    p.add_argument("--from", dest="window", help="search window start (default: now-15m, then indexed spans for now-1d)")
    p.add_argument("--site", default=os.environ.get("DD_SITE"), help="for the API-key fallback")
    p.add_argument("--pup", default="pup")
    p.add_argument("--wait", type=int, default=0, metavar="SECONDS", help="poll until tagged")
    p.add_argument("--interval", type=int, default=15)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    use_pup = pup_ready(args.pup)

    def search(body):
        return search_pup(args.pup, body) if use_pup else search_keys(args.site, body)

    deadline = time.monotonic() + args.wait
    try:
        while True:
            result = check(search, args.service, args.env, args.window)
            if result["status"] == TAGGED or time.monotonic() >= deadline:
                break
            time.sleep(args.interval)
    except CheckError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if args.json else describe(result))
    return EXIT[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
