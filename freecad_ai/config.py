"""Configuration system for FreeCAD AI.

Stores settings as JSON at <config-dir>/config.json. Path resolution:

  1. ``$FREECAD_AI_CONFIG_DIR`` env var when set (tests, power users).
  2. ``<FreeCAD user config dir>/FreeCADAI/`` when FreeCAD is importable.
     On FreeCAD 1.1+ this is ``~/.config/FreeCAD/v1-1/FreeCADAI/`` (or platform
     equivalent — version-scoped under XDG_CONFIG_HOME). We use the user
     *config* dir (XDG_CONFIG_HOME) rather than the user *data* dir
     (XDG_DATA_HOME, where ``Mod/`` lives) because the workbench stores
     config-shaped data: settings, secrets, conversation logs.
  3. ``~/.config/FreeCAD/FreeCADAI/`` legacy fallback when FreeCAD is not
     importable (pytest, console scripts, plain Python REPL).

A one-shot migration runs on first import per (target, marker) pair. The
migration looks at *historical candidate paths* in priority order, picks
the first one that has data as the source, ``shutil.move``s it to the new
target, and renames any stale leftovers as ``.duplicate-cleanup-<ts>/``.
A marker file inside the new target blocks subsequent re-runs.

On every launch (whether or not migration ran), a sweep also fires: if the
marker is present and any historical candidate path *still* has content,
that path is renamed as ``.duplicate-cleanup-<ts>/``. Catches the case
where an aborted/buggy prior migration left duplicate data behind.
"""

import datetime
import json
import os
import shutil
import sys
import time
import time
from dataclasses import dataclass, field, asdict


# Marker filename inside the active config dir. Its presence signals that
# this version of the workbench has already migrated into this target.
_ACTIVE_MARKER_FILE = ".freecad_ai_active_marker"

# Suffix used when target itself exists without a marker (rare; would mean
# someone manually placed something at the target path). Renamed out of
# the way so the move can proceed without overwriting.
_SNAPSHOT_BACKUP_SUFFIX = ".pre-v0.13-snapshot"

# Suffix used when a historical candidate path still has data after migration
# has already completed. The path is renamed; user can rm when confident.
_DUPLICATE_CLEANUP_SUFFIX = ".duplicate-cleanup"


def _legacy_config_dir() -> str:
    """The pre-v0.13 unversioned config dir (~/.config/FreeCAD/FreeCADAI).

    Every workbench release before v0.13.0-alpha hardcoded this path, so
    every existing user has data here. The most common migration source.
    """
    return os.path.join(os.path.expanduser("~"), ".config", "FreeCAD", "FreeCADAI")


def _get_freecad_user_app_data_dir():
    """Return ``FreeCAD.getUserAppDataDir()``, or None if FreeCAD isn't importable.

    On FreeCAD 1.1+ Linux this is e.g. ``~/.local/share/FreeCAD/v1-1/`` (XDG
    data dir, version-scoped — where ``Mod/``, ``Macro/`` live). Used only
    to locate the historical ``<UAD>/FreeCADAI/`` path that the buggy
    v0.13.0-alpha pre-release wrote to on the maintainer's machine; not the
    canonical config target.
    """
    try:
        import FreeCAD
        path = FreeCAD.getUserAppDataDir()
        return path or None
    except Exception:
        return None


