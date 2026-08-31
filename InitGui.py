"""FreeCAD AI Workbench — GUI initialization."""

import FreeCADGui as Gui
import FreeCAD as App


class FreeCADAIWorkbench(Gui.Workbench):
    """AI assistant workbench for FreeCAD."""

    # FreeCAD auto-translates MenuText/ToolTip using class name as context.
    # The .qm file provides translations under the "FreeCADAIWorkbench" context.
    # MenuText is the unbranded default; __init__ replaces it with the host's
    # branded product name.
    MenuText = "FreeCAD AI"
    ToolTip = "AI-powered assistant for 3D modeling"

    def __init__(self):
        from freecad_ai.branding import PRODUCT_NAME
        from freecad_ai.paths import get_icon_path
        # Assigned here rather than in the class body: FreeCAD exec()s this
        # file with separate globals/locals mappings, so class and function
        # bodies cannot see module-level names. Same reason Icon is set here.
        self.__class__.MenuText = PRODUCT_NAME
        icon = get_icon_path()
        if icon:
            self.__class__.Icon = icon

    def Initialize(self):
        """Called when the workbench is first activated."""
        from freecad_ai.branding import PRODUCT_NAME
        self.appendToolbar(PRODUCT_NAME, ["FreeCADAI_OpenChat", "FreeCADAI_OpenSettings",
                                          "FreeCADAI_ToggleMCPServer"])
        self.appendMenu(PRODUCT_NAME, ["FreeCADAI_OpenChat", "FreeCADAI_OpenSettings",
                                       "FreeCADAI_ToggleMCPServer","FreeCADAI_ToggleKeepDock"])

    def Activated(self):
        """Called when the workbench is selected."""
        from freecad_ai.ui.chat_widget import get_chat_dock
        dock = get_chat_dock()
        if dock:
            dock.show()
        self._sync_command_ticks()

    def _sync_command_ticks(self):
        """Push both checkable commands' state onto their actions.

        FreeCAD never asks a Python command whether it is checked, so the
        ticks are wrong until something pushes them. Both are stale at this
        point for real reasons: a server started from the command line or the
        Python console is already running before this workbench was ever
        opened, and the keep-dock flag is restored from the saved config.

        Split out of ``Activated`` so it is testable without a QApplication.
        """
        from freecad_ai.ui.command_state import set_command_checked
        try:
            from freecad_ai.mcp.gui_server import get_server_controller
            set_command_checked("FreeCADAI_ToggleMCPServer",
                                get_server_controller().is_running())
        except Exception:
            pass
        try:
            from freecad_ai.config import get_config
            set_command_checked("FreeCADAI_ToggleKeepDock",
                                get_config().keep_dock_on_workbench_switch)
        except Exception:
            pass

    def Deactivated(self):
        """Called when leaving this workbench.

        By default the chat dock is hidden when leaving the workbench. When
        the ``keep_dock_on_workbench_switch`` setting is enabled, the dock
        stays open so the FreeCAD AI panel remains usable in other
        workbenches.
        """
        try:
            from freecad_ai.config import get_config
            if get_config().keep_dock_on_workbench_switch:
                return
        except Exception:
            pass
        from freecad_ai.ui.chat_widget import get_chat_dock
        dock = get_chat_dock(create=False)
        if dock:
            dock.hide()

    def GetClassName(self):
        return "Gui::PythonWorkbench"


class OpenChatCommand:
    """Command to open/show the AI chat panel."""

    def GetResources(self):
        from freecad_ai.branding import PRODUCT_NAME
        from freecad_ai.paths import get_icon_path
        from freecad_ai.i18n import translate, translate_branded
        d = {
            "GroupName": PRODUCT_NAME,
            "MenuText": translate("OpenChatCommand", "Open AI Chat"),
            "ToolTip": translate_branded("OpenChatCommand", "Open the %2 chat panel"),
        }
        icon = get_icon_path()
        if icon:
            d["Pixmap"] = icon
        return d

    def Activated(self, index=0):
        from freecad_ai.ui.chat_widget import get_chat_dock
        dock = get_chat_dock()
        if dock:
            dock.show()
            dock.raise_()

    def IsActive(self):
        return True


