"""Conversation history manager.

Stores chat messages, persists them to disk, and handles context
window management by truncating old messages when needed.

Message types in internal (provider-neutral) format:
  - User:       {"role": "user", "content": "..."}
  - Assistant:   {"role": "assistant", "content": "...", "tool_calls": [...]}
  - Tool result: {"role": "tool_result", "tool_call_id": "...", "content": "..."}
  - System:      {"role": "user", "content": "[System] ..."}

The get_messages_for_api() method converts to provider-specific format.
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

    def add_assistant_message(self, content: str, tool_calls: list[dict] | None = None):
        """Add an assistant message, optionally with tool calls."""
        msg = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)

    def add_tool_result(self, tool_call_id: str, content: str):
        """Add a tool result message."""
        self.messages.append({
            "role": "tool_result",
            "tool_call_id": tool_call_id,
            "content": content,
        })

    def add_system_message(self, content: str):
        """Add a system-level message (execution results, errors, etc.)."""
        # System messages are stored as user messages with a prefix,
        # since not all LLM APIs support arbitrary system messages mid-conversation
        self.messages.append({
            "role": "user",
            "content": f"[System] {content}",
        })

    def get_messages_for_api(self, max_chars: int = 100000,
                             api_style: str = "openai") -> list[dict]:
        """Get messages formatted for the LLM API.

        Truncates older messages if the total content exceeds max_chars.
        Converts from internal format to provider-specific format.
        Never splits a tool_call/tool_result pair during truncation.
        """
        if not self.messages:
            return []

        # Walk backwards, collecting messages while respecting max_chars
        # and never splitting tool_call/tool_result pairs
        result = []
        total_chars = 0

        i = len(self.messages) - 1
        while i >= 0:
            msg = self.messages[i]
            msg_chars = len(msg.get("content", ""))

            # If this is a tool_result, we must also include the preceding assistant
            # message that contains the tool_call. Walk back to find the pair.
            if msg["role"] == "tool_result":
                # Collect all consecutive tool_results
                tool_group = [msg]
                j = i - 1
                while j >= 0 and self.messages[j]["role"] == "tool_result":
                    tool_group.insert(0, self.messages[j])
                    j -= 1
                # The message before should be the assistant with tool_calls
                if j >= 0 and self.messages[j]["role"] == "assistant":
                    tool_group.insert(0, self.messages[j])
                    j -= 1

                group_chars = sum(len(m.get("content", "")) for m in tool_group)
                if total_chars + group_chars > max_chars and result:
                    break
                result = tool_group + result
                total_chars += group_chars
                i = j
                continue

            if total_chars + msg_chars > max_chars and result:
                break
            result.insert(0, msg)
            total_chars += msg_chars
            i -= 1

        # Ensure the first message is a user message (API requirement)
        while result and result[0]["role"] not in ("user",):
            result.pop(0)

        # Convert to provider format
        if api_style == "anthropic":
            return self._to_anthropic_format(result)
        else:
            return self._to_openai_format(result)

    def _to_openai_format(self, messages: list[dict]) -> list[dict]:
        """Convert internal messages to OpenAI API format."""
        result = []
        for msg in messages:
            if msg["role"] == "tool_result":
                result.append({
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "content": msg["content"],
                })
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                oai_msg = {
                    "role": "assistant",
                    "content": msg.get("content") or None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                        for tc in msg["tool_calls"]
                    ],
                }
                result.append(oai_msg)
            else:
                result.append({"role": msg["role"], "content": msg["content"]})
        return result

    def _to_anthropic_format(self, messages: list[dict]) -> list[dict]:
        """Convert internal messages to Anthropic API format."""
        result = []
        for msg in messages:
            if msg["role"] == "tool_result":
                result.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg["tool_call_id"],
                            "content": msg["content"],
                        }
                    ],
                })
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                content_blocks = []
                if msg.get("content"):
                    content_blocks.append({"type": "text", "text": msg["content"]})
                for tc in msg["tool_calls"]:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["arguments"],
                    })
                result.append({"role": "assistant", "content": content_blocks})
            else:
                result.append({"role": msg["role"], "content": msg["content"]})
        return result

    def clear(self):
        """Clear all messages."""
        self.messages.clear()

    def estimated_tokens(self) -> int:
        """Rough token estimate (chars / 4)."""
        total_chars = sum(len(m.get("content", "")) for m in self.messages)
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