def _get_freecad_user_config_dir():
    """Return the version-scoped user config dir for the current FreeCAD,
    or None when FreeCAD isn't importable.

    Tries ``FreeCAD.getUserConfigDir()`` first (likely available on FreeCAD
    1.1+; we don't assume — falls through if absent). Otherwise derives
    ``<XDG_CONFIG_HOME>/FreeCAD/v<major>-<minor>/`` from ``FreeCAD.Version()``
    and ``$XDG_CONFIG_HOME`` (default ``~/.config``).

    The version segment matches FreeCAD's own naming (``v1-1`` for 1.1.x,
    not ``1.1`` or ``v1.1``) so the workbench config sits in the same
    directory as FreeCAD's own ``v1-1/FreeCAD.conf``, ``user.cfg``, etc.
    """
    try:
        import FreeCAD
        getter = getattr(FreeCAD, "getUserConfigDir", None)
        if getter is not None:
            path = getter()
            if path:
                return path
        version = FreeCAD.Version()
        if version and len(version) >= 2:
            major = str(version[0]).strip()
            minor = str(version[1]).strip()
            xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
                os.path.expanduser("~"), ".config"
            )
            return os.path.join(xdg, "FreeCAD", f"v{major}-{minor}")
    except Exception:
        pass
    return None


def _new_target_dir():
    """The v0.13.0+ canonical user config path: ``<user config dir>/FreeCADAI/``.

    Returns None when FreeCAD isn't importable. Callers fall back to the
    legacy unversioned path in that case so tests / console scripts still work.
    """
    base = _get_freecad_user_config_dir()
    if not base:
        return None
    return os.path.join(base, "FreeCADAI")


def _historical_candidate_paths() -> list[str]:
    """Paths the workbench may have written to in past or pre-release builds.

    Listed in source-priority order: first one that has user content wins
    as the migration source; remaining ones get swept to ``.duplicate-cleanup``.
    The new target (``<user config dir>/FreeCADAI/``) is intentionally NOT
    in this list — it's the destination, not a source. If the new target
    already exists without our marker it'll be treated as a stale snapshot
    by ``_migrate_to_target`` and renamed to ``.pre-v0.13-snapshot``.
    """
    candidates: list[str] = []
    uad = _get_freecad_user_app_data_dir()
    if uad:
        # v0.13.0-alpha pre-release wrote to ``<UAD>/FreeCADAI/`` (under
        # XDG_DATA_HOME, wrong namespace). Released builds went straight
        # to the user config dir. Only relevant for the maintainer's
        # machine. Listed first: if both this and the legacy unversioned
        # path exist, this one wins as source because the pre-release
        # write was more recent.
        candidates.append(os.path.join(uad, "FreeCADAI"))
    # Pre-v0.13: hardcoded ~/.config/FreeCAD/FreeCADAI/. Every released build.
    candidates.append(_legacy_config_dir())
    return candidates


def _drop_marker(target: str) -> None:
    """Write the active-dir marker file inside *target*."""
    marker = os.path.join(target, _ACTIVE_MARKER_FILE)
    with open(marker, "w") as f:
        f.write(
            "FreeCAD AI v0.13.0+ -- active config dir.\n"
            f"Created: {datetime.datetime.now().isoformat()}\n"
            "Removing this file will trigger re-migration on next launch.\n"
        )


def _has_user_content(path: str) -> bool:
    """True if *path* is a directory containing more than just the marker file.

    A bare marker file means a previous migration created an empty target
    here — not a real source we should migrate from.
    """
    if not os.path.isdir(path):
        return False
    try:
        entries = [e for e in os.listdir(path) if e != _ACTIVE_MARKER_FILE]
    except OSError:
        return False
    return bool(entries)


def _rename_with_collision_suffix(src: str, base_suffix: str) -> str:
    """Rename *src* to ``src + base_suffix``, appending a Unix timestamp if
    that name is already taken. Returns the resolved backup path."""
    backup = src + base_suffix
    if os.path.exists(backup):
        backup = f"{backup}-{int(time.time())}"
    os.rename(src, backup)
    return backup


def _sweep_stale_candidates(candidates: list[str], target: str) -> list[str]:
    """Rename every candidate that still has content out of the way.

    Skips ``target`` itself (no rename to backup of the active dir!) and
    any candidate that doesn't exist or has no user content. Returns the
    list of renamed paths so callers can log them.
    """
    target_real = os.path.realpath(target)
    renamed: list[str] = []
    for c in candidates:
        if not _has_user_content(c):
            continue
        if os.path.realpath(c) == target_real:
            continue
        try:
            renamed.append(_rename_with_collision_suffix(c, _DUPLICATE_CLEANUP_SUFFIX))
        except OSError as e:
            print(
                f"FreeCAD AI: could not rename stale dir {c} ({e!r}); leaving in place",
                file=sys.stderr,
            )
    return renamed


