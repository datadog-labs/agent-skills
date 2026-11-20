"""Shared errors and sanitization for the agent fleet installation flow."""

from __future__ import annotations

import os
import re
from typing import Any

SENSITIVE_FIELDS = {
    "api_key",
    "app_key",
    "password",
    "private_key",
    "sa_private_key",
    "secret",
    "static_api_key",
    "token",
}


def _sensitive_field(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return (
        normalized in SENSITIVE_FIELDS
        or normalized.endswith("_token")
        or normalized.endswith("_password")
        or normalized.endswith("_secret")
    )


class SetupError(Exception):
    def __init__(
        self,
        reason: str,
        message: str,
        *,
        recoverable: bool = True,
        command: list[str] | None = None,
        exit_code: int | None = None,
        stderr: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.recoverable = recoverable
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        value = {
            "reason": self.reason,
            "message": sanitize_text(self.message),
            "recoverable": self.recoverable,
            "command": sanitize_value(self.command),
            "exit_code": self.exit_code,
            "stderr": sanitize_text(self.stderr),
            "details": sanitize_value(self.details),
        }
        return {key: item for key, item in value.items() if item not in (None, {})}


def sanitize_text(value: str | None, limit: int = 2000) -> str | None:
    if not value:
        return None
    sanitized = value
    for name in ("DD_API_KEY", "DD_APP_KEY"):
        secret = os.environ.get(name)
        if secret:
            sanitized = sanitized.replace(secret, f"[REDACTED_{name}]")
    sanitized = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED_PRIVATE_KEY]",
        sanitized,
        flags=re.DOTALL,
    )
    sanitized = re.sub(
        r"(?i)([\"']?(?:token|api[_-]?key|app[_-]?key|private[_-]?key|"
        r"static[_-]?api[_-]?key|sa[_-]?private[_-]?key)[\"']?\s*[=:]\s*)"
        r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}]+)",
        r"\1[REDACTED]",
        sanitized,
    )
    return (
        f"{sanitized[:limit]}…[truncated]"
        if len(sanitized) > limit
        else sanitized
    )


def sanitize_value(value: Any, *, field: str | None = None) -> Any:
    if field and _sensitive_field(field):
        return "[REDACTED]"
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        result = []
        redact_next = False
        for item in value:
            if redact_next:
                result.append("[REDACTED]")
                redact_next = False
                continue
            result.append(sanitize_value(item))
            if isinstance(item, str) and item.startswith("-") and "=" not in item:
                redact_next = _sensitive_field(item.lstrip("-"))
        return result
    if isinstance(value, dict):
        return {
            key: sanitize_value(item, field=str(key))
            for key, item in value.items()
        }
    return value
