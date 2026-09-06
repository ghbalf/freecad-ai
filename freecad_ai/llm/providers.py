"""LLM provider registry.

Each provider defines its base URL, default model, API style
(either 'anthropic' for Anthropic's native API, or 'openai' for
OpenAI-compatible /chat/completions endpoints), and whether it
supports native tool calling.
"""

PROVIDERS = {
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-6",
        "api_style": "anthropic",
        "supports_tools": True,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "api_style": "openai",
        "supports_tools": True,
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3",
        "api_style": "openai",
        "supports_tools": True,
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.0-flash",
        "api_style": "openai",
        "supports_tools": True,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "anthropic/claude-sonnet-4-6",
        "api_style": "openai",
        "supports_tools": True,
    },
    "cloudflare-workers-ai": {
        # Cloudflare Workers AI exposes an OpenAI-compatible chat-completions
        # endpoint per account. Users must replace {ACCOUNT_ID} with their
        # Cloudflare account ID and supply a Workers AI API token as ApiKey.
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1",
        "default_model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "api_style": "openai",
        "supports_tools": True,
    },
    "moonshot": {
        "base_url": "https://api.moonshot.ai/v1",
        "default_model": "kimi-k2.5",
        "api_style": "openai",
        "supports_tools": True,
        "default_params": {
            "temperature": 0.6,
            "top_p": 0.95,
            "n": 1,
            "presence_penalty": 0.0,
            "frequency_penalty": 0.0,
        },
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "api_style": "openai",
        "supports_tools": True,
    },
    "qwen": {
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "api_style": "openai",
        "supports_tools": True,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "api_style": "openai",
        "supports_tools": True,
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-large-latest",
        "api_style": "openai",
        "supports_tools": True,
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "api_style": "openai",
        "supports_tools": True,
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "default_model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "api_style": "openai",
        "supports_tools": True,
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-3",
        "api_style": "openai",
        "supports_tools": True,
    },
    "cohere": {
        "base_url": "https://api.cohere.ai/compatibility/v1",
        "default_model": "command-a-03-2025",
        "api_style": "openai",
        "supports_tools": True,
    },
    "sambanova": {
        "base_url": "https://api.sambanova.ai/v1",
        "default_model": "Meta-Llama-3.3-70B-Instruct",
        "api_style": "openai",
        "supports_tools": True,
    },
    "minimax": {
        "base_url": "https://api.minimax.io/v1",
        "default_model": "MiniMax-M1",
        "api_style": "openai",
        "supports_tools": True,
    },
    "llama": {
        "base_url": "https://api.llama.com/v1",
        "default_model": "Llama-4-Maverick-17B-128E-Instruct",
        "api_style": "openai",
        "supports_tools": True,
    },
    "github": {
        "base_url": "https://models.inference.ai.azure.com",
        "default_model": "gpt-4o",
        "api_style": "openai",
        "supports_tools": True,
        # GitHub Models (the marketplace API at models.inference.ai.azure.com,
        # distinct from a Copilot subscription) enforces a small per-request
        # input cap independent of the underlying model's native context
        # window. With ~50 built-in tool schemas attached, a fresh Act-mode
        # turn overshoots the cap before the user types anything. The keyword
        # reranker @ top_n≈8 keeps the request under the cap. See issue #10.
        # The Settings dialog applies this on provider switch only when the
        # reranker is still at its factory default (off, top_n=15) so an
        # explicit user choice is never silently overwritten.
        "default_rerank": {"method": "keyword", "top_n": 8},
    },
    "huggingface": {
        "base_url": "https://api-inference.huggingface.co/v1",
        "default_model": "Qwen/Qwen2.5-72B-Instruct",
        "api_style": "openai",
        "supports_tools": True,
    },
    "zhipu": {
        "base_url": "https://open.z.ai/api/paas/v4",
        "default_model": "glm-5",
        "api_style": "openai",
        "supports_tools": True,
    },
    "custom": {
        "base_url": "",
        "default_model": "",
        "api_style": "openai",
        # Custom endpoints are OpenAI-compatible and there is no per-model
        # tool probe for them (the /api/show capability check is Ollama-only),
        # so this static flag is the sole source of truth. Default True: most
        # custom servers (vLLM, llama.cpp --jinja, SGLang) support tool calling,
        # and a False default silently strips the tools array from every
        # request (issue #38). A genuinely tool-less endpoint opts out via
        # tools_detected=false in config.json.
        "supports_tools": True,
    },
}


def get_api_style(provider_name: str) -> str:
    """Return the API style for a provider ('anthropic' or 'openai')."""
    return PROVIDERS.get(provider_name, {}).get("api_style", "openai")


def supports_tools(provider_name: str) -> bool:
    """Return whether a provider supports native tool calling."""
    return PROVIDERS.get(provider_name, {}).get("supports_tools", False)


def get_default_params(provider_name: str) -> dict:
    """Return the default model parameters for a provider (empty if none)."""
    return dict(PROVIDERS.get(provider_name, {}).get("default_params", {}))


def get_provider_names() -> list[str]:
    """Return list of available provider names."""
    return list(PROVIDERS.keys())