def _migrate_to_target(candidates: list[str], target: str) -> None:
    """One-shot migration. Moves the first candidate with content to *target*.

    Preconditions:
      * No marker file exists in *target* (caller verified).

    Steps:
      1. If *target* exists without a marker (unexpected — Mod/ is FreeCAD's
         code dir, not data — but possible on weird setups), rename it
         out of the way to ``<target>.pre-v0.13-snapshot[-ts]/``.
      2. Find the first candidate that has user content. ``shutil.move`` it
         to *target*. If none exist, create an empty *target*.
      3. Sweep any remaining candidates that still have content (renamed
         to ``.duplicate-cleanup[-ts]/``).
      4. Drop the marker file.

    No backup of the moved candidate is kept — its data lives at *target*
    after the move. Other candidates are renamed (not deleted) as a safety
    net since they may pre-date our marker semantics.
    """
    target_real_pre = os.path.realpath(target) if os.path.exists(target) else target

    if os.path.isdir(target):
        _rename_with_collision_suffix(target, _SNAPSHOT_BACKUP_SUFFIX)

    os.makedirs(os.path.dirname(target), exist_ok=True)

    source = next((c for c in candidates if _has_user_content(c)), None)

    # If a candidate's realpath equals the target's, the move would be a
    # no-op rename onto itself; just ensure the dir + marker.
    if source and os.path.realpath(source) == target_real_pre:
        os.makedirs(target, exist_ok=True)
        remaining = [c for c in candidates if c != source]
    elif source:
        shutil.move(source, target)
        remaining = [c for c in candidates if c != source]
    else:
        os.makedirs(target, exist_ok=True)
        remaining = list(candidates)

    _sweep_stale_candidates(remaining, target)
    _drop_marker(target)


def _resolve_config_dir() -> str:
    """Resolve the active workbench config dir.

    Runs migration if it hasn't yet (no marker at target). On every launch,
    sweeps any stale historical candidate dirs that still have content —
    catches the case where an aborted/buggy prior migration (e.g. the
    v0.13.0-alpha-pre-release copy-based draft) left duplicate data behind.

    On migration failure logs to stderr and falls back to a candidate path
    that still exists, so the workbench still loads. Better degraded than
    dead.
    """
    env = os.environ.get("FREECAD_AI_CONFIG_DIR")
    if env:
        os.makedirs(env, exist_ok=True)
        return env

    target = _new_target_dir()
    if target is None:
        return _legacy_config_dir()

    candidates = _historical_candidate_paths()
    marker_path = os.path.join(target, _ACTIVE_MARKER_FILE)

    if os.path.exists(marker_path):
        try:
            _sweep_stale_candidates(candidates, target)
        except Exception as e:
            print(
                f"FreeCAD AI: stale legacy sweep failed ({e!r}); leaving as-is",
                file=sys.stderr,
            )
        return target

    try:
        _migrate_to_target(candidates, target)
        return target
    except Exception as e:
        print(
            f"FreeCAD AI: config migration to {target} failed ({e!r}); "
            f"falling back to a legacy candidate",
            file=sys.stderr,
        )
        for c in candidates:
            if os.path.isdir(c):
                return c
        return candidates[-1] if candidates else _legacy_config_dir()


CONFIG_DIR = _resolve_config_dir()
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
CONVERSATIONS_DIR = os.path.join(CONFIG_DIR, "conversations")
SKILLS_DIR = os.path.join(CONFIG_DIR, "skills")
USER_TOOLS_DIR = os.path.join(CONFIG_DIR, "tools")
HOOKS_DIR = os.path.join(CONFIG_DIR, "hooks")
LOGS_DIR = os.path.join(CONFIG_DIR, "logs")
BACKUPS_DIR = os.path.join(CONFIG_DIR, "backups")


