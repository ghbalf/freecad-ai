"""Unified LLM client using only stdlib (urllib, json, ssl).

Supports two API styles:
  - OpenAI-compatible: /chat/completions (OpenAI, Ollama, Gemini, OpenRouter, custom)
  - Anthropic: /v1/messages (Anthropic's native API)

Both streaming and non-streaming modes are supported.
"""

import json
import ssl
import urllib.request
import urllib.error
from typing import Generator

from .providers import get_api_style

# Anthropic API version header
ANTHROPIC_API_VERSION = "2023-06-01"


class LLMError(Exception):
    """Error communicating with the LLM provider."""
    pass


class LLMClient:
    """Unified client for multiple LLM providers."""

    def __init__(self, provider_name: str, base_url: str, api_key: str,
                 model: str, max_tokens: int = 4096, temperature: float = 0.3):
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.api_style = get_api_style(provider_name)

        # SSL context for HTTPS requests
        self._ssl_ctx = ssl.create_default_context()

    # ── Public API ──────────────────────────────────────────────

    def send(self, messages: list[dict], system: str = "") -> str:
        """Send a non-streaming completion request. Returns the full response text."""
        if self.api_style == "anthropic":
            return self._send_anthropic(messages, system, stream=False)
        else:
            return self._send_openai(messages, system, stream=False)

    def stream(self, messages: list[dict], system: str = "") -> Generator[str, None, None]:
        """Send a streaming request. Yields text deltas as they arrive."""
        if self.api_style == "anthropic":
            yield from self._stream_anthropic(messages, system)
        else:
            yield from self._stream_openai(messages, system)

    def test_connection(self) -> str:
        """Send a minimal test message. Returns the response or raises LLMError."""
        test_messages = [{"role": "user", "content": "Say 'hello' in one word."}]
        return self.send(test_messages, system="Respond briefly.")

    # ── OpenAI-compatible ───────────────────────────────────────

    def _openai_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _openai_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _openai_body(self, messages: list[dict], system: str, stream: bool) -> dict:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        return {
            "model": self.model,
            "messages": msgs,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
        }

    def _send_openai(self, messages: list[dict], system: str, stream: bool = False) -> str:
        body = self._openai_body(messages, system, stream=False)
        data = self._http_post(self._openai_url(), self._openai_headers(), body)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected response format: {e}\n{json.dumps(data, indent=2)}")

    def _stream_openai(self, messages: list[dict], system: str) -> Generator[str, None, None]:
        body = self._openai_body(messages, system, stream=True)
        for chunk in self._http_stream(self._openai_url(), self._openai_headers(), body):
            # OpenAI SSE: data contains choices[0].delta.content
            try:
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
            except (KeyError, IndexError):
                continue

    # ── Anthropic ───────────────────────────────────────────────

    def _anthropic_url(self) -> str:
        return f"{self.base_url}/v1/messages"

    def _anthropic_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
        }

    def _anthropic_body(self, messages: list[dict], system: str, stream: bool) -> dict:
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
        }
        if system:
            body["system"] = system
        return body

    def _send_anthropic(self, messages: list[dict], system: str, stream: bool = False) -> str:
        body = self._anthropic_body(messages, system, stream=False)
        data = self._http_post(self._anthropic_url(), self._anthropic_headers(), body)
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected response format: {e}\n{json.dumps(data, indent=2)}")

    def _stream_anthropic(self, messages: list[dict], system: str) -> Generator[str, None, None]:
        body = self._anthropic_body(messages, system, stream=True)
        for chunk in self._http_stream(self._anthropic_url(), self._anthropic_headers(), body):
            # Anthropic SSE: content_block_delta events with delta.text
            event_type = chunk.get("type", "")
            if event_type == "content_block_delta":
                delta = chunk.get("delta", {})
                text = delta.get("text")
                if text:
                    yield text

    # ── HTTP helpers ────────────────────────────────────────────

    def _http_post(self, url: str, headers: dict, body: dict) -> dict:
        """Make an HTTP POST request and return parsed JSON response."""
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

        try:
            ctx = self._ssl_ctx if url.startswith("https") else None
            with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                pass
            raise LLMError(f"HTTP {e.code}: {e.reason}\n{error_body}")
        except urllib.error.URLError as e:
            raise LLMError(f"Connection error: {e.reason}")
        except Exception as e:
            raise LLMError(f"Request failed: {e}")

    def _http_stream(self, url: str, headers: dict, body: dict) -> Generator[dict, None, None]:
        """Make a streaming HTTP POST and yield parsed SSE data chunks."""
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

        try:
            ctx = self._ssl_ctx if url.startswith("https") else None
            resp = urllib.request.urlopen(req, context=ctx, timeout=120)
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                pass
            raise LLMError(f"HTTP {e.code}: {e.reason}\n{error_body}")
        except urllib.error.URLError as e:
            raise LLMError(f"Connection error: {e.reason}")
        except Exception as e:
            raise LLMError(f"Request failed: {e}")

        try:
            buffer = ""
            for raw_line in resp:
                line = raw_line.decode("utf-8")
                buffer += line
                # Process complete lines
                while "\n" in buffer:
                    text_line, buffer = buffer.split("\n", 1)
                    text_line = text_line.strip()

                    if not text_line:
                        continue
                    if text_line.startswith(":"):
                        # SSE comment, skip
                        continue
                    if text_line == "data: [DONE]":
                        return
                    if text_line.startswith("event:"):
                        # Anthropic uses event: lines but the data follows on next line
                        continue
                    if text_line.startswith("data: "):
                        json_str = text_line[6:]
                        try:
                            yield json.loads(json_str)
                        except json.JSONDecodeError:
                            continue
        finally:
            resp.close()


def create_client_from_config() -> LLMClient:
    """Create an LLMClient from the current application config."""
    from ..config import get_config
    cfg = get_config()
    return LLMClient(
        provider_name=cfg.provider.name,
        base_url=cfg.provider.base_url,
        api_key=cfg.provider.api_key,
        model=cfg.provider.model,
        max_tokens=cfg.max_tokens,
        temperature=cfg.temperature,
    )