class OpenSettingsCommand:
    """Command to open the settings dialog."""

    def GetResources(self):
        from freecad_ai.branding import PRODUCT_NAME
        from freecad_ai.i18n import translate, translate_branded
        return {
            "GroupName": PRODUCT_NAME,
            "MenuText": translate("OpenSettingsCommand", "AI Settings"),
            "ToolTip": translate_branded("OpenSettingsCommand", "Configure %2 providers and options"),
        }

    def Activated(self, index=0):
        from freecad_ai.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(Gui.getMainWindow())
        dlg.exec()

    def IsActive(self):
        return True


class ToggleKeepDockCommand:
    """Command to toggle 'keep chat panel open across workbench switches'.

    Registered as a command so it can be bound to a keyboard shortcut via
    Edit -> Preferences -> Keyboard. Flips the persisted config flag and
    syncs the dock's visibility so the keybind has an immediate effect.
    """

    def GetResources(self):
        from freecad_ai.branding import PRODUCT_NAME
        from freecad_ai.i18n import translate, translate_branded
        return {
            "GroupName": PRODUCT_NAME,
            "MenuText": translate("ToggleKeepDockCommand", "Keep Chat Panel Open"),
            "ToolTip": translate_branded(
                "ToggleKeepDockCommand",
                "Toggle whether the %2 chat panel stays open when "
                "switching to other workbenches"),
            # False = starts unticked, matching the config default. FreeCAD
            # 1.1.x reads this as the action's *initial* state rather than as
            # "may this be checked", and never calls IsChecked(), so True
            # showed a permanent checkmark whatever the setting said (#62).
            "Checkable": False,
        }

    def Activated(self, index=0):
        from freecad_ai.config import get_config, save_current_config
        cfg = get_config()
        cfg.keep_dock_on_workbench_switch = not cfg.keep_dock_on_workbench_switch
        save_current_config()
        # Make the change visible right away: showing when turned on,
        # hiding when turned off.
        from freecad_ai.ui.chat_widget import get_chat_dock
        if cfg.keep_dock_on_workbench_switch:
            dock = get_chat_dock()
            if dock:
                dock.show()
                dock.raise_()
        else:
            dock = get_chat_dock(create=False)
            if dock:
                dock.hide()
        self._sync_action()

    def _sync_action(self):
        """FreeCAD never calls IsChecked(), so drive the tick by hand (#62)."""
        from freecad_ai.ui.command_state import set_command_checked
        from freecad_ai.config import get_config
        set_command_checked("FreeCADAI_ToggleKeepDock",
                            get_config().keep_dock_on_workbench_switch)

    def IsChecked(self):
        # Kept for a future FreeCAD that honours it; 1.1.x never calls this,
        # so do not trust it to drive the tick — _sync_action() does that.
        try:
            from freecad_ai.config import get_config
            return bool(get_config().keep_dock_on_workbench_switch)
        except Exception:
            return False

    def IsActive(self):
        return True