def prune_oldest_files(
    directory: str,
    pattern_fn,
    keep: int,
    max_age_days: int = 0,
) -> int:
    """Delete files in *directory* matching *pattern_fn* by retention rules.

    A file survives only if both checks pass:
      * It's within the *keep* newest matches (by mtime). 0 disables this check.
      * Its mtime is younger than *max_age_days*. 0 disables this check.

    Files violating either condition are deleted. Returns the number of files
    deleted. Best-effort — individual unlink errors are swallowed so callers
    can use this from save paths without disrupting the user.
    """
    if not os.path.isdir(directory):
        return 0
    matches = [
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if pattern_fn(name)
    ]
    if not matches:
        return 0

    # Newest-first; index < keep is "within count cap".
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    cutoff_mtime = None
    if max_age_days > 0:
        cutoff_mtime = time.time() - (max_age_days * 86400)

    deleted = 0
    for idx, path in enumerate(matches):
        over_count = keep > 0 and idx >= keep
        too_old = cutoff_mtime is not None and os.path.getmtime(path) < cutoff_mtime
        if not (over_count or too_old):
            continue
        try:
            os.remove(path)
            deleted += 1
        except OSError:
            pass
    return deleted

# Provider presets — derived from the canonical PROVIDERS dict in llm/providers.py.
# Each preset contains only base_url and default_model (the fields the Settings
# dialog needs for auto-filling when the user switches providers).
from .llm.providers import PROVIDERS as _PROVIDERS

PROVIDER_PRESETS = {
    name: {
        "base_url": p["base_url"],
        "default_model": p["default_model"],
        "default_params": p.get("default_params", {}),
        "default_rerank": p.get("default_rerank", {}),
    }
    for name, p in _PROVIDERS.items()
}


@dataclass
class ProviderConfig:
    """One named connection: vendor, endpoint, credential, model, params.

    ``name`` is the *vendor* key into PROVIDER_PRESETS. A profile's own
    label is its key in ``AppConfig.profiles``, not a field here.
    """
    name: str = "anthropic"
    api_key: str = ""
    base_url: str = "https://api.anthropic.com"
    model: str = "claude-sonnet-4-6"
    params: dict = field(default_factory=dict)

    def apply_preset(self, provider_name: str):
        """Apply a provider preset, updating base_url and model to defaults."""
        preset = PROVIDER_PRESETS.get(provider_name, {})
        self.name = provider_name
        self.base_url = preset.get("base_url", self.base_url)
        self.model = preset.get("default_model", self.model)


