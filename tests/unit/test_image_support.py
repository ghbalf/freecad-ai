"""Tests for image support: content blocks, API format conversion, viewport utils."""

import base64

import pytest

from freecad_ai.core.conversation import Conversation


FAKE_IMAGE = {"type": "image", "source": "base64", "media_type": "image/png", "data": "aWdub3Jl"}


class TestAddUserMessageWithImages:
    def test_without_images_stays_string(self):
        c = Conversation()
        c.add_user_message("Hello")
        assert c.messages[0]["content"] == "Hello"

    def test_with_images_creates_blocks(self):
        c = Conversation()
        c.add_user_message("Look at this", images=[FAKE_IMAGE])
        content = c.messages[0]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "Look at this"}
        assert content[1] == FAKE_IMAGE

    def test_multiple_images(self):
        c = Conversation()
        img2 = {**FAKE_IMAGE, "data": "c2Vjb25k"}
        c.add_user_message("Two images", images=[FAKE_IMAGE, img2])
        content = c.messages[0]["content"]
        assert len(content) == 3  # 1 text + 2 images

    def test_empty_images_list_stays_string(self):
        c = Conversation()
        c.add_user_message("No images", images=[])
        # Empty list is falsy -> plain string
        assert c.messages[0]["content"] == "No images"


class TestOpenAIFormatWithImages:
    def test_text_only_unchanged(self):
        c = Conversation()
        c.add_user_message("Hello")
        msgs = c.get_messages_for_api(api_style="openai")
        assert msgs[0]["content"] == "Hello"

    def test_image_blocks_converted(self):
        c = Conversation()
        c.add_user_message("See this", images=[FAKE_IMAGE])
        msgs = c.get_messages_for_api(api_style="openai")
        content = msgs[0]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "See this"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == "data:image/png;base64,aWdub3Jl"


class TestAnthropicFormatWithImages:
    def test_text_only_unchanged(self):
        c = Conversation()
        c.add_user_message("Hello")
        msgs = c.get_messages_for_api(api_style="anthropic")
        assert msgs[0]["content"] == "Hello"

    def test_image_blocks_converted(self):
        c = Conversation()
        c.add_user_message("See this", images=[FAKE_IMAGE])
        msgs = c.get_messages_for_api(api_style="anthropic")
        content = msgs[0]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "See this"}
        assert content[1]["type"] == "image"
        assert content[1]["source"] == {
            "type": "base64",
            "media_type": "image/png",
            "data": "aWdub3Jl",
        }


class TestEstimatedTokensWithImages:
    def test_text_only(self):
        c = Conversation()
        c.add_user_message("Hello")
        assert c.estimated_tokens() == 1  # 5 chars / 4

    def test_with_image_adds_estimate(self):
        c = Conversation()
        c.add_user_message("Hi", images=[FAKE_IMAGE])
        tokens = c.estimated_tokens()
        # "Hi" = 2 chars + 1000 for image = 1002 chars / 4 = 250
        assert tokens == 250


class TestExtractText:
    def test_string_content(self):
        assert Conversation.extract_text("Hello") == "Hello"

    def test_list_content(self):
        content = [{"type": "text", "text": "Hello"}, FAKE_IMAGE]
        assert Conversation.extract_text(content) == "Hello"

    def test_empty(self):
        assert Conversation.extract_text("") == ""
        assert Conversation.extract_text(None) == ""


class TestContentChars:
    def test_string(self):
        assert Conversation._content_chars("Hello") == 5

    def test_list_with_image(self):
        content = [{"type": "text", "text": "Hi"}, FAKE_IMAGE]
        assert Conversation._content_chars(content) == 1002  # 2 + 1000

    def test_empty(self):
        assert Conversation._content_chars("") == 0
        assert Conversation._content_chars(None) == 0


class TestOllamaImageConversion:
    def test_converts_image_url_blocks(self):
        from freecad_ai.llm.client import LLMClient

        msgs = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ],
        }]
        LLMClient._convert_ollama_images(msgs)
        assert msgs[0]["content"] == "Look"
        assert msgs[0]["images"] == ["abc123"]

    def test_skips_plain_string_messages(self):
        from freecad_ai.llm.client import LLMClient

        msgs = [{"role": "user", "content": "Hello"}]
        LLMClient._convert_ollama_images(msgs)
        assert msgs[0]["content"] == "Hello"
        assert "images" not in msgs[0]

    def test_multiple_images(self):
        from freecad_ai.llm.client import LLMClient

        msgs = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Two"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,img1"}},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,img2"}},
            ],
        }]
        LLMClient._convert_ollama_images(msgs)
        assert msgs[0]["content"] == "Two"
        assert msgs[0]["images"] == ["img1", "img2"]


class TestViewportUtils:
    def test_resolution_presets(self):
        from freecad_ai.utils.viewport import RESOLUTION_PRESETS
        assert RESOLUTION_PRESETS["low"] == (400, 300)
        assert RESOLUTION_PRESETS["medium"] == (800, 600)
        assert RESOLUTION_PRESETS["high"] == (1280, 960)

    def test_image_to_base64_png(self):
        from freecad_ai.utils.viewport import image_to_base64_png
        data = b"\x89PNG\r\n"
        result = image_to_base64_png(data)
        assert result == base64.b64encode(data).decode("ascii")

    def test_make_image_content_block(self):
        from freecad_ai.utils.viewport import make_image_content_block
        block = make_image_content_block(b"test")
        assert block["type"] == "image"
        assert block["source"] == "base64"
        assert block["media_type"] == "image/png"
        assert block["data"] == base64.b64encode(b"test").decode("ascii")


class TestMessageViewWithImages:
    def test_render_text_only(self):
        from freecad_ai.ui.message_view import render_message
        html = render_message("user", "Hello")
        assert "Hello" in html

    def test_render_content_blocks(self):
        from freecad_ai.ui.message_view import render_message
        content = [
            {"type": "text", "text": "Check this"},
            {"type": "image", "media_type": "image/png", "data": "aWdub3Jl"},
        ]
        html = render_message("user", content)
        assert "Check this" in html
        assert '<img src="data:image/png;base64,aWdub3Jl"' in html
        assert 'href="image:1"' in html
