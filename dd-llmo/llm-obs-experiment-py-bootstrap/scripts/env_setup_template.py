# ─── 1. Env setup ─────────────────────────────────────────────────────────────
#
# Auto-discovers .env files at runtime. NO python-dotenv dependency — the
# `_load_env_files` helper below is a tiny pure-Python walker.
#
# Discovery order (first non-empty value wins per key; shell env always
# overrides files):
#   1. ENV_FILE_OVERRIDE entries below (set at generation time from --env-file)
#   2. This file's directory: ./.env and .env.local
#   3. Current working directory: ./.env and .env.local
#   4. Parent walk from cwd up to /
#   5. ~/.datadog/credentials
#
# To override on a per-run basis, just `export DD_API_KEY=...` in your shell
# before running this file — the loader never overwrites a value already in
# os.environ.

import os
import pathlib


def _load_env_files(extra: list[str] | None = None) -> list[str]:
    here = pathlib.Path(__file__).resolve().parent
    cwd = pathlib.Path.cwd().resolve()
    candidates: list[pathlib.Path] = []
    for p in extra or []:
        candidates.append(pathlib.Path(p).expanduser())
    candidates += [
        here / ".env",
        here / ".env.local",
        cwd / ".env",
        cwd / ".env.local",
    ]
    p = cwd
    while p != p.parent and p != pathlib.Path.home().parent:
        candidates.append(p / ".env")
        p = p.parent
    candidates.append(pathlib.Path.home() / ".datadog" / "credentials")

    loaded: list[str] = []
    seen: set[pathlib.Path] = set()
    for path in candidates:
        try:
            path = path.resolve()
        except Exception:
            continue
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            text = path.read_text()
        except Exception:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
        loaded.append(str(path))
    return loaded


# Baked in at generation time from --env-file (always tried first). Edit this
# list to point at a different .env without regenerating the file.
ENV_FILE_OVERRIDE: list[str] = {{ENV_FILE_OVERRIDE_LIST}}

_loaded_env_paths = _load_env_files(ENV_FILE_OVERRIDE)
if _loaded_env_paths:
    print(f"Loaded credentials from: {', '.join(_loaded_env_paths)}")

# ─── Required key assertions ─────────────────────────────────────────────────
# Datadog keys are always required. Provider-key assertions are emitted
# conditionally by the skill (see Workflow step 2.6 — only the keys the wired
# task_fn actually calls).

assert os.getenv("DD_API_KEY"), (
    "DD_API_KEY is not set. Export it in your shell or add it to a discovered "
    ".env file (this file checked: cwd, app dir, parent dirs, "
    "~/.datadog/credentials)."
)
assert os.getenv("DD_APPLICATION_KEY") or os.getenv("DD_APP_KEY"), (
    "DD_APPLICATION_KEY (or its alias DD_APP_KEY) is not set. Same fallback "
    "paths as above."
)
{{PROVIDER_ASSERTS}}
