"""Tests for the runtime branding lookup."""

import importlib
import os
import sys
import types

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _reload_branding(monkeypatch, config_values, raises=False):
    """Reload freecad_ai.branding against a stubbed FreeCAD module.

    Passing config_values=None removes FreeCAD entirely, which is what a
    headless run outside the host looks like.
    """
    if config_values is None:
        monkeypatch.setitem(sys.modules, "FreeCAD", None)
    else:
        fake = types.ModuleType("FreeCAD")

        def config_get(key):
            if raises:
                raise RuntimeError("boom")
            return config_values.get(key, "")

        fake.ConfigGet = config_get
        monkeypatch.setitem(sys.modules, "FreeCAD", fake)

    import freecad_ai.branding as branding
    return importlib.reload(branding)


@pytest.fixture(autouse=True)
def restore_branding():
    """Leave the real module state behind for every other test module.

    The names are module-level constants, so anything that captured them at
    import time has to be reloaded too, or a branded value leaks sideways
    into unrelated tests.
    """
    yield
    import freecad_ai.branding as branding
    importlib.reload(branding)
    import freecad_ai.core.system_prompt as sp
    importlib.reload(sp)


class TestAppNameResolution:
    def test_reads_exe_name(self, monkeypatch):
        b = _reload_branding(monkeypatch, {"ExeName": "RioD", "Application": "RioD"})
        assert b.APP_NAME == "RioD"
        assert b.PRODUCT_NAME == "RioD AI"
        assert b.IS_BRANDED is True

    def test_falls_through_to_application(self, monkeypatch):
        """An unbranded ExeName still lets the Application key win."""
        b = _reload_branding(monkeypatch, {"ExeName": "", "Application": "RioD"})
        assert b.APP_NAME == "RioD"

    def test_strips_whitespace(self, monkeypatch):
        b = _reload_branding(monkeypatch, {"ExeName": "  RioD \n"})
        assert b.APP_NAME == "RioD"

    def test_empty_config_falls_back(self, monkeypatch):
        """ConfigGet returns "" for unknown keys rather than raising."""
        b = _reload_branding(monkeypatch, {})
        assert b.APP_NAME == "FreeCAD"
        assert b.PRODUCT_NAME == "FreeCAD AI"
        assert b.IS_BRANDED is False

    def test_config_get_raising_falls_back(self, monkeypatch):
        b = _reload_branding(monkeypatch, {"ExeName": "RioD"}, raises=True)
        assert b.APP_NAME == "FreeCAD"

    def test_no_freecad_module_falls_back(self, monkeypatch):
        """Headless: pytest, console scripts, plain REPL."""
        b = _reload_branding(monkeypatch, None)
        assert b.APP_NAME == "FreeCAD"
        assert b.IS_BRANDED is False


class TestTranslateBranded:
    def test_substitutes_both_placeholders(self, monkeypatch):
        _reload_branding(monkeypatch, {"ExeName": "RioD"})
        import freecad_ai.i18n as i18n
        out = i18n.translate_branded("Ctx", "%2 runs inside %1.")
        assert out == "RioD AI runs inside RioD."

    def test_leaves_text_without_placeholders_alone(self, monkeypatch):
        _reload_branding(monkeypatch, {"ExeName": "RioD"})
        import freecad_ai.i18n as i18n
        assert i18n.translate_branded("Ctx", "Open AI Chat") == "Open AI Chat"

    def test_repeated_placeholder(self, monkeypatch):
        _reload_branding(monkeypatch, {"ExeName": "RioD"})
        import freecad_ai.i18n as i18n
        out = i18n.translate_branded("Ctx", "Leave the %2 workbench to hide %2.")
        assert out == "Leave the RioD AI workbench to hide RioD AI."