class ToggleMCPServerCommand:
    """Start/stop the HTTP+SSE MCP server inside this FreeCAD process.

    Checkable, and the tick is driven from the shared controller rather than
    any state of its own — so a server started with
    ``FreeCAD.AppImage mcp_server_http.py`` or from the Python console shows
    as on here, and can be stopped from this button.

    FreeCAD 1.1.x reads ``Checkable`` as the action's *initial* tick state —
    the key's mere presence is what makes the action checkable — and never
    calls ``IsChecked()`` on a Python command. So the value below must be
    False, and the tick has to be pushed by ``_sync_action()``.
    """

    def GetResources(self):
        from freecad_ai.branding import PRODUCT_NAME
        from freecad_ai.i18n import translate, translate_branded
        return {
            "GroupName": PRODUCT_NAME,
            "MenuText": translate("ToggleMCPServerCommand", "MCP Server"),
            "ToolTip": translate_branded(
                "ToggleMCPServerCommand",
                "Start or stop the MCP server, letting external clients such "
                "as Claude Code drive this %1 session. The server has no "
                "authentication; set its address in AI Settings."),
            # False = starts unticked. FreeCAD treats this as the initial
            # state, not as "may this action be checked"; True showed a ticked
            # button on a fresh session with no server running.
            "Checkable": False,
        }

    def Activated(self, index=0):
        from freecad_ai.mcp.gui_server import (
            get_server_controller, resolve_server_address)
        controller = get_server_controller()

        if controller.is_running():
            controller.stop()
            from freecad_ai.branding import PRODUCT_NAME
            App.Console.PrintMessage(
                "%s: MCP server stopped\n" % PRODUCT_NAME)
            self._sync_action()
            return

        from freecad_ai.config import get_config
        host, port = resolve_server_address(get_config())
        try:
            url = controller.start(host, port)
        except OSError as exc:
            self._report_failure(host, port, exc)
            self._sync_action()  # a failed start must leave the button unticked
            return

        from freecad_ai.branding import PRODUCT_NAME
        App.Console.PrintMessage(
            "%s: MCP server listening on %s\n" % (PRODUCT_NAME, url))
        window = Gui.getMainWindow()
        if window:
            window.statusBar().showMessage(
                "MCP server listening on %s" % url, 10000)
        self._sync_action()

    def _sync_action(self):
        """FreeCAD 1.1.x never calls IsChecked() on a Python command, so the
        action's tick has to be driven from the controller by hand."""
        try:
            from freecad_ai.mcp.gui_server import get_server_controller
            from freecad_ai.ui.command_state import set_command_checked
            set_command_checked("FreeCADAI_ToggleMCPServer",
                                get_server_controller().is_running())
        except Exception:
            pass

    def _report_failure(self, host, port, exc):
        """Modal, because the click has to visibly fail.

        This used to be a traceback in a daemon thread that nothing surfaced.
        """
        from freecad_ai.branding import PRODUCT_NAME
        from freecad_ai.i18n import translate_branded
        from freecad_ai.ui.compat import QtWidgets
        message = translate_branded(
            "ToggleMCPServerCommand",
            "Could not start the MCP server on {address}.\n\n{error}\n\n"
            "Change the address in %2 → AI Settings → "
            "MCP Servers.").format(address="%s:%d" % (host, port), error=exc)
        App.Console.PrintError(
            "%s: %s\n" % (PRODUCT_NAME, message.replace("\n\n", " ")))
        QtWidgets.QMessageBox.warning(
            Gui.getMainWindow(),
            translate("ToggleMCPServerCommand", "MCP server failed to start"),
            message)

    def IsChecked(self):
        # Kept for a future FreeCAD that honours it; 1.1.x never calls this,
        # so do not trust it to drive the tick — _sync_action() does that.
        try:
            from freecad_ai.mcp.gui_server import get_server_controller
            return get_server_controller().is_running()
        except Exception:
            return False

    def IsActive(self):
        return True


# Register translation path early so command strings are translated
# before the workbench is activated.
try:
    from freecad_ai.paths import get_translations_path as _gtp
    _tr_path = _gtp()
    if _tr_path:
        Gui.addLanguagePath(_tr_path)
        Gui.updateLocale()
except Exception:
    pass

# Register the icons directory so FreeCAD can find preferences-freecadai.svg
# (the sidebar icon for our Edit → Preferences page).
try:
    from freecad_ai.paths import get_icons_dir as _gid
    _icons_dir = _gid()
    if _icons_dir:
        Gui.addIconPath(_icons_dir)
except Exception:
    pass

# Register the preferences page in Edit → Preferences. The
# Gui::Pref* widgets in the form auto-save to BaseApp/Preferences/Mod/FreeCADAI;
# our config layer mirrors values from there into ~/.config/FreeCAD/FreeCADAI/config.json
# on load so both this page and the workbench's Settings dialog stay in sync.
try:
    from freecad_ai.branding import PRODUCT_NAME as _pn
    from freecad_ai.paths import get_prefs_ui_path as _gpup
    _prefs_ui = _gpup()
    if _prefs_ui:
        Gui.addPreferencePage(_prefs_ui, _pn)
except Exception:
    pass

# Seed the FreeCAD parameter store from JSON so the preferences page shows
# current values even when the user goes straight to Edit → Preferences
# without first activating the workbench. load_config writes JSON values to
# the param store, where the Gui::Pref* widgets read from.
try:
    from freecad_ai.config import get_config as _gcfg
    _gcfg()
except Exception:
    pass

Gui.addCommand("FreeCADAI_OpenChat", OpenChatCommand())
Gui.addCommand("FreeCADAI_OpenSettings", OpenSettingsCommand())
Gui.addCommand("FreeCADAI_ToggleKeepDock", ToggleKeepDockCommand())
Gui.addCommand("FreeCADAI_ToggleMCPServer", ToggleMCPServerCommand())
Gui.addWorkbench(FreeCADAIWorkbench())
