"""Unified LLM client using only stdlib (urllib, json, ssl).

Supports two API styles:
  - OpenAI-compatible: /chat/completions (OpenAI, Ollama, Gemini, OpenRouter, custom)
  - Anthropic: /v1/messages (Anthropic's native API)

Both streaming and non-streaming modes are supported, with optional tool calling.
"""

import base64
import json
import logging
import os
import random
try:
    import ssl
    _HAS_SSL = True
except ImportError:
    _HAS_SSL = False
import subprocess
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Generator

logger = logging.getLogger(__name__)

from .providers import get_api_style

# Anthropic API version header
ANTHROPIC_API_VERSION = "2023-06-01"


@dataclass
class ToolCall:
    """A tool call requested by the LLM."""
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    """Response from a non-streaming LLM call."""
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"


@dataclass
class LLMStreamEvent:
    """A single event from a streaming LLM response."""
    type: str  # "text_delta", "thinking_delta", "tool_call_start", "tool_call_delta", "tool_call_end", "done"
    text: str = ""
    tool_call: ToolCall | None = None
    argument_delta: str = ""


class LLMError(Exception):
    """Error communicating with the LLM provider."""
    pass


def _generate_probe_image() -> tuple[int, bytes]:
    """Generate a small PNG with a random 3-digit number for vision probing.

    Returns (number, png_bytes).
    Uses QPainter if available, falls back to a minimal manual PNG.
    """
    number = random.randint(100, 999)
    try:
        # Use the compat shim — FreeCAD 1.0+ ships PySide6, older builds use
        # PySide2. Hard-coding PySide2 here silently fell through to the 1x1
        # pixel fallback on FreeCAD 1.1.0, breaking OCR-based vision probing
        # for non-Ollama providers.
        from ..ui.compat import QtCore as _QtCore, QtGui as _QtGui
        QImage = _QtGui.QImage
        QPainter = _QtGui.QPainter
        QFont = _QtGui.QFont
        QColor = _QtGui.QColor
        Qt = _QtCore.Qt
        # Require a running QCoreApplication — if none exists Qt may crash
        if _QtCore.QCoreApplication.instance() is None:
            raise RuntimeError("No QApplication")

        # 128x64 / 32pt: empirically clean threshold. 64x32 sat right
        # at qwen3-vl's preprocessing cliff (anything smaller returned
        # an empty response in 0.1s — image rejected before inference).
        # 128x64 gives 4x area headroom while still being <1KB PNG.
        img = QImage(128, 64, QImage.Format_RGB32)
        img.fill(QColor(255, 255, 255))
        painter = QPainter(img)
        painter.setPen(QColor(0, 0, 0))
        font = QFont("Sans", 32)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(img.rect(), Qt.AlignCenter, str(number))
        painter.end()

        buf = _QtCore.QBuffer()
        buf.open(_QtCore.QBuffer.WriteOnly)
        img.save(buf, "PNG")
        png_bytes = bytes(buf.data())
        buf.close()
        return number, png_bytes
    except (ImportError, RuntimeError):
        # Fallback: create minimal 1x1 white PNG (for unit tests without Qt)
        import struct
        import zlib

        def _minimal_png() -> bytes:
            signature = b'\x89PNG\r\n\x1a\n'
            # IHDR
            ihdr_data = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b'IHDR' + ihdr_data) & 0xFFFFFFFF
            ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + struct.pack('>I', ihdr_crc)
            # IDAT
            raw = zlib.compress(b'\x00\xff\xff\xff')
            idat_crc = zlib.crc32(b'IDAT' + raw) & 0xFFFFFFFF
            idat = struct.pack('>I', len(raw)) + b'IDAT' + raw + struct.pack('>I', idat_crc)
            # IEND
            iend_crc = zlib.crc32(b'IEND') & 0xFFFFFFFF
            iend = struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc)
            return signature + ihdr + idat + iend

        return number, _minimal_png()


