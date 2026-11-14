"""Tests for API key resolution (file:, cmd:, literal)."""

import os
import tempfile

from freecad_ai.llm.client import LLMClient


def _make_client(api_key: str) -> LLMClient:
    return LLMClient(
        provider_name="custom",
        base_url="http://localhost",
        api_key=api_key,
        model="test",
    )


class TestResolveApiKey:
    def test_literal_key(self):
        client = _make_client("sk-abc123")
        assert client._resolve_api_key() == "sk-abc123"

    def test_empty_key(self):
        client = _make_client("")
        assert client._resolve_api_key() == ""

    def test_file_prefix(self, tmp_path):
        token_file = tmp_path / "token.txt"
        token_file.write_text("my-oauth-token-xyz\n")
        client = _make_client(f"file:{token_file}")
        assert client._resolve_api_key() == "my-oauth-token-xyz"

    def test_file_prefix_strips_whitespace(self, tmp_path):
        token_file = tmp_path / "token.txt"
        token_file.write_text("  token-with-spaces  \n\n")
        client = _make_client(f"file:{token_file}")
        assert client._resolve_api_key() == "token-with-spaces"

    def test_file_prefix_missing_file(self):
        client = _make_client("file:/nonexistent/path/token.txt")
        assert client._resolve_api_key() == ""

    def test_file_prefix_empty_file(self, tmp_path):
        token_file = tmp_path / "token.txt"
        token_file.write_text("")
        client = _make_client(f"file:{token_file}")
        assert client._resolve_api_key() == ""

    def test_file_prefix_tilde_expansion(self, tmp_path, monkeypatch):
        token_file = tmp_path / "token.txt"
        token_file.write_text("tilde-token")
        monkeypatch.setenv("HOME", str(tmp_path))
        client = _make_client("file:~/token.txt")
        assert client._resolve_api_key() == "tilde-token"

    def test_file_prefix_env_var_expansion(self, tmp_path, monkeypatch):
        """Windows users write file:%APPDATA%\\... — env vars must expand."""
        token_file = tmp_path / "token.txt"
        token_file.write_text("env-var-token")
        # POSIX-style $VAR works on every OS via os.path.expandvars; the
        # Windows-style %VAR% form is also supported but only resolves on
        # Windows. Test the cross-platform $VAR form here.
        monkeypatch.setenv("FREECAD_AI_TEST_DIR", str(tmp_path))
        client = _make_client("file:$FREECAD_AI_TEST_DIR/token.txt")
        assert client._resolve_api_key() == "env-var-token"

    def test_cmd_prefix(self):
        client = _make_client("cmd:echo my-token")
        assert client._resolve_api_key() == "my-token"

    def test_cmd_prefix_strips_whitespace(self):
        client = _make_client("cmd:echo '  spaced-token  '")
        assert client._resolve_api_key() == "spaced-token"

    def test_cmd_prefix_failure(self):
        client = _make_client("cmd:false")
        assert client._resolve_api_key() == ""

    def test_cmd_prefix_nonexistent_command(self):
        client = _make_client("cmd:nonexistent_command_xyz_12345")
        assert client._resolve_api_key() == ""

    def test_file_rereads_on_each_call(self, tmp_path):
        """Token file is re-read each call, picking up refreshed tokens."""
        token_file = tmp_path / "token.txt"
        token_file.write_text("token-v1")
        client = _make_client(f"file:{token_file}")
        assert client._resolve_api_key() == "token-v1"

        token_file.write_text("token-v2")
        assert client._resolve_api_key() == "token-v2"

    def test_headers_use_resolved_key(self, tmp_path):
        """OpenAI headers use the resolved key, not the raw prefix."""
        token_file = tmp_path / "token.txt"
        token_file.write_text("resolved-bearer-token")
        client = _make_client(f"file:{token_file}")
        headers = client._openai_headers()
        assert headers["Authorization"] == "Bearer resolved-bearer-token"

    def test_openai_headers_set_user_agent(self):
        """OpenAI/custom requests send an explicit User-Agent (PR #33).

        urllib otherwise defaults to ``Python-urllib/x.y``, which some WAFs
        and proxies in front of self-hosted OpenAI-compatible endpoints
        reject with a 403. An explicit UA avoids the denylisted default.
        """
        client = _make_client("sk-abc123")
        assert client._openai_headers()["User-Agent"] == "FreeCAD-AI"

    def test_openai_headers_perplexity_integration(self):
        """Requests to api.perplexity.ai identify the integration."""
        client = LLMClient(
            provider_name="perplexity",
            base_url="https://api.perplexity.ai/router/v1",
            api_key="pplx-abc",
            model="test",
        )
        headers = client._openai_headers()
        assert headers["X-Pplx-Integration"] == "freecad-ai"

    def test_openai_headers_no_integration_for_other_hosts(self):
        """Other hosts, including lookalikes, get no Perplexity header."""
        for url in ("http://localhost",
                    "https://api.perplexity.ai.example.com/v1",
                    "https://example.com/api.perplexity.ai"):
            client = LLMClient(
                provider_name="custom", base_url=url,
                api_key="sk-abc123", model="test",
            )
            assert "X-Pplx-Integration" not in client._openai_headers()

    def test_anthropic_headers_set_user_agent(self):
        """Anthropic requests send the same explicit User-Agent (PR #33
        consistency): a proxied Anthropic-compatible endpoint has the same
        denylisted-default gap as the OpenAI path.
        """
        client = _make_client("sk-abc123")
        assert client._anthropic_headers()["User-Agent"] == "FreeCAD-AI"
