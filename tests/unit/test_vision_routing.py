"""Tests for vision routing."""

from freecad_ai.config import AppConfig


class TestVisionConfig:
    """Config fields and supports_vision property."""

    def test_defaults(self):
        cfg = AppConfig()
        assert cfg.provider.vision_detected is None
        assert cfg.provider.vision_override is None

    def test_supports_vision_override_takes_precedence(self):
        cfg = AppConfig()
        cfg.provider.vision_detected = False
        cfg.provider.vision_override = True
        assert cfg.supports_vision is True

    def test_supports_vision_detected_used_when_no_override(self):
        cfg = AppConfig()
        cfg.provider.vision_detected = True
        assert cfg.supports_vision is True

    def test_supports_vision_false_when_detected_false(self):
        cfg = AppConfig()
        cfg.provider.vision_detected = False
        assert cfg.supports_vision is False

    def test_supports_vision_false_when_untested(self):
        cfg = AppConfig()
        assert cfg.supports_vision is False

    def test_vision_fields_roundtrip_json(self):
        cfg = AppConfig()
        cfg.provider.vision_detected = True
        cfg.provider.vision_override = False
        d = cfg.to_dict()
        cfg2 = AppConfig.from_dict(d)
        assert cfg2.provider.vision_detected is True
        assert cfg2.provider.vision_override is False

    def test_vision_fields_none_roundtrip(self):
        cfg = AppConfig()
        d = cfg.to_dict()
        cfg2 = AppConfig.from_dict(d)
        assert cfg2.provider.vision_detected is None
        assert cfg2.provider.vision_override is None


class TestVisionProbe:
    """Vision probe image generation and response parsing."""

    def test_generate_probe_image_returns_png_bytes(self):
        from freecad_ai.llm.client import _generate_probe_image
        number, png_bytes = _generate_probe_image()
        assert 100 <= number <= 999
        assert png_bytes[:8] == b'\x89PNG\r\n\x1a\n'  # PNG magic bytes
        assert len(png_bytes) > 50  # non-trivial content

    def test_generate_probe_image_random(self):
        from freecad_ai.llm.client import _generate_probe_image
        numbers = {_generate_probe_image()[0] for _ in range(20)}
        assert len(numbers) > 1  # not always the same number

    def test_check_probe_response_exact_match(self):
        from freecad_ai.llm.client import _check_probe_response
        assert _check_probe_response("427", 427) is True

    def test_check_probe_response_in_sentence(self):
        from freecad_ai.llm.client import _check_probe_response
        assert _check_probe_response("The number shown is 427.", 427) is True

    def test_check_probe_response_wrong_number(self):
        from freecad_ai.llm.client import _check_probe_response
        assert _check_probe_response("The number is 123.", 427) is False

    def test_check_probe_response_no_number(self):
        from freecad_ai.llm.client import _check_probe_response
        assert _check_probe_response("I cannot see any image.", 427) is False

    def test_check_probe_response_empty(self):
        from freecad_ai.llm.client import _check_probe_response
        assert _check_probe_response("", 427) is False


from unittest.mock import patch, MagicMock


class TestVisionProbeMethod:
    """LLMClient.vision_probe() integration."""

    @patch("freecad_ai.llm.client._generate_probe_image")
    def test_vision_probe_returns_true_for_correct_answer(self, mock_gen):
        from freecad_ai.llm.client import LLMClient
        mock_gen.return_value = (427, b'\x89PNG\r\n\x1a\nfakedata')
        client = LLMClient("openai", "http://localhost", "", "test-model")
        with patch.object(client, "send", return_value="427"):
            assert client.vision_probe() is True

    @patch("freecad_ai.llm.client._generate_probe_image")
    def test_vision_probe_returns_false_for_wrong_answer(self, mock_gen):
        from freecad_ai.llm.client import LLMClient
        mock_gen.return_value = (427, b'\x89PNG\r\n\x1a\nfakedata')
        client = LLMClient("openai", "http://localhost", "", "test-model")
        with patch.object(client, "send", return_value="I cannot see images"):
            assert client.vision_probe() is False

    @patch("freecad_ai.llm.client._generate_probe_image")
    def test_vision_probe_returns_false_on_error(self, mock_gen):
        from freecad_ai.llm.client import LLMClient
        mock_gen.return_value = (427, b'\x89PNG\r\n\x1a\nfakedata')
        client = LLMClient("openai", "http://localhost", "", "test-model")
        with patch.object(client, "send", side_effect=Exception("API error")):
            assert client.vision_probe() is False


