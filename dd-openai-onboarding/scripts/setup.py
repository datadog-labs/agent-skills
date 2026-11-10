#!/usr/bin/env python3
"""Interactive Datadog OpenAI integration setup."""

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

# Admin keys (organization owners only) are created here; usage metrics and
# costs need one.
TOKEN_URL = "https://platform.openai.com/settings/organization/admin-keys"
ACCOUNTS_PATH = "/api/v2/web-integrations/openai/accounts"
# OpenAI secret keys start with sk- (admin keys sk-admin-..., project keys
# sk-proj-...); which kind it is, and whether it works, is OpenAI's answer to
# the checks below.
TOKEN_PATTERN = re.compile(r"^sk-\S+$")
OPENAI_API = "https://api.openai.com"
# Admin keys can read organization usage; project keys cannot, but can list
# models.
OPENAI_ADMIN_TEST_PATH = "/v1/organization/usage/completions"
OPENAI_PROJECT_TEST_PATH = "/v1/models"
OPENAI_TIMEOUT = 30
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
    sanitized = re.sub(r"sk-[A-Za-z0-9_-]{8,}\S*", "[REDACTED_OPENAI_KEY]", sanitized)
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
                # Unset follows the integration defaults: both off.
                "ccm_enabled": settings.get("ccm_enabled") is True,
                "llm_observability_enabled": settings.get("llm_observability_enabled") is True,
                "custom_tags": settings.get("custom_tags")
                if isinstance(settings.get("custom_tags"), str) else "",
            }
        )
    return result


def _list_accounts() -> list[dict[str, Any]]:
    return _account_summary(_pup_api("GET", ACCOUNTS_PATH))


