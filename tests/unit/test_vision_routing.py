"""Tests for vision routing."""

from freecad_ai.config import AppConfig


class TestVisionConfig:
    """Config fields and supports_vision property."""

    def test_defaults(self):
        cfg = AppConfig()
        assert cfg.vision_detected is None
        assert cfg.vision_override is None

    def test_supports_vision_override_takes_precedence(self):
        cfg = AppConfig()
        cfg.vision_detected = False
        cfg.vision_override = True
        assert cfg.supports_vision is True

    def test_supports_vision_detected_used_when_no_override(self):
        cfg = AppConfig()
        cfg.vision_detected = True
        assert cfg.supports_vision is True

    def test_supports_vision_false_when_detected_false(self):
        cfg = AppConfig()
        cfg.vision_detected = False
        assert cfg.supports_vision is False

    def test_supports_vision_false_when_untested(self):
        cfg = AppConfig()
        assert cfg.supports_vision is False

    def test_vision_fields_roundtrip_json(self):
        cfg = AppConfig()
        cfg.vision_detected = True
        cfg.vision_override = False
        d = cfg.to_dict()
        cfg2 = AppConfig.from_dict(d)
        assert cfg2.vision_detected is True
        assert cfg2.vision_override is False

    def test_vision_fields_none_roundtrip(self):
        cfg = AppConfig()
        d = cfg.to_dict()
        cfg2 = AppConfig.from_dict(d)
        assert cfg2.vision_detected is None
        assert cfg2.vision_override is None


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

        def mock_describe(b64_data):
            return f"Description of image ({len(b64_data)} bytes)"

        msgs = conv.get_messages_for_api(api_style="openai", describe_fn=mock_describe)
        content = msgs[0]["content"]
        types = [b["type"] for b in content]
        assert "image_url" not in types
        assert "text" in types
        desc_blocks = [b for b in content if "Description of image" in b.get("text", "")]
        assert len(desc_blocks) == 1
        assert "(6 bytes)" in desc_blocks[0]["text"]

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

        def mock_describe(b64_data):
            desc = f"Described {b64_data}"
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

        def flaky_describe(b64_data):
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
        should_disable = (cfg.vision_detected is not None
                          and not cfg.supports_vision
                          and True)  # simulate no fallback
        assert should_disable is False  # optimistic for untested

    def test_gating_disabled_when_no_vision_no_fallback(self):
        cfg = AppConfig()
        cfg.vision_detected = False
        should_disable = (cfg.vision_detected is not None
                          and not cfg.supports_vision
                          and True)  # no fallback
        assert should_disable is True

    def test_gating_enabled_when_fallback_exists(self):
        cfg = AppConfig()
        cfg.vision_detected = False
        fallback = "server__describe_image"  # simulate fallback exists
        should_disable = (cfg.vision_detected is not None
                          and not cfg.supports_vision
                          and fallback is None)
        assert should_disable is False

    def test_gating_enabled_when_vision_supported(self):
        cfg = AppConfig()
        cfg.vision_detected = True
        should_disable = (cfg.vision_detected is not None
                          and not cfg.supports_vision)
        assert should_disable is False

    def test_gating_enabled_with_override(self):
        cfg = AppConfig()
        cfg.vision_detected = False
        cfg.vision_override = True
        should_disable = (cfg.vision_detected is not None
                          and not cfg.supports_vision
                          and True)
        assert should_disable is False  # override makes supports_vision True
