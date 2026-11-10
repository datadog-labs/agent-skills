#!/usr/bin/env python3
"""Interactive Datadog Anthropic Usage and Costs integration setup."""

from __future__ import annotations

import argparse
import getpass
import hmac
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
import webbrowser
from pathlib import Path
from typing import Any, TextIO

ACCOUNTS_PATH = "/api/v2/web-integrations/anthropic-usage-and-costs/accounts"
ANTHROPIC_ORG_URL = "https://api.anthropic.com/v1/organizations/me"
ANTHROPIC_ANALYTICS_URL = "https://api.anthropic.com/v1/organizations/analytics/user_usage_report"
# The Anthropic plan decides which key the integration takes:
# - platform: a key with Admin API access (an Admin key sk-ant-admin01-... or an
#   organization-scoped sk-ant-usr... key). Only the sk-ant- family is checked
#   here; whether the key can read usage and cost data is Anthropic's answer to
#   /v1/organizations/me.
# - enterprise: an Analytics API key with the read:analytics scope, created by
#   the organization's Primary Owner. Its format is not documented, so only
#   whitespace and length are checked; Anthropic's analytics endpoint decides.
PLANS: dict[str, dict[str, Any]] = {
    "platform": {
        "token_url": "https://platform.claude.com/settings/keys?ws=default",
        "token_pattern": re.compile(r"^sk-ant-\S+$"),
        "format_hint": "start with sk-ant- and contain no whitespace",
        "key_label": "key with Admin API access",
        "instructions": (
            "Use an Admin key or an organization-scoped key; a workspace API key "
            "cannot read usage and cost data."
        ),
    },
    "enterprise": {
        # Per Anthropic's docs; not yet confirmed against a live Enterprise org.
        # The earlier location was https://claude.ai/analytics/api-keys.
        "token_url": "https://claude.ai/admin-settings/api-access",
        "token_pattern": re.compile(r"^\S{20,}$"),
        "format_hint": "be at least 20 characters with no whitespace",
        "key_label": "Analytics API key",
        "instructions": (
            "Create it with the read:analytics scope. Only the organization's "
            "Primary Owner can create Analytics API keys. If that page does not "
            "offer Analytics API keys, try https://claude.ai/analytics/api-keys."
        ),
    },
}
ANTHROPIC_VERSION = "2023-06-01"
# Usage baseline taken just before the account is created, so verify can count
# only data that arrives after the install. The window starts 3 hours back
# because the first data collected covers that far back; its total can only grow when new
# data (or late usage in an existing bucket) lands.
USAGE_METRIC = "anthropic.messages.input_tokens"
USAGE_LOOKBACK_SECONDS = 3 * 3600
ANTHROPIC_TIMEOUT = 30
WINDOWS_CREATE_NEW_CONSOLE = 0x00000010
HELPER_MESSAGE_LIMIT = 16384
HELPER_CHALLENGE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
HELPER_CONNECT_TIMEOUT = 60


class SetupError(Exception):
    def __init__(
        self,
        reason: str,
        message: str,
        *,
        recoverable: bool = True,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.recoverable = recoverable
        self.details = details or {}

    def as_dict(self, *, token: str | None = None) -> dict[str, Any]:
        value = {
            "reason": self.reason,
            "message": _sanitize_text(self.message, token=token),
            "recoverable": self.recoverable,
            "details": _sanitize_value(self.details, token=token),
        }
        return {key: item for key, item in value.items() if item not in (None, {})}


def _sanitize_text(value: str | None, *, token: str | None = None) -> str | None:
    if not value:
        return None
    sanitized = value
    for secret in [token]:
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    sanitized = re.sub(r"sk-ant-\S+", "[REDACTED_ANTHROPIC_KEY]", sanitized)
    sanitized = re.sub(
        r"(?i)([\"']?(?:api[_-]?token|api[_-]?key|app[_-]?key|token|secret)"
        r"[\"']?\s*[=:]\s*)"
        r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}]+)",
        r"\1[REDACTED]",
        sanitized,
    )
    return (
        f"{sanitized[:2000]}…[truncated]"
        if len(sanitized) > 2000
        else sanitized
    )


def _sanitize_value(
    value: Any,
    *,
    field: str | None = None,
    token: str | None = None,
) -> Any:
    normalized = re.sub(r"[^a-z0-9]+", "_", (field or "").lower()).strip("_")
    if normalized in {"api_token", "api_key", "app_key", "token", "secret"}:
        return "[REDACTED]"
    if isinstance(value, str):
        return _sanitize_text(value, token=token)
    if isinstance(value, list):
        return [_sanitize_value(item, token=token) for item in value]
    if isinstance(value, dict):
        return {
            key: _sanitize_value(item, field=str(key), token=token)
            for key, item in value.items()
        }
    return value