@dataclass
class AppConfig:
    profiles: dict = field(default_factory=dict)      # label -> ProviderConfig
    active_profile: str = ""                          # label chat uses
    provider_keys: dict = field(default_factory=dict) # vendor -> default api key
    utility_profiles: dict = field(default_factory=dict)  # utility -> label
    mode: str = "plan"  # "plan" or "act"
    max_tokens: int = 4096
    context_window: int = 20000  # tokens — compaction triggers above this
    temperature: float = 0.3
    model_params: dict = field(default_factory=dict)
    # Per-model parameter overrides, keyed by model name:
    # {"gemma4:27b": {"temperature": 1.0, "top_p": 0.95, "top_k": 64}, ...}
    auto_execute: bool = False
    max_retries: int = 3
    # Max agentic tool-loop turns per user message. 0 = endless (the Stop
    # button is then the only brake). Default 30 preserves prior behavior.
    max_tool_turns: int = 30
    # Wall-clock budget (seconds) for executing a single generated code block —
    # applied to BOTH the headless sandbox dry-run and the live SIGALRM. Kept
    # user-tunable so heavy but valid geometry ops (e.g. scaling a detailed
    # model via Shape.transformGeometry, whose cost grows with face count) can
    # be given more time on large models (issue #14).
    execution_timeout: int = 30
    enable_tools: bool = True
    thinking: str = "off"  # "off", "on", "extended"
    strip_thinking_history: bool | None = None  # None=auto-detect, True/False=override
    viewport_capture: str = "off"  # "off", "every_message", "after_changes"
    viewport_resolution: str = "medium"  # "low", "medium", "high"
    mcp_servers: list = field(default_factory=list)
    # Each entry: {"name": str, "command": str, "args": list, "env": dict, "enabled": bool}
    # Address the addon listens on when acting AS an MCP server (the toolbar
    # toggle and mcp_server_http.py). Host is deliberately unrestricted,
    # including non-loopback: the server has no authentication unless a
    # bearer token is configured below (issue #59), so the Settings dialog
    # warns about the exposure rather than pretending a restricted field made
    # it safe. MCP_HOST / MCP_PORT override both.
    mcp_server_host: str = "127.0.0.1"
    mcp_server_port: int = 3000
    # Host headers the server answers to. Empty means "let the transport pick
    # its own default" (loopback, plus the rejection of a wildcard bind that
    # would otherwise 403 every client — #60). A non-empty list here is the
    # deliberate opt-in to a wider policy, e.g. naming the LAN address or
    # container hostname clients actually dial. MCP_ALLOWED_HOSTS overrides.
    mcp_server_allowed_hosts: list = field(default_factory=list)
    # Bearer token every MCP request must present (issue #59). Empty (the
    # default) leaves the server unauthenticated, same as before this field
    # existed. MCP_AUTH_TOKEN overrides.
    mcp_server_auth_token: str = ""
    user_tools_disabled: list = field(default_factory=list)
    scan_freecad_macros: bool = False
    # Dangerous mode: relaxes executor safety layers (static pattern blocking,
    # headless sandbox pre-check, execution timeout) and widens run_macro's
    # file-resolution reach to arbitrary paths. The GUI never writes True here;
    # persistence is only via hand-editing config.json. Honored on load.
    dangerous_skip_safety: bool = False
    hooks_disabled: list = field(default_factory=list)
    # When True, hook/user-tool Edit and New buttons open files via the OS
    # default handler (xdg-open / Launch Services / file association). When
    # False (default), they open in FreeCAD's docked Gui::PythonEditor — which
    # requires closing the modal Settings dialog first since the editor is
    # an MDI sub-window of the main window.
    use_external_editor: bool = False
    system_prompt_override: str = ""  # empty = use default; non-empty = use as-is
    vision_detected: bool | None = None   # None=not tested, True/False=probe result
    vision_override: bool | None = None   # user manual override, takes precedence
    # Tool-calling capability (Ollama /api/show "tools"). None=untested or
    # non-Ollama (in which case provider.supports_tools is the source of truth).
    # False explicitly = the model doesn't support tools (e.g. embedding/reranker
    # picked as main model) → suppress tools array in chat sends.
    tools_detected: bool | None = None
    # Thinking capability (Ollama /api/show "thinking"). Diagnostic-only today.
    thinking_detected: bool | None = None

    # Tool reranking — when active, only the top-N most relevant tools
    # (plus pinned tools) are sent to the LLM on each user turn. Saves
    # prompt tokens when many tools are registered.
    # "off" = disabled, "keyword" = IDF-weighted token match (free, fast),
    # "llm" = semantic ranking via a small/fast LLM (better filter quality).
    rerank_method: str = "off"
    rerank_top_n: int = 15
    rerank_pinned_tools: list = field(default_factory=list)
    # LLM reranker provider override. Empty = inherit from main provider.
    # Lets users run reranking through a cheap/local model (e.g. Ollama)
    # while the main chat uses an expensive cloud model.
    rerank_llm_provider_name: str = ""
    rerank_llm_base_url: str = ""
    rerank_llm_api_key: str = ""
    rerank_llm_model: str = ""
    # Sampling params for the reranker's *override* model. Kept in their own
    # namespace (never the shared model_params dict) so the reranker can never
    # overwrite the main model's params — the bug behind issue #30. Only used
    # when rerank_llm_model is set; in inherit mode the reranker reads the main
    # model's params instead.
    rerank_params: dict = field(default_factory=dict)

    # Chat dock layout persistence. FreeCAD's native mw.restoreState runs
    # before the workbench activates, so our dock misses the restore and
    # lands at its default area every startup. We snapshot our own state
    # on dock-move events and reapply in get_chat_dock().
    # When True, the chat dock is NOT hidden when switching away from the
    # FreeCAD AI workbench — the panel stays open and usable in any other
    # workbench. When False (default), leaving the workbench hides the dock.
    keep_dock_on_workbench_switch: bool = False
    chat_dock_floating: bool = False
    chat_dock_area: str = "right"  # "left", "right", "top", "bottom"
    chat_dock_geometry: list = field(default_factory=list)  # [x, y, w, h] when floating
    chat_dock_tabified_with: list = field(default_factory=list)  # sibling objectNames
    # Base64-encoded QMainWindow.saveState() — captures tabification reliably
    # (surgical tabified_with list can miss tabify-by-drag because no Qt signal
    # fires in that case).
    chat_dock_mw_state: str = ""

    # Retention applied lazily on save. Both dimensions are checked: a file is
    # kept only if it's both within the newest-N AND younger than max_age_days.
    # All default to 0 (disabled) so an upgrade from v0.13.0-alpha never
    # silently deletes user files — opt in via config.json.
    max_saved_conversations: int = 0
    max_session_logs: int = 0
    max_retention_age_days: int = 0
    # Pre-execution recovery snapshots kept in BACKUPS_DIR (#46). 0 = keep all;
    # each source document reuses one hash-tagged file (overwritten), so growth
    # is naturally bounded by the number of distinct documents ever edited.
    max_backups: int = 0

    def __post_init__(self):
        self._ensure_profile()

    def _ensure_profile(self) -> None:
        """Guarantee at least one profile and a valid active label.

        A config must never leave the dialog unusable, so an empty or
        dangling ``active_profile`` resolves rather than raising. Cheap
        and idempotent, so the ``provider`` property can call it on every
        access and never hand back a KeyError.
        """
        if not self.profiles:
            default = ProviderConfig()
            self.profiles = {default.name: default}
            self.active_profile = default.name
        if self.active_profile not in self.profiles:
            self.active_profile = next(iter(self.profiles))

    @property
    def provider(self) -> ProviderConfig:
        """The active profile.

        Every ``cfg.provider.*`` read and write in the codebase means "the
        active chat connection", so they all keep working through here.
        Reads AND writes: the FreeCAD parameter-store bridge assigns to
        ``cfg.provider.model`` etc., and those land in the stored profile.
        """
        self._ensure_profile()
        return self.profiles[self.active_profile]

    @property
    def supports_vision(self) -> bool:
        """Whether the current LLM supports vision (images in content blocks)."""
        if self.vision_override is not None:
            return self.vision_override
        if self.vision_detected is not None:
            return self.vision_detected
        return False

    @property
    def supports_tools(self) -> bool:
        """Whether the current LLM supports tool calling.

        Detected capability (from Ollama /api/show) takes precedence — it
        catches the case where someone picks an embedding/reranker model
        as the main model on a provider that the static table marks as
        tool-capable. Otherwise fall back to the provider-wide flag.
        """
        if self.tools_detected is not None:
            return self.tools_detected
        from .llm.providers import supports_tools as _provider_supports_tools
        return _provider_supports_tools(self.provider.name)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        data = dict(data)  # never mutate the caller's parsed JSON
        legacy_provider = data.pop("provider", None)
        if not isinstance(legacy_provider, dict):
            # Malformed legacy "provider" (e.g. a bad config.json hand-edit):
            # treat as absent rather than raising deep in the migration path.
            legacy_provider = None

        raw_profiles = data.pop("profiles", None)
        profiles = {
            label: ProviderConfig(**p)
            for label, p in (raw_profiles or {}).items()
        }

        known = {f.name for f in cls.__dataclass_fields__.values()} - {"profiles"}
        filtered = {k: v for k, v in data.items() if k in known}
        cfg = cls(profiles=profiles, **filtered)

        if not raw_profiles:
            cls._migrate_flat_provider(cfg, legacy_provider or {}, data)
        return cfg

    @staticmethod
    def _migrate_flat_provider(cfg: "AppConfig", legacy: dict, data: dict) -> None:
        """Turn a pre-profiles config into one or two profiles.

        Only runs when the JSON carries no ``profiles`` key, so it is a
        one-time upgrade rather than something that fights an already
        migrated file on every load.
        """
        main = ProviderConfig(**{
            k: v for k, v in legacy.items()
            if k in {"name", "api_key", "base_url", "model"}
        })
        main.params = dict(cfg.model_params.get(main.model, {}))
        cfg.profiles = {main.name: main}
        cfg.active_profile = main.name
        if main.api_key:
            cfg.provider_keys[main.name] = main.api_key

        # The old reranker override inherited each empty field from the
        # main provider (chat_widget._build_rerank_llm_client). Bake those
        # `or` fallbacks into a standalone profile.
        if data.get("rerank_llm_model"):
            cfg.profiles["rerank"] = ProviderConfig(
                name=data.get("rerank_llm_provider_name") or main.name,
                base_url=data.get("rerank_llm_base_url") or main.base_url,
                api_key=data.get("rerank_llm_api_key") or main.api_key,
                model=data["rerank_llm_model"],
                params=dict(data.get("rerank_params") or {}),
            )
            cfg.utility_profiles["rerank"] = "rerank"


