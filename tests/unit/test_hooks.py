"""Tests for the hooks system."""
import os
from freecad_ai.hooks.registry import HookRegistry


class TestHookRegistry:
    def test_init_empty(self, tmp_path, monkeypatch):
        import freecad_ai.hooks.registry as hooks_mod
        monkeypatch.setattr(hooks_mod, "HOOKS_DIR", str(tmp_path / "nonexistent"))
        monkeypatch.setattr(hooks_mod, "BUILTIN_HOOKS_DIR", str(tmp_path / "nonexistent2"))
        reg = HookRegistry()
        assert reg.discovered_hooks == []

    def test_discovers_hook(self, tmp_path, monkeypatch):
        import freecad_ai.hooks.registry as hooks_mod
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook_dir = hooks_dir / "my-hook"
        hook_dir.mkdir()
        (hook_dir / "hook.py").write_text(
            "def on_post_tool_use(context):\n    pass\n")
        monkeypatch.setattr(hooks_mod, "HOOKS_DIR", str(hooks_dir))
        monkeypatch.setattr(hooks_mod, "BUILTIN_HOOKS_DIR", str(tmp_path / "empty"))
        monkeypatch.setattr(hooks_mod, "_get_disabled", lambda: [])
        reg = HookRegistry()
        hooks = reg.discovered_hooks
        assert len(hooks) == 1
        assert hooks[0]["name"] == "my-hook"
        assert "post_tool_use" in hooks[0]["events"]

    def test_skips_disabled_hook(self, tmp_path, monkeypatch):
        import freecad_ai.hooks.registry as hooks_mod
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook_dir = hooks_dir / "disabled-hook"
        hook_dir.mkdir()
        (hook_dir / "hook.py").write_text(
            "def on_post_response(context):\n    pass\n")
        monkeypatch.setattr(hooks_mod, "HOOKS_DIR", str(hooks_dir))
        monkeypatch.setattr(hooks_mod, "BUILTIN_HOOKS_DIR", str(tmp_path / "empty"))
        monkeypatch.setattr(hooks_mod, "_get_disabled", lambda: ["disabled-hook"])
        reg = HookRegistry()
        assert reg.discovered_hooks == []

    def test_skips_non_callable(self, tmp_path, monkeypatch):
        import freecad_ai.hooks.registry as hooks_mod
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook_dir = hooks_dir / "bad-hook"
        hook_dir.mkdir()
        (hook_dir / "hook.py").write_text("on_pre_tool_use = 'not a function'\n")
        monkeypatch.setattr(hooks_mod, "HOOKS_DIR", str(hooks_dir))
        monkeypatch.setattr(hooks_mod, "BUILTIN_HOOKS_DIR", str(tmp_path / "empty"))
        monkeypatch.setattr(hooks_mod, "_get_disabled", lambda: [])
        reg = HookRegistry()
        hooks = reg.discovered_hooks
        assert len(hooks) == 1
        assert hooks[0]["events"] == []

    def test_syntax_error_in_hook(self, tmp_path, monkeypatch):
        import freecad_ai.hooks.registry as hooks_mod
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook_dir = hooks_dir / "broken-hook"
        hook_dir.mkdir()
        (hook_dir / "hook.py").write_text("def on_pre_tool_use(:\n")
        monkeypatch.setattr(hooks_mod, "HOOKS_DIR", str(hooks_dir))
        monkeypatch.setattr(hooks_mod, "BUILTIN_HOOKS_DIR", str(tmp_path / "empty"))
        monkeypatch.setattr(hooks_mod, "_get_disabled", lambda: [])
        reg = HookRegistry()
        hooks = reg.discovered_hooks
        assert len(hooks) == 1
        assert hooks[0]["has_error"] is True

    def test_multiple_events_in_one_hook(self, tmp_path, monkeypatch):
        import freecad_ai.hooks.registry as hooks_mod
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook_dir = hooks_dir / "multi-hook"
        hook_dir.mkdir()
        (hook_dir / "hook.py").write_text(
            "def on_pre_tool_use(context):\n    pass\n\n"
            "def on_post_tool_use(context):\n    pass\n")
        monkeypatch.setattr(hooks_mod, "HOOKS_DIR", str(hooks_dir))
        monkeypatch.setattr(hooks_mod, "BUILTIN_HOOKS_DIR", str(tmp_path / "empty"))
        monkeypatch.setattr(hooks_mod, "_get_disabled", lambda: [])
        reg = HookRegistry()
        hooks = reg.discovered_hooks
        assert len(hooks) == 1
        assert "pre_tool_use" in hooks[0]["events"]
        assert "post_tool_use" in hooks[0]["events"]


