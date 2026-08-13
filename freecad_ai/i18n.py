"""Internationalization helpers for FreeCAD AI workbench."""

try:
    from FreeCAD import Qt
    translate = Qt.translate
except Exception:
    def translate(context, text):
        return text

def translate_branded(context, text):
    """translate() with the brand placeholders filled in.

    Source strings use %1 for the host application name and %2 for this
    product's name, matching the Qt convention the host uses in C++
    (src/Gui/CommandStd.cpp, src/Gui/Assistant.cpp). FreeCAD.Qt.translate
    returns a plain str, so Qt's QString.arg() is not available here.
    """
    from .branding import APP_NAME, PRODUCT_NAME
    return translate(context, text).replace("%2", PRODUCT_NAME).replace("%1", APP_NAME)


try:
    from PySide2.QtCore import QT_TRANSLATE_NOOP
except Exception:
    try:
        from PySide6.QtCore import QT_TRANSLATE_NOOP
    except Exception:
        def QT_TRANSLATE_NOOP(context, text):
            return text
