"""Tests for conversation history management."""

import json
import os

import pytest

from freecad_ai.core.conversation import Conversation


class TestConversationInit:
    def test_auto_generates_id(self):
        c = Conversation()
        assert c.conversation_id.startswith("conv_")

    def test_auto_generates_timestamp(self):
        c = Conversation()
        assert c.created_at > 0

    def test_custom_id_preserved(self):
        c = Conversation(conversation_id="my-id")
        assert c.conversation_id == "my-id"

    def test_starts_with_empty_messages(self):
        c = Conversation()
        assert c.messages == []


class TestAddMessages:
    def test_add_user_message(self):
        c = Conversation()
        c.add_user_message("Hello")
        assert len(c.messages) == 1
        assert c.messages[0]["role"] == "user"
        assert c.messages[0]["content"] == "Hello"

    def test_add_assistant_message(self):
        c = Conversation()
        c.add_assistant_message("Hi there")
        assert c.messages[0]["role"] == "assistant"
        assert c.messages[0]["content"] == "Hi there"

    def test_add_assistant_with_tool_calls(self):
        c = Conversation()
        tc = [{"id": "tc1", "name": "create_body", "arguments": {"label": "Box"}}]
        c.add_assistant_message("Creating...", tool_calls=tc)
        msg = c.messages[0]
        assert msg["tool_calls"] == tc

    def test_add_assistant_without_tool_calls(self):
        c = Conversation()
        c.add_assistant_message("Just text")
        assert "tool_calls" not in c.messages[0]

    def test_add_tool_result(self):
        c = Conversation()
        c.add_tool_result("tc1", "Body created")
        msg = c.messages[0]
        assert msg["role"] == "tool_result"
        assert msg["tool_call_id"] == "tc1"
        assert msg["content"] == "Body created"

    def test_add_system_message(self):
        c = Conversation()
        c.add_system_message("Code executed")
        msg = c.messages[0]
        assert msg["role"] == "user"
        assert msg["content"].startswith("[System]")

    def test_message_ordering(self):
        c = Conversation()
        c.add_user_message("Question")
        c.add_assistant_message("Answer")
        c.add_user_message("Follow-up")
        assert [m["role"] for m in c.messages] == ["user", "assistant", "user"]


class TestOpenAIFormat:
    def test_basic_messages(self):
        c = Conversation()
        c.add_user_message("Hi")
        c.add_assistant_message("Hello")
        msgs = c.get_messages_for_api(api_style="openai")
        assert msgs[0] == {"role": "user", "content": "Hi"}
        assert msgs[1] == {"role": "assistant", "content": "Hello"}

    def test_tool_result_becomes_tool_role(self):
        c = Conversation()
        c.add_user_message("Create a box")
        tc = [{"id": "tc1", "name": "create_primitive", "arguments": {"shape_type": "box"}}]
        c.add_assistant_message("", tool_calls=tc)
        c.add_tool_result("tc1", "Created box")
        msgs = c.get_messages_for_api(api_style="openai")
        assert msgs[2]["role"] == "tool"
        assert msgs[2]["tool_call_id"] == "tc1"

    def test_tool_calls_format(self):
        c = Conversation()
        c.add_user_message("Go")
        tc = [{"id": "tc1", "name": "test", "arguments": {"x": 1}}]
        c.add_assistant_message("", tool_calls=tc)
        msgs = c.get_messages_for_api(api_style="openai")
        oai_tc = msgs[1]["tool_calls"][0]
        assert oai_tc["type"] == "function"
        assert oai_tc["function"]["name"] == "test"
        assert json.loads(oai_tc["function"]["arguments"]) == {"x": 1}