class TestOllamaCapabilities:
    """LLMClient._ollama_capabilities() and ollama path of vision_probe."""

    def test_capabilities_returns_none_for_non_ollama(self):
        from freecad_ai.llm.client import LLMClient
        client = LLMClient("openai", "http://localhost/v1", "", "gpt-4o")
        assert client._ollama_capabilities() is None

    def test_capabilities_strips_v1_suffix(self):
        from freecad_ai.llm.client import LLMClient
        client = LLMClient("ollama", "http://localhost:11434/v1", "", "qwen2.5vl:7b")
        captured = {}

        def fake_post(url, headers, body):
            captured["url"] = url
            captured["body"] = body
            return {"capabilities": ["completion", "vision"]}

        with patch.object(client, "_http_post", side_effect=fake_post):
            caps = client._ollama_capabilities()

        assert captured["url"] == "http://localhost:11434/api/show"
        assert captured["body"] == {"model": "qwen2.5vl:7b"}
        assert caps == {"completion", "vision"}

    def test_capabilities_handles_no_v1_suffix(self):
        from freecad_ai.llm.client import LLMClient
        client = LLMClient("ollama", "http://localhost:11434", "", "gemma3:12b")
        captured = {}

        def fake_post(url, headers, body):
            captured["url"] = url
            return {"capabilities": ["vision"]}

        with patch.object(client, "_http_post", side_effect=fake_post):
            client._ollama_capabilities()

        assert captured["url"] == "http://localhost:11434/api/show"

    def test_capabilities_returns_none_on_http_error(self):
        from freecad_ai.llm.client import LLMClient, LLMError
        client = LLMClient("ollama", "http://localhost:11434/v1", "", "qwen2.5vl:7b")
        with patch.object(client, "_http_post", side_effect=LLMError("404")):
            assert client._ollama_capabilities() is None

    def test_capabilities_returns_none_when_field_missing(self):
        """Older Ollama versions don't return a capabilities key at all."""
        from freecad_ai.llm.client import LLMClient
        client = LLMClient("ollama", "http://localhost:11434/v1", "", "qwen2.5vl:7b")
        with patch.object(client, "_http_post", return_value={"modelfile": "..."}):
            assert client._ollama_capabilities() is None

    def test_vision_probe_uses_capabilities_for_ollama_vision(self):
        """If Ollama says vision is supported, return True without behavioral probe."""
        from freecad_ai.llm.client import LLMClient
        client = LLMClient("ollama", "http://localhost:11434/v1", "", "qwen2.5vl:7b")
        with patch.object(client, "_ollama_capabilities", return_value={"completion", "vision"}):
            with patch.object(client, "send") as mock_send:
                assert client.vision_probe() is True
                mock_send.assert_not_called()  # behavioral probe skipped

    def test_vision_probe_uses_capabilities_for_ollama_no_vision(self):
        """If Ollama says vision isn't supported, return False without behavioral probe."""
        from freecad_ai.llm.client import LLMClient
        client = LLMClient("ollama", "http://localhost:11434/v1", "", "llama3")
        with patch.object(client, "_ollama_capabilities", return_value={"completion", "tools"}):
            with patch.object(client, "send") as mock_send:
                assert client.vision_probe() is False
                mock_send.assert_not_called()

    @patch("freecad_ai.llm.client._generate_probe_image")
    def test_vision_probe_falls_back_to_behavioral_when_caps_unavailable(self, mock_gen):
        """Older Ollama (no capabilities field) → fall back to OCR probe."""
        from freecad_ai.llm.client import LLMClient
        mock_gen.return_value = (427, b'\x89PNG\r\n\x1a\nfakedata')
        client = LLMClient("ollama", "http://localhost:11434/v1", "", "old-model")
        with patch.object(client, "_ollama_capabilities", return_value=None):
            with patch.object(client, "send", return_value="427"):
                assert client.vision_probe() is True

    def test_capabilities_cached_per_instance(self):
        """Repeated calls share one /api/show round-trip."""
        from freecad_ai.llm.client import LLMClient
        client = LLMClient("ollama", "http://localhost:11434/v1", "", "qwen2.5vl:7b")
        with patch.object(client, "_http_post", return_value={"capabilities": ["vision"]}) as mock_post:
            client._ollama_capabilities()
            client._ollama_capabilities()
            client._ollama_capabilities()
            assert mock_post.call_count == 1


