"""Internationalization helpers for FreeCAD AI workbench."""

try:
    from FreeCAD import Qt
    translate = Qt.translate
except Exception:
    def translate(context, text):
        return text

try:
    from PySide2.QtCore import QT_TRANSLATE_NOOP
except Exception:
    try:
        from PySide6.QtCore import QT_TRANSLATE_NOOP
    except Exception:
        def QT_TRANSLATE_NOOP(context, text):
            return text