class TestInitGuiUnderExecScoping:
    """FreeCAD exec()s InitGui.py with SEPARATE globals and locals mappings.

    Top-level bindings land in locals, but class and function bodies resolve
    free names through globals — so a module-level ``from ... import X`` is
    invisible inside any method, and using it there raises NameError at
    startup. Every brand lookup in InitGui.py must therefore be a local
    import. runpy/import-based checks pass one shared dict and do NOT catch
    this.
    """

    def _exec_initgui(self, monkeypatch, app_name="RioD"):
        _reload_branding(monkeypatch, {"ExeName": app_name})

        recorded = {"toolbars": [], "menus": [], "commands": {}, "prefs_page": None,
                    "workbench": None}

        class _Workbench:
            def appendToolbar(self, name, cmds):
                recorded["toolbars"].append(name)

            def appendMenu(self, name, cmds):
                recorded["menus"].append(name)

        gui = types.ModuleType("FreeCADGui")
        gui.Workbench = _Workbench
        gui.addLanguagePath = lambda p: None
        gui.updateLocale = lambda: None
        gui.addIconPath = lambda p: None
        gui.addPreferencePage = lambda ui, group: recorded.__setitem__("prefs_page", group)
        gui.addCommand = lambda name, obj: recorded["commands"].__setitem__(name, obj)
        gui.addWorkbench = lambda wb: recorded.__setitem__("workbench", wb)
        monkeypatch.setitem(sys.modules, "FreeCADGui", gui)

        # Resolve resource lookups so every guarded branch actually runs.
        import freecad_ai.paths as paths
        monkeypatch.setattr(paths, "get_prefs_ui_path", lambda: "prefs.ui")
        monkeypatch.setattr(paths, "get_icon_path", lambda: "icon.svg")
        monkeypatch.setattr(paths, "get_translations_path", lambda: "translations")
        monkeypatch.setattr(paths, "get_icons_dir", lambda: "icons")

        src = open(os.path.join(PROJECT_ROOT, "InitGui.py"), encoding="utf-8").read()
        g = {"__name__": "__main__", "__builtins__": __builtins__}
        exec(compile(src, "InitGui.py", "exec"), g, {})
        return recorded

    def test_no_name_error_and_everything_branded(self, monkeypatch):
        rec = self._exec_initgui(monkeypatch)
        wb = rec["workbench"]
        wb.Initialize()

        assert wb.MenuText == "RioD AI"
        assert rec["toolbars"] == ["RioD AI"]
        assert rec["menus"] == ["RioD AI"]
        assert rec["prefs_page"] == "RioD AI"

        assert set(rec["commands"]) == {
            "FreeCADAI_OpenChat", "FreeCADAI_OpenSettings", "FreeCADAI_ToggleKeepDock",
        }
        for cmd in rec["commands"].values():
            res = cmd.GetResources()
            assert res["GroupName"] == "RioD AI"
            assert "FreeCAD" not in res["ToolTip"]

    def test_command_ids_are_not_rebranded(self, monkeypatch):
        """Command IDs persist in user keyboard shortcuts — they must not move."""
        rec = self._exec_initgui(monkeypatch)
        assert all(name.startswith("FreeCADAI_") for name in rec["commands"])


class TestSystemPromptAnchor:
    def test_branded_prompt_anchors_to_freecad(self, monkeypatch):
        """The model has no training signal for the brand — keep the API anchor."""
        _reload_branding(monkeypatch, {"ExeName": "RioD"})
        import freecad_ai.core.system_prompt as sp
        importlib.reload(sp)
        assert "You are RioD AI" in sp.IDENTITY
        assert "built on FreeCAD" in sp.IDENTITY
        assert "FreeCAD Python API" in sp.IDENTITY

    def test_unbranded_prompt_drops_circular_anchor(self, monkeypatch):
        _reload_branding(monkeypatch, {})
        import freecad_ai.core.system_prompt as sp
        importlib.reload(sp)
        assert "You are FreeCAD AI" in sp.IDENTITY
        assert "built on FreeCAD" not in sp.IDENTITY
        assert "You understand the FreeCAD Python API deeply." in sp.IDENTITY
