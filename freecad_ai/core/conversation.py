"""Conversation history manager.

Stores chat messages, persists them to disk, and handles context
window management by truncating old messages when needed.
"""

import json
import os
import time
from dataclasses import dataclass, field

from ..config import CONVERSATIONS_DIR


@dataclass
class Conversation:
    """Manages a single conversation's message history."""

    messages: list[dict] = field(default_factory=list)
    conversation_id: str = ""
    created_at: float = 0.0
    model: str = ""

    def __post_init__(self):
        if not self.conversation_id:
            self.conversation_id = f"conv_{int(time.time() * 1000)}"
        if not self.created_at:
            self.created_at = time.time()

    def add_user_message(self, content: str):
        """Add a user message."""
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str):
        """Add an assistant message."""
        self.messages.append({"role": "assistant", "content": content})

    def add_system_message(self, content: str):
        """Add a system-level message (execution results, errors, etc.)."""
        # System messages are stored as user messages with a prefix,
        # since not all LLM APIs support arbitrary system messages mid-conversation
        self.messages.append({
            "role": "user",
            "content": f"[System] {content}",
        })

    def get_messages_for_api(self, max_chars: int = 100000) -> list[dict]:
        """Get messages formatted for the LLM API.

        Truncates older messages if the total content exceeds max_chars
        (rough token estimate: chars / 4).
        """
        # Always keep at least the last message
        if not self.messages:
            return []

        # Walk backwards, accumulating messages until we hit the limit
        result = []
        total_chars = 0
        for msg in reversed(self.messages):
            msg_chars = len(msg["content"])
            if total_chars + msg_chars > max_chars and result:
                break
            result.append(msg)
            total_chars += msg_chars

        result.reverse()

        # Ensure the first message is a user message (API requirement for most providers)
        while result and result[0]["role"] == "assistant":
            result.pop(0)

        return result

    def clear(self):
        """Clear all messages."""
        self.messages.clear()

    def estimated_tokens(self) -> int:
        """Rough token estimate (chars / 4)."""
        total_chars = sum(len(m["content"]) for m in self.messages)
        return total_chars // 4

    # ── Persistence ──────────────────────────────────────────

    def save(self):
        """Save conversation to disk."""
        os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
        path = os.path.join(CONVERSATIONS_DIR, f"{self.conversation_id}.json")
        data = {
            "conversation_id": self.conversation_id,
            "created_at": self.created_at,
            "model": self.model,
            "messages": self.messages,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, conversation_id: str) -> "Conversation":
        """Load a conversation from disk."""
        path = os.path.join(CONVERSATIONS_DIR, f"{conversation_id}.json")
        with open(path, "r") as f:
            data = json.load(f)
        return cls(
            messages=data.get("messages", []),
            conversation_id=data.get("conversation_id", conversation_id),
            created_at=data.get("created_at", 0),
            model=data.get("model", ""),
        )

    @staticmethod
    def list_saved() -> list[str]:
        """List saved conversation IDs, most recent first."""
        if not os.path.exists(CONVERSATIONS_DIR):
            return []
        files = [f for f in os.listdir(CONVERSATIONS_DIR) if f.endswith(".json")]
        # Sort by modification time, newest first
        files.sort(key=lambda f: os.path.getmtime(
            os.path.join(CONVERSATIONS_DIR, f)), reverse=True)
        return [f.replace(".json", "") for f in files]