class TestAnthropicFormat:
    def test_basic_messages(self):
        c = Conversation()
        c.add_user_message("Hi")
        c.add_assistant_message("Hello")
        msgs = c.get_messages_for_api(api_style="anthropic")
        assert msgs[0] == {"role": "user", "content": "Hi"}

    def test_tool_result_becomes_user_with_content_block(self):
        c = Conversation()
        c.add_user_message("Create a box")
        tc = [{"id": "tc1", "name": "test", "arguments": {}}]
        c.add_assistant_message("", tool_calls=tc)
        c.add_tool_result("tc1", "Done")
        msgs = c.get_messages_for_api(api_style="anthropic")
        tool_msg = msgs[2]
        assert tool_msg["role"] == "user"
        assert tool_msg["content"][0]["type"] == "tool_result"
        assert tool_msg["content"][0]["tool_use_id"] == "tc1"

    def test_tool_calls_as_content_blocks(self):
        c = Conversation()
        c.add_user_message("Go")
        tc = [{"id": "tc1", "name": "test", "arguments": {"x": 1}}]
        c.add_assistant_message("Thinking...", tool_calls=tc)
        msgs = c.get_messages_for_api(api_style="anthropic")
        assistant = msgs[1]
        assert assistant["role"] == "assistant"
        blocks = assistant["content"]
        assert blocks[0]["type"] == "text"
        assert blocks[0]["text"] == "Thinking..."
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["name"] == "test"


class TestTruncation:
    def test_no_truncation_within_limit(self):
        c = Conversation()
        c.add_user_message("Short message")
        c.add_assistant_message("Short reply")
        msgs = c.get_messages_for_api(max_chars=10000)
        assert len(msgs) == 2

    def test_truncation_drops_oldest(self):
        c = Conversation()
        for i in range(20):
            c.add_user_message(f"Message {i} " + "x" * 100)
            c.add_assistant_message(f"Reply {i} " + "y" * 100)
        msgs = c.get_messages_for_api(max_chars=500)
        assert len(msgs) < 40

    def test_truncation_preserves_tool_pairs(self):
        c = Conversation()
        c.add_user_message("Start")
        # Add a tool call pair
        tc = [{"id": "tc1", "name": "test", "arguments": {}}]
        c.add_assistant_message("Calling tool", tool_calls=tc)
        c.add_tool_result("tc1", "Done")
        # Add more messages to force truncation
        for i in range(10):
            c.add_user_message("x" * 200)
            c.add_assistant_message("y" * 200)
        msgs = c.get_messages_for_api(max_chars=500)
        # Check: no tool_result without preceding assistant+tool_calls
        for i, m in enumerate(msgs):
            if isinstance(m.get("content"), str) and m.get("role") == "tool":
                # Must have preceding assistant with tool_calls
                assert i > 0

    def test_first_message_is_user(self):
        c = Conversation()
        tc = [{"id": "tc1", "name": "test", "arguments": {}}]
        c.add_assistant_message("Orphaned", tool_calls=tc)
        c.add_tool_result("tc1", "Result")
        c.add_user_message("User message")
        msgs = c.get_messages_for_api()
        if msgs:
            assert msgs[0]["role"] == "user"

    def test_empty_conversation(self):
        c = Conversation()
        assert c.get_messages_for_api() == []


class TestEstimatedTokens:
    def test_empty_conversation(self):
        c = Conversation()
        assert c.estimated_tokens() == 0

    def test_includes_content_chars(self):
        c = Conversation()
        c.add_user_message("a" * 400)  # 400 chars = ~100 tokens
        tokens = c.estimated_tokens()
        assert tokens == 100

    def test_includes_tool_call_args(self):
        c = Conversation()
        tc = [{"id": "tc1", "name": "test", "arguments": {"key": "value" * 10}}]
        c.add_assistant_message("", tool_calls=tc)
        tokens = c.estimated_tokens()
        assert tokens > 0