def _check_probe_response(response: str, expected_number: int) -> bool:
    """Check if the LLM response contains the expected number."""
    return str(expected_number) in response


class LLMClient:
    """Unified client for multiple LLM providers."""

    def __init__(self, provider_name: str, base_url: str, api_key: str,
                 model: str, max_tokens: int = 4096, temperature: float = 0.3,
                 thinking: str = "off", model_params: dict | None = None):
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.thinking = thinking  # "off", "on", "extended"
        self.model_params = model_params or {}  # freeform per-model params
        self.api_style = get_api_style(provider_name)

        # Set when the last stream ended because it hit the output-token limit
        # rather than finishing. Read by the UI to warn the user (issue #50).
        self.response_truncated = False

        # SSL context for HTTPS requests.
        # Snap-packaged FreeCAD may lack the _ssl C extension, in which
        # case we fall back to no certificate verification.  The connection
        # is still TLS-encrypted; only server identity is unverified.
        if _HAS_SSL:
            try:
                self._ssl_ctx = ssl.create_default_context()
            except Exception:
                # Cert store unavailable (e.g. snap sandbox)
                self._ssl_ctx = ssl._create_unverified_context()
        else:
            self._ssl_ctx = None

    # ── API key resolution ─────────────────────────────────────

    def _resolve_api_key(self) -> str:
        """Resolve the API key, supporting file: and cmd: prefixes.

        - ``file:/path/to/token`` — reads token from file (re-read each call)
        - ``cmd:some command``    — runs command, uses stdout as token
        - anything else           — used as-is (literal key)
        """
        key = self.api_key
        if not key:
            return ""

        if key.startswith("file:"):
            # Expand both ~ (all OSes) and env vars like %APPDATA% / $HOME
            # so Windows users can write "file:%APPDATA%\\freecad-ai\\key".
            path = os.path.expandvars(os.path.expanduser(key[5:].strip()))
            try:
                with open(path) as f:
                    token = f.read().strip()
                if not token:
                    logger.warning("Token file '%s' is empty", path)
                return token
            except OSError as e:
                logger.error("Failed to read token file '%s': %s", path, e)
                return ""

        if key.startswith("cmd:"):
            command = key[4:].strip()
            try:
                result = subprocess.run(
                    command, shell=True, capture_output=True, text=True,
                    timeout=10,
                )
                if result.returncode != 0:
                    logger.error("Token command failed (rc=%d): %s",
                                 result.returncode, result.stderr.strip())
                    return ""
                token = result.stdout.strip()
                if not token:
                    logger.warning("Token command produced empty output")
                return token
            except subprocess.TimeoutExpired:
                logger.error("Token command timed out after 10s: %s", command)
                return ""
            except OSError as e:
                logger.error("Failed to run token command: %s", e)
                return ""

        return key

    # ── Public API ──────────────────────────────────────────────

    def send(self, messages: list[dict], system: str = "") -> str:
        """Send a non-streaming completion request. Returns the full response text."""
        if self.api_style == "anthropic":
            return self._send_anthropic(messages, system, stream=False)
        else:
            return self._send_openai(messages, system, stream=False)

    def stream(self, messages: list[dict], system: str = "") -> Generator[str, None, None]:
        """Send a streaming request. Yields text deltas as they arrive."""
        self.response_truncated = False  # never carry a stale warning into a new turn
        if self.api_style == "anthropic":
            yield from self._stream_anthropic(messages, system)
        else:
            yield from self._stream_openai(messages, system)

    def send_with_tools(self, messages: list[dict], system: str = "",
                        tools: list[dict] | None = None) -> LLMResponse:
        """Send a non-streaming request with tool definitions. Returns full response."""
        self.response_truncated = False  # never carry a stale warning into a new turn
        if self.api_style == "anthropic":
            return self._send_anthropic_tools(messages, system, tools)
        else:
            return self._send_openai_tools(messages, system, tools)

    def stream_with_tools(self, messages: list[dict], system: str = "",
                          tools: list[dict] | None = None) -> Generator[LLMStreamEvent, None, None]:
        """Send a streaming request with tool definitions. Yields LLMStreamEvents."""
        self.response_truncated = False  # never carry a stale warning into a new turn
        if self.api_style == "anthropic":
            yield from self._stream_anthropic_tools(messages, system, tools)
        else:
            yield from self._stream_openai_tools(messages, system, tools)

    def test_connection(self) -> str:
        """Send a minimal test message. Returns the response or raises LLMError."""
        test_messages = [{"role": "user", "content": "Say 'hello' in one word."}]
        return self.send(test_messages, system="Respond briefly.")

    def _ollama_capabilities(self) -> set[str] | None:
        """Query Ollama's /api/show for the model's capabilities array.

        Returns a set like {"completion", "tools", "vision"} or None if the
        call fails (server unreachable, model not pulled, older Ollama
        without capabilities support, etc.). /api/show is at the host
        root, not under /v1 — strip /v1 if the configured base_url has it.

        Result is cached on the instance so callers (vision_probe,
        detect_capabilities, settings dialog) don't each pay a round trip.
        """
        if self.provider_name != "ollama":
            return None
        if hasattr(self, "_ollama_caps_cache"):
            return self._ollama_caps_cache
        base = self.base_url
        if base.endswith("/v1"):
            base = base[:-3]
        url = f"{base}/api/show"
        try:
            data = self._http_post(
                url, {"Content-Type": "application/json"}, {"model": self.model}
            )
        except Exception:
            self._ollama_caps_cache = None
            return None
        caps = data.get("capabilities")
        result = {str(c).lower() for c in caps} if isinstance(caps, list) else None
        self._ollama_caps_cache = result
        return result

    def detect_capabilities(self) -> dict:
        """Detect provider/model capabilities for the Settings dialog.

        Returns a dict with at least "vision" set. For Ollama with
        /api/show available, also includes "tools" and "thinking" as
        explicit booleans. For non-Ollama providers, only "vision" is
        populated (via the behavioral OCR probe) — tools support comes
        from the provider-wide static flag, no per-model detection.
        """
        if self.provider_name == "ollama":
            caps = self._ollama_capabilities()
            if caps is not None:
                return {
                    "vision": "vision" in caps,
                    "tools": "tools" in caps,
                    "thinking": "thinking" in caps,
                }
        return {"vision": self.vision_probe()}

    def vision_probe(self) -> bool:
        """Test if the model supports vision.

        For Ollama, prefer the structured /api/show capability check —
        the behavioral OCR probe below is too fragile for vision models
        that handle real images fine but struggle to read tiny text in a
        64×32 PNG. For other providers, fall back to the behavioral probe.
        """
        if self.provider_name == "ollama":
            caps = self._ollama_capabilities()
            if caps is not None:
                return "vision" in caps
            # capability call failed (older Ollama, transient network) →
            # fall through to the behavioral probe rather than give up
        try:
            number, png_bytes = _generate_probe_image()
            b64 = base64.b64encode(png_bytes).decode("ascii")

            if self.api_style == "anthropic":
                messages = [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "What number is shown in this image? Reply with only the number.",
                        },
                    ],
                }]
            else:
                # OpenAI-compatible format
                data_uri = f"data:image/png;base64,{b64}"
                messages = [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri},
                        },
                        {
                            "type": "text",
                            "text": "What number is shown in this image? Reply with only the number.",
                        },
                    ],
                }]

            response = self.send(messages, system="Respond briefly.")
            return _check_probe_response(response, number)
        except Exception:
            return False

    # ── OpenAI-compatible ───────────────────────────────────────

    def _openai_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _openai_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "FreeCAD-AI",
        }
        resolved_key = self._resolve_api_key()
        if resolved_key:
            headers["Authorization"] = f"Bearer {resolved_key}"
        return headers

    def _openai_body(self, messages: list[dict], system: str, stream: bool,
                     tools: list[dict] | None = None) -> dict:
        msgs = []
        if system:
            sys_content = system
            # For Ollama: append /think or /no_think tags for models that support them
            # (models that don't will just ignore these as text)
            if self.provider_name == "ollama":
                if self.thinking == "off":
                    sys_content += "\n/no_think"
                else:
                    sys_content += "\n/think"
            msgs.append({"role": "system", "content": sys_content})
        msgs.extend(messages)

        body = {
            "model": self.model,
            "messages": msgs,
            "temperature": self.model_params.get("temperature", self.temperature),
            "stream": stream,
            "max_tokens": self.max_tokens,
        }
        # Merge freeform model params (top_p, top_k, etc.) into body.
        # Keys already set above (model, messages, stream, max_tokens) are
        # not overwritten — only new keys like top_p, top_k, n, etc. are added.
        _RESERVED = {"model", "messages", "stream", "max_tokens", "temperature",
                      "tools", "tool_choice"}
        for key, value in self.model_params.items():
            if key not in _RESERVED:
                body[key] = value

        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        # OpenAI reasoning models (o1, o3, etc.)
        elif self.thinking != "off":
            effort_map = {"on": "medium", "extended": "high"}
            body["reasoning_effort"] = effort_map.get(self.thinking, "medium")

        # Provider-specific API transformations
        self._apply_provider_overrides(body)

        return body

    def _apply_provider_overrides(self, body: dict) -> None:
        """Apply provider-specific API transformations.

        These are structural changes the API requires (parameter renames,
        removals) — NOT sampling parameter defaults.  Sampling defaults
        belong in providers.py ``default_params`` and are user-editable
        via the Model Parameters table in Settings.
        """
        if self.provider_name == "openai" and (self.model or "").lower().startswith("gpt-5"):
            # Official OpenAI Chat Completions: gpt-5.x rejects max_tokens and non-default temperature.
            body.pop("temperature", None)
            if "max_tokens" in body:
                body["max_completion_tokens"] = body.pop("max_tokens")

    def _send_openai(self, messages: list[dict], system: str, stream: bool = False) -> str:
        body = self._openai_body(messages, system, stream=False)
        data = self._http_post(self._openai_url(), self._openai_headers(), body)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected response format: {e}\n{json.dumps(data, indent=2)}")

    def _send_openai_tools(self, messages: list[dict], system: str,
                           tools: list[dict] | None) -> LLMResponse:
        body = self._openai_body(messages, system, stream=False, tools=tools)
        data = self._http_post(self._openai_url(), self._openai_headers(), body)
        try:
            choice = data["choices"][0]
            msg = choice["message"]
            text = msg.get("content") or ""
            finish = choice.get("finish_reason", "stop")

            tool_calls = []
            for tc in msg.get("tool_calls", []):
                args = tc["function"].get("arguments", "{}")
                if isinstance(args, str):
                    args = json.loads(args)
                tool_calls.append(ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=args,
                ))

            if finish == "length":
                self.response_truncated = True

            stop_reason = "tool_use" if (finish == "tool_calls" or tool_calls) else "end_turn"
            return LLMResponse(text=text, tool_calls=tool_calls, stop_reason=stop_reason)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
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
                    if choices[0].get("finish_reason") == "length":
                        self.response_truncated = True
                    # Skip reasoning_content in simple stream mode
            except (KeyError, IndexError):
                continue

    def _stream_openai_tools(self, messages: list[dict], system: str,
                             tools: list[dict] | None) -> Generator[LLMStreamEvent, None, None]:
        body = self._openai_body(messages, system, stream=True, tools=tools)
        # Track in-progress tool calls: {index: {"id": ..., "name": ..., "arguments_json": ...}}
        pending_tools: dict[int, dict] = {}

        for chunk in self._http_stream(self._openai_url(), self._openai_headers(), body):
            try:
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})
                finish = choice.get("finish_reason")

                # Thinking/reasoning content (Ollama qwen3, OpenAI o1/o3)
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    yield LLMStreamEvent(type="thinking_delta", text=reasoning)

                # Text content
                content = delta.get("content")
                if content:
                    yield LLMStreamEvent(type="text_delta", text=content)

                # Tool calls
                for tc_delta in delta.get("tool_calls", []):
                    idx = tc_delta.get("index", 0)
                    if idx not in pending_tools:
                        pending_tools[idx] = {
                            "id": tc_delta.get("id", ""),
                            "name": "",
                            "arguments_json": "",
                        }

                    pt = pending_tools[idx]
                    if tc_delta.get("id"):
                        pt["id"] = tc_delta["id"]

                    func = tc_delta.get("function", {})
                    if func.get("name"):
                        pt["name"] = func["name"]
                        yield LLMStreamEvent(
                            type="tool_call_start",
                            tool_call=ToolCall(id=pt["id"], name=pt["name"], arguments={}),
                        )

                    arg_chunk = func.get("arguments", "")
                    if arg_chunk:
                        pt["arguments_json"] += arg_chunk
                        yield LLMStreamEvent(type="tool_call_delta", argument_delta=arg_chunk)

                # Cut off at the output limit. Any pending tool call is
                # incomplete — its arguments JSON stops mid-object — so it is
                # deliberately dropped rather than emitted with silently
                # defaulted arguments. Record the truncation and end the turn;
                # the loop halts on it instead of acting on a partial payload
                # (issue #52).
                if finish == "length":
                    self.response_truncated = True
                    yield LLMStreamEvent(type="done")
                    return

                # Finish — emit any pending tool calls regardless of
                # finish_reason, because some providers (e.g. Moonshot/Kimi)
                # return "stop" instead of "tool_calls" even when the
                # response contains tool calls.
                if finish in ("tool_calls", "stop"):
                    if pending_tools:
                        for idx, pt in sorted(pending_tools.items()):
                            try:
                                args = json.loads(pt["arguments_json"]) if pt["arguments_json"] else {}
                            except json.JSONDecodeError:
                                args = {}
                            yield LLMStreamEvent(
                                type="tool_call_end",
                                tool_call=ToolCall(id=pt["id"], name=pt["name"], arguments=args),
                            )
                    yield LLMStreamEvent(type="done")
                    return

            except (KeyError, IndexError):
                continue

        yield LLMStreamEvent(type="done")

    @staticmethod
    def _convert_ollama_images(msgs: list[dict]):
        """Convert OpenAI-style content block arrays to Ollama's flat images field.

        Ollama expects: {"role": "user", "content": "text", "images": ["base64..."]}
        instead of content block arrays with image_url types.
        Modifies msgs in place.
        """
        for msg in msgs:
            if not isinstance(msg.get("content"), list):
                continue
            text_parts = []
            images = []
            for block in msg["content"]:
                if block.get("type") == "text":
                    text_parts.append(block["text"])
                elif block.get("type") == "image_url":
                    # Extract base64 from data URI: "data:image/png;base64,..."
                    url = block.get("image_url", {}).get("url", "")
                    if ";base64," in url:
                        images.append(url.split(";base64,", 1)[1])
                elif block.get("type") == "image":
                    # Internal format — shouldn't reach here but handle gracefully
                    images.append(block.get("data", ""))
            msg["content"] = "\n".join(text_parts)
            if images:
                msg["images"] = images

    # ── Anthropic ───────────────────────────────────────────────

    def _anthropic_url(self) -> str:
        return f"{self.base_url}/v1/messages"

    def _anthropic_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "FreeCAD-AI",
            "x-api-key": self._resolve_api_key(),
            "anthropic-version": ANTHROPIC_API_VERSION,
        }
        if self.thinking != "off":
            headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"
        return headers

    def _anthropic_body(self, messages: list[dict], system: str, stream: bool,
                        tools: list[dict] | None = None) -> dict:
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }
        # Anthropic extended thinking requires temperature=1 and a budget
        if self.thinking != "off":
            budget_map = {"on": 4096, "extended": 16384}
            budget = budget_map.get(self.thinking, 4096)
            body["temperature"] = 1
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }
        else:
            body["temperature"] = self.model_params.get(
                "temperature", self.temperature
            )
        if system:
            body["system"] = system
        if tools:
            body["tools"] = tools
        return body

    def _send_anthropic(self, messages: list[dict], system: str, stream: bool = False) -> str:
        body = self._anthropic_body(messages, system, stream=False)
        data = self._http_post(self._anthropic_url(), self._anthropic_headers(), body)
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected response format: {e}\n{json.dumps(data, indent=2)}")

    def _send_anthropic_tools(self, messages: list[dict], system: str,
                              tools: list[dict] | None) -> LLMResponse:
        body = self._anthropic_body(messages, system, stream=False, tools=tools)
        data = self._http_post(self._anthropic_url(), self._anthropic_headers(), body)
        try:
            text = ""
            tool_calls = []
            for block in data.get("content", []):
                if block["type"] == "text":
                    text += block["text"]
                elif block["type"] == "tool_use":
                    tool_calls.append(ToolCall(
                        id=block["id"],
                        name=block["name"],
                        arguments=block.get("input", {}),
                    ))
            stop_reason = data.get("stop_reason", "end_turn")
            if stop_reason == "max_tokens":
                self.response_truncated = True
            return LLMResponse(text=text, tool_calls=tool_calls, stop_reason=stop_reason)
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
            elif event_type == "message_delta":
                if chunk.get("delta", {}).get("stop_reason") == "max_tokens":
                    self.response_truncated = True

    def _stream_anthropic_tools(self, messages: list[dict], system: str,
                                tools: list[dict] | None) -> Generator[LLMStreamEvent, None, None]:
        body = self._anthropic_body(messages, system, stream=True, tools=tools)
        # Track current tool call being streamed
        current_tool_id = ""
        current_tool_name = ""
        current_tool_json = ""

        for chunk in self._http_stream(self._anthropic_url(), self._anthropic_headers(), body):
            event_type = chunk.get("type", "")

            if event_type == "content_block_start":
                block = chunk.get("content_block", {})
                if block.get("type") == "tool_use":
                    current_tool_id = block.get("id", "")
                    current_tool_name = block.get("name", "")
                    current_tool_json = ""
                    yield LLMStreamEvent(
                        type="tool_call_start",
                        tool_call=ToolCall(id=current_tool_id, name=current_tool_name, arguments={}),
                    )

            elif event_type == "content_block_delta":
                delta = chunk.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        yield LLMStreamEvent(type="text_delta", text=text)
                elif delta.get("type") == "thinking_delta":
                    thinking_text = delta.get("thinking", "")
                    if thinking_text:
                        yield LLMStreamEvent(type="thinking_delta", text=thinking_text)
                elif delta.get("type") == "input_json_delta":
                    json_chunk = delta.get("partial_json", "")
                    if json_chunk:
                        current_tool_json += json_chunk
                        yield LLMStreamEvent(type="tool_call_delta", argument_delta=json_chunk)

            elif event_type == "content_block_stop":
                if current_tool_name:
                    try:
                        args = json.loads(current_tool_json) if current_tool_json else {}
                    except json.JSONDecodeError:
                        args = {}
                    yield LLMStreamEvent(
                        type="tool_call_end",
                        tool_call=ToolCall(id=current_tool_id, name=current_tool_name, arguments=args),
                    )
                    current_tool_name = ""
                    current_tool_id = ""
                    current_tool_json = ""

            elif event_type == "message_stop":
                yield LLMStreamEvent(type="done")
                return

            elif event_type == "message_delta":
                # Check stop_reason
                delta = chunk.get("delta", {})
                if delta.get("stop_reason") == "max_tokens":
                    self.response_truncated = True  # issue #52
                elif delta.get("stop_reason") == "tool_use":
                    pass  # tool_call_end already emitted from content_block_stop

        yield LLMStreamEvent(type="done")

    # ── HTTP helpers ────────────────────────────────────────────

    _MAX_RETRIES = 5
    _BASE_BACKOFF = 2  # seconds

    def _check_ssl(self, url: str) -> None:
        """Raise LLMError if HTTPS is requested but SSL is unavailable."""
        if url.startswith("https") and not _HAS_SSL:
            raise LLMError(
                "HTTPS is not available (Python _ssl module missing). "
                "This can happen when the host application ships a Python built "
                "without SSL support (e.g. snap packages). "
                "Use Ollama (http://localhost:11434) or fix that Python's SSL support."
            )

    def _get_retry_delay(self, error: urllib.error.HTTPError, attempt: int) -> float:
        """Calculate retry delay from Retry-After header or exponential backoff."""
        retry_after = error.headers.get("Retry-After") if error.headers else None
        if retry_after:
            try:
                return max(float(retry_after), 1.0)
            except ValueError:
                pass
        return self._BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 1)

    def _http_post(self, url: str, headers: dict, body: dict) -> dict:
        """Make an HTTP POST request with retry on 429. Returns parsed JSON."""
        self._check_ssl(url)
        payload = json.dumps(body).encode("utf-8")
        timeout = 300 if self.provider_name == "ollama" else 120
        ctx = self._ssl_ctx if url.startswith("https") else None

        for attempt in range(self._MAX_RETRIES + 1):
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < self._MAX_RETRIES:
                    delay = self._get_retry_delay(e, attempt)
                    logger.warning("Rate limited (429), retrying in %.1fs (attempt %d/%d)",
                                   delay, attempt + 1, self._MAX_RETRIES)
                    time.sleep(delay)
                    continue
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
        """Make a streaming HTTP POST with retry on 429. Yields parsed SSE data chunks."""
        self._check_ssl(url)
        payload = json.dumps(body).encode("utf-8")
        timeout = 300 if self.provider_name == "ollama" else 120
        ctx = self._ssl_ctx if url.startswith("https") else None

        resp = None
        for attempt in range(self._MAX_RETRIES + 1):
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            try:
                resp = urllib.request.urlopen(req, context=ctx, timeout=timeout)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < self._MAX_RETRIES:
                    delay = self._get_retry_delay(e, attempt)
                    logger.warning("Rate limited (429), retrying in %.1fs (attempt %d/%d)",
                                   delay, attempt + 1, self._MAX_RETRIES)
                    time.sleep(delay)
                    continue
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


# Models that require thinking content to be stripped from conversation
# history.  Matched case-insensitively against the start of the model name.
_STRIP_THINKING_MODELS = (
    "gemma",     # Gemma 3/4: "No Thinking Content in History"
)


def should_strip_thinking(model: str, override: bool | None = None) -> bool:
    """Determine whether thinking/reasoning content should be stripped from history.

    Args:
        model: Model name string.
        override: Explicit user setting from config.strip_thinking_history.
            None=auto-detect from model name, True/False=user override.
    """
    if override is not None:
        return override
    model_lower = (model or "").lower()
    return any(model_lower.startswith(prefix) for prefix in _STRIP_THINKING_MODELS)


def create_client_from_config() -> LLMClient:
    """Create an LLMClient from the current application config."""
    from ..config import get_config
    cfg = get_config()
    # Resolve per-model params: saved params for this model, or empty dict.
    model_params = cfg.model_params.get(cfg.provider.model, {})
    return LLMClient(
        provider_name=cfg.provider.name,
        base_url=cfg.provider.base_url,
        api_key=cfg.provider.api_key,
        model=cfg.provider.model,
        max_tokens=cfg.max_tokens,
        temperature=cfg.temperature,
        thinking=cfg.thinking,
        model_params=model_params,
    )
