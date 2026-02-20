"""FreeCAD AI Workbench — GUI initialization."""

import FreeCADGui as Gui
import FreeCAD as App


class FreeCADAIWorkbench(Gui.Workbench):
    """AI assistant workbench for FreeCAD."""

    MenuText = "FreeCAD AI"
    ToolTip = "AI-powered assistant for 3D modeling"

    def __init__(self):
        from freecad_ai.paths import get_icon_path
        icon = get_icon_path()
        if icon:
            self.__class__.Icon = icon

    def Initialize(self):
        """Called when the workbench is first activated."""
        self.appendToolbar("FreeCAD AI", ["FreeCADAI_OpenChat", "FreeCADAI_OpenSettings"])
        self.appendMenu("FreeCAD AI", ["FreeCADAI_OpenChat", "FreeCADAI_OpenSettings"])

    def Activated(self):
        """Called when the workbench is selected."""
        from freecad_ai.ui.chat_widget import get_chat_dock
        dock = get_chat_dock()
        if dock:
            dock.show()

    def Deactivated(self):
        """Called when leaving this workbench."""
        from freecad_ai.ui.chat_widget import get_chat_dock
        dock = get_chat_dock(create=False)
        if dock:
            dock.hide()

    def GetClassName(self):
        return "Gui::PythonWorkbench"


class OpenChatCommand:
    """Command to open/show the AI chat panel."""

    def GetResources(self):
        from freecad_ai.paths import get_icon_path
        d = {
            "MenuText": "Open AI Chat",
            "ToolTip": "Open the FreeCAD AI chat panel",
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
        return {
            "MenuText": "AI Settings",
            "ToolTip": "Configure FreeCAD AI providers and options",
        }

    def Activated(self, index=0):
        from freecad_ai.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(Gui.getMainWindow())
        dlg.exec()

    def IsActive(self):
        return True


Gui.addCommand("FreeCADAI_OpenChat", OpenChatCommand())
Gui.addCommand("FreeCADAI_OpenSettings", OpenSettingsCommand())
Gui.addWorkbench(FreeCADAIWorkbench())