class TestDetectCapabilities:
    """LLMClient.detect_capabilities() unified entry point."""

    def test_ollama_returns_full_dict_from_api_show(self):
        from freecad_ai.llm.client import LLMClient
        client = LLMClient("ollama", "http://localhost:11434/v1", "", "qwen3-vl:32b")
        with patch.object(
            client, "_ollama_capabilities",
            return_value={"completion", "tools", "vision", "thinking"},
        ):
            caps = client.detect_capabilities()
        assert caps == {"vision": True, "tools": True, "thinking": True}

    def test_ollama_no_caps_falls_back_to_behavioral_vision_only(self):
        """Older Ollama without capabilities field → only vision via OCR probe.

        tools/thinking should NOT be in the dict, so the Settings receiver
        leaves tools_detected/thinking_detected as None and the provider-wide
        static flag stays in effect for tool calling.
        """
        from freecad_ai.llm.client import LLMClient
        client = LLMClient("ollama", "http://localhost:11434/v1", "", "old-ollama")
        with patch.object(client, "_ollama_capabilities", return_value=None):
            with patch.object(client, "vision_probe", return_value=False):
                caps = client.detect_capabilities()
        assert caps == {"vision": False}
        assert "tools" not in caps
        assert "thinking" not in caps

    def test_non_ollama_only_returns_vision(self):
        """OpenAI/Anthropic/etc only get vision via behavioral probe."""
        from freecad_ai.llm.client import LLMClient
        client = LLMClient("openai", "https://api.openai.com/v1", "key", "gpt-4o")
        with patch.object(client, "vision_probe", return_value=True):
            caps = client.detect_capabilities()
        assert caps == {"vision": True}


class TestSupportsToolsConfig:
    """AppConfig.supports_tools combines static flag with detected override."""

    def test_supports_tools_uses_detected_when_set(self):
        from freecad_ai.config import AppConfig
        cfg = AppConfig()
        cfg.provider.name = "ollama"  # statically marked tool-capable
        cfg.provider.tools_detected = False    # detected: model doesn't support tools
        assert cfg.supports_tools is False

    def test_supports_tools_falls_back_to_provider_when_untested(self):
        from freecad_ai.config import AppConfig
        cfg = AppConfig()
        cfg.provider.name = "anthropic"
        cfg.provider.tools_detected = None
        assert cfg.supports_tools is True  # anthropic provider flag is True

    def test_supports_tools_detected_true_overrides_provider(self):
        from freecad_ai.config import AppConfig
        cfg = AppConfig()
        cfg.provider.name = "openrouter"
        cfg.provider.tools_detected = True
        assert cfg.supports_tools is True

    def test_tools_detected_roundtrip_json(self):
        from freecad_ai.config import AppConfig
        cfg = AppConfig()
        cfg.provider.tools_detected = True
        cfg.provider.thinking_detected = False
        d = cfg.to_dict()
        cfg2 = AppConfig.from_dict(d)
        assert cfg2.provider.tools_detected is True
        assert cfg2.provider.thinking_detected is False

    def test_custom_provider_supports_tools_by_default(self):
        """Issue #38: a custom OpenAI-compatible endpoint must get tools.

        There is no per-model tool probe for the custom provider (the
        /api/show capability check is Ollama-only), so tools_detected stays
        None and the static provider flag is the sole source of truth. It
        must therefore default to True — most custom servers (vLLM,
        llama.cpp --jinja, SGLang) support tool calling, and a False default
        silently strips the tools array from every request.
        """
        from freecad_ai.config import AppConfig
        cfg = AppConfig()
        cfg.provider.name = "custom"
        cfg.provider.tools_detected = None  # no Ollama probe runs for custom
        assert cfg.supports_tools is True

    def test_custom_provider_tools_opt_out_still_honored(self):
        """A genuinely tool-less custom endpoint can still opt out.

        Setting tools_detected=False (via config.json) must override the
        now-True static default, so the escape hatch keeps working.
        """
        from freecad_ai.config import AppConfig
        cfg = AppConfig()
        cfg.provider.name = "custom"
        cfg.provider.tools_detected = False
        assert cfg.supports_tools is False