def _validate_pup() -> dict[str, Any]:
    if shutil.which("pup") is None:
        raise SetupError(
            "pup_cli_missing",
            "Install Pup and authenticate with `pup auth login`.",
            recoverable=False,
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
            f"Multiple Datadog OpenAI accounts are named {account_name}.",
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


def _read_token_from_tty(tty: TextIO) -> str:
    tty.write(
        "\nThis is a local terminal session. No AI agent is running here, and the admin key you paste is not visible to the agent.\n"
        "\nCreate an OpenAI admin key (organization owners only) at:\n"
        f"  {TOKEN_URL}\n"
        "A project key connects but collects no usage or cost data, so it is refused.\n"
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass(
                "Paste the OpenAI admin key: ",
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
            "OpenAI admin key entry was cancelled.",
        ) from error
    if not TOKEN_PATTERN.fullmatch(token):
        raise SetupError(
            "invalid_openai_key",
            "The key must start with sk- and contain no whitespace.",
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
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise SetupError(
                "terminal_launch_failed",
                "No graphical display is available to open a credential terminal.",
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
) -> str:
    server.listen(1)
    server.settimeout(connect_timeout)
    _launch_terminal(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--credential-helper",
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
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        raise SetupError(
            "credential_helper_invalid",
            "The credential helper did not return a valid OpenAI admin key.",
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
            )

    # Unix socket paths are limited to ~104 bytes, which a deep TMPDIR (common
    # in agent sandboxes) can exceed, so keep the private directory under /tmp.
    with tempfile.TemporaryDirectory(
        prefix="dd-openai-prompt-",
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


def _credential_helper(endpoint: str, nonce: str) -> int:
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
                token = _read_token_from_tty(tty)
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


def _open_token_page() -> None:
    if os.environ.get("WSL_DISTRO_NAME") and shutil.which("explorer.exe"):
        try:
            subprocess.Popen(["explorer.exe", TOKEN_URL], start_new_session=True)
            return
        except OSError:
            pass  # Non-fatal: the credential terminal also prints TOKEN_URL.
    webbrowser.open(TOKEN_URL, new=2)


def _prompt_for_token() -> str:
    _open_token_page()
        
    return _receive_terminal_token()


def _build_payload(
    account_name: str,
    token: str,
    *,
    ccm_enabled: bool = True,
    llm_observability_enabled: bool = False,
    custom_tags: list[str] | None = None,
) -> bytes:
    settings: dict[str, Any] = {
        "ccm_enabled": ccm_enabled,
        "llm_observability_enabled": llm_observability_enabled,
    }
    if custom_tags:
        # The integration stores its custom tags as one comma-separated string.
        settings["custom_tags"] = ",".join(custom_tags)
    return json.dumps(
        {
            "data": {
                "type": "Account",
                "attributes": {
                    "name": account_name,
                    "settings": settings,
                    "secrets": {"api_key": token},
                },
            }
        },
        separators=(",", ":"),
    ).encode()


def _openai_get(path: str, token: str, params: dict[str, Any] | None = None) -> int:
    """GET an OpenAI API path with the key as an unredirected header; return the status."""
    url = OPENAI_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(url, method="GET")
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=OPENAI_TIMEOUT) as response:
            return response.getcode()
    except urllib.error.HTTPError as error:
        return error.code


def _openai_key_type(token: str) -> str:
    """Confirm the key with OpenAI: "admin" or "project"; raises if it is rejected.

    A key that can read organization usage is an admin key; one that cannot but
    still authenticates is a project key. The key goes only to OpenAI."""
    try:
        admin = _openai_get(
            OPENAI_ADMIN_TEST_PATH,
            token,
            {"start_time": int(time.time()) - 60, "limit": 1, "bucket_width": "1m"},
        )
        if 200 <= admin < 300:
            return "admin"
        if admin not in {401, 403}:
            # A 429 or 5xx says nothing about the key; do not let the project
            # probe turn a temporary failure into "replace your key".
            raise SetupError(
                "openai_check_failed",
                "OpenAI could not confirm the key; try again shortly.",
                details={"http_status": admin},
            )
        project = _openai_get(OPENAI_PROJECT_TEST_PATH, token)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SetupError(
            "openai_check_failed",
            "OpenAI could not be reached to confirm the key.",
            details={"error_type": type(error).__name__},
        ) from error
    if 200 <= project < 300:
        return "project"
    if project in {401, 403}:
        raise SetupError(
            "openai_key_rejected",
            f"OpenAI rejected the key. Use an active admin key from {TOKEN_URL}.",
            details={"http_status": project},
        )
    raise SetupError(
        "openai_check_failed",
        "OpenAI could not confirm the key.",
        details={"http_status": project},
    )


def _register_account(
    *,
    account_name: str,
    token: str,
    ccm_enabled: bool = True,
    llm_observability_enabled: bool = False,
    custom_tags: list[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    payload = _build_payload(
        account_name,
        token,
        ccm_enabled=ccm_enabled,
        llm_observability_enabled=llm_observability_enabled,
        custom_tags=custom_tags,
    )
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
                    "Datadog rejected the OpenAI account request, and no "
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


def _raw_settings(listing: Any, account_id: Any) -> dict[str, Any]:
    """The account's settings exactly as Datadog returned them (none are secret)."""
    for item in (listing.get("data") or []) if isinstance(listing, dict) else []:
        if isinstance(item, dict) and item.get("id") == account_id:
            settings = (item.get("attributes") or {}).get("settings")
            return settings if isinstance(settings, dict) else {}
    return {}


def enable_ccm(*, account_name: str) -> dict[str, Any]:
    """Turn on cost data ingestion for an existing account; no key is needed."""
    name = account_name.strip()
    pup_identity = _validate_pup()
    listing = _pup_api("GET", ACCOUNTS_PATH)
    account = _existing_account(_account_summary(listing), account_name=name)
    if not account:
        raise SetupError(
            "account_not_found",
            f"No Datadog OpenAI account is named {name}.",
            recoverable=False,
        )
    result = {"datadog_auth": {"provider": "pup", **pup_identity}}
    if account["ccm_enabled"]:
        return {"status": "ccm_already_enabled", "account": account, **result}
    # Settings are replaced as a whole, so send back every current setting
    # (LLM Observability, custom tags, ...) with only ccm_enabled changed.
    settings = dict(_raw_settings(listing, account["id"]))
    settings["ccm_enabled"] = True
    payload = json.dumps(
        {
            "data": {
                "type": "Account",
                "attributes": {"name": name, "settings": settings},
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
    *,
    account_name: str,
    ccm_enabled: bool = True,
    llm_observability_enabled: bool = False,
    custom_tags: list[str] | None = None,
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
        result = {
            "status": "already_configured",
            "account": existing,
            "datadog_auth": {"provider": "pup", **pup_identity},
            "credential_requested": False,
        }
        # The existing account is left unchanged, so report any setting that
        # differs from what this run asked for.
        requested = {
            "ccm_enabled": ccm_enabled,
            "llm_observability_enabled": llm_observability_enabled,
        }
        settings_differ = [
            {"setting": setting, "requested": wanted, "current": existing.get(setting)}
            for setting, wanted in requested.items()
            if existing.get(setting) != wanted
        ]
        if settings_differ:
            result["settings_differ"] = settings_differ
            result["warnings"] = [
                "The existing account was left unchanged and its settings differ "
                "from this request: "
                + ", ".join(
                    f"{item['setting']} is {str(item['current']).lower()} "
                    f"(requested {str(item['requested']).lower()})"
                    for item in settings_differ
                )
                + "."
            ]
        return result

    token: str | None = None
    try:
        token = _prompt_for_token()
        key_type = _openai_key_type(token)
        if key_type != "admin":
            # Usage metrics and costs need an admin key (see the Datadog OpenAI
            # integration docs): with a project key the account would collect
            # nothing. Refuse before anything is written to Datadog.
            raise SetupError(
                "admin_key_required",
                (
                    "That is a project key, which connects but collects no usage "
                    "or cost data. Create an admin key (organization owners only) "
                    f"at {TOKEN_URL} and rerun."
                ),
            )
        account, registration_warnings = _register_account(
            account_name=name,
            token=token,
            ccm_enabled=ccm_enabled,
            llm_observability_enabled=llm_observability_enabled,
            custom_tags=custom_tags,
        )
        result = {
            "status": "complete",
            "account": account,
            "key_type": key_type,
            "datadog_auth": {"provider": "pup", **pup_identity},
            "credential_storage": "memory_only",
            "ccm_requested": ccm_enabled,
            "llm_observability_requested": llm_observability_enabled,
            "custom_tags_requested": list(custom_tags or []),
            # Datadog tags every OpenAI metric with the account it came from.
            "metric_query": f"openai.* tagged account_id:{account['id']}",
        }
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
            "Setup failed without persisting the OpenAI key.",
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
        description="Set up the Datadog OpenAI integration."
    )
    parser.add_argument("--account-name", required=False)
    parser.add_argument(
        "--list-accounts",
        action="store_true",
        help="Read-only: report the existing Datadog OpenAI accounts and exit, "
             "without requesting a key or writing anything.",
    )
    parser.add_argument(
        "--disable-ccm",
        action="store_true",
        help="Do not ingest OpenAI cost data into Cloud Cost Management (on by default).",
    )
    parser.add_argument(
        "--enable-llm-observability",
        action="store_true",
        help="Also let LLM Observability use the key for evaluations (off by default).",
    )
    parser.add_argument(
        "--custom-tags",
        action="append",
        default=[],
        metavar="TAG",
        help="Custom tag for OpenAI metrics and logs, such as team:ml; repeat for more.",
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
    if not args.list_accounts and not args.account_name:
        raise SetupError(
            "invalid_arguments",
            "--account-name is required unless --list-accounts is set.",
            recoverable=False,
        )
    if args.turn_on_ccm and args.disable_ccm:
        raise SetupError(
            "invalid_arguments",
            "--turn-on-ccm and --disable-ccm cannot be combined.",
            recoverable=False,
        )
    tags = [tag.strip() for tag in args.custom_tags]
    if any(not tag or any(c.isspace() or c == "," for c in tag) for tag in tags):
        raise SetupError(
            "invalid_custom_tags",
            "Custom tags must be non-empty and contain no whitespace or commas.",
            recoverable=False,
        )
    if tags and args.turn_on_ccm:
        raise SetupError(
            "invalid_arguments",
            "--custom-tags applies only when creating an account, not with --turn-on-ccm.",
            recoverable=False,
        )
    args.custom_tags = tags
    return args


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    token: str | None = None
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--credential-helper":
        if len(arguments) != 3:
            print("Invalid credential-helper invocation.", file=sys.stderr)
            return 1
        return _credential_helper(arguments[1], arguments[2])
    try:
        args = parse_args(arguments)
        if args.list_accounts:
            emit(_accounts_status())
        elif args.turn_on_ccm:
            emit(enable_ccm(account_name=args.account_name))
        else:
            emit(run_setup(
                account_name=args.account_name,
                ccm_enabled=not args.disable_ccm,
                llm_observability_enabled=args.enable_llm_observability,
                custom_tags=args.custom_tags,
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
