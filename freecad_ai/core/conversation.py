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
import secrets
import time
from dataclasses import dataclass, field

from ..config import CONVERSATIONS_DIR, get_config, prune_oldest_files
from .references import format_reference_block


@dataclass
class Conversation:
    """Manages a single conversation's message history."""

    messages: list[dict] = field(default_factory=list)
    conversation_id: str = ""
    created_at: float = 0.0
    model: str = ""

    def __post_init__(self):
        if not self.conversation_id:
            # The millisecond alone collided: it names the save file, and
            # since #47 it is also the provider's cache-routing key, where
            # two conversations sharing one would ask to be scheduled onto
            # the same prefix cache. Timestamp first so the directory still
            # sorts chronologically.
            self.conversation_id = "conv_{}_{}".format(
                int(time.time() * 1000), secrets.token_hex(3))
        if not self.created_at:
            self.created_at = time.time()
        self.compaction_enabled = True

    def add_user_message(self, content: str, images: list[dict] | None = None,
                         documents: list[dict] | None = None,
                         references: list[dict] | None = None):
        """Add a user message, optionally with images and/or documents.

        Args:
            content: The text content of the message.
            images: Optional list of image dicts, each with keys:
                    type, source, media_type, data (base64).
            documents: Optional list of document dicts, each with keys:
                       filename, text.
            references: Optional list of selection-reference dicts, each with
                        keys: name, label, sub, text.
        """
        if images or documents or references:
            blocks = [{"type": "text", "text": content}]
            # Selection references annotate the typed text, so they come
            # right after it, ahead of any file documents.
            for ref in (references or []):
                blocks.append({
                    "type": "text",
                    "text": format_reference_block(ref),
                })
            # Append document content as labeled text blocks
            for doc in (documents or []):
                blocks.append({
                    "type": "text",
                    "text": f"--- Attached file: {doc['filename']} ---\n{doc['text']}",
                })
            blocks.extend(images or [])
            self.messages.append({"role": "user", "content": blocks})
        else:
            self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str, tool_calls: list[dict] | None = None,
                              reasoning_content: str | None = None):
        """Add an assistant message, optionally with tool calls.

        ``reasoning_content`` is the thinking the agentic loop already
        echoed back to the provider for this turn. Storing it is what lets
        the next request re-render the turn as the bytes it was sent with;
        without it the prefix diverges here and the cache match stops at
        the static head (#47). ``_to_openai_format`` has always been able
        to emit it -- nothing ever wrote it.

        Empty means nothing was sent (a silent turn, a model that rejects
        thinking in history, or caching mode off), and then no key is
        added at all, so the message is byte-identical to the old one.
        """
        msg = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        self.messages.append(msg)

    def add_tool_result(self, tool_call_id: str, content: str):
        """Add a tool result message."""
        self.messages.append({
            "role": "tool_result",
            "tool_call_id": tool_call_id,
            "content": content,
        })

    def add_system_message(self, content: str, images: list[dict] | None = None):
        """Add a system-level message (execution results, errors, etc.).

        Optionally attach images (e.g. a viewport capture) so vision-capable
        LLMs can see the state the system message is describing.
        """
        # System messages are stored as user messages with a prefix,
        # since not all LLM APIs support arbitrary system messages mid-conversation
        prefixed = f"[System] {content}"
        if images:
            blocks = [{"type": "text", "text": prefixed}]
            blocks.extend(images)
            self.messages.append({"role": "user", "content": blocks})
        else:
            self.messages.append({"role": "user", "content": prefixed})

    def attach_document_context(self, block: str):
        """Record ``block`` as the document snapshot for the latest turn.

        Stored beside the content rather than merged into it, for two
        reasons. The chat pane and the session file render ``content``, so
        the user's own words stay their own; and the snapshot is pinned to
        the turn it was taken for, which is what makes re-rendering
        deterministic.

        That determinism is the whole point (#47). A prefix cache compares
        from token zero and stops at the first differing byte, so a message
        that has been sent once must render identically forever. The first
        attempt at this appended the live block to whichever user message
        happened to be last, which rewrote earlier turns on every send and
        threw the cache away at ``messages[0]``.

        Re-attaching replaces, so a retry of the same turn does not stack
        two snapshots. An empty block records nothing, and a conversation
        with no user turn is left alone.
        """
        if not block:
            return
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                msg["doc_context"] = block
                return

    def clear_document_context(self):
        """Forget every recorded snapshot.

        Called on each send while ``optimize_prompt_caching`` is off, so
        turning the switch back off genuinely restores the old behaviour
        rather than leaving the snapshots from when it was on. The warning
        on that switch tells people to flip it back if replies get worse,
        which only works if flipping it back undoes everything.
        """
        for msg in self.messages:
            msg.pop("doc_context", None)

    @staticmethod
    def _expand_document_context(messages: list) -> list:
        """Fold each recorded snapshot back onto the end of its message."""
        out = []
        for msg in messages:
            block = msg.get("doc_context")
            if not block:
                out.append(msg)
                continue
            copy = dict(msg)
            copy.pop("doc_context", None)
            content = copy.get("content")
            if isinstance(content, list):
                copy["content"] = content + [{"type": "text", "text": block}]
            else:
                copy["content"] = "{}\n\n{}".format(content, block)
            out.append(copy)
        return out

    def window_start(self, max_chars: int = 100000) -> int:
        """Index of the oldest message the max_chars window keeps.

        Walks backwards from the newest message and never splits a
        tool_call/tool_result pair.
        """
        total_chars = 0
        start = len(self.messages)
        i = len(self.messages) - 1
        while i >= 0:
            msg = self.messages[i]
            if msg["role"] == "tool_result":
                j = i - 1
                while j >= 0 and self.messages[j]["role"] == "tool_result":
                    j -= 1
                if j >= 0 and self.messages[j]["role"] == "assistant":
                    j -= 1
                group_chars = sum(self._content_chars(m.get("content", ""))
                                  for m in self.messages[j + 1:i + 1])
                if total_chars + group_chars > max_chars and start < len(self.messages):
                    break
                total_chars += group_chars
                start = j + 1
                i = j
                continue
            # The snapshot is billed like any other text, so it counts
            # against the budget even though it lives beside the content.
            msg_chars = (self._content_chars(msg.get("content", ""))
                         + len(msg.get("doc_context", "")))
            if total_chars + msg_chars > max_chars and start < len(self.messages):
                break
            total_chars += msg_chars
            start = i
            i -= 1
        return start

    def get_messages_for_api(self, max_chars: int = 100000,
                             api_style: str = "openai",
                             describe_fn=None,
                             strip_images: bool = False,
                             strip_thinking: bool = False,
                             start_index: int | None = None) -> list[dict]:
        """Get messages formatted for the LLM API.

        Truncates older messages if the total content exceeds max_chars.
        Converts from internal format to provider-specific format.
        Never splits a tool_call/tool_result pair during truncation.

        Args:
            describe_fn: If given, image blocks in history are replaced with
                text descriptions produced by this callable (vision fallback).
            strip_images: If True (and no describe_fn), image blocks in history
                are replaced with a text placeholder. Use for non-vision models
                so a stale image from earlier in the conversation isn't sent to
                a model that would reject it (issue #30).
            strip_thinking: If True, remove reasoning_content from assistant
                messages in the history.  Required by models like Gemma that
                reject thinking content in multi-turn conversations.
            start_index: The first message to keep, from window_start().
                A turn computes it once so later rounds of the tool loop
                don't drop older messages from under the prompt cache
                (#104, #47). None = compute it now from max_chars.
        """
        if not self.messages:
            return []

        if start_index is None:
            start_index = self.window_start(max_chars)
        result = list(self.messages[start_index:])

        # Ensure the first message is a user message (API requirement)
        while result and result[0]["role"] not in ("user",):
            result.pop(0)

        # Re-attach the document snapshot each turn was sent with, so a
        # message renders the same bytes every time it is sent (#47).
        result = self._expand_document_context(result)

        # Replace image blocks with text descriptions if describe_fn is
        # provided, else drop them to a placeholder when strip_images is set.
        if describe_fn:
            result = self._replace_images_with_descriptions(result, describe_fn)
        elif strip_images:
            result = self._strip_images(result)

        # Convert to provider format
        if api_style == "anthropic":
            return self._to_anthropic_format(result)
        else:
            return self._to_openai_format(result, strip_thinking=strip_thinking)

    def fork_for_turn(self) -> "Conversation":
        """A working copy for one turn's tool rounds (#104).

        The worker records each round here and renders every request from
        it; the real conversation is written once, at the end of the turn,
        by the widget. Message dicts are shared, never mutated.
        """
        return Conversation(messages=list(self.messages),
                            conversation_id=self.conversation_id,
                            created_at=self.created_at, model=self.model)

    def has_images(self, start_index: int = 0) -> bool:
        """Whether any message from start_index on carries an image block."""
        return any(
            isinstance(msg.get("content"), list)
            and any(b.get("type") == "image" for b in msg["content"])
            for msg in self.messages[start_index:])

    def _to_openai_format(self, messages: list[dict],
                          strip_thinking: bool = False) -> list[dict]:
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
                if msg.get("reasoning_content") and not strip_thinking:
                    oai_msg["reasoning_content"] = msg["reasoning_content"]
                result.append(oai_msg)
            elif isinstance(msg.get("content"), list):
                # Content blocks (text + images)
                oai_blocks = []
                for block in msg["content"]:
                    if block.get("type") == "text":
                        oai_blocks.append({"type": "text", "text": block["text"]})
                    elif block.get("type") == "image":
                        data_uri = f"data:{block['media_type']};base64,{block['data']}"
                        oai_blocks.append({
                            "type": "image_url",
                            "image_url": {"url": data_uri},
                        })
                result.append({"role": msg["role"], "content": oai_blocks})
            else:
                out = {"role": msg["role"], "content": msg["content"]}
                if msg.get("reasoning_content") and not strip_thinking:
                    out["reasoning_content"] = msg["reasoning_content"]
                result.append(out)
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
            elif isinstance(msg.get("content"), list):
                # Content blocks (text + images)
                anth_blocks = []
                for block in msg["content"]:
                    if block.get("type") == "text":
                        anth_blocks.append({"type": "text", "text": block["text"]})
                    elif block.get("type") == "image":
                        anth_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block["media_type"],
                                "data": block["data"],
                            },
                        })
                result.append({"role": msg["role"], "content": anth_blocks})
            else:
                result.append({"role": msg["role"], "content": msg["content"]})
        return result

    @staticmethod
    def _replace_images_with_descriptions(messages: list[dict],
                                          describe_fn) -> list[dict]:
        """Replace image content blocks with text descriptions from describe_fn.

        Images are processed serially. On failure, an error text block is
        substituted so remaining images can still be processed.
        """
        result = []
        for msg in messages:
            if not isinstance(msg.get("content"), list):
                result.append(msg)
                continue
            new_blocks = []
            for block in msg["content"]:
                if block.get("type") == "image":
                    b64_data = block.get("data", "")
                    mime = block.get("mime_type", "image/png")
                    data_url = f"data:{mime};base64,{b64_data}"
                    try:
                        description = describe_fn(data_url)
                        new_blocks.append({
                            "type": "text",
                            "text": f"[Image described by llm-vision-mcp: {description}]",
                        })
                    except Exception as e:
                        new_blocks.append({
                            "type": "text",
                            "text": f"[Image: description unavailable — MCP error: {e}]",
                        })
                else:
                    new_blocks.append(block)
            result.append({**msg, "content": new_blocks})
        return result

    @staticmethod
    def _strip_images(messages: list[dict]) -> list[dict]:
        """Replace image content blocks with a placeholder text block.

        For models without vision support and no describe_image fallback, so
        history images aren't sent raw to a provider that would reject them.
        """
        result = []
        for msg in messages:
            if not isinstance(msg.get("content"), list):
                result.append(msg)
                continue
            new_blocks = []
            for block in msg["content"]:
                if block.get("type") == "image":
                    new_blocks.append({
                        "type": "text",
                        "text": "[Image omitted — the current model has no vision support]",
                    })
                else:
                    new_blocks.append(block)
            result.append({**msg, "content": new_blocks})
        return result

    @staticmethod
    def _content_chars(content) -> int:
        """Return character count for content (str or list of blocks)."""
        if isinstance(content, list):
            total = 0
            for block in content:
                if block.get("type") == "text":
                    total += len(block.get("text", ""))
                elif block.get("type") == "image":
                    total += 1000
            return total
        return len(content) if content else 0

    @staticmethod
    def extract_text(content) -> str:
        """Extract plain text from content (str or list of blocks)."""
        if isinstance(content, list):
            parts = []
            for block in content:
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "\n".join(parts)
        return content or ""

    def clear(self):
        """Clear all messages."""
        self.messages.clear()

    def estimated_tokens(self) -> int:
        """Rough token estimate (chars / 4)."""
        total_chars = 0
        for m in self.messages:
            content = m.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        total_chars += len(block.get("text", ""))
                    elif block.get("type") == "image":
                        total_chars += 1000  # rough estimate for image tokens
            else:
                total_chars += len(content)
            # Also count tool call arguments
            for tc in m.get("tool_calls", []):
                total_chars += len(str(tc.get("arguments", {})))
        return total_chars // 4

    def needs_compaction(self, threshold_tokens: int = 20000) -> bool:
        """Check if conversation is long enough to benefit from compaction."""
        if not self.compaction_enabled:
            return False
        return self.estimated_tokens() > threshold_tokens and len(self.messages) > 6

    def compact(self, summary: str, keep_recent: int = 4):
        """Replace older messages with a summary, keeping the most recent messages.

        Args:
            summary: Text summarizing the older messages
            keep_recent: Number of recent messages to preserve (at minimum)
        """
        if len(self.messages) <= keep_recent + 1:
            return  # Not enough messages to compact

        # Find a safe split point: never split in the middle of a tool_call/tool_result pair
        split_idx = len(self.messages) - keep_recent

        # Walk backward from split_idx to ensure we don't break a tool pair
        while split_idx > 0 and self.messages[split_idx]["role"] == "tool_result":
            split_idx -= 1
        # Also don't split after an assistant message with tool_calls
        if split_idx > 0 and self.messages[split_idx - 1]["role"] == "assistant":
            if self.messages[split_idx - 1].get("tool_calls"):
                split_idx -= 1

        if split_idx <= 1:
            return  # Would compact everything, not useful

        # Replace old messages with summary
        summary_msg = {
            "role": "user",
            "content": (
                "[Context Summary — earlier conversation was compacted to save space]\n\n"
                + summary
            ),
        }
        self.messages = [summary_msg] + self.messages[split_idx:]

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
        cfg = get_config()
        prune_oldest_files(
            CONVERSATIONS_DIR,
            lambda n: n.endswith(".json"),
            cfg.max_saved_conversations,
            cfg.max_retention_age_days,
        )

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