def _ensure_dirs():
    """Create config directories if they don't exist."""
    for d in (CONFIG_DIR, CONVERSATIONS_DIR, SKILLS_DIR, USER_TOOLS_DIR, HOOKS_DIR, LOGS_DIR, BACKUPS_DIR):
        os.makedirs(d, exist_ok=True)


def load_config() -> AppConfig:
    """Load configuration from disk. Returns defaults if file doesn't exist.

    After loading from JSON, layers any values present in FreeCAD's parameter
    store (BaseApp/Preferences/Mod/FreeCADAI) on top — so changes the user
    made via Edit → Preferences propagate to the workbench's settings on
    next load even though they're written by FreeCAD's Pref* widgets.

    Then mirrors the merged result back to the parameter store so the
    Edit → Preferences page (which reads Pref* widgets directly from the
    param store) reflects current values. Without this, users upgrading
    from a version without the bridge would see blank fields in the
    preferences page until they saved through the AI Settings dialog.
    """
    _ensure_dirs()
    cfg = AppConfig()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            cfg = AppConfig.from_dict(data)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    # Migrate pre-namespace configs: the reranker override model's params used
    # to live in the shared model_params dict. Seed the new rerank_params slot
    # from there so override users don't silently lose their params on upgrade.
    # Idempotent — only fills an empty rerank_params (issue #30 follow-up).
    if cfg.rerank_llm_model and not cfg.rerank_params:
        cfg.rerank_params = dict(cfg.model_params.get(cfg.rerank_llm_model, {}))
    _apply_param_store_overrides(cfg)
    _write_to_param_store(cfg)
    return cfg


