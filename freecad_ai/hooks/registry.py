"""Hook registry -- discovers, loads, and fires user-defined hooks."""
import importlib.util
import logging
import os

logger = logging.getLogger(__name__)

from ..config import CONFIG_DIR

HOOKS_DIR = os.path.join(CONFIG_DIR, "hooks")

BUILTIN_HOOKS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "hooks",
)

VALID_EVENTS = ("pre_tool_use", "post_tool_use", "user_prompt_submit", "post_response", "file_attach")


def _get_disabled() -> list:
    try:
        from ..config import get_config
        return get_config().hooks_disabled
    except Exception:
        return []


def _hook_dirs() -> list:
    """Hook directories in descending precedence order.

    Unlike skills, the first directory to define a hook name wins (see the
    dedup check in _load_hooks), so built-ins come first and the user
    directory last.
    """
    dirs = [BUILTIN_HOOKS_DIR]
    try:
        from ..extensions.module_assets import asset_subdirs
        dirs.extend(asset_subdirs("hooks"))
    except Exception:
        pass  # Module asset discovery is optional
    dirs.append(HOOKS_DIR)
    return dirs


class HookRegistry:
    def __init__(self):
        self._hooks: dict[str, list[tuple[str, callable]]] = {}
        self._hook_info: list[dict] = []
        self._load_hooks()

    def _load_hooks(self):
        hooks = {}
        info = []
        disabled = _get_disabled()

        for hooks_dir in _hook_dirs():
            if not os.path.isdir(hooks_dir):
                continue
            for entry in sorted(os.listdir(hooks_dir)):
                hook_dir = os.path.join(hooks_dir, entry)
                hook_file = os.path.join(hook_dir, "hook.py")
                if not os.path.isdir(hook_dir) or not os.path.isfile(hook_file):
                    continue
                if entry in disabled:
                    continue
                if any(h["name"] == entry for h in info):
                    continue

                hook_info = {
                    "name": entry,
                    "path": hook_dir,
                    "events": [],
                    "has_error": False,
                    "error_message": "",
                    "builtin": hooks_dir == BUILTIN_HOOKS_DIR,
                }

                try:
                    spec = importlib.util.spec_from_file_location(
                        f"hook_{entry}", hook_file)
                    if not spec or not spec.loader:
                        hook_info["has_error"] = True
                        hook_info["error_message"] = "Failed to create module spec"
                        info.append(hook_info)
                        continue

                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    for event in VALID_EVENTS:
                        func_name = f"on_{event}"
                        func = getattr(module, func_name, None)
                        if func is not None and callable(func):
                            if event not in hooks:
                                hooks[event] = []
                            hooks[event].append((entry, func))
                            hook_info["events"].append(event)

                except Exception as e:
                    hook_info["has_error"] = True
                    hook_info["error_message"] = str(e)
                    logger.error("Failed to load hook '%s': %s", entry, e)

                info.append(hook_info)

        self._hooks = hooks
        self._hook_info = info

    def fire(self, event: str, context: dict) -> dict:
        handlers = self._hooks.get(event, [])
        if not handlers:
            return {}

        merged = {}
        for hook_name, handler in handlers:
            if "modify" in merged and "text" in context:
                context["text"] = merged["modify"]
            try:
                result = handler(context)
            except Exception as e:
                logger.error("Hook '%s' raised %s in %s: %s",
                             hook_name, type(e).__name__, event, e)
                continue
            if result and isinstance(result, dict):
                if result.get("block"):
                    return result
                merged.update(result)
        return merged

    def reload(self):
        old_hooks = self._hooks
        old_info = self._hook_info
        try:
            self._load_hooks()
        except Exception as e:
            logger.error("Hook reload failed: %s", e)
            self._hooks = old_hooks
            self._hook_info = old_info

    @property
    def discovered_hooks(self) -> list[dict]:
        return list(self._hook_info)