class TestCompaction:
    def test_compact_replaces_old_messages(self):
        c = Conversation()
        for i in range(10):
            c.add_user_message(f"Message {i}")
            c.add_assistant_message(f"Reply {i}")
        original_count = len(c.messages)
        c.compact("Summary of earlier conversation")
        assert len(c.messages) < original_count
        assert "[Context Summary" in c.messages[0]["content"]

    def test_compact_preserves_recent(self):
        c = Conversation()
        for i in range(10):
            c.add_user_message(f"Message {i}")
            c.add_assistant_message(f"Reply {i}")
        c.compact("Summary", keep_recent=4)
        # Last 4 messages should be preserved
        assert any("Message 9" in m["content"] for m in c.messages if m["role"] == "user")

    def test_compact_noop_when_too_few_messages(self):
        c = Conversation()
        c.add_user_message("Only one")
        c.compact("Summary")
        assert len(c.messages) == 1
        assert c.messages[0]["content"] == "Only one"

    def test_compact_doesnt_split_tool_pairs(self):
        c = Conversation()
        for i in range(5):
            c.add_user_message(f"Msg {i}")
            tc = [{"id": f"tc{i}", "name": "test", "arguments": {}}]
            c.add_assistant_message(f"Call {i}", tool_calls=tc)
            c.add_tool_result(f"tc{i}", f"Result {i}")
        c.compact("Summary", keep_recent=4)
        # After compact, no orphaned tool_results
        for i, m in enumerate(c.messages):
            if m["role"] == "tool_result" and i > 0:
                prev = c.messages[i - 1]
                # Previous should be assistant or another tool_result
                assert prev["role"] in ("assistant", "tool_result")

    def test_needs_compaction(self):
        c = Conversation()
        assert c.needs_compaction() is False
        # Add enough messages to exceed threshold
        for i in range(50):
            c.add_user_message("x" * 2000)
            c.add_assistant_message("y" * 2000)
        assert c.needs_compaction() is True

    def test_needs_compaction_requires_enough_messages(self):
        c = Conversation()
        # Few long messages shouldn't trigger
        c.add_user_message("x" * 100000)
        assert c.needs_compaction() is False  # only 1 message


class TestClear:
    def test_clear_removes_all(self):
        c = Conversation()
        c.add_user_message("Test")
        c.add_assistant_message("Reply")
        c.clear()
        assert c.messages == []


class TestPersistence:
    def test_save_and_load(self, tmp_config_dir, monkeypatch):
        import freecad_ai.core.conversation as conv_mod
        conv_dir = os.path.join(str(tmp_config_dir), "conversations")
        monkeypatch.setattr(conv_mod, "CONVERSATIONS_DIR", conv_dir)

        c = Conversation(conversation_id="test-conv-1", model="test-model")
        c.add_user_message("Hello")
        c.add_assistant_message("Hi")
        c.save()

        loaded = Conversation.load("test-conv-1")
        assert loaded.conversation_id == "test-conv-1"
        assert loaded.model == "test-model"
        assert len(loaded.messages) == 2

    def test_list_saved(self, tmp_config_dir, monkeypatch):
        import freecad_ai.core.conversation as conv_mod
        conv_dir = os.path.join(str(tmp_config_dir), "conversations")
        monkeypatch.setattr(conv_mod, "CONVERSATIONS_DIR", conv_dir)

        c1 = Conversation(conversation_id="conv-a")
        c1.add_user_message("A")
        c1.save()

        c2 = Conversation(conversation_id="conv-b")
        c2.add_user_message("B")
        c2.save()

        saved = Conversation.list_saved()
        assert "conv-a" in saved
        assert "conv-b" in saved

    def test_list_saved_empty_dir(self, tmp_config_dir, monkeypatch):
        import freecad_ai.core.conversation as conv_mod
        monkeypatch.setattr(conv_mod, "CONVERSATIONS_DIR", "/nonexistent/path")
        assert Conversation.list_saved() == []


class TestCompactionEnabled:
    def test_compaction_enabled_default_true(self):
        from freecad_ai.core.conversation import Conversation
        conv = Conversation()
        assert conv.compaction_enabled is True

    def test_compaction_disabled_prevents_needs_compaction(self):
        from freecad_ai.core.conversation import Conversation
        conv = Conversation()
        for i in range(20):
            conv.add_user_message("x" * 5000)
            conv.add_assistant_message("y" * 5000)
        assert conv.needs_compaction() is True
        conv.compaction_enabled = False
        assert conv.needs_compaction() is False

    def test_compaction_reenabled(self):
        from freecad_ai.core.conversation import Conversation
        conv = Conversation()
        conv.compaction_enabled = False
        for i in range(20):
            conv.add_user_message("x" * 5000)
            conv.add_assistant_message("y" * 5000)
        assert conv.needs_compaction() is False
        conv.compaction_enabled = True
        assert conv.needs_compaction() is True