def save_config(config: AppConfig):
    """Save configuration to disk and mirror to FreeCAD's parameter store."""
    _ensure_dirs()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config.to_dict(), f, indent=2)
    _write_to_param_store(config)


# ── FreeCAD parameter-store bridge ──────────────────────────────────────
#
# Edit → Preferences uses Gui::Pref* widgets that auto-save to
# BaseApp/Preferences/Mod/FreeCADAI/. AppConfig stores everything in JSON
# at ~/.config/FreeCAD/FreeCADAI/config.json. We mirror the subset of
# fields exposed in the preferences page so both UIs stay coherent.
#
# Indices stored in the param store correspond to the order of items in
# resources/panels/FreeCADAIPrefs.ui — keep these lists in sync.

_PARAM_PROVIDERS = [
    "anthropic", "openai", "ollama", "gemini", "openrouter",
    "moonshot", "deepseek", "qwen", "groq", "mistral", "together",
]
_PARAM_MODES = ["plan", "act"]
_PARAM_THINKING = ["off", "on", "extended"]


def _get_param_group():
    """Return the FreeCAD ParamGet group, or None when running outside FreeCAD."""
    try:
        import FreeCAD
        return FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/FreeCADAI")
    except (ImportError, RuntimeError):
        return None


def _apply_param_store_overrides(cfg: AppConfig) -> None:
    """Layer ParamGet values onto cfg for fields exposed in the prefs page.

    Only overrides when the param store has an explicit value. The Pref*
    widgets only write on first interaction, so an unset key means the user
    hasn't touched the preferences page — JSON value stays authoritative.
    """
    group = _get_param_group()
    if group is None:
        return
    keys = set(group.GetStrings()) | set(group.GetInts()) | set(group.GetBools())

    if "ProviderIndex" in keys:
        idx = group.GetInt("ProviderIndex", 0)
        if 0 <= idx < len(_PARAM_PROVIDERS):
            cfg.provider.name = _PARAM_PROVIDERS[idx]
    if "Model" in keys:
        cfg.provider.model = group.GetString("Model", cfg.provider.model)
    if "BaseUrl" in keys:
        cfg.provider.base_url = group.GetString("BaseUrl", cfg.provider.base_url)
    if "ApiKey" in keys:
        cfg.provider.api_key = group.GetString("ApiKey", cfg.provider.api_key)
    if "ModeIndex" in keys:
        idx = group.GetInt("ModeIndex", 0)
        if 0 <= idx < len(_PARAM_MODES):
            cfg.mode = _PARAM_MODES[idx]
    if "ThinkingIndex" in keys:
        idx = group.GetInt("ThinkingIndex", 0)
        if 0 <= idx < len(_PARAM_THINKING):
            cfg.thinking = _PARAM_THINKING[idx]
    if "MaxTokens" in keys:
        cfg.max_tokens = group.GetInt("MaxTokens", cfg.max_tokens)
    if "EnableTools" in keys:
        cfg.enable_tools = group.GetBool("EnableTools", cfg.enable_tools)


