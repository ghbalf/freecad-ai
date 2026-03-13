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