class TestFallbackDiscovery:
    """MCP fallback tool search."""

    def test_find_vision_fallback_found(self):
        from freecad_ai.tools.registry import ToolRegistry, ToolDefinition, ToolResult
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="llm-vision-mcp__describe_image",
            description="Describe an image",
            parameters=[],
            handler=lambda **kw: ToolResult(success=True, output="test"),
            category="mcp",
        ))
        from freecad_ai.mcp.manager import find_vision_fallback
        assert find_vision_fallback(registry) == "llm-vision-mcp__describe_image"

    def test_find_vision_fallback_not_found(self):
        from freecad_ai.tools.registry import ToolRegistry
        registry = ToolRegistry()
        from freecad_ai.mcp.manager import find_vision_fallback
        assert find_vision_fallback(registry) is None

    def test_find_vision_fallback_partial_name_match(self):
        from freecad_ai.tools.registry import ToolRegistry, ToolDefinition, ToolResult
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="my_server__describe_image",
            description="Vision tool",
            parameters=[],
            handler=lambda **kw: ToolResult(success=True, output="test"),
            category="mcp",
        ))
        from freecad_ai.mcp.manager import find_vision_fallback
        assert find_vision_fallback(registry) == "my_server__describe_image"


class TestImageInterception:
    """Image block replacement in get_messages_for_api."""

    def _make_conversation_with_image(self):
        from freecad_ai.core.conversation import Conversation
        conv = Conversation()
        conv.add_user_message("Look at this", images=[
            {"type": "image", "source": "base64", "media_type": "image/png", "data": "abc123"},
        ])
        return conv

    def test_no_describe_fn_keeps_images(self):
        conv = self._make_conversation_with_image()
        msgs = conv.get_messages_for_api(api_style="openai")
        content = msgs[0]["content"]
        types = [b["type"] for b in content]
        assert "image_url" in types

    def test_describe_fn_replaces_images(self):
        conv = self._make_conversation_with_image()

        def mock_describe(data_url):
            assert data_url.startswith("data:image/png;base64,")
            return f"Description of image ({data_url})"

        msgs = conv.get_messages_for_api(api_style="openai", describe_fn=mock_describe)
        content = msgs[0]["content"]
        types = [b["type"] for b in content]
        assert "image_url" not in types
        assert "text" in types
        desc_blocks = [b for b in content if "Description of image" in b.get("text", "")]
        assert len(desc_blocks) == 1
        assert "data:image/png;base64,abc123" in desc_blocks[0]["text"]

    def test_describe_fn_error_produces_error_text(self):
        conv = self._make_conversation_with_image()

        def failing_describe(b64_data):
            raise RuntimeError("MCP server crashed")

        msgs = conv.get_messages_for_api(api_style="openai", describe_fn=failing_describe)
        content = msgs[0]["content"]
        types = [b["type"] for b in content]
        assert "image_url" not in types
        desc_blocks = [b for b in content if "description unavailable" in b.get("text", "")]
        assert len(desc_blocks) == 1
        assert "MCP server crashed" in desc_blocks[0]["text"]

    def test_describe_fn_with_anthropic_format(self):
        conv = self._make_conversation_with_image()

        def mock_describe(b64_data):
            return "A red square"

        msgs = conv.get_messages_for_api(api_style="anthropic", describe_fn=mock_describe)
        content = msgs[0]["content"]
        types = [b["type"] for b in content]
        assert "image" not in types
        desc_blocks = [b for b in content if "A red square" in b.get("text", "")]
        assert len(desc_blocks) == 1

    def test_multiple_images_all_replaced(self):
        from freecad_ai.core.conversation import Conversation
        conv = Conversation()
        conv.add_user_message("Two images", images=[
            {"type": "image", "source": "base64", "media_type": "image/png", "data": "img1"},
            {"type": "image", "source": "base64", "media_type": "image/png", "data": "img2"},
        ])
        descriptions = []

        def mock_describe(data_url):
            desc = f"Described {data_url}"
            descriptions.append(desc)
            return desc

        msgs = conv.get_messages_for_api(api_style="openai", describe_fn=mock_describe)
        content = msgs[0]["content"]
        assert len(descriptions) == 2
        image_blocks = [b for b in content if b.get("type") == "image_url"]
        assert len(image_blocks) == 0

    def test_partial_failure_continues(self):
        from freecad_ai.core.conversation import Conversation
        conv = Conversation()
        conv.add_user_message("Two images", images=[
            {"type": "image", "source": "base64", "media_type": "image/png", "data": "img1"},
            {"type": "image", "source": "base64", "media_type": "image/png", "data": "img2"},
        ])
        call_count = [0]

        def flaky_describe(data_url):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Server timeout")
            return "Second image described"

        msgs = conv.get_messages_for_api(api_style="openai", describe_fn=flaky_describe)
        content = msgs[0]["content"]
        texts = [b.get("text", "") for b in content if b.get("type") == "text"]
        full_text = " ".join(texts)
        assert "description unavailable" in full_text
        assert "Second image described" in full_text