def _pup_base(command: str) -> list[str]:
    return ["pup", command, "--no-envelope", "--output", "json"]


def _run_pup(
    command: list[str],
    *,
    payload: bytes | None = None,
    token: str | None = None,
) -> Any:
    # No client-side timeout: wait for Datadog to answer (or time out on the
    # server) or for the caller to kill the command. Abandoning a POST locally
    # would only make its outcome ambiguous.
    try:
        result = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise SetupError(
            "pup_transport_error",
            "Pup could not complete the authenticated Datadog request.",
            details={
                "command": command[:2],
                "error_type": type(error).__name__,
                "message": _sanitize_text(str(error), token=token),
            },
        ) from error
    stdout = result.stdout.decode(errors="replace")
    stderr = result.stderr.decode(errors="replace")
    if result.returncode != 0:
        # Pup reports API errors on stderr as "Error: HTTP <status> <url>: ...".
        status = re.search(r"\bHTTP (\d{3})\b", stderr)
        raise SetupError(
            "pup_request_failed",
            "Pup rejected or could not complete the Datadog request.",
            details={
                "command": command[:2],
                "exit_code": result.returncode,
                "http_status": int(status.group(1)) if status else None,
                "stdout": _sanitize_text(stdout, token=token),
                "stderr": _sanitize_text(stderr, token=token),
            },
        )
    if not stdout.strip():
        return {}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as error:
        raise SetupError(
            "pup_response_invalid",
            "Pup returned a non-JSON Datadog response.",
            details={"stdout": _sanitize_text(stdout, token=token)},
        ) from error


def _pup_api(
    method: str,
    path: str,
    *,
    payload: bytes | None = None,
    token: str | None = None,
) -> Any:
    command = _pup_base("api")
    command.extend(["--method", method])
    if payload is not None:
        command.extend(["--input", "-"])
    command.append(path)
    return _run_pup(
        command,
        payload=payload,
        token=token,
    )


def _account_summary(value: Any) -> list[dict[str, Any]]:
    items = value.get("data", []) if isinstance(value, dict) else []
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        attributes = item.get("attributes", {})
        settings = attributes.get("settings", {}) if isinstance(attributes, dict) else {}
        if not isinstance(settings, dict):
            settings = {}
        result.append(
            {
                "id": item.get("id"),
                "name": attributes.get("name"),
                # Unset follows the schema default: cost data ingestion off.
                "ccm_enabled": settings.get("ccm_enabled") is True,
            }
        )
    return result


def _list_accounts() -> list[dict[str, Any]]:
    return _account_summary(_pup_api("GET", ACCOUNTS_PATH))


def _pup_version() -> tuple[int, int, int] | None:
    """Parse `pup --version` output such as "pup 2.4.3"; None when unreadable."""
    try:
        result = subprocess.run(
            ["pup", "--version"],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    stdout = result.stdout.decode(errors="replace") if result.stdout else ""
    if result.returncode:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", stdout)
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)) if match.group(3) else 0,
    )


def _validate_pup() -> dict[str, Any]:
    if shutil.which("pup") is None:
        raise SetupError(
            "pup_cli_missing",
            (
                "Pup is not installed. Install it with `brew tap datadog-labs/pack && "
                "brew install datadog-labs/pack/pup`, `curl -fsSL "
                "https://raw.githubusercontent.com/DataDog/pup/main/install.sh | sh`, "
                "or `npm install -g @datadog/pup` (see https://github.com/DataDog/pup), then run `pup auth login`."
            ),
            recoverable=False,
        )
    version = _pup_version()
    if version is None or version[0] < 2:
        # The scripts rely on v2-only CLI flags such as --no-envelope; older
        # Pup versions fail on the first request with an opaque error.
        raise SetupError(
            "pup_cli_outdated",
            "Pup 2 or later is required; the installed Pup is too old.",
            recoverable=True,
            details={
                "installed_version": ".".join(str(part) for part in version)
                if version
                else None,
                "remediation": (
                    "Upgrade Pup from https://github.com/DataDog/pup/releases "
                    "(download the release archive, verify its checksum, and "
                    "install it, for example "
                    "`sudo install -m 0755 ./pup /usr/local/bin/pup`), then "
                    "rerun setup."
                ),
            },
        )
    status_command = [
        "pup",
        "auth",
        "status",
        "--no-envelope",
        "--output",
        "json",
    ]
    status = _run_pup(status_command)
    if not isinstance(status, dict) or not status.get("authenticated"):
        raise SetupError(
            "pup_not_authenticated",
            "Authenticate to Datadog with `pup auth login`.",
        )
    if status.get("token_type") == "bearer":
        scopes = {
            item for item in status.get("scopes", [])
            if isinstance(item, str)
        }
        required = {"integrations_read", "manage_integrations"}
        missing = sorted(required - scopes)
        if missing:
            raise SetupError(
                "pup_scopes_missing",
                "Pup's OAuth session lacks required integration permissions.",
                details={
                    "missing_scopes": missing,
                    "remediation": (
                        "Run `pup auth login --extra-scopes "
                        "integrations_read,manage_integrations`."
                    ),
                },
            )
    try:
        _pup_api("GET", "/api/v2/current_user")
    except SetupError as error:
        raise SetupError(
            "pup_authentication_failed",
            (
                "Pup could not authenticate to Datadog. Run `pup auth refresh`; "
                "if that fails, run `pup auth login`."
            ),
            details={"pup_error": error.as_dict()},
        ) from error
    return {
        "site": status.get("site"),
        "org": status.get("org"),
        "token_type": status.get("token_type"),
    }


