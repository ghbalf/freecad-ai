"""API Route profiles use the shared OpenAI-compatible client."""

import pytest

from freecad_ai.config import _LEGACY_PARAM_PROVIDERS, AppConfig
from freecad_ai.llm.client import create_client
from freecad_ai.llm.providers import get_provider_names


def test_api_route_profile_survives_configuration_roundtrip():
    cfg = AppConfig()
    cfg.provider.apply_preset("api-route")
    cfg.provider_keys["api-route"] = "test-api-route-key"

    restored = AppConfig.from_dict(cfg.to_dict())
    client = create_client(restored)

    assert client.base_url == "https://global.api-route.com/v1"
    assert client.model == "gpt-6.1-sol"
    assert client.api_key == "test-api-route-key"
    assert client.api_style == "openai"
    assert restored.supports_tools is True
    assert "api-route" in get_provider_names()
    assert "api-route" not in _LEGACY_PARAM_PROVIDERS


@pytest.mark.parametrize("stream", [False, True])
def test_api_route_keeps_native_tools_without_vendor_cache_hint(stream):
    cfg = AppConfig()
    cfg.provider.apply_preset("api-route")
    cfg.optimize_prompt_caching = True
    client = create_client(cfg, cache_key="conversation-1")
    tools = [{"type": "function", "function": {"name": "get_time", "parameters": {"type": "object"}}}]

    body = client._openai_body([{"role": "user", "content": "What time is it?"}], "", stream, tools)

    assert body["model"] == "gpt-6.1-sol"
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"
    assert "prompt_cache_key" not in body


def test_api_route_profile_preserves_endpoint_model_and_key_overrides():
    cfg = AppConfig()
    cfg.provider.apply_preset("api-route")
    cfg.provider.base_url = "https://gateway.example/v1/"
    cfg.provider.model = "account-model"
    cfg.provider.api_key = "profile-key"
    cfg.provider_keys["api-route"] = "vendor-key"

    client = create_client(AppConfig.from_dict(cfg.to_dict()))

    assert client.base_url == "https://gateway.example/v1"
    assert client.model == "account-model"
    assert client._openai_headers()["Authorization"] == "Bearer profile-key"
    assert "HTTP-Referer" not in client._openai_headers()
    assert "X-Title" not in client._openai_headers()