class TestImageControlGating:
    """UI control gating logic (no actual Qt, just config logic)."""

    def test_gating_disabled_when_untested(self):
        """Controls should NOT be disabled when vision_detected is None."""
        cfg = AppConfig()
        # vision_detected=None, no fallback
        should_disable = (cfg.provider.vision_detected is not None
                          and not cfg.supports_vision
                          and True)  # simulate no fallback
        assert should_disable is False  # optimistic for untested

    def test_gating_disabled_when_no_vision_no_fallback(self):
        cfg = AppConfig()
        cfg.provider.vision_detected = False
        should_disable = (cfg.provider.vision_detected is not None
                          and not cfg.supports_vision
                          and True)  # no fallback
        assert should_disable is True

    def test_gating_enabled_when_fallback_exists(self):
        cfg = AppConfig()
        cfg.provider.vision_detected = False
        fallback = "server__describe_image"  # simulate fallback exists
        should_disable = (cfg.provider.vision_detected is not None
                          and not cfg.supports_vision
                          and fallback is None)
        assert should_disable is False

    def test_gating_enabled_when_vision_supported(self):
        cfg = AppConfig()
        cfg.provider.vision_detected = True
        should_disable = (cfg.provider.vision_detected is not None
                          and not cfg.supports_vision)
        assert should_disable is False

    def test_gating_enabled_with_override(self):
        cfg = AppConfig()
        cfg.provider.vision_detected = False
        cfg.provider.vision_override = True
        should_disable = (cfg.provider.vision_detected is not None
                          and not cfg.supports_vision
                          and True)
        assert should_disable is False  # override makes supports_vision True