def _existing_account(
    accounts: list[dict[str, Any]],
    *,
    account_name: str,
) -> dict[str, Any] | None:
    matches = [account for account in accounts if account.get("name") == account_name]
    if len(matches) > 1:
        raise SetupError(
            "duplicate_datadog_accounts",
            f"Multiple Datadog Anthropic accounts are named {account_name}.",
            recoverable=False,
            details={"account_ids": [item.get("id") for item in matches]},
        )
    if not matches:
        return None
    return matches[0]


def _open_tty() -> TextIO:
    device = "CONOUT$" if sys.platform == "win32" else "/dev/tty"
    try:
        tty = open(device, "w", encoding="utf-8", buffering=1)
    except OSError as error:
        raise SetupError(
            "interactive_tty_required",
            (
                "Run this script in a user-visible interactive terminal; "
                "credential entry cannot use redirected or captured input."
            ),
            recoverable=False,
        ) from error
    if not os.isatty(tty.fileno()):
        tty.close()
        raise SetupError(
            "interactive_tty_required",
            "The credential prompt requires a controlling TTY.",
            recoverable=False,
        )
    return tty


def _read_token_from_tty(tty: TextIO, plan: str = "platform") -> str:
    config = PLANS[plan]
    tty.write(
        "\nThis is a local terminal session. No AI agent is running here, and the key you paste is not visible to the agent.\n"
        f"\nCreate an Anthropic {config['key_label']} at:\n"
        f"  {config['token_url']}\n"
        f"{config['instructions']}\n"
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass(
                "Paste the Anthropic key: ",
                stream=tty,
            )
            token = token.strip()
    except getpass.GetPassWarning as error:
        raise SetupError(
            "secure_credential_input_unavailable",
            (
                "Terminal echo could not be disabled; credential entry was "
                "aborted before reading input."
            ),
            recoverable=False,
        ) from error
    except (EOFError, KeyboardInterrupt) as error:
        raise SetupError(
            "credential_entry_cancelled",
            "Anthropic key entry was cancelled.",
        ) from error
    if not config["token_pattern"].fullmatch(token):
        raise SetupError(
            "invalid_anthropic_key",
            f"The key must {config['format_hint']}.",
        )
    return token


def _launch_terminal(command: list[str]) -> None:
    if sys.platform == "win32":
        try:
            subprocess.Popen(
                command,
                creationflags=WINDOWS_CREATE_NEW_CONSOLE,
                close_fds=True,
            )
        except OSError as error:
            raise SetupError(
                "terminal_launch_failed",
                "Windows could not open a console for secure credential entry.",
                details={"error_type": type(error).__name__},
            ) from error
        return

    if sys.platform == "darwin":
        command_text = "exec " + shlex.join(command)
        apple_script = (
            'tell application "Terminal"\n'
            # AppleScript does not understand JSON's \uXXXX escapes.
            f"  do script {json.dumps(command_text, ensure_ascii=False)}\n"
            "  activate\n"
            "end tell"
        )
        result = subprocess.run(
            ["osascript", "-e", apple_script],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            raise SetupError(
                "terminal_launch_failed",
                "macOS Terminal could not be opened for secure credential entry.",
                details={
                    "exit_code": result.returncode,
                    "stderr": _sanitize_text(result.stderr),
                },
            )
        return

    if sys.platform.startswith("linux"):
        distro = os.environ.get("WSL_DISTRO_NAME")
        if distro:
            wsl_command = ["wsl.exe", "-d", distro, "--exec", *command]
            for windows_command in [
                ["wt.exe", *wsl_command],
                # No title argument: start only treats a quoted first argument
                # as a title, and an unquoted one would be run as the program.
                ["cmd.exe", "/c", "start", *wsl_command],
            ]:
                if shutil.which(windows_command[0]):
                    try:
                        subprocess.Popen(windows_command, start_new_session=True)
                    except OSError:
                        continue
                    return
        if os.environ.get("TMUX") and shutil.which("tmux"):
            # Inside a tmux session, a new tmux window is a user-visible
            # terminal even on headless hosts with no DISPLAY, and it beats a
            # graphical emulator that may open where the user cannot see it.
            try:
                result = subprocess.run(
                    [
                        "tmux",
                        "new-window",
                        "-n",
                        "anthropic-token",
                        "exec " + shlex.join(command),
                    ],
                    start_new_session=True,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                if result.returncode == 0:
                    return
            except (OSError, subprocess.TimeoutExpired):
                pass  # Fall through to the graphical candidates below.
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise SetupError(
                "terminal_launch_failed",
                "No graphical display and no tmux session are available to "
                "open a credential terminal.",
            )
        # x-terminal-emulator goes last: it can resolve to an emulator whose
        # -e takes a single command string, which would mangle the helper.
        candidates = [
            (["gnome-terminal"], ["--"]),
            (["konsole"], ["-e"]),
            (["xterm"], ["-e"]),
            (["x-terminal-emulator"], ["-e"]),
        ]
        for executable, separator in candidates:
            if shutil.which(executable[0]):
                try:
                    subprocess.Popen(
                        [*executable, *separator, *command],
                        start_new_session=True,
                    )
                except OSError:
                    continue
                return
        raise SetupError(
            "terminal_launch_failed",
            "No supported graphical Linux terminal emulator was found.",
            details={
                "supported": [
                    "gnome-terminal",
                    "konsole",
                    "xterm",
                    "x-terminal-emulator",
                ]
            },
        )

    raise SetupError(
        "terminal_launch_unsupported",
        f"Opening a credential terminal is unsupported on {sys.platform}.",
        recoverable=False,
    )


def _receive_helper_result(
    server: socket.socket,
    *,
    endpoint: str,
    nonce: str,
    timeout: int,
    connect_timeout: int,
    plan: str = "platform",
) -> str:
    server.listen(1)
    server.settimeout(connect_timeout)
    _launch_terminal(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--credential-helper",
            plan,
            endpoint,
            nonce,
        ]
    )
    try:
        connection, _ = server.accept()
    # Before Python 3.10, socket.timeout is not a TimeoutError; from 3.10 it is
    # a deprecated alias. Catch both so timeouts are classified on any version.
    except (socket.timeout, TimeoutError) as error:
        raise SetupError(
            "terminal_helper_not_connected",
            "The credential terminal did not start or could not connect back.",
        ) from error
    with connection:
        connection.settimeout(timeout)
        try:
            hello = _receive_helper_message(connection)
            challenge = hello.get("challenge")
            if (
                not isinstance(challenge, str)
                or not HELPER_CHALLENGE_PATTERN.fullmatch(challenge)
            ):
                raise SetupError(
                    "credential_helper_invalid",
                    "The credential helper response could not be authenticated.",
                    recoverable=False,
                )
            _send_helper_message(
                connection,
                {"proof": _helper_proof(nonce, "parent", challenge)},
            )
            helper_auth = _receive_helper_message(connection)
            if not hmac.compare_digest(
                str(helper_auth.get("proof", "")),
                _helper_proof(nonce, "helper", challenge),
            ):
                raise SetupError(
                    "credential_helper_invalid",
                    "The credential helper response could not be authenticated.",
                    recoverable=False,
                )
            _send_helper_message(connection, {"ready": True})
            result = _receive_helper_message(connection)
        except (socket.timeout, TimeoutError) as error:
            raise SetupError(
                "credential_entry_timeout",
                "Timed out waiting for credential entry in the new terminal.",
            ) from error
        except OSError as error:
            raise SetupError(
                "credential_helper_invalid",
                "The credential helper connection failed.",
                recoverable=False,
            ) from error
    if isinstance(result.get("error"), dict):
        error = result["error"]
        raise SetupError(
            str(error.get("reason", "credential_entry_failed")),
            str(error.get("message", "Credential entry failed.")),
            recoverable=bool(error.get("recoverable", True)),
        )
    token = result.get("token")
    if not isinstance(token, str) or not PLANS[plan]["token_pattern"].fullmatch(token):
        raise SetupError(
            "credential_helper_invalid",
            "The credential helper did not return a valid Anthropic key.",
            recoverable=False,
        )
    return token


def _receive_helper_message(connection: socket.socket) -> dict[str, Any]:
    payload = bytearray()
    while len(payload) <= HELPER_MESSAGE_LIMIT:
        chunk = connection.recv(min(4096, HELPER_MESSAGE_LIMIT + 1 - len(payload)))
        if not chunk:
            raise SetupError(
                "credential_helper_invalid",
                "The credential helper disconnected before returning a response.",
                recoverable=False,
            )
        payload.extend(chunk)
        if b"\n" in payload:
            encoded, separator, extra = payload.partition(b"\n")
            if separator and not extra:
                break
            raise SetupError(
                "credential_helper_invalid",
                "The credential helper returned an invalid response.",
                recoverable=False,
            )
    else:
        encoded = b""
    if len(payload) > HELPER_MESSAGE_LIMIT:
        raise SetupError(
            "credential_helper_invalid",
            "The credential helper returned an oversized response.",
            recoverable=False,
        )
    try:
        result = json.loads(encoded)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SetupError(
            "credential_helper_invalid",
            "The credential helper returned an invalid response.",
            recoverable=False,
        ) from error
    if not isinstance(result, dict):
        raise SetupError(
            "credential_helper_invalid",
            "The credential helper returned an invalid response.",
            recoverable=False,
        )
    return result


def _receive_terminal_token(
    *,
    timeout: int = 300,
    connect_timeout: int = HELPER_CONNECT_TIMEOUT,
    plan: str = "platform",
) -> str:
    nonce = secrets.token_urlsafe(32)
    if sys.platform == "win32":
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            endpoint = str(server.getsockname()[1])
            return _receive_helper_result(
                server,
                endpoint=endpoint,
                nonce=nonce,
                timeout=timeout,
                connect_timeout=connect_timeout,
                plan=plan,
            )

    # Unix socket paths are limited to ~104 bytes, which a deep TMPDIR (common
    # in agent sandboxes) can exceed, so keep the private directory under /tmp.
    with tempfile.TemporaryDirectory(
        prefix="dd-anthropic-prompt-",
        dir="/tmp",
    ) as directory:
        os.chmod(directory, 0o700)
        socket_path = str(Path(directory) / "credential.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(socket_path)
            os.chmod(socket_path, 0o600)
            return _receive_helper_result(
                server,
                endpoint=socket_path,
                nonce=nonce,
                timeout=timeout,
                connect_timeout=connect_timeout,
                plan=plan,
            )


def _send_helper_message(
    connection: socket.socket,
    value: dict[str, Any],
) -> None:
    encoded = json.dumps(value, separators=(",", ":")).encode() + b"\n"
    connection.sendall(encoded)


def _helper_proof(nonce: str, role: str, challenge: str) -> str:
    message = f"{role}:{challenge}".encode()
    return hmac.new(nonce.encode(), message, "sha256").hexdigest()


def _connect_helper(endpoint: str, nonce: str) -> socket.socket:
    family = socket.AF_INET if sys.platform == "win32" else socket.AF_UNIX
    address: tuple[str, int] | str
    if family == socket.AF_INET:
        address = ("127.0.0.1", int(endpoint))
    else:
        address = endpoint
    client = socket.socket(family, socket.SOCK_STREAM)
    try:
        client.settimeout(10)
        client.connect(address)
        challenge = secrets.token_urlsafe(32)
        _send_helper_message(client, {"challenge": challenge})
        parent_auth = _receive_helper_message(client)
        if not hmac.compare_digest(
            str(parent_auth.get("proof", "")),
            _helper_proof(nonce, "parent", challenge),
        ):
            raise SetupError(
                "credential_helper_invalid",
                "The credential setup process could not be authenticated.",
                recoverable=False,
            )
        _send_helper_message(
            client,
            {"proof": _helper_proof(nonce, "helper", challenge)},
        )
        ready = _receive_helper_message(client)
        if ready != {"ready": True}:
            raise SetupError(
                "credential_helper_invalid",
                "The credential setup process could not be authenticated.",
                recoverable=False,
            )
        client.settimeout(None)
        return client
    except (OSError, TimeoutError, SetupError):
        client.close()
        raise


def _watch_parent(
    connection: socket.socket,
    prompt_complete: threading.Event,
) -> None:
    try:
        while connection.recv(1):
            pass
    except (OSError, TimeoutError):
        # Not ignored: a socket error means the parent went away, the same
        # signal as EOF, and is handled below. Raising here would only end this
        # daemon thread and leave the main thread blocked in getpass.
        pass
    if not prompt_complete.is_set():
        os._exit(1)


def _credential_helper(endpoint: str, nonce: str, plan: str = "platform") -> int:
    try:
        connection = _connect_helper(endpoint, nonce)
    except (OSError, TimeoutError, SetupError) as error:
        print(
            "Could not connect securely to the setup process: "
            f"{type(error).__name__}.",
            file=sys.stderr,
        )
        return 1
    prompt_complete = threading.Event()
    watchdog = threading.Thread(
        target=_watch_parent,
        args=(connection, prompt_complete),
        daemon=True,
    )
    watchdog.start()
    try:
        try:
            with _open_tty() as tty:
                token = _read_token_from_tty(tty, plan)
                tty.write("\nCredential received. You may close this terminal.\n")
            result: dict[str, Any] = {"token": token}
            exit_code = 0
        except SetupError as error:
            result = {"error": error.as_dict()}
            exit_code = 1
        prompt_complete.set()
        try:
            _send_helper_message(connection, result)
        except (OSError, TimeoutError) as error:
            print(
                "Could not return the credential to the setup process: "
                f"{type(error).__name__}.",
                file=sys.stderr,
            )
            return 1
        return exit_code
    finally:
        prompt_complete.set()
        connection.close()


def _open_token_page(plan: str = "platform") -> None:
    url = PLANS[plan]["token_url"]
    if os.environ.get("WSL_DISTRO_NAME") and shutil.which("explorer.exe"):
        try:
            subprocess.Popen(["explorer.exe", url], start_new_session=True)
            return
        except OSError:
            pass  # Non-fatal: the credential terminal also prints the URL.
    webbrowser.open(url, new=2)
    # Headless and remote hosts often have no visible browser; stderr keeps
    # the JSON contract on stdout while still surfacing the URL to the
    # operator (the credential terminal prints it too).
    print(f"If no browser opened, create the token at: {url}", file=sys.stderr)


def _prompt_for_token(plan: str = "platform") -> str:
    _open_token_page(plan)
    return _receive_terminal_token(plan=plan)


def _build_payload(account_name: str, token: str, *, ccm_enabled: bool = True) -> bytes:
    return json.dumps(
        {
            "data": {
                "type": "Account",
                "attributes": {
                    "name": account_name,
                    "settings": {"ccm_enabled": ccm_enabled},
                    "secrets": {"api_key": token},
                },
            }
        },
        separators=(",", ":"),
    ).encode()


def _anthropic_get(url: str, token: str) -> dict[str, Any]:
    """GET an Anthropic API URL with the key as an unredirected header."""
    request = urllib.request.Request(url, method="GET")
    request.add_unredirected_header("x-api-key", token)
    request.add_unredirected_header("anthropic-version", ANTHROPIC_VERSION)
    with urllib.request.urlopen(request, timeout=ANTHROPIC_TIMEOUT) as response:
        body = json.loads(response.read().decode("utf-8", "replace") or "{}")
    return body if isinstance(body, dict) else {}


def _anthropic_organization(token: str, plan: str = "platform") -> dict[str, Any]:
    """Confirm the key with Anthropic and return its organization.

    A Platform key must reach /v1/organizations/me (Admin API access), and an Enterprise
    Analytics key must reach the analytics usage report, whose envelope carries
    the organization id (Anthropic exposes no name to analytics keys). A key
    Anthropic refuses is rejected before anything is written to Datadog; the id
    is what Datadog tags the usage metrics with."""
    config = PLANS[plan]
    if plan == "enterprise":
        end = int(time.time())
        url = ANTHROPIC_ANALYTICS_URL + "?" + urllib.parse.urlencode({
            "starting_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end - 60)),
            "ending_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end)),
            "limit": 1,
        })
        id_field, name_field = "organization_id", None
    else:
        url, id_field, name_field = ANTHROPIC_ORG_URL, "id", "name"
    try:
        body = _anthropic_get(url, token)
    except urllib.error.HTTPError as error:
        if error.code in {401, 403}:
            raise SetupError(
                "anthropic_key_rejected",
                (
                    f"Anthropic rejected the key. Use an active {config['key_label']} "
                    f"from {config['token_url']}. {config['instructions']}"
                ),
                details={"http_status": error.code},
            ) from error
        raise SetupError(
            "anthropic_check_failed",
            "Anthropic could not confirm the key.",
            details={"http_status": error.code},
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise SetupError(
            "anthropic_check_failed",
            "Anthropic could not be reached to confirm the key.",
            details={"error_type": type(error).__name__},
        ) from error
    if not body.get(id_field):
        raise SetupError(
            "anthropic_check_failed",
            "Anthropic returned an unexpected organization response.",
        )
    return {
        "id": str(body[id_field]),
        "name": str(body.get(name_field) or "") if name_field else "",
    }


def metrics_summary_link(site: Any) -> str | None:
    """Metrics Summary filtered to `anthropic` on the user's own Datadog site."""
    if not isinstance(site, str) or not site.strip():
        return None
    site = site.strip().lower()
    host = site if site.count(".") >= 2 else f"app.{site}"
    return f"https://{host}/metric/summary?" + urllib.parse.urlencode({"filter": "anthropic"})


def usage_query(org_id: str) -> str:
    return f"sum:{USAGE_METRIC}{{org_id:{org_id}}}.as_count().rollup(sum, 60)"


def usage_total(org_id: str, *, since: int, now: int, run: Any = None) -> float:
    """Total `USAGE_METRIC` for the Anthropic org over [since, now]."""
    query = urllib.parse.urlencode(
        {"from": since, "to": now + 60, "query": usage_query(org_id)}
    )
    response = (run or _pup_api)("GET", f"/api/v1/query?{query}")
    total = 0.0
    for series in (response.get("series") or []) if isinstance(response, dict) else []:
        for point in series.get("pointlist") or []:
            if isinstance(point, list) and len(point) == 2 and point[1] is not None:
                total += float(point[1])
    return total


def _usage_baseline(org_id: str) -> dict[str, Any] | None:
    """The org's usage total right before the account exists, or None.

    Never blocks setup: without it, verify takes its own baseline and says so."""
    since = int(time.time()) - USAGE_LOOKBACK_SECONDS
    try:
        return {"since": since, "total": usage_total(org_id, since=since, now=int(time.time()))}
    except SetupError:
        return None


def _register_account(
    *,
    account_name: str,
    token: str,
    ccm_enabled: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    payload = _build_payload(account_name, token, ccm_enabled=ccm_enabled)
    try:
        _pup_api(
            "POST",
            ACCOUNTS_PATH,
            payload=payload,
            token=token,
        )
    except SetupError as error:
        try:
            match = _existing_account(
                _list_accounts(),
                account_name=account_name,
            )
        except SetupError as reconciliation_error:
            raise SetupError(
                "ambiguous_datadog_post",
                (
                    "Pup's POST result is ambiguous and account reconciliation "
                    "also failed. No second POST was attempted."
                ),
                recoverable=False,
                details={
                    "post_error": error.as_dict(token=token),
                    "reconciliation_error": reconciliation_error.as_dict(),
                },
            ) from error
        if match:
            return match, ["post_reported_error_but_account_exists"]
        # Only an explicit client-error response proves nothing was written.
        # A transport failure, 5xx, 408, or 429 may still have been committed,
        # and the account may not be listed yet, so a rerun could duplicate it.
        status = error.details.get("http_status")
        if isinstance(status, int) and 400 <= status < 500 and status not in {408, 429}:
            raise SetupError(
                "datadog_create_failed",
                (
                    "Datadog rejected the Anthropic account request, and no "
                    "exact account exists. No retry was attempted."
                ),
                details={"pup_error": error.as_dict(token=token)},
            ) from error
        raise SetupError(
            "ambiguous_datadog_post",
            (
                "Pup's POST result is ambiguous and no exact account is visible "
                "yet. No second POST was attempted."
            ),
            recoverable=False,
            details={"post_error": error.as_dict(token=token)},
        ) from error
    match = _existing_account(
        _list_accounts(),
        account_name=account_name,
    )
    if not match:
        raise SetupError(
            "datadog_account_not_visible",
            "Datadog accepted the account, but exact registration verification failed.",
        )
    return match, []


def enable_ccm(*, account_name: str) -> dict[str, Any]:
    """Turn on cost data ingestion for an existing account; no key is needed."""
    name = account_name.strip()
    pup_identity = _validate_pup()
    account = _existing_account(_list_accounts(), account_name=name)
    if not account:
        raise SetupError(
            "account_not_found",
            f"No Datadog Anthropic account is named {name}.",
            recoverable=False,
        )
    result = {"datadog_auth": {"provider": "pup", **pup_identity}}
    if account["ccm_enabled"]:
        return {"status": "ccm_already_enabled", "account": account, **result}
    payload = json.dumps(
        {
            "data": {
                "type": "Account",
                "attributes": {"name": name, "settings": {"ccm_enabled": True}},
            }
        },
        separators=(",", ":"),
    ).encode()
    _pup_api("PATCH", f"{ACCOUNTS_PATH}/{account['id']}", payload=payload)
    updated = _existing_account(_list_accounts(), account_name=name)
    if not updated or not updated["ccm_enabled"]:
        raise SetupError(
            "ccm_not_enabled",
            "Datadog accepted the update, but the account still shows cost ingestion off.",
            details={"account": updated},
        )
    return {"status": "ccm_enabled", "account": updated, **result}


def run_setup(
    *, account_name: str, plan: str = "platform", ccm_enabled: bool = True
) -> dict[str, Any]:
    name = account_name.strip()
    if not name:
        raise SetupError(
            "invalid_account_name",
            "Datadog integration account name cannot be empty.",
            recoverable=False,
        )
    pup_identity = _validate_pup()
    existing = _existing_account(
        _list_accounts(),
        account_name=name,
    )
    if existing:
        return {
            "status": "already_configured",
            "account": existing,
            "datadog_auth": {"provider": "pup", **pup_identity},
            "credential_requested": False,
        }

    token: str | None = None
    try:
        token = _prompt_for_token(plan)
        organization = _anthropic_organization(token, plan)
        baseline = _usage_baseline(organization["id"])
        account, registration_warnings = _register_account(
            account_name=name,
            token=token,
            ccm_enabled=ccm_enabled,
        )
        result = {
            "status": "complete",
            "account": account,
            "anthropic_organization": organization,
            "plan": plan,
            "datadog_auth": {"provider": "pup", **pup_identity},
            "credential_storage": "memory_only",
            "ccm_requested": ccm_enabled,
            "metric_query": f"anthropic.messages.* tagged org_id:{organization['id']}",
            "usage_baseline": baseline,
        }
        link = metrics_summary_link(pup_identity.get("site"))
        if link:
            # Every anthropic.* metric, on the site the account lives on.
            result["links"] = {"metrics": link}
        if baseline is None:
            registration_warnings = [*registration_warnings, "usage_baseline_unavailable"]
        if registration_warnings:
            result["warnings"] = registration_warnings
        return result
    except SetupError as error:
        # Re-raise with the key scrubbed from anything a step attached.
        raise SetupError(
            error.reason,
            _sanitize_text(error.message, token=token) or error.message,
            recoverable=error.recoverable,
            details=_sanitize_value(error.details, token=token),
        ) from error
    except Exception as error:
        raise SetupError(
            "unexpected_setup_error",
            "Setup failed without persisting the Anthropic key.",
            details={
                "error_type": type(error).__name__,
                "message": _sanitize_text(str(error), token=token),
            },
        ) from error


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        # argparse would print usage and exit; keep the JSON output contract.
        raise SetupError("invalid_arguments", message, recoverable=False)


def _accounts_status() -> dict[str, Any]:
    """Read-only report behind --list-accounts: no key is requested, nothing is written."""
    identity = _validate_pup()
    return {
        "status": "accounts",
        "site": identity.get("site"),
        "org": identity.get("org"),
        "accounts": _list_accounts(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _ArgumentParser(
        description="Set up the Datadog Anthropic Usage and Costs integration."
    )
    parser.add_argument("--account-name", required=False)
    parser.add_argument(
        "--list-accounts",
        action="store_true",
        help="Read-only: report the existing Datadog Anthropic accounts and exit, "
             "without requesting a key or writing anything.",
    )
    parser.add_argument(
        "--plan",
        choices=sorted(PLANS),
        default="platform",
        help="Anthropic plan: platform (key with Admin API access) or enterprise "
             "(Analytics API key).",
    )
    parser.add_argument(
        "--disable-ccm",
        action="store_true",
        help="Do not ingest Anthropic cost data into Cloud Cost Management (on by default).",
    )
    parser.add_argument(
        "--turn-on-ccm",
        action="store_true",
        help="Turn on cost data ingestion for an existing account instead of creating one.",
    )
    args, unknown = parser.parse_known_args(
        list(sys.argv[1:] if argv is None else argv)
    )
    if unknown:
        raise SetupError(
            "unexpected_arguments",
            f"Unsupported arguments: {' '.join(unknown)}.",
            recoverable=False,
        )
    if args.turn_on_ccm and args.disable_ccm:
        raise SetupError(
            "invalid_arguments",
            "--turn-on-ccm and --disable-ccm cannot be combined.",
            recoverable=False,
        )
    if not args.list_accounts and not args.account_name:
        raise SetupError(
            "invalid_arguments",
            "--account-name is required unless --list-accounts is set.",
            recoverable=False,
        )
    return args


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    token: str | None = None
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--credential-helper":
        if len(arguments) != 4 or arguments[1] not in PLANS:
            print("Invalid credential-helper invocation.", file=sys.stderr)
            return 1
        return _credential_helper(arguments[2], arguments[3], arguments[1])
    try:
        args = parse_args(arguments)
        if args.list_accounts:
            emit(_accounts_status())
        elif args.turn_on_ccm:
            emit(enable_ccm(account_name=args.account_name))
        else:
            emit(run_setup(
                account_name=args.account_name,
                plan=args.plan,
                ccm_enabled=not args.disable_ccm,
            ))
        return 0
    except SetupError as error:
        emit({"status": "error", "error": error.as_dict(token=token)})
        return 1
    except KeyboardInterrupt:
        emit(
            {
                "status": "error",
                "error": SetupError(
                    "setup_cancelled",
                    "Setup was cancelled.",
                ).as_dict(),
            }
        )
        return 130
    except Exception as error:
        # run_setup sanitizes failures after the token is read, so anything
        # reaching here happened before a credential was requested.
        emit(
            {
                "status": "error",
                "error": SetupError(
                    "unexpected_setup_error",
                    "Setup failed unexpectedly.",
                    details={
                        "error_type": type(error).__name__,
                        "message": _sanitize_text(str(error)),
                    },
                ).as_dict(),
            }
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