class TestHookFiring:
    def _make_registry(self, tmp_path, monkeypatch, hook_code):
        import freecad_ai.hooks.registry as hooks_mod
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        hook_dir = hooks_dir / "test-hook"
        hook_dir.mkdir(exist_ok=True)
        (hook_dir / "hook.py").write_text(hook_code)
        monkeypatch.setattr(hooks_mod, "HOOKS_DIR", str(hooks_dir))
        monkeypatch.setattr(hooks_mod, "BUILTIN_HOOKS_DIR", str(tmp_path / "empty"))
        monkeypatch.setattr(hooks_mod, "_get_disabled", lambda: [])
        return HookRegistry()

    def test_fire_no_hooks(self, tmp_path, monkeypatch):
        reg = self._make_registry(tmp_path, monkeypatch,
            "def on_post_response(context):\n    pass\n")
        result = reg.fire("pre_tool_use", {"tool_name": "test"})
        assert result == {}

    def test_fire_blocking_hook(self, tmp_path, monkeypatch):
        reg = self._make_registry(tmp_path, monkeypatch,
            "def on_pre_tool_use(context):\n"
            "    if context['tool_name'] == 'dangerous':\n"
            "        return {'block': True, 'reason': 'Too dangerous'}\n"
            "    return {}\n")
        result = reg.fire("pre_tool_use", {"tool_name": "dangerous"})
        assert result["block"] is True
        assert "dangerous" in result["reason"]
        result2 = reg.fire("pre_tool_use", {"tool_name": "safe_tool"})
        assert result2.get("block") is not True

    def test_fire_modify_hook(self, tmp_path, monkeypatch):
        reg = self._make_registry(tmp_path, monkeypatch,
            "def on_user_prompt_submit(context):\n"
            "    return {'modify': context['text'].upper()}\n")
        result = reg.fire("user_prompt_submit",
                          {"text": "hello", "images": [], "mode": "act"})
        assert result["modify"] == "HELLO"

    def test_fire_exception_continues(self, tmp_path, monkeypatch):
        import freecad_ai.hooks.registry as hooks_mod
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook_a = hooks_dir / "a-crasher"
        hook_a.mkdir()
        (hook_a / "hook.py").write_text(
            "def on_post_tool_use(context):\n    raise RuntimeError('boom')\n")
        hook_b = hooks_dir / "b-logger"
        hook_b.mkdir()
        (hook_b / "hook.py").write_text(
            "results = []\n"
            "def on_post_tool_use(context):\n"
            "    results.append(context['tool_name'])\n")
        monkeypatch.setattr(hooks_mod, "HOOKS_DIR", str(hooks_dir))
        monkeypatch.setattr(hooks_mod, "BUILTIN_HOOKS_DIR", str(tmp_path / "empty"))
        monkeypatch.setattr(hooks_mod, "_get_disabled", lambda: [])
        reg = HookRegistry()
        # Should not raise
        reg.fire("post_tool_use", {
            "tool_name": "test", "arguments": {},
            "success": True, "output": "", "error": "", "turn": 1,
        })

    def test_fire_block_wins_over_modify(self, tmp_path, monkeypatch):
        import freecad_ai.hooks.registry as hooks_mod
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook_a = hooks_dir / "a-modifier"
        hook_a.mkdir()
        (hook_a / "hook.py").write_text(
            "def on_user_prompt_submit(context):\n"
            "    return {'modify': 'modified text'}\n")
        hook_b = hooks_dir / "b-blocker"
        hook_b.mkdir()
        (hook_b / "hook.py").write_text(
            "def on_user_prompt_submit(context):\n"
            "    return {'block': True, 'reason': 'nope'}\n")
        monkeypatch.setattr(hooks_mod, "HOOKS_DIR", str(hooks_dir))
        monkeypatch.setattr(hooks_mod, "BUILTIN_HOOKS_DIR", str(tmp_path / "empty"))
        monkeypatch.setattr(hooks_mod, "_get_disabled", lambda: [])
        reg = HookRegistry()
        result = reg.fire("user_prompt_submit",
                          {"text": "hello", "images": [], "mode": "act"})
        assert result.get("block") is True

    def test_fire_modify_chains(self, tmp_path, monkeypatch):
        import freecad_ai.hooks.registry as hooks_mod
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook_a = hooks_dir / "a-upper"
        hook_a.mkdir()
        (hook_a / "hook.py").write_text(
            "def on_user_prompt_submit(context):\n"
            "    return {'modify': context['text'].upper()}\n")
        hook_b = hooks_dir / "b-exclaim"
        hook_b.mkdir()
        (hook_b / "hook.py").write_text(
            "def on_user_prompt_submit(context):\n"
            "    return {'modify': context['text'] + '!'}\n")
        monkeypatch.setattr(hooks_mod, "HOOKS_DIR", str(hooks_dir))
        monkeypatch.setattr(hooks_mod, "BUILTIN_HOOKS_DIR", str(tmp_path / "empty"))
        monkeypatch.setattr(hooks_mod, "_get_disabled", lambda: [])
        reg = HookRegistry()
        result = reg.fire("user_prompt_submit",
                          {"text": "hello", "images": [], "mode": "act"})
        assert result["modify"] == "HELLO!"

    def test_reload_picks_up_new_hooks(self, tmp_path, monkeypatch):
        import freecad_ai.hooks.registry as hooks_mod
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        monkeypatch.setattr(hooks_mod, "HOOKS_DIR", str(hooks_dir))
        monkeypatch.setattr(hooks_mod, "BUILTIN_HOOKS_DIR", str(tmp_path / "empty"))
        monkeypatch.setattr(hooks_mod, "_get_disabled", lambda: [])
        reg = HookRegistry()
        assert reg.discovered_hooks == []
        hook_dir = hooks_dir / "new-hook"
        hook_dir.mkdir()
        (hook_dir / "hook.py").write_text(
            "def on_post_response(context):\n    pass\n")
        reg.reload()
        assert len(reg.discovered_hooks) == 1
