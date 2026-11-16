"""Secrets given literally, as ``file:/path`` or as ``cmd:command``.

Shared by provider API keys and the MCP server token (#141), so a secret can
live in a keyring (``cmd:secret-tool lookup …``) instead of config.json.
Stdlib only: ``freecad_ai.mcp`` imports it without the LLM client.
"""

import os
import subprocess

from .proc import hidden_process_kwargs

_TIMEOUT = 10


class SecretSourceError(ValueError):
    """A file:/cmd: source failed. Names the source, never the secret.

    A ValueError, so the MCP start paths report it like a bad token."""


def secret_source_kind(value: str) -> str | None:
    """``"cmd"`` or ``"file"`` for a source, ``None`` for a literal."""
    value = (value or "").strip()
    for kind in ("cmd", "file"):
        if value.startswith(kind + ":"):
            return kind
    return None


def _read_file(spec: str) -> str:
    # Expand both ~ (all OSes) and env vars like %APPDATA% / $HOME, so
    # Windows users can write "file:%APPDATA%\\freecad-ai\\key".
    path = os.path.expandvars(os.path.expanduser(spec))
    try:
        with open(path) as f:
            secret = f.read().strip()
    except OSError as e:
        raise SecretSourceError(f"cannot read token file {path!r}: {e}") from e
    if not secret:
        raise SecretSourceError(f"token file {path!r} is empty")
    return secret


def _run(command: str) -> str:
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=_TIMEOUT,
            # shell=True runs cmd.exe on Windows, which would otherwise open
            # a console window for every resolution.
            **hidden_process_kwargs(),
        )
    except subprocess.TimeoutExpired as e:
        raise SecretSourceError(
            f"token command timed out after {_TIMEOUT}s: {command!r}") from e
    except OSError as e:
        raise SecretSourceError(
            f"cannot run token command {command!r}: {e}") from e
    if result.returncode != 0:
        # stdout is never quoted: a failing helper may still have printed it.
        raise SecretSourceError(
            f"token command {command!r} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}")
    secret = result.stdout.strip()
    if not secret:
        raise SecretSourceError(f"token command {command!r} printed nothing")
    return secret


def resolve_secret(value: str) -> str:
    """The secret ``value`` names. Re-reads the source on every call.

    Raises SecretSourceError when a file:/cmd: source fails or is empty.
    """
    value = (value or "").strip()
    kind = secret_source_kind(value)
    if kind == "file":
        return _read_file(value[5:].strip())
    if kind == "cmd":
        return _run(value[4:].strip())
    return value