def _write_to_param_store(cfg: AppConfig) -> None:
    """Mirror cfg values to ParamGet so the preferences page reflects them.

    Lets the user open Edit → Preferences after using the Settings dialog
    and see current values rather than stale Pref widget defaults.
    """
    group = _get_param_group()
    if group is None:
        return
    if cfg.provider.name in _PARAM_PROVIDERS:
        group.SetInt("ProviderIndex", _PARAM_PROVIDERS.index(cfg.provider.name))
    else:
        # Provider isn't representable in the prefs combo (e.g. "custom",
        # "github", "huggingface", "zhipu"). Clear any stale index left
        # over from a previous prefs-page interaction so the load path
        # doesn't shadow the JSON name with a wrong provider. See #12.
        try:
            group.RemInt("ProviderIndex")
        except (AttributeError, RuntimeError):
            pass
    group.SetString("Model", cfg.provider.model)
    group.SetString("BaseUrl", cfg.provider.base_url)
    group.SetString("ApiKey", cfg.provider.api_key)
    if cfg.mode in _PARAM_MODES:
        group.SetInt("ModeIndex", _PARAM_MODES.index(cfg.mode))
    if cfg.thinking in _PARAM_THINKING:
        group.SetInt("ThinkingIndex", _PARAM_THINKING.index(cfg.thinking))
    group.SetInt("MaxTokens", int(cfg.max_tokens))
    group.SetBool("EnableTools", bool(cfg.enable_tools))


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
