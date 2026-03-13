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
