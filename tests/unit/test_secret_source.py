"""file:/cmd: secret sources, shared by API keys and the MCP token (#141)."""

import os
import subprocess

import pytest

from freecad_ai.utils.secret_source import (
    SecretSourceError, resolve_secret, secret_source_kind)

posix = pytest.mark.skipif(os.name != "posix", reason="POSIX shell commands")


def test_literal_is_returned():
    assert resolve_secret("abc123") == "abc123"


def test_file_is_read_and_stripped(tmp_path):
    p = tmp_path / "tok"
    p.write_text("tok\n")
    assert resolve_secret(f"file:{p}") == "tok"


def test_file_expands_user_and_env(tmp_path, monkeypatch):
    (tmp_path / "tok").write_text("envtok")
    monkeypatch.setenv("SECRET_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_secret("file:$SECRET_DIR/tok") == "envtok"
    assert resolve_secret("file:~/tok") == "envtok"


def test_missing_file_raises(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(SecretSourceError) as e:
        resolve_secret(f"file:{missing}")
    assert str(missing) in str(e.value)


@posix
def test_cmd_stdout_is_stripped():
    assert resolve_secret("cmd:printf 'tok\\n'") == "tok"


@posix
def test_cmd_nonzero_raises_without_leaking_stdout():
    with pytest.raises(SecretSourceError) as e:
        resolve_secret("cmd:echo s3cret; exit 3")
    assert "s3cret" not in str(e.value).replace("echo s3cret", "")
    assert "exit 3" in str(e.value)


def test_cmd_timeout_raises(monkeypatch):
    def slow(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="x", timeout=10)
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(SecretSourceError, match="timed out"):
        resolve_secret("cmd:sleep 99")


@posix
def test_empty_output_raises(tmp_path):
    with pytest.raises(SecretSourceError):
        resolve_secret("cmd:true")
    empty = tmp_path / "empty"
    empty.write_text("\n")
    with pytest.raises(SecretSourceError):
        resolve_secret(f"file:{empty}")


def test_kind():
    assert secret_source_kind("cmd:x") == "cmd"
    assert secret_source_kind(" file:/x") == "file"
    assert secret_source_kind("abc") is None
    assert secret_source_kind("") is None


def test_error_is_a_value_error():
    assert issubclass(SecretSourceError, ValueError)
