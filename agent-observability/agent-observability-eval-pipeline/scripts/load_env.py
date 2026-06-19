"""Discover .env-style files and import their keys into os.environ.

Used by both:
- scripts/publish_dataset.py (imported at runtime)
- agent-observability/agent-observability-experiment-py-bootstrap/scripts/env_setup_template.py
  (kept in sync as a verbatim copy — that copy is embedded into the
  user's generated experiment file so the file runs standalone without
  depending on this skill being installed)

Discovery order (first hit per variable wins; shell env vars always
override file-loaded values):

  1. Any path in `extra` (typically the resolved --env-file argument)
  2. <this-script-dir>/.env  and  .env.local
  3. <cwd>/.env  and  .env.local
  4. Parent-walk from cwd up to /  (stops at $HOME's parent)
  5. ~/.datadog/credentials  (well-known Datadog convention)

Never overwrites a value that is already in os.environ — that is the
contract that lets users override anything by `export KEY=...` in
their shell before running.
"""
from __future__ import annotations

import os
import pathlib


def load_env_files(extra: list[str] | None = None) -> list[str]:
    """Walk discovery locations and return the absolute paths of files loaded."""
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
            if k and k not in os.environ:  # shell wins over file
                os.environ[k] = v
        loaded.append(str(path))
    return loaded
