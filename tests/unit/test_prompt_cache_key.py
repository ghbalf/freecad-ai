"""The conversation's sticky-routing hint reaches the request body (#47).

A byte-perfect prefix is necessary but not sufficient. Moonshot's staff
describe a backend of many clusters, each holding its own KV blocks: a
follow-up load-balanced onto a cluster that never saw this conversation
misses the cache however careful the client was with its bytes.
``prompt_cache_key`` asks the gateway for the same cluster again, and both
Moonshot and OpenAI document one value per conversation.

So the value has to be *stable across a conversation and distinct between
conversations* -- which is what ``Conversation.conversation_id`` already is,
including across a save and reload, matching Moonshot's "if the session is
exited and later resumed, this value should remain the same".

Three ways this could ship inert or harmful, one test class each: the field
never reaching the body, the body carrying a key nobody kept stable, and the
key going to a vendor that does not document it -- an unknown top-level
member is exactly what a thin OpenAI-compatible proxy rejects with a 400.
Like the rest of #47 it rides on ``optimize_prompt_caching``.
"""

import pytest

from freecad_ai.config import AppConfig
from freecad_ai.core.conversation import Conversation
from freecad_ai.llm.client import LLMClient, create_client


def _client(provider="moonshot", **kw):
    return LLMClient(
        provider_name=provider, base_url="https://example.invalid",
        api_key="k", model="m", **kw)


def _body(**kw):
    return _client(**kw)._openai_body([{"role": "user", "content": "hi"}],
                                      system="", stream=False)


class TestWhenTheHintGoesOut:

    def test_an_opted_in_conversation_carries_it(self):
        assert _body(prompt_caching=True,
                     cache_key="conv_1")["prompt_cache_key"] == "conv_1"

    def test_the_off_switch_still_means_off(self):
        """Default behaviour is the pre-#47 request, byte for byte."""
        assert "prompt_cache_key" not in _body(cache_key="conv_1")

    def test_a_call_site_with_no_conversation_sends_nothing(self):
        """Utility clients have nothing to be sticky to, and a blank key
        would herd every unrelated request onto one cluster."""
        assert "prompt_cache_key" not in _body(prompt_caching=True)

    def test_it_goes_out_on_streams_and_plain_posts_alike(self):
        """Unlike stream_options, this one is worth sending either way."""
        c = _client(prompt_caching=True, cache_key="conv_1")

        for stream in (True, False):
            body = c._openai_body([{"role": "user", "content": "hi"}],
                                  system="", stream=stream)
            assert body["prompt_cache_key"] == "conv_1"


class TestWhichVendorsHearAboutIt:

    @pytest.mark.parametrize("provider", ["moonshot", "openai"])
    def test_the_two_that_document_the_field(self, provider):
        body = _body(provider=provider, prompt_caching=True,
                     cache_key="conv_1")

        assert body["prompt_cache_key"] == "conv_1"

    @pytest.mark.parametrize("provider",
                             ["custom", "ollama", "deepseek", "openrouter",
                              "groq", "cloudflare"])
    def test_everyone_else_gets_the_body_they_got_before(self, provider):
        """Silently 400-ing someone's chat to save them money is a bad
        trade; these endpoints are only OpenAI-*compatible*."""
        body = _body(provider=provider, prompt_caching=True,
                     cache_key="conv_1")

        assert "prompt_cache_key" not in body

    def test_a_user_who_set_their_own_keeps_it(self):
        """Model Parameters is freeform, so this key can already be in
        there -- and their bucketing beats ours."""
        body = _body(prompt_caching=True, cache_key="conv_1",
                     model_params={"prompt_cache_key": "mine"})

        assert body["prompt_cache_key"] == "mine"


class TestTheKeyIsTheConversation:

    def test_create_client_hands_it_over(self):
        cfg = AppConfig()
        cfg.optimize_prompt_caching = True

        client = create_client(cfg, cache_key="conv_1")

        assert client.cache_key == "conv_1"

    def test_no_call_site_means_no_key(self):
        assert create_client(AppConfig()).cache_key == ""

    def test_a_reloaded_session_asks_for_the_same_cluster(self, tmp_path,
                                                          monkeypatch):
        """The prefix survives a reload (test_prompt_cache_prefix), so the
        routing hint that finds it has to survive one too."""
        monkeypatch.setattr(
            "freecad_ai.core.conversation.CONVERSATIONS_DIR", str(tmp_path))
        conv = Conversation()
        conv.add_user_message("make a box")
        conv.save()

        restored = Conversation.load(conv.conversation_id)

        assert restored.conversation_id == conv.conversation_id

    def test_two_conversations_do_not_share_a_key(self):
        assert Conversation().conversation_id != Conversation().conversation_id


class TestSomethingActuallySuppliesIt:
    """The half that is easy to forget: a client field with no caller is a
    silent no-op, and this one would look fine in every test above."""

    def _run(self, conversation, monkeypatch):
        from freecad_ai.ui import chat_widget

        seen = {}

        def fake_create(*, cache_key=""):
            seen["cache_key"] = cache_key
            return _client(prompt_caching=True, cache_key=cache_key)

        monkeypatch.setattr(
            "freecad_ai.llm.client.create_client_from_config", fake_create)
        monkeypatch.setattr(
            "freecad_ai.llm.client.should_strip_thinking", lambda *a: False)

        class _Worker:
            conversation = None
            describe_fn = None
            tools = []
            api_style = "openai"

            class error_occurred:
                @staticmethod
                def emit(msg):
                    raise AssertionError("run() failed: {}".format(msg))

            def _simple_stream(self, client):
                seen["client"] = client

        worker = _Worker()
        worker.conversation = conversation
        chat_widget._LLMWorker.run(worker)
        return seen

    def test_the_worker_passes_the_conversation_id(self, monkeypatch):
        conv = Conversation()

        seen = self._run(conv, monkeypatch)

        assert seen["cache_key"] == conv.conversation_id
        assert seen["client"].cache_key == conv.conversation_id

    def test_a_worker_without_a_conversation_passes_nothing(self, monkeypatch):
        """Act mode always has one; the simple-stream path may not."""
        assert self._run(None, monkeypatch)["cache_key"] == ""
