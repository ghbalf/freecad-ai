"""Configuration system for FreeCAD AI.

Stores settings as JSON at ~/.config/FreeCAD/FreeCADAI/config.json
"""

import json
import os
from dataclasses import dataclass, field, asdict


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "FreeCAD", "FreeCADAI")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
CONVERSATIONS_DIR = os.path.join(CONFIG_DIR, "conversations")
SKILLS_DIR = os.path.join(CONFIG_DIR, "skills")

# Provider presets
PROVIDER_PRESETS = {
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-20250514",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.0-flash",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "anthropic/claude-sonnet-4-20250514",
    },
    "custom": {
        "base_url": "",
        "default_model": "",
    },
}


@dataclass
class ProviderConfig:
    name: str = "anthropic"
    api_key: str = ""
    base_url: str = "https://api.anthropic.com"
    model: str = "claude-sonnet-4-20250514"

    def apply_preset(self, provider_name: str):
        """Apply a provider preset, updating base_url and model to defaults."""
        preset = PROVIDER_PRESETS.get(provider_name, {})
        self.name = provider_name
        self.base_url = preset.get("base_url", self.base_url)
        self.model = preset.get("default_model", self.model)


@dataclass
class AppConfig:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    mode: str = "plan"  # "plan" or "act"
    max_tokens: int = 4096
    temperature: float = 0.3
    auto_execute: bool = False
    max_retries: int = 3
    enable_tools: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        provider_data = data.pop("provider", {})
        provider = ProviderConfig(**provider_data)
        return cls(provider=provider, **data)


def _ensure_dirs():
    """Create config directories if they don't exist."""
    for d in (CONFIG_DIR, CONVERSATIONS_DIR, SKILLS_DIR):
        os.makedirs(d, exist_ok=True)


def load_config() -> AppConfig:
    """Load configuration from disk. Returns defaults if file doesn't exist."""
    _ensure_dirs()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            return AppConfig.from_dict(data)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    return AppConfig()


def save_config(config: AppConfig):
    """Save configuration to disk."""
    _ensure_dirs()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config.to_dict(), f, indent=2)


# Singleton config instance
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Get the current configuration (lazy-loaded singleton)."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def save_current_config():
    """Save the current singleton config to disk."""
    if _config is not None:
        save_config(_config)


def reload_config():
    """Force reload configuration from disk."""
    global _config
    _config = load_config()
